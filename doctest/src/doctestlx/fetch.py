"""Stage 1a: download the doc page (use the same major version as the database the tests target)."""
from __future__ import annotations

from pathlib import Path

import requests

from .config import Config


def fetch(cfg: Config) -> Path:
    """Save the page to data/raw/<page>.html."""
    resp = requests.get(cfg.docs.url, timeout=30, headers={"User-Agent": "doctest-linuxone/0.1"})
    resp.raise_for_status()
    cfg.raw_html.parent.mkdir(parents=True, exist_ok=True)
    cfg.raw_html.write_bytes(resp.content)
    return cfg.raw_html
