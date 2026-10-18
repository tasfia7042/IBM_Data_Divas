from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

ROOT = Path(os.environ.get("DOCTEST_HOME", Path(__file__).resolve().parents[2]))
DATA = ROOT / "data"
RAW = DATA / "raw"
SOURCE = DATA / "source.json"              # where the doc came from (URL or teammate's JSON file)
CHUNKS = DATA / "chunks.jsonl"
REQUIREMENTS = DATA / "requirements.jsonl"
SCENARIOS = DATA / "scenarios.jsonl"
SCENARIO_LINKS = DATA / "scenario_links.jsonl"   # scenario_id -> req_id (the scenario format has no req_id)
LLM_CALLS = DATA / "llm_calls.jsonl"


class LLMConfig(BaseModel):
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    model: str = "qwen2.5:7b-instruct"
    temperature: float = 0.0
    timeout_s: float = 300


class DocsConfig(BaseModel):
    url: str
    scenario_prefix: Optional[str] = None


class Limits(BaseModel):
    max_requirements: int = 60
    context_chunks: int = 2
    max_scenarios_per_requirement: int = 10   # cap when a requirement does not say how many


class Config(BaseModel):
    llm: LLMConfig
    docs: DocsConfig
    limits: Limits = Limits()

    @property
    def page(self) -> str:
        """'functions-json' for .../docs/16/functions-json.html"""
        return Path(self.docs.url.split("?")[0].split("#")[0]).stem

    @property
    def docs_major(self) -> Optional[int]:
        m = re.search(r"/docs/(\d+)/", self.docs.url)
        return int(m.group(1)) if m else None

    @property
    def prefix(self) -> str:
        if self.docs.scenario_prefix:
            return self.docs.scenario_prefix
        return self.page.removeprefix("functions-").split("-")[0] or "sc"

    @property
    def raw_html(self) -> Path:
        return RAW / f"{self.page}.html"


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Load KEY=VALUE lines from .env without overriding variables already set."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def load_config(path: Optional[str] = None) -> Config:
    load_dotenv()
    path = Path(path or os.environ.get("DOCTEST_CONFIG", ROOT / "config.yaml"))
    cfg = Config.model_validate(yaml.safe_load(path.read_text()))
    if cfg.llm.api_key.startswith("env:"):  # e.g. "env:HF_TOKEN"
        var = cfg.llm.api_key[4:]
        cfg.llm.api_key = os.environ.get(var, "")
        if not cfg.llm.api_key and not os.environ.get("DOCTEST_LLM_API_KEY"):
            raise SystemExit(f"llm.api_key wants ${var}; set it in the environment or in {ROOT / '.env'}")
    # environment overrides, so switching LLMs or hiding keys needs no file edit
    for field, env in (("base_url", "DOCTEST_LLM_BASE_URL"), ("api_key", "DOCTEST_LLM_API_KEY"),
                       ("model", "DOCTEST_LLM_MODEL")):
        if os.environ.get(env):
            setattr(cfg.llm, field, os.environ[env])
    return cfg


# ---------------------------------------------------------------- json helpers

def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            if isinstance(r, BaseModel):
                r = r.model_dump(by_alias=True)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
