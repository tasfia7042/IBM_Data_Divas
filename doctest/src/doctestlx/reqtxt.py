"""Requirements written by hand in a .txt file -> requirements + chunks (step 2 without mining).

Format (one block per requirement; the scenario count and title are optional):

    Requirement-1 (5 scenarios) - "Customer-order lookup and read-only history"

    A customer is identified by customers.customer_id. The lookup returns ...

Without any "Requirement-..." header lines, every blank-line-separated paragraph is one requirement.
Each requirement's text also becomes a chunk, so scenario evidence can cite the requirement itself."""
from __future__ import annotations

import re
from pathlib import Path

from .schemas import Chunk, Requirement
from .text import norm

HEADER = re.compile(
    r"^\s*(?P<id>req(?:uirement)?[\s\-_#]*[\w.\-]+)\s*"
    r"(?:\(\s*(?P<count>\d+)\s*scenarios?\s*\))?\s*"
    r"(?:[-–—:]\s*)?(?:\"(?P<qtitle>[^\"]*)\"|(?P<title>.*?))\s*$",
    re.I)


def parse(path, stem: str | None = None) -> tuple[list[Requirement], list[Chunk]]:
    stem = stem or Path(path).stem
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    blocks = []  # (source_id, count, title, body lines)
    for line in lines:
        m = HEADER.match(line)
        if m and re.match(r"\s*req", line, re.I) and len(line) < 200:
            title = (m.group("qtitle") if m.group("qtitle") is not None else m.group("title") or "").strip()
            blocks.append([m.group("id").strip(), int(m.group("count")) if m.group("count") else None, title, []])
        elif blocks:
            blocks[-1][3].append(line)
    if not blocks:  # no headers: one requirement per paragraph
        text = "\n".join(lines)
        blocks = [[f"P{i}", None, "", [p]] for i, p in enumerate(re.split(r"\n\s*\n", text), 1) if p.strip()]

    reqs, chunks = [], []
    for i, (sid, count, title, body) in enumerate(blocks, 1):
        text = norm(" ".join(body))
        if not text:
            continue
        cid = f"{stem}#req{i}"
        chunks.append(Chunk(chunk_id=cid, kind="paragraph", heading=title or sid, text=text))
        reqs.append(Requirement(req_id=f"req-{len(reqs) + 1:03d}", chunk_id=cid, text=text,
                                feature=title or sid, category="normal", scenario_count=count, source_id=sid))
    return reqs, chunks
