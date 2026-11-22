"""Optional LLM access through an OpenAI-compatible endpoint.

Configured only through the environment or backend/.env (never commit keys):
  LLM_BASE_URL  e.g. https://api.tokenfactory.us-north1.nebius.com/v1/
  LLM_API_KEY
  LLM_MODEL     e.g. deepseek-ai/DeepSeek-V4.1-Flash-1017
Every caller has a deterministic fallback, so Argus works without an LLM.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

TIMEOUT = 8.0
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
KEYS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")


def load_dotenv(path: Path = ENV_FILE) -> bool:
    """Load LLM_* settings from backend/.env without overriding the real environment.

    Skipped when ARGUS_NO_DOTENV is set (the test suite runs without a key).
    """
    if os.environ.get("ARGUS_NO_DOTENV") or not path.is_file():
        return False
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k in KEYS and v and not os.environ.get(k):
            os.environ[k] = v
    return True


def configured() -> bool:
    return all(os.environ.get(k) for k in KEYS)


def model() -> str | None:
    return os.environ.get("LLM_MODEL") if configured() else None


def chat(system: str, user: str, timeout: float = TIMEOUT, max_tokens: int = 900) -> str | None:
    if not configured():
        return None
    try:
        import httpx

        body = {"model": os.environ["LLM_MODEL"], "temperature": 0.2, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                # Fast instruct use: no hidden reasoning (otherwise flash reasoning models spend the budget thinking).
                "reasoning_effort": "none"}
        url = os.environ["LLM_BASE_URL"].rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {os.environ['LLM_API_KEY']}"}
        resp = httpx.post(url, headers=headers, json=body, timeout=timeout)
        if resp.status_code in (400, 422):          # endpoint does not know reasoning_effort: retry without it
            body.pop("reasoning_effort")
            resp = httpx.post(url, headers=headers, json=body, timeout=timeout)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"] or None
    except Exception:  # noqa: BLE001 - any failure falls back to templates
        return None


def chat_json(system: str, user: str, max_tokens: int = 900) -> dict | None:
    text = chat(system + " Answer with a single JSON object and nothing else.", user, max_tokens=max_tokens)
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        out = json.loads(text[start: end + 1])
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None
