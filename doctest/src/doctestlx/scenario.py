"""Stage 3: requirement + its chunk + top BM25 neighbours -> scenario in the team format.

The LLM writes feature, category, precondition, behavior_under_test, expected_result, evidence and
ambiguities. Code checks every evidence span against the doc, then fills in related_scenarios and
confidence. Spans not found in the doc are removed and listed in ambiguities; the scenario is kept."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Optional

from pydantic import ValidationError
from rank_bm25 import BM25Okapi
from rapidfuzz import fuzz
from tqdm import tqdm

from . import prompts
from .config import Config
from .llm import LLM
from .schemas import (BehaviorUnderTest, Chunk, Column, Evidence, ExpectedResult, Precondition, Requirement,
                      Scenario, TableSchema)
from .text import CELL_SEP, locate, looks_cut, tokenize

EVIDENCE_MISSING = "evidence not found in docs"
REQUIRED_KEYS = ("behavior_under_test", "expected_result", "evidence")
NO_VALUE = {"", "none", "n/a", "na", "no", "nothing", "null", "no ambiguities", "none found", "not applicable"}
CATEGORIES = {"normal", "boundary", "error"}
KINDS = {"result_set", "scalar", "error", "state_change"}
KIND_ALIASES = {"rows": "result_set", "resultset": "result_set", "result": "result_set", "table": "result_set",
                "set": "result_set", "value": "scalar", "single_value": "scalar", "boolean": "scalar",
                "exception": "error", "failure": "error", "state": "state_change", "side_effect": "state_change"}
_ID_TAG = re.compile(r"\s*\[[^\]\s]*#[^\]\s]*\]\s*")  # "[functions-json#t5r3]" labels copied from the prompt


class Context:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.by_id = {c.chunk_id: c for c in chunks}
        self.bm25 = BM25Okapi([tokenize(c.heading + " " + c.text) or ["_"] for c in chunks])

    def for_requirement(self, req: Requirement, k: int) -> list[Chunk]:
        own = self.by_id[req.chunk_id]
        scores = self.bm25.get_scores(tokenize(req.text))
        ranked = sorted(range(len(self.chunks)), key=lambda i: -scores[i])
        others = [self.chunks[i] for i in ranked if self.chunks[i].chunk_id != own.chunk_id and scores[i] > 0][:k]
        return [own] + others


# ---------------------------------------------------------------- coercion of model output

def _str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v).strip()


def _str_list(v) -> list[str]:
    items = v if isinstance(v, list) else ([] if v is None else [v])
    return [s for s in (_str(x) for x in items) if s.lower().rstrip(".") not in NO_VALUE]


def _columns(v) -> list[Column]:
    cols = []
    for c in v if isinstance(v, list) else []:
        if isinstance(c, dict):
            name = _str(c.get("name") or c.get("column"))
            if name:
                cols.append(Column(name=name, type=_str(c.get("type"))))
        elif _str(c):
            parts = _str(c).split(None, 1)  # "a INTEGER"
            cols.append(Column(name=parts[0], type=parts[1] if len(parts) > 1 else ""))
    return cols


def coerce_precondition(p) -> Precondition:
    if not isinstance(p, dict):
        text = _str(p)
        return Precondition(data={} if text.lower() in NO_VALUE else {"_description": text})
    tables = []
    for t in p.get("schema") or []:
        if isinstance(t, dict) and _str(t.get("table") or t.get("name")):
            tables.append(TableSchema(table=_str(t.get("table") or t.get("name")), columns=_columns(t.get("columns"))))
    data = p.get("data") or {}
    if isinstance(data, dict):
        data = {str(k): _str(v) for k, v in data.items() if _str(v)}
    else:
        data = {} if _str(data).lower() in NO_VALUE else {"_description": _str(data)}
    state = _str(p.get("session_state"))
    return Precondition(schema=tables, data=data, config=_str_list(p.get("config")),
                        session_state=None if state.lower() in NO_VALUE else state)


def coerce_expected(e) -> ExpectedResult:
    if not isinstance(e, dict):
        e = {"assertions": [e]}
    kind = _str(e.get("kind")).lower().replace(" ", "_").replace("-", "_")
    kind = kind if kind in KINDS else KIND_ALIASES.get(kind, "scalar")
    osr = e.get("order_sensitive_rows")
    if isinstance(osr, str):
        osr = osr.strip().lower() in ("true", "yes", "1")
    return ExpectedResult(kind=kind, assertions=_str_list(e.get("assertions")), order_sensitive_rows=bool(osr))


def coerce_behavior(b) -> BehaviorUnderTest:
    if not isinstance(b, dict):
        b = {"description": b}
    return BehaviorUnderTest(description=_str(b.get("description")), sql_sketch=_str(b.get("sql_sketch")),
                             property=_str(b.get("property")))


# ---------------------------------------------------------------- evidence

def find_span(span: str, preferred: Optional[str], context: list[Chunk]) -> Optional[tuple[str, str]]:
    """(chunk_id, exact doc text) for a span, tolerating copied [chunk-id] labels, wrapping quotes and
    spans stitched from several ' | ' cells of one table row. The named chunk is tried first."""
    cleaned = _ID_TAG.sub(" ", span).strip()
    candidates = [span, cleaned]
    if len(cleaned) > 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "'\"":
        candidates.append(cleaned[1:-1])
    ordered = sorted(context, key=lambda c: c.chunk_id != preferred)
    for cand in candidates:
        for c in ordered:
            hit = locate(cand, c.text)
            if hit:
                return c.chunk_id, hit.lstrip(' .,;:"').strip() or hit  # fuzzy snaps can pick up a leading ". "
    pieces = [p.strip() for p in re.split(r"\s\|\s", cleaned) if len(p.strip()) >= 8]
    if len(pieces) > 1:
        for c in ordered:
            hits = [locate(p, c.text) for p in pieces]
            if all(hits):
                return c.chunk_id, " | ".join(hits)
    return None


def check_evidence(raw, context: list[Chunk]) -> tuple[list[Evidence], list[str], int]:
    """Verified evidence, ambiguity notes for spans not found, and how many spans the model gave."""
    items = raw if isinstance(raw, list) else ([] if raw is None else [raw])
    found, notes, given = [], [], 0
    for item in items:
        if isinstance(item, dict):
            span, chunk = _str(item.get("span") or item.get("text")), _str(item.get("chunk")).strip("[] ")
        else:
            span, chunk = _str(item), None
        if not span:
            continue
        given += 1
        hit = find_span(span, chunk, context)
        if hit and all(hit[1] != e.span for e in found):
            found.append(Evidence(chunk=hit[0], span=hit[1]))
        elif not hit:
            notes.append(f"evidence span not found in docs: {span[:120]}")
    if not found:
        notes.append(EVIDENCE_MISSING)
    return found, notes, given


def example_calls(text: str) -> list[str]:
    """Left side of documented example calls in a table row: "json_typeof('-123.4') → number" -> call."""
    calls = []
    for cell in text.split(CELL_SEP):
        left = cell.split("→")[0].strip() if "→" in cell else ""
        if left and ("'" in left or "::" in left):  # a literal: an example, not a signature like "f ( json )"
            calls.append(left)
    return calls


def repair_sketch(sketch: str, req: Requirement, own: Chunk) -> tuple[str, Optional[str]]:
    """Small models cut SQL at the first double quote of a JSON literal. If the sketch looks cut off,
    rebuild it from the documented example call it started; otherwise flag it."""
    if sketch and not looks_cut(sketch):
        return sketch, None
    body = re.sub(r"^\s*SELECT\s+", "", sketch or "", flags=re.I)
    calls = example_calls(req.text) + [c for c in example_calls(own.text) if c not in req.text]
    best = max(calls, key=lambda c: (len(_common_prefix(body, c)), -calls.index(c)), default=None)
    if best and (not body or _common_prefix(body, best)):
        return f"SELECT {best}", None
    return sketch, "sql_sketch looks incomplete"


def _common_prefix(a: str, b: str) -> str:
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return a[:n]


def confidence(req: Requirement, n_found: int, n_given: int, ambiguities: int, complete: bool) -> float:
    """Transparent heuristic, not a model probability:
    0.40 base + 0.35 x share of evidence spans found in the docs + 0.15 if the requirement is a documented
    example call (→) + 0.10 if sql_sketch and assertions are filled in − 0.05 per ambiguity (max −0.20)."""
    score = 0.40 + 0.35 * (n_found / n_given if n_given else 0.0)
    score += 0.15 if "→" in req.text else 0.0
    score += 0.10 if complete else 0.0
    score -= min(0.20, 0.05 * ambiguities)
    return round(max(0.0, min(1.0, score)), 2)


# ---------------------------------------------------------------- build

def title_prefix(req: Requirement) -> str:
    """'Customer-order lookup ...' -> 'cus' (falls back to 'req')."""
    word = re.findall(r"[A-Za-z]+", req.feature or "")
    return word[0][:3].lower() if word and len(word[0]) >= 3 else "req"


def build(cfg: Config, reqs: list[Requirement], chunks: list[Chunk], llm: LLM, prefix_per_requirement: bool = False,
          progress=None) -> tuple[list[Scenario], list[dict], list[dict]]:
    """Returns (scenarios, links scenario->requirement, dropped). With prefix_per_requirement, ids use a
    prefix from each requirement's title (cus-0001, pro-0001, ...) instead of one fixed prefix."""
    ctx = Context(chunks)
    scenarios, links, dropped = [], [], []
    counters: dict[str, int] = defaultdict(int)
    for n_req, req in enumerate(tqdm(reqs, desc="scenarios", unit="req")):
        if progress:  # progress(requirement index, total, scenarios so far); used by the web UI
            progress(n_req, len(reqs), len(scenarios))
        context = ctx.for_requirement(req, cfg.limits.context_chunks)
        doc = "\n".join(f"[{c.chunk_id}] {c.text}" for c in context)
        if req.scenario_count:  # the requirement says how many
            wanted = req.scenario_count
            aspects = plan_aspects(req, wanted, llm) if wanted > 1 else []
        elif req.source_id:  # hand-written requirement without a count: one scenario per behavior found
            aspects = plan_all_aspects(req, cfg.limits.max_scenarios_per_requirement, llm)
            wanted = max(1, len(aspects))
        else:  # mined requirement: one scenario
            wanted, aspects = 1, []
        done_here: list[Scenario] = []
        for i in range(1, wanted + 1):
            focus = _focus(i, wanted, done_here, aspects[i - 1] if i <= len(aspects) else None)
            out = llm.json(prompts.render("scenario", text=req.text, chunk_id=req.chunk_id, documentation=doc,
                                          focus=focus), step="scenario", required=REQUIRED_KEYS)
            if out is None:
                dropped.append({"req_id": req.req_id, "n": i, "reason": "no valid JSON after 2 tries"})
                continue
            prefix = title_prefix(req) if prefix_per_requirement else cfg.prefix
            sid = f"{prefix}-{counters[prefix] + 1:04d}"
            s, reason = _to_scenario(sid, out, req, context)
            if s is not None:
                twin = next((d for d in done_here if is_same_scenario(s, d)), None)
                if twin:
                    s, reason = None, f"duplicate of {twin.scenario_id}"
            if s is None:
                dropped.append({"req_id": req.req_id, "n": i, "reason": reason,
                                "aspect": aspects[i - 1] if i <= len(aspects) else None, "llm": out})
                continue
            counters[prefix] += 1
            scenarios.append(s)
            done_here.append(s)
            links.append({"scenario_id": sid, "req_id": req.req_id, "chunk_id": req.chunk_id})
    link_related(scenarios, links)
    return scenarios, links, dropped


