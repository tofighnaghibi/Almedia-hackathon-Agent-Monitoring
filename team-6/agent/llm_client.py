"""LLM clients for the agent: ClaudeClient (Anthropic SDK), the older
OpenAI-compatible ChatClient, a record/replay/live file cache, and helpers.

Configuration comes only from the environment (never hard-coded):

    ANTHROPIC_API_KEY   read by the anthropic SDK itself; never printed or logged
    LLM_MODEL           model for choosing investigation steps
    LLM_REPORT_MODEL    model for writing report text (defaults to LLM_MODEL)
    LLM_CACHE_MODE      live | record | replay   (default live)
    LLM_CACHE_DIR       cache folder for record/replay (default data/llm_cache)
    LLM_TIMEOUT         seconds per request (default 30)
    LLM_MAX_TOKENS      reply budget per call (default 1024)
    LLM_EFFORT          low | medium | high | none   (default low)
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)

log = logging.getLogger("agent.llm")

CacheMode = Literal["replay", "record", "live"]
CACHE_MODES = ("replay", "record", "live")


class LLMUnavailable(RuntimeError):
    """The LLM cannot be used (no API key, no model configured, SDK missing)."""


class RecordedFailure(RuntimeError):
    """A failure saved in record mode, raised again in replay mode."""

    def __init__(self, description: str) -> None:
        super().__init__(description)
        self.description = description


def describe_error(exc: BaseException) -> str:
    """'Type: message' (a replayed failure reads exactly like the recorded one)."""
    if isinstance(exc, RecordedFailure):
        return exc.description
    return f"{type(exc).__name__}: {exc}"


@dataclass
class LLMUsageStats:
    """Per-run usage counters (calls that reached the API, tokens, failures)."""

    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    failures: int = 0

    def to_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


def extract_first_json_object(raw: str) -> dict[str, Any]:
    """Strip code fences/reasoning text and parse the first JSON object."""
    text = raw.strip()
    if not text:
        raise ValueError("LLM returned empty response content")

    candidates = [text]
    for match in _CODE_FENCE_RE.finditer(text):
        candidates.insert(0, match.group(1).strip())

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if escape:
                escape = False
                continue
            if ch == "\\" and in_string:
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start : idx + 1])
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)

    raise ValueError(f"Could not extract valid JSON object from LLM response: {raw!r}")


class ChatClient:
    """Generic OpenAI-compatible chat client with a single ``complete_json`` method."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = 90.0,
    ) -> None:
        resolved_url = base_url if base_url is not None else os.environ.get("LLM_BASE_URL", "")
        resolved_model = model if model is not None else os.environ.get("LLM_MODEL", "")
        resolved_key = api_key if api_key is not None else os.environ.get("LLM_API_KEY", "")

        missing = [
            name
            for name, val in (
                ("LLM_BASE_URL", resolved_url),
                ("LLM_MODEL", resolved_model),
                ("LLM_API_KEY", resolved_key),
            )
            if not val or not val.strip()
        ]
        if missing:
            raise ValueError(
                f"Missing required LLM environment variable(s): {', '.join(missing)}"
            )

        self.base_url = resolved_url.strip()
        self.model = resolved_model.strip()
        self.timeout = float(timeout)
        self.usage = LLMUsageStats()
        from openai import OpenAI

        self._client = OpenAI(
            base_url=self.base_url,
            api_key=resolved_key.strip(),
            timeout=self.timeout,
            max_retries=1,
        )

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """Send a chat completion request and return the parsed JSON object."""
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0,
            max_tokens=400,
        )
        self.usage.calls += 1
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.usage.tokens_in += int(getattr(usage, "prompt_tokens", 0) or 0)
            self.usage.tokens_out += int(getattr(usage, "completion_tokens", 0) or 0)

        content = response.choices[0].message.content if response.choices else ""
        if not content:
            raise ValueError("LLM returned empty response content")
        return extract_first_json_object(content)


# --- Claude ---------------------------------------------------------------------------------------

