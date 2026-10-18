"""PDF doc -> central JSON doc file (same chunk format as chunks.jsonl), so mine/scenarios/export work
unchanged. Paragraphs keep their nearest heading; table rows become table_row chunks with parts joined by
' | ' (signature | description | example → result), like the HTML chunker. Chunk ids carry the page:
"<stem>#pg3p12" (paragraph) and "<stem>#pg3t2r5" (table 2, row 5)."""
from __future__ import annotations

import logging
import re
import time
from collections import Counter
from pathlib import Path

from .schemas import Chunk
from .text import norm

MIN_CHARS = 40
X_TOLERANCE = 1  # max gap (points) inside a word; pdfplumber's default 3 glues "target jsonb" in small fonts
BOX_TITLES = {"note", "tip", "warning", "caution", "important"}
TABLE_TITLE = re.compile(r"^Table \d+(\.\d+)*\.?\s")
PAGE_NUMBER = re.compile(r"^(page\s*)?\d+(\s*(of|/)\s*\d+)?$", re.I)


def _inside(w: dict, box) -> bool:
    x0, top, x1, bottom = box
    return w["x0"] >= x0 - 1 and w["x1"] <= x1 + 1 and w["top"] >= top - 1 and w["bottom"] <= bottom + 1


def _lines(words: list[dict]) -> list[dict]:
    """Group words into visual lines."""
    lines = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        # same line when the word overlaps the line vertically (code fonts sit a little lower)
        if lines and w["top"] < lines[-1]["bottom"] - 1 and w["bottom"] > lines[-1]["top"] + 1:
            lines[-1]["words"].append(w)
            lines[-1]["bottom"] = max(lines[-1]["bottom"], w["bottom"])
        else:
            lines.append({"top": w["top"], "bottom": w["bottom"], "words": [w]})
    for ln in lines:
        ws = sorted(ln["words"], key=lambda w: w["x0"])
        ln["text"] = " ".join(w["text"] for w in ws)
        ln["bottom"] = max(w["bottom"] for w in ws)
        ln["size"] = Counter(round(w["size"], 1) for w in ws).most_common(1)[0][0]
        ln["bold"] = sum("bold" in w["fontname"].lower() for w in ws) > len(ws) / 2
        ln["n"] = len(ws)
    return lines


def _split_cell(cell: str) -> list[str]:
    """A function-table cell holds signature line(s), wrapped description lines and example line(s).
    Lines with '→' are their own part; consecutive other lines are one wrapped sentence."""
    parts, buf = [], []
    for line in (cell or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if "→" in line:
            if buf:
                parts.append(" ".join(buf))
                buf = []
            parts.append(line)
        else:
            buf.append(line)
    if buf:
        parts.append(" ".join(buf))
    return [norm(p) for p in parts if norm(p)]


def _join(lines: list[str]) -> str:
    out = ""
    for ln in lines:
        if out.endswith("-") and len(out) > 1 and out[-2].isalpha() and ln[:1].islower():
            out = out[:-1] + ln  # word hyphenated across lines
        else:
            out = f"{out} {ln}" if out else ln
    return norm(out)


def pdf_to_chunks(path, stem: str) -> tuple[list[Chunk], dict]:
    try:
        import pdfplumber
    except ImportError as e:
        raise SystemExit("PDF support needs pdfplumber: .venv/bin/pip install -e '.[pdf]'") from e
    logging.getLogger("pdfminer").setLevel(logging.ERROR)

    with pdfplumber.open(path) as pdf:
        n_pages = len(pdf.pages)
        title = (pdf.metadata or {}).get("Title") or ""
        pages = []
        for page in pdf.pages:
            tables = page.find_tables()
            boxes = [t.bbox for t in tables]
            words = [w for w in page.extract_words(x_tolerance=X_TOLERANCE, extra_attrs=["size", "fontname"])
                     if not any(_inside(w, b) for b in boxes)]
            pages.append({"lines": _lines(words), "tables": tables})

    # running headers/footers: the same line (digits ignored) on at least half the pages
    def key(t):
        return re.sub(r"\d+", "#", t.strip().lower())
    seen = Counter(k for p in pages for k in {key(ln["text"]) for ln in p["lines"]})
    repeated = {k for k, c in seen.items() if n_pages >= 3 and c >= n_pages / 2}

    sizes = Counter()
    for p in pages:
        for ln in p["lines"]:
            sizes[ln["size"]] += ln["n"]
    body = sizes.most_common(1)[0][0] if sizes else 10

    chunks: list[Chunk] = []
    heading, table_title = title, ""
    p_n = t_n = 0

    def emit_paragraph(buf, pno):
        nonlocal p_n
        text = _join([ln["text"] for ln in buf])
        if len(text) >= MIN_CHARS:
            p_n += 1
            chunks.append(Chunk(chunk_id=f"{stem}#pg{pno}p{p_n}", kind="paragraph", heading=heading, text=text))

    for pno, p in enumerate(pages, 1):
        items = [(ln["top"], "line", ln) for ln in p["lines"]
                 if key(ln["text"]) not in repeated and not PAGE_NUMBER.match(ln["text"].strip())]
        items += [(t.bbox[1], "table", t) for t in p["tables"]]
        items.sort(key=lambda x: x[0])
        buf = []
        for _, kind, obj in items:
            if kind == "table":
                if buf:
                    emit_paragraph(buf, pno)
                    buf = []
                t_n += 1
                head = " / ".join(x for x in (heading, table_title) if x)
                for r_n, row in enumerate(obj.extract(x_tolerance=X_TOLERANCE), 1):
                    parts = [part for cell in row for part in _split_cell(cell)]
                    text = " | ".join(parts)
                    if len(text) >= MIN_CHARS:
                        chunks.append(Chunk(chunk_id=f"{stem}#pg{pno}t{t_n}r{r_n}", kind="table_row",
                                            heading=head, text=text))
                continue
            ln = obj
            text = ln["text"].strip()
            is_heading = ln["n"] <= 20 and (ln["size"] >= body * 1.15 or (ln["bold"] and ln["n"] <= 12
                                                                            and not text.endswith(".")))
            if TABLE_TITLE.match(text):
                if buf:
                    emit_paragraph(buf, pno)
                    buf = []
                table_title = norm(text)
                continue
            if is_heading and text.lower().rstrip(":") in BOX_TITLES:
                continue  # "Note"/"Tip" box titles are not section headings
            if is_heading:
                if buf:
                    emit_paragraph(buf, pno)
                    buf = []
                heading, table_title = norm(text), ""
                continue
            if buf:
                prev = buf[-1]
                gap = ln["top"] - prev["bottom"]
                if gap > 0.8 * (prev["bottom"] - prev["top"]) or ln["size"] != prev["size"]:
                    emit_paragraph(buf, pno)
                    buf = []
            buf.append(ln)
        if buf:
            emit_paragraph(buf, pno)  # paragraphs are not continued across pages

    meta = {"pdf": str(Path(path).resolve()), "pages": n_pages, "converted_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    return chunks, meta


def pdf_to_central_json(path, out: Path, stem: str | None = None) -> tuple[Path, list[Chunk]]:
    """Write the central JSON doc file: {"source": {...}, "chunks": [...]}."""
    import json
    stem = stem or Path(path).stem
    chunks, meta = pdf_to_chunks(path, stem)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"source": meta, "chunks": [c.model_dump() for c in chunks]},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    return out, chunks
