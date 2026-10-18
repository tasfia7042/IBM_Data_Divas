"""OpenAI-compatible LLM client: JSON output, one retry on bad JSON, every call logged and cached by
prompt hash in data/llm_calls.jsonl (so a dropped tunnel only costs the calls not yet made)."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Optional

import json_repair
import openai
from openai import OpenAI

from .config import LLM_CALLS, Config, read_jsonl

SYSTEM = "You help test PostgreSQL against its documentation. Always reply with a single JSON object and nothing else."


class LLMUnavailable(RuntimeError):
    pass


def parse_json(text: str):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


class LLM:
    def __init__(self, cfg: Config, log_path: Path = LLM_CALLS):
        self.cfg = cfg.llm
        self.client = OpenAI(base_url=self.cfg.base_url, api_key=self.cfg.api_key or "none",
                             timeout=self.cfg.timeout_s, max_retries=2)
        self.log_path = log_path
        self.lock = threading.Lock()
        self.cache = {row["hash"]: row["response"] for row in read_jsonl(log_path)
                      if row.get("ok") and row.get("response") is not None}
        self.json_mode = True

    def _key(self, prompt: str) -> str:
        blob = json.dumps([self.cfg.model, self.cfg.temperature, SYSTEM, prompt], ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()

    def _log(self, row: dict) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock, self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _complete(self, prompt: str, json_mode: Optional[bool] = None) -> str:
        kwargs = dict(model=self.cfg.model, temperature=self.cfg.temperature,
                      messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}])
        if self.json_mode if json_mode is None else json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except openai.BadRequestError as e:
            if self.json_mode and "response_format" in str(e):
                self.json_mode = False  # some servers reject json_object; the prompt still asks for JSON
                return self._complete(prompt)
            raise
        except (openai.APIConnectionError, openai.APITimeoutError) as e:
            raise LLMUnavailable(f"cannot reach the LLM at {self.cfg.base_url} ({e}). "
                                 "Is the SSH reverse tunnel / Ollama running?") from e
        return resp.choices[0].message.content or ""

    def json(self, prompt: str, step: str, required: tuple = ()) -> Optional[dict]:
        """Return the reply as a dict, or None after 2 invalid replies (logged).

        If keys in `required` are missing, the strict JSON mode probably cut the reply at an unescaped
        quote (e.g. SQL with a JSON literal). One more try then runs without strict mode and parses
        leniently with json_repair; the reply with more required keys wins."""
        key = self._key(prompt)
        if key in self.cache and all(self.cache[key].get(k) for k in required):
            return self.cache[key]
        obj = self._strict(prompt, step, key)
        missing = [k for k in required if not (obj or {}).get(k)]
        if required and missing:
            loose = self._lenient(prompt, step, key)
            if loose is not None and sum(1 for k in required if loose.get(k)) > len(required) - len(missing):
                obj = loose
        if obj is not None:
            self.cache[key] = obj
            self._log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "step": step, "hash": key, "model": self.cfg.model,
                       "attempt": "final", "ms": 0, "ok": True, "prompt": prompt, "response": obj, "raw": None})
        return obj

    def _lenient(self, prompt: str, step: str, key: str) -> Optional[dict]:
        t0 = time.time()
        raw = self._complete(prompt, json_mode=False)
        try:
            obj = json_repair.loads(re.sub(r"<think>.*?</think>", "", raw, flags=re.S))
        except Exception:  # noqa: BLE001 - repair is best effort
            obj = None
        ok = isinstance(obj, dict)
        self._log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "step": step, "hash": key, "model": self.cfg.model,
                   "attempt": "lenient", "ms": int((time.time() - t0) * 1000), "ok": False, "prompt": prompt,
                   "response": None, "raw": raw})
        return obj if ok else None

    def _strict(self, prompt: str, step: str, key: str) -> Optional[dict]:
        for attempt in range(2):
            p = prompt if attempt == 0 else prompt + "\n\nYour last reply was not valid JSON. Reply with one JSON object only."
            t0 = time.time()
            raw, obj = self._complete(p), None
            try:
                obj = parse_json(raw)
            except (json.JSONDecodeError, ValueError):
                pass
            ok = isinstance(obj, dict)
            # logged as not-final (ok False) so the cache only ever loads the chosen "final" reply
            self._log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "step": step, "hash": key, "model": self.cfg.model,
                       "attempt": attempt, "ms": int((time.time() - t0) * 1000), "ok": False, "prompt": prompt,
                       "response": None, "raw": raw})
            if ok:
                return obj
        return None
