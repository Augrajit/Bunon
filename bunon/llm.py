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

    RETRY_WAITS = (5, 20)  # seconds; only for temporary server errors (503 "high demand", 500)

    def generate_json(self, prompt: str, json_schema: dict) -> str:
        import time
        for attempt in range(len(self.RETRY_WAITS) + 1):
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
                break
            except Exception as e:  # network, quota, auth: surface as one error type
                temporary = type(e).__name__ == "ServerError" or " 503 " in f" {e} " or "UNAVAILABLE" in str(e)
                if temporary and attempt < len(self.RETRY_WAITS):
                    time.sleep(self.RETRY_WAITS[attempt])
                    continue
                raise LLMError(f"{type(e).__name__}: {e}") from e
        if not resp.text:
            raise LLMError("empty response")
        return resp.text


class GeminiToolSession:
    """One live tool-calling conversation (manual function calling, generateContent).

    Verified 17 Nov 2026 against the docs and the installed SDK (google-genai 3.3.0): tools as dict
    declarations in types.Tool; automatic calling disabled; read response.function_calls; append the
    model's own Content (it carries thought signatures) and a user-role Content with FunctionResponse
    parts echoing each call's id. Part.from_function_response has no `id` argument in 3.3.0, so the
    FunctionResponse is built directly.
    """

    RETRY_WAITS = (5, 20)

    def __init__(self, client, model: str, system: str, question: str, declarations: list[dict]):
        from google.genai import types
        self._t = types
        self._client, self._model = client, model
        self.contents = [types.Content(role="user", parts=[types.Part(text=question)])]
        base = dict(system_instruction=system, temperature=C.LLM_TEMPERATURE, seed=C.LLM_SEED,
                    tools=[types.Tool(function_declarations=declarations)],
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        self._config = types.GenerateContentConfig(**base)
        self._config_no_tools = types.GenerateContentConfig(
            **base, tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="NONE")))

    def step(self, tool_results: list[dict] | None = None, user_text: str | None = None,
             force_text: bool = False) -> dict:
        import time
        t = self._t
        if tool_results:
            self.contents.append(t.Content(role="user", parts=[
                t.Part(function_response=t.FunctionResponse(id=r["id"], name=r["name"], response=r["response"]))
                for r in tool_results]))
        if user_text:
            self.contents.append(t.Content(role="user", parts=[t.Part(text=user_text)]))
        for attempt in range(len(self.RETRY_WAITS) + 1):
            try:
                resp = self._client.models.generate_content(
                    model=self._model, contents=self.contents,
                    config=self._config_no_tools if force_text else self._config)
                break
            except Exception as e:
                temporary = type(e).__name__ == "ServerError" or "UNAVAILABLE" in str(e)
                if temporary and attempt < len(self.RETRY_WAITS):
                    time.sleep(self.RETRY_WAITS[attempt])
                    continue
                raise LLMError(f"{type(e).__name__}: {e}") from e
        self.contents.append(resp.candidates[0].content)
        calls = [{"id": fc.id or f"call{i}", "name": fc.name, "args": dict(fc.args or {})}
                 for i, fc in enumerate(resp.function_calls or [])]
        return {"calls": calls} if calls else {"text": resp.text or ""}


def _gemini_tool_session(self, system: str, question: str, declarations: list[dict]) -> GeminiToolSession:
    return GeminiToolSession(self._client, self.model, system, question, declarations)


GeminiProvider.start_tools = _gemini_tool_session


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

    # --- step cache for multi-turn tool use (analyst.py) ----------------------------------------
    def step_key(self, task: str, prompt_version: str, conversation: object) -> str:
        blob = json.dumps({"provider": self.provider_name, "model": self.model, "task": task,
                           "prompt_version": prompt_version, "conversation": conversation},
                          sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def cache_get(self, key: str) -> dict | None:
        path = self.cache_dir / f"{key}.json"
        return json.loads(path.read_text(encoding="utf-8"))["step"] if path.exists() else None

    def cache_put(self, key: str, task: str, prompt_version: str, step: dict, conversation: object) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / f"{key}.json").write_text(json.dumps({
            "task": task, "prompt_version": prompt_version, "provider": self.provider_name, "model": self.model,
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "conversation": conversation, "step": step}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    def tool_session(self, system: str, question: str, declarations: list[dict]):
        return self._provider_or_create().start_tools(system, question, declarations)

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
