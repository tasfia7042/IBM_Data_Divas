"""Write the hand-off file: the teammate's doc JSON with `requirements` and `scenarios` added.
The teammate's original file is never modified; the result goes to a new file."""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

from .config import (CHUNKS, DATA, REQUIREMENTS, SCENARIO_LINKS, SCENARIOS, SOURCE, Config, read_json, read_jsonl,
                     write_json)
from .schemas import Requirement, Scenario


def default_output(cfg: Config) -> Path:
    src = read_json(SOURCE, {}) or {}
    origin = src.get("path") or src.get("requirements_txt")
    stem = Path(origin).stem if origin else cfg.page
    return DATA / f"{stem}_with_scenarios.json"


def export(cfg: Config, out: Optional[Path] = None, base_path: Optional[str] = None) -> tuple[Path, dict]:
    src = read_json(SOURCE, {}) or {}
    base_path = base_path or src.get("path")
    if not base_path and src.get("requirements_txt"):  # requirements came from a txt file
        base = {"source": {"requirements_txt": src["requirements_txt"], "doc": src.get("doc")}}
    elif base_path and Path(base_path).exists():
        base = json.loads(Path(base_path).read_text(encoding="utf-8"))
        if not isinstance(base, dict):
            base = {"doc": base}  # teammate sent a bare list of records
    else:  # doc came from fetch + chunk: include the chunks so the file stands alone
        base = {"source": {"url": src.get("url", cfg.docs.url)}, "chunks": read_jsonl(CHUNKS)}

    scenarios = read_jsonl(SCENARIOS)
    by_req = defaultdict(list)
    for link in read_jsonl(SCENARIO_LINKS):
        by_req[link["req_id"]].append(link["scenario_id"])
    requirements = [{**r, "scenario_ids": by_req[r["req_id"]]} for r in read_jsonl(REQUIREMENTS)]

    counts = {"requirements": len(requirements), "scenarios": len(scenarios),
              "scenarios_with_all_evidence_found": sum(
                  1 for s in scenarios if not any(a.startswith("evidence") for a in s["ambiguities"]))}
    base["requirements"] = requirements
    base["scenarios"] = scenarios
    base["doctest"] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model": cfg.llm.model,
        "doc_source": base_path or src.get("requirements_txt") or src.get("url") or cfg.docs.url,
        "counts": counts,
        "notes": "requirements[].text and scenarios[].evidence[].span are exact doc text. confidence is a "
                 "heuristic from evidence found, documented example calls and ambiguities, not a probability.",
        "requirement_schema": {**Requirement.model_json_schema(),
                               "properties": {**Requirement.model_json_schema()["properties"],
                                              "scenario_ids": {"type": "array", "items": {"type": "string"}}}},
        "scenario_schema": Scenario.model_json_schema(by_alias=True),
    }
    out = out or default_output(cfg)
    write_json(out, base)
    return out, counts
