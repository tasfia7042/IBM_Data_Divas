"""Calls Phase 1 (doctest pipeline: documents -> scenarios) and Phase 2 (DBMS_testgen generator: scenarios ->
DBMS_testgen/results/all_tests.json). Pipeline logic stays in doctest/ and DBMS_testgen/; this file only reads
uploads into Phase 1's formats, calls the phases, and stores Phase 1 output per run. It never connects to a
database and never runs SQL."""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "doctest" / "src"))  # works even if doctestlx is not pip-installed

from doctestlx import chunk as chunk_mod  # noqa: E402
from doctestlx import mine as mine_mod  # noqa: E402
from doctestlx import reqtxt  # noqa: E402
from doctestlx import scenario as scenario_mod  # noqa: E402
from doctestlx.config import load_config  # noqa: E402
from doctestlx.llm import LLM, LLMUnavailable  # noqa: E402
from doctestlx.schemas import Chunk, Requirement  # noqa: E402
from doctestlx.text import norm  # noqa: E402

from jobs import JobError  # noqa: E402

log = logging.getLogger("doctest-ui")
RUNS = Path(os.environ.get("DOCTEST_RUNS_DIR", ROOT / "runs"))
DEMO = HERE / "demo"
TESTGEN = ROOT / "DBMS_testgen"
ALLOWED = {".txt", ".pdf", ".csv", ".md", ".html", ".htm"}
REQUIREMENT_TYPES = {".txt", ".csv"}           # hand-written requirements
MAX_FILE_BYTES = 20 * 1024 * 1024
PHASE2_SCRIPTS = ("main.py", "generate.py", "run.py", "testgen.py", "cli.py")  # auto-detected in DBMS_testgen/


def tests_path() -> Path:
    p = Path(os.environ.get("TESTS_PATH", "DBMS_testgen/results/all_tests.json"))
    return p if p.is_absolute() else ROOT / p


def pipeline_config():
    """doctest's config.yaml, with LLM_BASE_URL / LLM_MODEL / LLM_API_KEY taking precedence."""
    cfg = load_config(str(ROOT / "doctest" / "config.yaml"))
    for env, field in (("LLM_BASE_URL", "base_url"), ("LLM_MODEL", "model"), ("LLM_API_KEY", "api_key")):
        if os.environ.get(env):
            setattr(cfg.llm, field, os.environ[env])
    return cfg


# ---------------------------------------------------------------- run storage

class Run:
    """runs/<run_id>/: files/ (uploads), scenarios.json (Phase 1 output), scenarios_view.json, meta.json."""

    def __init__(self, run_id: str):
        self.id = run_id
        self.dir = RUNS / run_id
        self.files_dir = self.dir / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def new(cls) -> "Run":
        return cls(time.strftime("%Y%m%d-%H%M%S"))

    @classmethod
    def latest(cls) -> "Run":
        runs = sorted((p for p in RUNS.glob("*") if p.is_dir()), key=lambda p: p.stat().st_mtime) if RUNS.exists() else []
        return cls(runs[-1].name) if runs else cls.new()

    def read(self, name: str, default=None):
        p = self.dir / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

    def write(self, name: str, obj) -> None:
        tmp = self.dir / (name + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.dir / name)

    def clear(self, *names: str) -> None:
        for n in names:
            (self.dir / n).unlink(missing_ok=True)

    def files(self) -> list[dict]:
        return [{"name": p.name, "type": p.suffix.lower().lstrip("."), "size": p.stat().st_size,
                 "role": "requirements" if p.suffix.lower() in REQUIREMENT_TYPES else "document"}
                for p in sorted(self.files_dir.iterdir()) if p.is_file() and not p.name.startswith(".")]

    def meta(self) -> dict:
        return self.read("meta.json", {}) or {}


def safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(name).name).strip(" .")
    return name or "file"


def save_upload(run: Run, name: str, data: bytes) -> str:
    """Store one uploaded file; JobError with a readable message if it is not acceptable."""
    name = safe_name(name)
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise JobError(f"{name}: file type {ext or '(none)'} is not supported. Use .txt, .pdf, .csv, .md or .html.")
    if len(data) > MAX_FILE_BYTES:
        raise JobError(f"{name}: file is larger than 20 MB.")
    if not data:
        raise JobError(f"{name}: file is empty.")
    (run.files_dir / name).write_bytes(data)
    return name


