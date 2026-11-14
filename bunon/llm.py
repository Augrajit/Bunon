"""The single LLM interface (PLAN.md §7). Only this file talks to a provider SDK.

- Every output is cached to data/llm_cache/<sha256>.json, so the demo runs offline.
- Mode "offline" (default) reads only the cache; a miss raises CacheMiss and callers fall back
  to a deterministic template. Mode "live" calls the provider on a miss and stores the result.
- The cache key covers provider, model, task, prompt version and the exact prompt text, so a
  changed prompt or input never reuses a stale answer.
- Swapping providers = another class with `name`, `model` and `generate_json`.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from bunon import config as C

T = TypeVar("T", bound=BaseModel)
Mode = Literal["offline", "live"]


class CacheMiss(Exception):
    """Offline mode and no cached answer for this exact prompt."""


class LLMError(Exception):
    """The provider failed or returned something that does not match the schema."""


@dataclass(frozen=True)
class CallMeta:
    source: Literal["cache", "live", "template"]
    provider: str
    model: str
    task: str
    prompt_version: str
    cache_key: str


class Provider(Protocol):
    name: str
    model: str

    def generate_json(self, prompt: str, json_schema: dict) -> str: ...


def _load_dotenv(path: Path = C.ROOT / ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines). Never prints values; never overrides real env vars."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class GeminiProvider:
    """Gemini via google-genai (generateContent + JSON schema). The key comes from GEMINI_API_KEY."""

    name = "gemini"

    def __init__(self, model: str = C.LLM_MODEL, api_key: str | None = None):
        self.model = model
        _load_dotenv()
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise LLMError("No GEMINI_API_KEY set. Use offline mode, or put the key in .env (gitignored).")
        from google import genai  # lazy: offline runs never need the SDK
        self._client = genai.Client(api_key=key)

    def generate_json(self, prompt: str, json_schema: dict) -> str:
        try:
            resp = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": json_schema,
                    "temperature": C.LLM_TEMPERATURE,
                    "seed": C.LLM_SEED,
                },
            )
        except Exception as e:  # network, quota, auth: surface as one error type
            raise LLMError(f"{type(e).__name__}: {e}") from e
        if not resp.text:
            raise LLMError("empty response")
        return resp.text


def current_mode() -> Mode:
    _load_dotenv()
    return "live" if os.environ.get(C.LLM_MODE_ENV, "offline").lower() == "live" else "offline"


class LLMClient:
    def __init__(self, provider: Provider | None = None, mode: Mode | None = None,
                 cache_dir: Path = C.LLM_CACHE_DIR, provider_name: str = C.LLM_PROVIDER,
                 model: str = C.LLM_MODEL):
        self.mode: Mode = mode or current_mode()
        self._provider = provider
        self.provider_name = provider.name if provider else provider_name
        self.model = provider.model if provider else model
        self.cache_dir = cache_dir
        self.calls = 0  # live provider calls made by this client (for tests and the UI)

    def _provider_or_create(self) -> Provider:
        if self._provider is None:
            self._provider = GeminiProvider(self.model)
        return self._provider

    def cache_key(self, task: str, prompt_version: str, prompt: str) -> str:
        blob = json.dumps({"provider": self.provider_name, "model": self.model, "task": task,
                           "prompt_version": prompt_version, "prompt": prompt}, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def complete_json(self, task: str, prompt_version: str, prompt: str, schema: type[T],
                      refresh: bool = False) -> tuple[T, CallMeta, str]:
        """Return (parsed output, meta, raw text). Raises CacheMiss (offline) or LLMError."""
        key = self.cache_key(task, prompt_version, prompt)
        path = self.cache_dir / f"{key}.json"
        meta = lambda src: CallMeta(src, self.provider_name, self.model, task, prompt_version, key)  # noqa: E731

        if path.exists() and not refresh:
            raw = json.loads(path.read_text(encoding="utf-8"))["raw"]
            return schema.model_validate_json(raw), meta("cache"), raw
        if self.mode == "offline":
            raise CacheMiss(f"{task}/{prompt_version}: no cached answer for this input")

        raw = self._provider_or_create().generate_json(prompt, schema.model_json_schema())
        self.calls += 1
        try:
            parsed = schema.model_validate_json(raw)
        except ValidationError as e:
            raise LLMError(f"schema mismatch: {e.error_count()} errors") from e
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "task": task, "prompt_version": prompt_version, "provider": self.provider_name,
            "model": self.model, "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "prompt": prompt, "raw": raw,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        return parsed, meta("live"), raw
