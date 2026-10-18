"""Prompt templates live next to this file as .md; placeholders look like {name}."""
from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).resolve().parent


def render(name: str, raw: tuple = (), **values) -> str:
    """Fill placeholders. Double quotes in values are shown escaped (\\") so that when the model copies
    doc text into its JSON reply, the copy is valid JSON instead of ending the string early.
    Names in `raw` are inserted unchanged (e.g. text that is already JSON)."""
    text = (_DIR / f"{name}.md").read_text(encoding="utf-8")
    for k, v in values.items():
        v = str(v)
        if k not in raw:
            v = v.replace("\\", "\\\\").replace('"', '\\"')
        text = text.replace("{" + k + "}", v)
    return text.strip()
