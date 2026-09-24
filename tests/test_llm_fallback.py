"""Fallback-model verification for llm.py (stdlib unittest, no pytest needed).

Run:  python3 tests/test_llm_fallback.py
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm
from llm import LLMConfig, LLMError, chat


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.ok = 200 <= status_code < 300
        self.text = str(payload)

    def json(self):
        return self._payload


def ok_response(content="hello from fallback"):
    return FakeResponse(200, {"choices": [{"message": {"content": content}}]})


def cfg():
    return LLMConfig(model="primary-model", fallback_model="fallback-model",
                     max_retries=3, retry_backoff_seconds=0.0,
                     timeout_seconds=5)


class TestFallback(unittest.TestCase):
    def test_primary_recovers_no_fallback_used(self):
        """A 429 on the first attempt followed by success must not touch
        the fallback model."""
        seen_models = []

        def fake_post(url, headers=None, json=None, timeout=None):
            seen_models.append(json["model"])
            if len(seen_models) == 1:
                return FakeResponse(429, {"error": "rate limited"})
            return ok_response("primary recovered")

        with patch.object(llm.requests, "post", side_effect=fake_post), \
             patch.object(llm.time, "sleep", lambda s: None):
            out = chat(cfg(), "sys", "hi", api_key="test-key")
        self.assertEqual(out, "primary recovered")
        self.assertEqual(seen_models, ["primary-model", "primary-model"])

    def test_fallback_succeeds_after_primary_429_exhaustion(self):
        """Primary exhausts all retries on 429; the fallback model must then
        be tried and its successful reply returned."""
        seen_models = []

        def fake_post(url, headers=None, json=None, timeout=None):
            seen_models.append(json["model"])
            if json["model"] == "primary-model":
                return FakeResponse(429, {"error": "quota exceeded"})
            return ok_response("fallback answered")

        with patch.object(llm.requests, "post", side_effect=fake_post), \
             patch.object(llm.time, "sleep", lambda s: None):
            out = chat(cfg(), "sys", "hi", api_key="test-key")
        self.assertEqual(out, "fallback answered")
        self.assertIn("fallback-model", seen_models)
        # primary got its full retry budget first: max_retries + 1 attempts
        self.assertEqual(seen_models.count("primary-model"), 4)

    def test_both_throttled_raises(self):
        """429 on both models through every retry must surface as LLMError,
        never a silent empty reply."""
        def fake_post(url, headers=None, json=None, timeout=None):
            return FakeResponse(429, {"error": "still throttled"})

        with patch.object(llm.requests, "post", side_effect=fake_post), \
             patch.object(llm.time, "sleep", lambda s: None):
            with self.assertRaises(LLMError):
                chat(cfg(), "sys", "hi", api_key="test-key")


if __name__ == "__main__":
    unittest.main(verbosity=2)
