"""Outreach suppression guard: draft_pitch/stage_pitch must refuse
quarantined, in-pipeline, or cooling-down domains (same guarantee
send_pitch already had). Run: python3 tests/test_outreach_guard.py"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools
from tools import ToolContext


def make_ctx(tmp: Path, quarantine=()) -> ToolContext:
    (tmp / "outbox" / "submissions").mkdir(parents=True, exist_ok=True)
    if quarantine:
        (tmp / "quarantine.json").write_text(
            json.dumps({"domains": list(quarantine)}))
    return ToolContext(cfg={}, ledger=None, inventory=None, gigs=None,
                       data=tmp, tick=9)


def add_lead(tmp: Path, domain: str):
    (tmp / "leads.json").write_text(json.dumps({"leads": [
        {"domain": domain, "score": 80, "issues": ["no meta description"],
         "emails": ["office@" + domain]}]}))


def add_submission(tmp: Path, domain: str, status: str, ts: float = None):
    sub = tmp / "outbox" / "submissions"
    slug = domain.replace(".", "-")
    (sub / f"submission_20260924T000000_pitch_{slug}.json").write_text(
        json.dumps({"ts": ts if ts is not None else time.time(),
                    "tick": 8, "status": status, "kind": "pitch",
                    "to": "office@" + domain, "subject": "s",
                    "body": "b", "lead_domain": domain}))


def add_sent(tmp: Path, domain: str, ts: float):
    sent = tmp / "outbox" / "sent"
    sent.mkdir(parents=True, exist_ok=True)
    (sent / f"sent_20260924T000000_{domain}.json").write_text(
        json.dumps({"ts": ts, "tick": 5, "domain": domain,
                    "to": "office@" + domain, "subject": "s", "via": "x"}))


class TestOutreachGuard(unittest.TestCase):
    def test_stage_blocked_by_hold(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); ctx = make_ctx(tmp)
            add_submission(tmp, "herbertplumbing.com", "hold")
            out = tools.stage_pitch(ctx, to="office@herbertplumbing.com",
                                    subject="s", body="b",
                                    lead_domain="herbertplumbing.com")
            self.assertIn("TOOL ERROR", out)
            self.assertIn("pipeline", out)

    def test_stage_blocked_by_quarantine(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); ctx = make_ctx(tmp, quarantine=["herbertplumbing.com"])
            out = tools.stage_pitch(ctx, to="office@herbertplumbing.com",
                                    subject="s", body="b",
                                    lead_domain="herbertplumbing.com")
            self.assertIn("TOOL ERROR", out)
            self.assertIn("quarantined", out)

    def test_stage_blocked_by_recent_send(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); ctx = make_ctx(tmp)
            add_sent(tmp, "example.com", time.time() - 3600)
            out = tools.stage_pitch(ctx, to="office@example.com",
                                    subject="s", body="b",
                                    lead_domain="example.com")
            self.assertIn("TOOL ERROR", out)
            self.assertIn("cooldown", out)

    def test_stage_allowed_after_cooldown(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); ctx = make_ctx(tmp)
            add_sent(tmp, "example.com", time.time() - 8 * 86400)
            out = tools.stage_pitch(ctx, to="office@example.com",
                                    subject="s", body="b",
                                    lead_domain="example.com")
            self.assertIn("Staged pitch", out)

    def test_draft_blocked_by_hold(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); ctx = make_ctx(tmp)
            add_lead(tmp, "herbertplumbing.com")
            add_submission(tmp, "herbertplumbing.com", "staged")
            out = tools.draft_pitch(ctx, lead_domain="herbertplumbing.com",
                                    service="SEO fix", price_usd=25)
            self.assertIn("TOOL ERROR", out)

    def test_draft_blocked_by_quarantine(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); ctx = make_ctx(tmp, quarantine=["herbertplumbing.com"])
            add_lead(tmp, "herbertplumbing.com")
            out = tools.draft_pitch(ctx, lead_domain="herbertplumbing.com",
                                    service="SEO fix", price_usd=25)
            self.assertIn("quarantined", out)

    def test_draft_allowed_fresh_lead(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); ctx = make_ctx(tmp)
            add_lead(tmp, "freshlead.com")
            out = tools.draft_pitch(ctx, lead_domain="freshlead.com",
                                    service="SEO fix", price_usd=25)
            self.assertIn("Draft saved", out)


if __name__ == "__main__":
    unittest.main()