def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def llm_configured() -> tuple[bool, str]:
    """(True, "") when ANTHROPIC_API_KEY and LLM_MODEL are set, else (False, why)."""
    missing = [n for n in ("ANTHROPIC_API_KEY", "LLM_MODEL") if not _env(n)]
    if missing:
        return False, f"missing environment variable(s): {', '.join(missing)}"
    return True, ""


class ClaudeClient:
    """Claude through the official ``anthropic`` SDK, with one ``complete_json`` method.

    The key is read by the SDK from ANTHROPIC_API_KEY; this class only checks
    that it is present and never stores, prints or logs it. Models come from
    LLM_MODEL (steps) and LLM_REPORT_MODEL (report text, defaults to LLM_MODEL).
    Requests use temperature 0 where the model accepts it, a small reply
    budget, a timeout and at most one retry (the SDK's own retry for
    connection errors, 429 and 5xx).
    """

    def __init__(
        self,
        model: str | None = None,
        report_model: str | None = None,
        timeout: float | None = None,
        max_tokens: int | None = None,
        effort: str | None = None,
        temperature: float | None = 0.0,
        sdk_client: Any = None,
    ) -> None:
        self.model = (model or _env("LLM_MODEL")).strip()
        if not self.model:
            raise LLMUnavailable("LLM_MODEL is not set (the model id is read from the environment)")
        self.report_model = (report_model or _env("LLM_REPORT_MODEL") or self.model).strip()
        self.timeout = float(timeout if timeout is not None else (_env("LLM_TIMEOUT") or 30))
        self.max_tokens = int(max_tokens if max_tokens is not None else (_env("LLM_MAX_TOKENS") or 1024))
        eff = (effort if effort is not None else (_env("LLM_EFFORT") or "low")).lower()
        self.effort = None if eff in ("", "none", "off") else eff
        self.temperature = temperature
        self.usage = LLMUsageStats()
        if sdk_client is not None:  # tests inject a fake SDK client here
            self._client = sdk_client
            return
        if not _env("ANTHROPIC_API_KEY"):
            raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - the package is in requirements.txt
            raise LLMUnavailable("the anthropic package is not installed (pip install anthropic)") from exc
        self._client = anthropic.Anthropic(timeout=self.timeout, max_retries=1)

    def __repr__(self) -> str:  # never shows the key
        return f"ClaudeClient(model={self.model!r}, report_model={self.report_model!r})"

    def _params(self) -> dict[str, Any]:
        p: dict[str, Any] = {}
        if self.temperature is not None:
            p["temperature"] = self.temperature
        if self.effort:
            p["output_config"] = {"effort": self.effort}
        return p

    def _create(self, model: str, system: str, user: str, max_tokens: int) -> Any:
        params = self._params()
        for attempt in (0, 1):
            try:
                return self._client.messages.create(
                    model=model, max_tokens=max_tokens, system=system,
                    messages=[{"role": "user", "content": user}], **params)
            except Exception as exc:
                # Newer models reject sampling parameters (and older ones reject effort) with a
                # 400: drop the rejected parameter for the rest of the run and send once more.
                msg = str(exc).lower()
                if attempt or type(exc).__name__ != "BadRequestError":
                    raise
                if "temperature" in msg and "temperature" in params:
                    params.pop("temperature")
                    self.temperature = None
                    log.info("model %s does not accept temperature; sending without it", model)
                elif ("effort" in msg or "output_config" in msg) and "output_config" in params:
                    params.pop("output_config")
                    self.effort = None
                    log.info("model %s does not accept effort; sending without it", model)
                else:
                    raise
        raise RuntimeError("unreachable")  # pragma: no cover

    def complete_json(self, system: str, user: str, model: str | None = None,
                      max_tokens: int | None = None) -> dict[str, Any]:
        """One request; returns the first JSON object in the reply (raises on any failure)."""
        use = model or self.model
        t0 = time.perf_counter()
        try:
            resp = self._create(use, system, user, int(max_tokens or self.max_tokens))
        except Exception as exc:
            self.usage.failures += 1
            log.warning("llm call failed model=%s error=%s", use, type(exc).__name__)
            raise
        self.usage.calls += 1
        u = getattr(resp, "usage", None)
        tin = int(getattr(u, "input_tokens", 0) or 0)
        tout = int(getattr(u, "output_tokens", 0) or 0)
        self.usage.tokens_in += tin
        self.usage.tokens_out += tout
        stop = getattr(resp, "stop_reason", None)
        log.info("llm call model=%s tokens_in=%d tokens_out=%d stop=%s ms=%d",
                 use, tin, tout, stop, int(1000 * (time.perf_counter() - t0)))
        if stop == "refusal":
            self.usage.failures += 1
            raise ValueError("the model declined the request (stop_reason=refusal)")
        text = "".join(getattr(b, "text", "") or "" for b in (getattr(resp, "content", None) or [])
                       if getattr(b, "type", None) == "text")
        try:
            return extract_first_json_object(text)
        except ValueError:
            self.usage.failures += 1
            if stop == "max_tokens":
                raise ValueError("reply cut off at max_tokens before a complete JSON object") from None
            raise