# ---------------------------------------------------------------- reading uploads into Phase 1 formats

_HEADING = re.compile(r"^(\d+(\.\d+)*\.?|[A-Z]\.)\s+[A-Z][^.]{2,80}$")
_NEW_BLOCK = re.compile(r"^(\d+(\.\d+)*\.?\s+\S|REQ-[A-Z]+-\d+|[•\-*]\s|Table \d)")


def _stem(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", path.stem)


def _lines_to_chunks(lines, stem, page, heading, start):
    """Group text lines into paragraph chunks; numbered headings set the heading."""
    chunks, buf = [], []

    def flush():
        text = norm(" ".join(buf))
        if len(text) >= 40:
            n = start + len(chunks) + 1
            chunks.append((Chunk(chunk_id=f"{stem}#pg{page}p{n}" if page else f"{stem}#p{n}", kind="paragraph",
                                 heading=heading, text=text), page))
        buf.clear()

    for raw in lines:
        line = raw.strip()
        if not line:
            flush()
            continue
        if _HEADING.match(line) and not line.endswith("."):
            flush()
            heading = norm(line)
            continue
        if _NEW_BLOCK.match(line):
            flush()
        buf.append(line)
    flush()
    return chunks, heading


def read_pdf(path: Path):
    from pypdf import PdfReader
    out, heading, stem = [], "", _stem(path)
    for pno, page in enumerate(PdfReader(str(path)).pages, 1):
        lines = [ln for ln in (page.extract_text() or "").splitlines() if not re.search(r"\|\s*Page \d+\s*$", ln)]
        chunks, heading = _lines_to_chunks(lines, stem, pno, heading, len(out))
        out += chunks
    return out


def read_markdown(path: Path):
    lines = [re.sub(r"^#+\s*", "1. ", ln) if ln.startswith("#") else ln
             for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()]
    return _lines_to_chunks(lines, _stem(path), None, "", 0)[0]


def read_html(path: Path):
    return [(c, None) for c in chunk_mod.chunk_html(path.read_text(encoding="utf-8", errors="replace"), _stem(path))]


def read_csv(path: Path):
    rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig", errors="replace"))))
    if not rows:
        return [], []
    cols = {c.lower().strip(): c for c in rows[0].keys() if c}
    text_col = cols.get("requirement") or cols.get("text") or next(iter(rows[0].keys()))
    id_col = cols.get("id")
    stem, reqs, chunks = _stem(path), [], []
    for i, row in enumerate(rows, 1):
        body = norm(row.get(text_col) or "")
        if not body:
            continue
        sid = (row.get(id_col) or "").strip() if id_col else ""
        cid = f"{stem}#row{i}"
        chunks.append(Chunk(chunk_id=cid, kind="paragraph", heading=sid or f"row {i}", text=body))
        reqs.append(Requirement(req_id="tmp", chunk_id=cid, text=body, feature=sid or f"row {i}", category="normal",
                                source_id=sid or f"row{i}"))
    return reqs, chunks


def read_inputs(run: Run):
    """Uploads -> (hand-written requirements, all chunks, chunk_id -> {file, page})."""
    reqs, chunks, where = [], [], {}
    for f in run.files():
        path = run.files_dir / f["name"]
        ext = path.suffix.lower()
        try:
            if ext == ".txt":
                r, c = reqtxt.parse(path, _stem(path))
                reqs, pairs = reqs + r, [(x, None) for x in c]
            elif ext == ".csv":
                r, c = read_csv(path)
                reqs, pairs = reqs + r, [(x, None) for x in c]
            elif ext == ".pdf":
                pairs = read_pdf(path)
            elif ext == ".md":
                pairs = read_markdown(path)
            else:
                pairs = read_html(path)
        except Exception as e:  # noqa: BLE001
            log.exception("could not read %s", path)
            raise JobError(f"Could not read {f['name']}: {e.__class__.__name__}.") from e
        for c, page in pairs:
            chunks.append(c)
            where[c.chunk_id] = {"file": f["name"], "page": page}
    for i, r in enumerate(reqs, 1):  # one numbering across all requirement files
        r.req_id = f"req-{i:03d}"
    return reqs, chunks, where


# ---------------------------------------------------------------- Phase 1

def run_phase1(run: Run, progress) -> str:
    cfg = pipeline_config()
    progress("Reading uploaded files")
    reqs, chunks, where = read_inputs(run)
    if not chunks:
        raise JobError("No readable text found in the uploaded files.")
    llm = LLM(cfg)
    try:
        if not reqs:  # only reference documents: extract requirements from them first
            reqs, _ = mine_mod.mine(cfg, chunks, llm, progress=lambda d, t: progress(
                f"Extracting requirements: chunk {d + 1} of {t}"))
            if not reqs:
                raise JobError("No testable requirements were found in the documents.")
        cfg.limits.context_chunks = max(cfg.limits.context_chunks, 4)
        scenarios, links, dropped = scenario_mod.build(
            cfg, reqs, chunks, llm, prefix_per_requirement=True,
            progress=lambda i, t, made: progress(f"Building scenarios for requirement {i + 1} of {t} "
                                                 f"({made} scenarios so far)"))
    except LLMUnavailable as e:
        log.warning("LLM unavailable: %s", e)
        raise JobError(f"The language model at {cfg.llm.base_url} is not reachable. Start it (for example "
                       "`ollama serve`), or use Load demo.") from e

    req_by_id = {r.req_id: r for r in reqs}
    req_of = {l["scenario_id"]: l["req_id"] for l in links}
    view = {}
    for s in scenarios:
        req = req_by_id[req_of[s.scenario_id]]
        view[s.scenario_id] = {"requirement_id": req.req_id, "requirement_source_id": req.source_id,
                               "requirement_text": req.text, "requirement_chunk": req.chunk_id,
                               "where": {e.chunk: where.get(e.chunk, {}) for e in s.evidence}
                               | {req.chunk_id: where.get(req.chunk_id, {})}}
    run.write("scenarios.json", [s.model_dump(by_alias=True) for s in scenarios])  # Phase 1 output, unchanged
    run.write("scenarios_view.json", view)
    run.write("scenarios_dropped.json", dropped)
    return f"{len(scenarios)} scenarios from {len(reqs)} requirements"


# ---------------------------------------------------------------- display helpers (no pipeline logic)

def _lower_first(s: str) -> str:
    """'A customer ...' -> 'a customer ...', but keep acronyms and identifiers ('SQL ...', 'ALFKI')."""
    if not s or (len(s) > 1 and (s[1].isupper() or s[1].isdigit())):
        return s
    return s[0].lower() + s[1:]


def plain_text(s: dict) -> str:
    """'Given <precondition>, when <behavior>, expect <expected result>.' from the Phase 1 fields."""
    p = s.get("precondition") or {}
    parts = []
    for t in p.get("schema") or []:
        cols = ", ".join(c.get("name", "") for c in t.get("columns") or [])
        parts.append(f"table {t.get('table')}" + (f" ({cols})" if cols else ""))
    parts += [v if str(k).startswith("_") else f"{k}: {v}" for k, v in (p.get("data") or {}).items()]
    parts += p.get("config") or []
    if p.get("session_state"):
        parts.append(p["session_state"])
    b = s.get("behavior_under_test") or {}
    when = re.sub(r"^\s*(test|check|verify)\s+(that\s+|whether\s+|the\s+)?", "", b.get("description", ""),
                  flags=re.I).rstrip(". ")
    asserts = (s.get("expected_result") or {}).get("assertions") or []
    expect = "; ".join(_lower_first(a.strip().rstrip(".")) for a in asserts[:3]) or b.get("property") or "the documented result"
    given = "; ".join(parts) if parts else "the existing data"
    return f"Given {given}, when {_lower_first(when)}, expect {_lower_first(expect)}."


def confidence_label(c) -> str:
    try:
        c = float(c)
    except (TypeError, ValueError):
        return "unknown"
    return "high" if c >= 0.85 else "medium" if c >= 0.6 else "low"


def _guess_where(chunk_id: str) -> dict:
    """Source of a chunk when the run has no record of it (e.g. demo data): file stem and page from the id."""
    stem, _, rest = chunk_id.partition("#")
    m = re.match(r"pg(\d+)", rest)
    return {"file": stem, "page": int(m.group(1)) if m else None}


def scenario_cards(run: Run) -> list[dict]:
    """Phase 1 scenarios plus what the page needs to show them; `scenario` is the untouched Phase 1 JSON."""
    scenarios = run.read("scenarios.json", []) or []
    view = run.read("scenarios_view.json", {}) or {}
    cards = []
    for i, s in enumerate(scenarios, 1):
        v = view.get(s.get("scenario_id"), {})
        w = v.get("where", {})
        ev = [{**e, **(w.get(e.get("chunk")) or _guess_where(e.get("chunk", "")))} for e in s.get("evidence") or []]
        first = ev[0] if ev else (w.get(v.get("requirement_chunk")) or {})
        cards.append({"label": f"Sn{i}", "id": s.get("scenario_id"), "scenario": s, "text": plain_text(s),
                      "source": {"file": first.get("file"), "page": first.get("page")},
                      "evidence": ev, "confidence_label": confidence_label(s.get("confidence")),
                      "requirement": {k: v.get(k) for k in ("requirement_id", "requirement_source_id", "requirement_text")}})
    return cards


# ---------------------------------------------------------------- Phase 2

def read_tests(path: Path | None = None) -> dict:
    """{tests, path, error}: reads the Phase 2 file fresh; never raises."""
    path = path or tests_path()
    if not path.exists():
        return {"tests": [], "path": str(path), "error": f"No test file yet at {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}. "
                                                       "Run Phase 2, or click Reload file once it exists."}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("cannot read %s: %s", path, e)
        return {"tests": [], "path": str(path), "error": f"The test file is not valid JSON ({e.__class__.__name__})."}
    if isinstance(data, dict):  # tolerate {"tests": [...]}
        data = data.get("tests") or data.get("all_tests") or []
    if not isinstance(data, list):
        return {"tests": [], "path": str(path), "error": "The test file should contain a JSON array of test cases."}
    return {"tests": data, "path": str(path), "error": None}


def phase2_command(scenarios_file: Path) -> list[str] | None:
    """PHASE2_CMD (with {scenarios} and {tests} placeholders), else a known script in DBMS_testgen/."""
    custom = os.environ.get("PHASE2_CMD")
    if custom:
        return shlex.split(custom.replace("{scenarios}", str(scenarios_file)).replace("{tests}", str(tests_path())))
    for name in PHASE2_SCRIPTS:
        if (TESTGEN / name).exists():
            return [sys.executable, str(TESTGEN / name), str(scenarios_file)]
    return None


def run_phase2(run: Run, progress) -> str:
    scenarios_file = run.dir / "scenarios.json"
    if not scenarios_file.exists():
        raise JobError("There are no scenarios yet. Run Phase 1 first.")
    cmd = phase2_command(scenarios_file)
    if not cmd:
        raise JobError("No Phase 2 generator found in DBMS_testgen/. Add it there (or set PHASE2_CMD), or use "
                       "Reload file to load tests produced elsewhere.")
    env = {**os.environ, "SCENARIOS_PATH": str(scenarios_file), "TESTS_PATH": str(tests_path())}
    log.info("Phase 2: %s", " ".join(cmd))
    progress("Running the Phase 2 generator")
    proc = subprocess.Popen(cmd, cwd=str(TESTGEN if TESTGEN.exists() else ROOT), env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    tail = []
    for line in proc.stdout:  # show the generator's latest output line as progress
        line = line.rstrip()
        log.info("phase2: %s", line)
        if line:
            tail = (tail + [line])[-20:]
            progress(f"Phase 2: {line[:140]}")
    if proc.wait() != 0:
        raise JobError(f"The Phase 2 generator stopped with an error (exit code {proc.returncode}). "
                       f"Last output: {tail[-1][:200] if tail else 'none'}. See the server log.")
    got = read_tests()
    if got["error"]:
        raise JobError("Phase 2 finished, but " + got["error"][0].lower() + got["error"][1:])
    return f"{len(got['tests'])} test cases in {Path(got['path']).name}"


# ---------------------------------------------------------------- demo

def load_demo() -> Run:
    run = Run.new()
    shutil.copyfile(DEMO / "scenarios.json", run.dir / "scenarios.json")
    run.write("meta.json", {"demo": True, "tests_path": str(DEMO / "all_tests.json")})
    return run


def tests_for(run: Run) -> dict:
    """Column 3 reads the demo copy in demo mode, otherwise TESTS_PATH."""
    demo_path = run.meta().get("tests_path")
    return read_tests(Path(demo_path) if demo_path else None)