def plan_aspects(req: Requirement, wanted: int, llm: LLM) -> list[str]:
    """One short call that splits a requirement into `wanted` distinct behaviors, one per scenario."""
    out = llm.json(prompts.render("plan", text=req.text, count=wanted), step="plan", required=("aspects",))
    aspects = _str_list((out or {}).get("aspects"))
    unique = []
    for a in aspects:
        if a.lower() not in {u.lower() for u in unique}:
            unique.append(a)
    return unique[:wanted]


def is_same_scenario(a: Scenario, b: Scenario) -> bool:
    """Small models sometimes ignore their assigned behavior and repeat an earlier scenario."""
    def sql(s):
        return " ".join(s.behavior_under_test.sql_sketch.lower().split()).rstrip(";")
    same_test = sql(a) == sql(b) and sorted(a.expected_result.assertions) == sorted(b.expected_result.assertions)
    same_words = fuzz.token_set_ratio(a.behavior_under_test.description, b.behavior_under_test.description) >= 90
    return same_test or (same_words and sql(a) == sql(b))


def plan_all_aspects(req: Requirement, cap: int, llm: LLM) -> list[str]:
    """Every distinct testable behavior the model finds in the requirement (at most `cap`)."""
    out = llm.json(prompts.render("plan_auto", text=req.text, max=cap), step="plan", required=("aspects",))
    unique = []
    for a in _str_list((out or {}).get("aspects")):
        if a.lower() not in {u.lower() for u in unique}:
            unique.append(a)
    return unique[:cap]


