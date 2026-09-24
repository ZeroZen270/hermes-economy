"""
Free brain for Hermes: StepFun Step 3.7 Flash via the Nous Research portal.

Nous runs an OpenAI-compatible inference API with a $0 free plan; Step 3.7
Flash is currently served free as `stepfun/step-3.7-flash:free`. Sign up at
https://portal.nousresearch.com, grab a key, and export it:

    export NOUS_PORTAL_API_KEY=nk-...

The client is provider-agnostic on purpose — point `base_url` at any
OpenAI-compatible endpoint (local Ollama, OpenRouter, ...) to swap brains
later without touching the heartbeat.
"""
from __future__ import annotations

from dataclasses import dataclass

import requests


@dataclass
class LLMConfig:
    base_url: str = "https://inference-api.nousresearch.com/v1"
    model: str = "stepfun/step-3.7-flash:free"
    api_key_env: str = "NOUS_PORTAL_API_KEY"
    timeout_seconds: int = 120
    max_tokens: int = 1024
    temperature: float = 0.7

    @classmethod
    def from_dict(cls, d: dict) -> "LLMConfig":
        import dataclasses
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


class LLMError(RuntimeError):
    """Anything that stops the brain from answering this tick."""


def chat(cfg: LLMConfig, system: str, user: str, api_key: str = "") -> str:
    """One chat completion. Raises LLMError on any failure — the heartbeat
    logs it and keeps the loop alive, so a dead brain never kills the body."""
    import os
    key = api_key or os.environ.get(cfg.api_key_env, "")
    if not key:
        raise LLMError(
            f"set {cfg.api_key_env} — free key at https://portal.nousresearch.com"
        )
    url = cfg.base_url.rstrip("/") + "/chat/completions"
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": cfg.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": cfg.max_tokens,
                "temperature": cfg.temperature,
            },
            timeout=cfg.timeout_seconds,
        )
    except requests.RequestException as e:
        raise LLMError(f"LLM request failed: {e}") from e
    if r.status_code == 402:
        raise LLMError(
            "portal returned 402: model is not on the free plan or the key lacks access"
        )
    if r.status_code == 429:
        raise LLMError("portal rate limit (429): free-tier quota hit, retry next tick")
    if not r.ok:
        raise LLMError(f"portal error {r.status_code}: {r.text[:200]}")
    try:
        return r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as e:
        raise LLMError(f"unexpected portal response: {r.text[:200]}") from e
