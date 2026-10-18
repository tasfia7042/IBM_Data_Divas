"""Exact-or-fuzzy matching of model-quoted text against chunk text (Sections 7 and 8)."""
from __future__ import annotations

import re
from typing import Optional

from rapidfuzz import fuzz


def norm(s: str) -> str:
    return " ".join((s or "").split())


def locate(needle: str, haystack: str, threshold: float = 95) -> Optional[str]:
    """The exact chunk text that `needle` quotes, or None.
    Exact after whitespace normalization, else rapidfuzz partial_ratio >= threshold (snapped to the chunk)."""
    n, h = norm((needle or "").replace('\\"', '"')), norm(haystack)
    if not n or not h:
        return None
    if n in h:
        return n
    if len(n) > len(h) + 3:  # partial_ratio would score the chunk inside the longer quote
        return None
    al = fuzz.partial_ratio_alignment(n, h)
    if al is not None and al.score >= threshold:
        return h[al.dest_start:al.dest_end]
    return None


CELL_SEP = " | "


def looks_cut(s: str) -> bool:
    """Unbalanced quotes/brackets or a dangling opener: the model stopped mid-example."""
    s = s.rstrip()
    return (s.count("'") % 2 == 1 or s.count('"') % 2 == 1 or s.count("(") > s.count(")")
            or s.count("[") > s.count("]") or s.count("{") > s.count("}")
            or s.endswith(("→", ",", ":", "=", "(", "<", ">", "WHERE", "AND", "OR")))


def expand_to_cells(found: str, row_text: str) -> str:
    """Widen a cut-off quote to whole ' | '-separated cells of a table row. Small models sometimes cut a
    quoted example at an inner double quote, e.g. json_array_length('[1,2,3,{ . Complete quotes are
    returned unchanged so separate rules inside one description cell stay separate."""
    if not looks_cut(found):
        return found
    i = row_text.find(found)
    if i == -1:
        return found
    start = row_text.rfind(CELL_SEP, 0, i)
    start = 0 if start == -1 else start + len(CELL_SEP)
    end = row_text.find(CELL_SEP, i + len(found))
    end = len(row_text) if end == -1 else end
    return row_text[start:end]


def is_duplicate(text: str, kept: list[str], threshold: float = 92) -> bool:
    return any(fuzz.token_set_ratio(text, k) >= threshold for k in kept)


_TOKEN = re.compile(r"[a-z0-9_]+")


def tokenize(s: str) -> list[str]:
    return _TOKEN.findall((s or "").lower())
