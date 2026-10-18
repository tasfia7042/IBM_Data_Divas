"""Stage 2: one LLM call per chunk lists checkable statements; code keeps only exact (or near-exact)
quotes, removes duplicates and stops at limits.max_requirements. Table rows with example calls go first."""
from __future__ import annotations

from tqdm import tqdm

from . import prompts
from .config import Config
from .llm import LLM
from .schemas import Chunk, Requirement
from .text import expand_to_cells, is_duplicate, locate

CATEGORIES = {"normal", "boundary", "error"}


def priority(c: Chunk) -> int:
    if c.kind == "table_row" and "→" in c.text:
        return 0  # documented example call with its result: the strongest tests
    return 1 if c.kind == "table_row" else 2


def mine(cfg: Config, chunks: list[Chunk], llm: LLM, progress=None) -> tuple[list[Requirement], list[dict]]:
    """`progress(done, total)` is called after each chunk if given (used by the web UI)."""
    ordered = sorted(chunks, key=priority)  # stable: keeps doc order within each group
    reqs: list[Requirement] = []
    dropped: list[dict] = []
    limit = cfg.limits.max_requirements
    bar = tqdm(ordered, desc="mine", unit="chunk")
    for n, c in enumerate(bar):
        if progress:
            progress(n, len(ordered))
        if len(reqs) >= limit:
            break
        out = llm.json(prompts.render("mine", heading=c.heading, text=c.text), step="mine")
        if out is None:
            dropped.append({"chunk_id": c.chunk_id, "reason": "no valid JSON after 2 tries"})
            continue
        for r in out.get("requirements") or []:
            if not isinstance(r, dict) or len(reqs) >= limit:
                continue
            exact = locate(str(r.get("text") or ""), c.text)
            if exact is None:
                dropped.append({"chunk_id": c.chunk_id, "text": r.get("text"), "reason": "not found in chunk"})
                continue
            if c.kind == "table_row":
                exact = expand_to_cells(exact, c.text)
            if is_duplicate(exact, [x.text for x in reqs]):
                dropped.append({"chunk_id": c.chunk_id, "text": exact, "reason": "duplicate"})
                continue
            category = str(r.get("category") or "normal").strip().lower()
            feature = str(r.get("feature") or "").strip() or c.text.split("(")[0].split()[0]
            reqs.append(Requirement(req_id=f"req-{len(reqs) + 1:03d}", chunk_id=c.chunk_id, text=exact,
                                    feature=feature, category=category if category in CATEGORIES else "normal"))
        bar.set_postfix(requirements=len(reqs))
    return reqs, dropped