def call_json(client: Any, system: str, user: str, model: str | None = None) -> dict[str, Any]:
    """Call ``client.complete_json``, passing ``model`` only if the client accepts it."""
    fn = client.complete_json
    if model is not None:
        try:
            accepts = "model" in inspect.signature(fn).parameters
        except (TypeError, ValueError):
            accepts = False
        if accepts:
            return fn(system, user, model=model)
    return fn(system, user)


def client_model(client: Any, report: bool = False) -> str:
    """The model id a client will use (for cache keys); falls back to LLM_MODEL."""
    if client is not None:
        name = getattr(client, "report_model", None) if report else None
        name = name or getattr(client, "model", None)
        if name:
            return str(name)
    return (_env("LLM_REPORT_MODEL") if report else "") or _env("LLM_MODEL") or "unknown"


# --- cache ----------------------------------------------------------------------------------------

class LLMCache:
    """File cache of model replies, keyed by sha256(model + exact prompt).

    - ``live``: always call the model, never read or write the cache
    - ``record``: call the model and save every reply (and every failure)
    - ``replay``: never call the model; a missing entry raises KeyError

    A recorded failure is replayed as the same failure, so a replayed run takes
    the same fallbacks as the recorded one and its trail is identical.
    """

    def __init__(self, mode: str | None = None, cache_dir: str | Path | None = None) -> None:
        m = (mode or _env("LLM_CACHE_MODE") or "live").lower()
        if m not in CACHE_MODES:
            raise ValueError(f"Invalid cache mode: {m!r} (choose from {', '.join(CACHE_MODES)})")
        self.mode: CacheMode = m  # type: ignore[assignment]
        self.dir = Path(cache_dir or _env("LLM_CACHE_DIR") or "data/llm_cache")
        self.hits = 0
        self.writes = 0

    @staticmethod
    def key(model: str, system: str, user: str) -> str:
        return hashlib.sha256(f"{model}\n---\n{system}\n---\n{user}".encode("utf-8")).hexdigest()

    def path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def call(self, client: Any, model: str, system: str, user: str, call_model: str | None = None) -> dict[str, Any]:
        """The reply for this prompt, from the cache or the client, by mode."""
        key = self.key(model, system, user)
        if self.mode == "replay":
            f = self.path(key)
            if not f.exists():
                raise KeyError(f"Missing LLM cache entry for key {key} in replay mode")
            data = json.loads(f.read_text(encoding="utf-8"))
            self.hits += 1
            if isinstance(data, dict) and data.get("_cache") == 1:
                if data.get("error"):
                    raise RecordedFailure(str(data["error"]))
                return data["reply"]
            return data  # an entry written before the envelope existed: the bare reply
        if client is None:
            raise LLMUnavailable("no LLM client")
        try:
            reply = call_json(client, system, user, model=call_model)
        except Exception as exc:
            if self.mode == "record":
                self._write(key, model, {"error": describe_error(exc)})
            raise
        if self.mode == "record":
            self._write(key, model, {"reply": reply})
        return reply

    def _write(self, key: str, model: str, body: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path(key).write_text(json.dumps({"_cache": 1, "model": model, **body}, indent=2, default=str),
                                  encoding="utf-8")
        self.writes += 1
