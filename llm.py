"""
Free brain for Hermes: Google AI Studio (Gemini) free tier.

$0, no credit card — just a Google account. Get a key at
https://aistudio.google.com/app/apikey and export it:

    export GEMINI_API_KEY=...

The client is provider-agnostic on purpose — point `base_url` at any
OpenAI-compatible endpoint to swap brains without touching the heartbeat:

  - Groq (free tier, no card): https://api.groq.com/openai/v1  (GROQ_API_KEY)
  - Nous Research portal:      https://inference-api.nousresearch.com/v1
    (needs a funded balance even for :free models — not actually $0)

Note: Google's free tier may use prompts to improve its models, so the
heartbeat prompt carries no secrets — only ledger balances and gig state.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass

import requests


@dataclass
class LLMConfig:
    base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    model: str = "gemini-3.6-flash"
    api_key_env: str = "GEMINI_API_KEY"
    timeout_seconds: int = 120
    max_tokens: int = 1024
    temperature: float = 0.7
    max_retries: int = 3               # extra attempts on retryable failures
    retry_backoff_seconds: float = 5.0  # base; waits base * 2^attempt + jitter

    @classmethod
    def from_dict(cls, d: dict) -> "LLMConfig":
        import dataclasses
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


class LLMError(RuntimeError):
    """Anything that stops the brain from answering this tick."""


# 429 = quota, 5xx = provider-side trouble (e.g. Gemini "high demand" 503s).
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def chat(cfg: LLMConfig, system: str, user: str, api_key: str = "") -> str:
    """One chat completion. Raises LLMError on any failure — the heartbeat
    logs it and keeps the loop alive, so a dead brain never kills the body."""
    return chat_messages(cfg, [{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                        api_key=api_key)


def chat_messages(cfg: LLMConfig, messages: list[dict],
                  api_key: str = "") -> str:
    """Multi-turn chat completion with retries. Transient provider failures
    (429/5xx, network blips) are retried with exponential backoff; anything
    else raises LLMError immediately."""
    import os
    key = api_key or os.environ.get(cfg.api_key_env, "")
    if not key:
        raise LLMError(
            f"set {cfg.api_key_env} — free key, no card: "
            f"https://aistudio.google.com/app/apikey"
        )
    url = cfg.base_url.rstrip("/") + "/chat/completions"
    last_err: LLMError | None = None
    for attempt in range(cfg.max_retries + 1):
        try:
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": cfg.model,
                    "messages": messages,
                    "max_tokens": cfg.max_tokens,
                    "temperature": cfg.temperature,
                },
                timeout=cfg.timeout_seconds,
            )
        except requests.RequestException as e:
            last_err = LLMError(f"LLM request failed (attempt {attempt + 1}): {e}")
        else:
            if r.status_code in RETRYABLE_STATUS:
                last_err = LLMError(
                    f"provider error {r.status_code} (attempt {attempt + 1}, "
                    f"retrying): {r.text[:200]}"
                )
            elif r.status_code == 402:
                raise LLMError("provider returned 402: payment required on this model/key")
            elif not r.ok:
                raise LLMError(f"provider error {r.status_code}: {r.text[:200]}")
            else:
                try:
                    content = r.json()["choices"][0]["message"]["content"]
                except (KeyError, IndexError, ValueError) as e:
                    raise LLMError(
                        f"unexpected provider response: {r.text[:200]}") from e
                if not content:
                    raise LLMError("provider returned empty content")
                return content
        if attempt < cfg.max_retries:
            time.sleep(cfg.retry_backoff_seconds * (2 ** attempt)
                       + random.uniform(0, 2))
    raise last_err  # type: ignore[misc]