def _focus(i: int, wanted: int, done: list[Scenario], aspect: Optional[str] = None) -> str:
    """For requirements that ask for several scenarios: make each one test a different behavior."""
    if wanted == 1:
        return ""
    if aspect:
        return (f"This is scenario {i} of {wanted} for this requirement. Test exactly this behavior and "
                f"nothing else: {aspect}")
    lines = [f"This is scenario {i} of {wanted} for this requirement. Test a different behavior stated in the "
             "requirement than the scenarios already written (normal case, boundary, error, no-change rules)."]
    if done:
        lines.append("Already written (do not repeat):")
        lines += [f"- {s.feature}: {s.behavior_under_test.description}" for s in done]
    return "\n".join(lines)


def _to_scenario(sid: str, out: dict, req: Requirement, context: list[Chunk]):
    try:
        behavior = coerce_behavior(out.get("behavior_under_test"))
        expected = coerce_expected(out.get("expected_result"))
        precondition = coerce_precondition(out.get("precondition"))
    except ValidationError as e:
        return None, f"bad shape: {e.errors()[0]['msg']}"
    if not behavior.description or not expected.assertions:
        return None, "no behavior description or no assertions"
    behavior.sql_sketch, sketch_note = repair_sketch(behavior.sql_sketch, req, context[0])
    evidence, notes, given = check_evidence(out.get("evidence"), context)
    if sketch_note:
        notes.append(sketch_note)
    model_amb = _str_list(out.get("ambiguities"))
    category = _str(out.get("category")).lower()
    return Scenario(
        scenario_id=sid,
        feature=_str(out.get("feature")) or req.feature,
        category=category if category in CATEGORIES else req.category,
        precondition=precondition, behavior_under_test=behavior, expected_result=expected,
        evidence=evidence, ambiguities=model_amb + notes,
        confidence=confidence(req, len(evidence), given, len(model_amb) + len(notes),
                              bool(behavior.sql_sketch and expected.assertions)),
    ), None


def link_related(scenarios: list[Scenario], links: list[dict], limit: int = 5) -> None:
    """related_scenarios: other scenarios from the same requirement or citing the same doc chunk."""
    req_of = {l["scenario_id"]: l["req_id"] for l in links}
    by_key = defaultdict(set)
    keys = {}
    for s in scenarios:
        k = {f"req:{req_of[s.scenario_id]}"} | {f"chunk:{e.chunk}" for e in s.evidence}
        keys[s.scenario_id] = k
        for key in k:
            by_key[key].add(s.scenario_id)
    for s in scenarios:
        related = set().union(*(by_key[k] for k in keys[s.scenario_id])) - {s.scenario_id}
        s.related_scenarios = sorted(related)[:limit]
