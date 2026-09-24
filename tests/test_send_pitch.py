"""send_pitch verification (stdlib unittest, no pytest needed).

Run:  python3 tests/test_send_pitch.py
"""
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools
from tools import ToolContext, _parse_draft


SEND_READY_DRAFT = """# PITCH DRAFT — NOT SENT (tick 7)
Lead: candchvac.com (score 2)
To: office@ccheatingandcooling.com
Send-ready: YES — public contact email found on their site (homepage)
Service: SEO basics fix @ $25.00

Subject: Quick SEO basics fix for candchvac.com

Hi — I audited candchvac.com and found: no_meta_description.

I can fix this as a one-off SEO basics fix for $25.00, delivered within 48 hours.
"""

NOT_READY_DRAFT = """# PITCH DRAFT — NOT SENT (tick 7)
Lead: example.com (score 2)
To: [no public email found — use their contact form or manual lookup]
Send-ready: NO
Service: SEO basics fix @ $25.00

Subject: Quick SEO basics fix for example.com

Hi — I audited example.com and found: no_meta_description.
"""


def make_ctx(tmp: Path, auto_send=True, **over):
    cfg = {
        "gig_seeker": {
            "auto_send": auto_send,
            "sender": {
                "agent_name": "Hermes",
                "operator_name": "Aaron",
                "business_name": "Technology Squared LLC",
                "reply_email": "techsquaredllc42071@gmail.com",
                "postal_address": "1706 Brooklyn Dr A, Murray, KY 42071",
            },
        },
        "outreach": {"max_per_tick": 2, "max_per_day": 3,
                     "resend_cooldown_days": 7},
    }
    cfg.update(over)
    drafts = tmp / "outbox" / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    return ToolContext(cfg=cfg, ledger=None, inventory=None, gigs=None,
                       data=tmp, tick=7)


def write_draft(ctx, name, text):
    p = ctx.data / "outbox" / "drafts" / name
    p.write_text(text)
    return name


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None):
        self.host = host

    def starttls(self):
        pass

    def login(self, user, pw):
        assert user and pw

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class SendPitchTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        FakeSMTP.sent = []
        self._env = dict(os.environ)
        os.environ.pop("OUTREACH_SMTP_HOST", None)
        os.environ.pop("OUTREACH_SMTP_USER", None)
        os.environ.pop("OUTREACH_SMTP_PASS", None)
        os.environ.pop("RESEND_API_KEY", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def _smtp_env(self):
        os.environ["OUTREACH_SMTP_HOST"] = "smtp.example.com"
        os.environ["OUTREACH_SMTP_PORT"] = "587"
        os.environ["OUTREACH_SMTP_USER"] = "bot@example.com"
        os.environ["OUTREACH_SMTP_PASS"] = "secret"

    def test_parse_draft(self):
        p = _parse_draft(SEND_READY_DRAFT)
        self.assertEqual(p["domain"], "candchvac.com")
        self.assertEqual(p["to"], "office@ccheatingandcooling.com")
        self.assertTrue(p["send_ready"])
        self.assertIn("SEO basics fix", p["subject"])
        self.assertIn("audited candchvac.com", p["body"])

    def test_refuses_when_auto_send_off(self):
        ctx = make_ctx(self.tmp, auto_send=False)
        write_draft(ctx, "draft_x_candchvac.com.md", SEND_READY_DRAFT)
        out = tools.send_pitch(ctx, draft_name="draft_x_candchvac.com.md")
        self.assertIn("auto_send is OFF", out)

    def test_refuses_when_email_not_configured(self):
        ctx = make_ctx(self.tmp, auto_send=True)
        write_draft(ctx, "draft_x_candchvac.com.md", SEND_READY_DRAFT)
        out = tools.send_pitch(ctx, draft_name="draft_x_candchvac.com.md")
        self.assertIn("not configured", out)

    def test_refuses_non_send_ready(self):
        ctx = make_ctx(self.tmp, auto_send=True)
        self._smtp_env()
        write_draft(ctx, "draft_x_example.com.md", NOT_READY_DRAFT)
        out = tools.send_pitch(ctx, draft_name="draft_x_example.com.md")
        self.assertIn("not send-ready", out)
        self.assertEqual(FakeSMTP.sent, [])

    def test_sends_and_logs(self):
        ctx = make_ctx(self.tmp, auto_send=True)
        self._smtp_env()
        name = write_draft(ctx, "draft_20260924T000000_candchvac.com.md",
                           SEND_READY_DRAFT)
        with patch("tools._smtplib.SMTP", FakeSMTP):
            out = tools.send_pitch(ctx, draft_name=name)
        self.assertIn("Sent pitch to office@ccheatingandcooling.com", out)
        self.assertEqual(len(FakeSMTP.sent), 1)
        msg = FakeSMTP.sent[0]
        self.assertEqual(msg["To"], "office@ccheatingandcooling.com")
        # draft marked sent, record logged
        drafts = list((ctx.data / "outbox" / "drafts").glob("*_SENT.md"))
        self.assertEqual(len(drafts), 1)
        sent = list((ctx.data / "outbox" / "sent").glob("sent_*.json"))
        self.assertEqual(len(sent), 1)
        rec = json.loads(sent[0].read_text())
        self.assertEqual(rec["domain"], "candchvac.com")
        self.assertEqual(rec["tick"], 7)

    def test_no_duplicate_send(self):
        ctx = make_ctx(self.tmp, auto_send=True)
        self._smtp_env()
        name = write_draft(ctx, "draft_20260924T000000_candchvac.com.md",
                           SEND_READY_DRAFT)
        with patch("tools._smtplib.SMTP", FakeSMTP):
            tools.send_pitch(ctx, draft_name=name)
            out = tools.send_pitch(
                ctx, draft_name=name[:-3] + "_SENT.md")
        self.assertIn("already sent", out)
        self.assertEqual(len(FakeSMTP.sent), 1)

    def test_domain_cooldown(self):
        ctx = make_ctx(self.tmp, auto_send=True)
        self._smtp_env()
        with patch("tools._smtplib.SMTP", FakeSMTP):
            n1 = write_draft(ctx, "draft_20260924T000000_candchvac.com.md",
                             SEND_READY_DRAFT)
            tools.send_pitch(ctx, draft_name=n1)
            n2 = write_draft(ctx, "draft_20260924T000001_candchvac.com.md",
                             SEND_READY_DRAFT)
            out = tools.send_pitch(ctx, draft_name=n2)
        self.assertIn("cooldown", out)
        self.assertEqual(len(FakeSMTP.sent), 1)

    def test_per_tick_cap(self):
        ctx = make_ctx(self.tmp, auto_send=True)
        ctx.cfg["outreach"]["max_per_tick"] = 1
        self._smtp_env()
        other = SEND_READY_DRAFT.replace("candchvac.com", "otherbiz.com") \
            .replace("office@ccheatingandcooling.com", "hello@otherbiz.com")
        with patch("tools._smtplib.SMTP", FakeSMTP):
            n1 = write_draft(ctx, "draft_20260924T000000_candchvac.com.md",
                             SEND_READY_DRAFT)
            tools.send_pitch(ctx, draft_name=n1)
            n2 = write_draft(ctx, "draft_20260924T000001_otherbiz.com.md", other)
            out = tools.send_pitch(ctx, draft_name=n2)
        self.assertIn("per-tick send cap", out)
        self.assertEqual(len(FakeSMTP.sent), 1)

    def test_daily_cap(self):
        ctx = make_ctx(self.tmp, auto_send=True)
        ctx.cfg["outreach"]["max_per_day"] = 1
        self._smtp_env()
        other = SEND_READY_DRAFT.replace("candchvac.com", "otherbiz.com") \
            .replace("office@ccheatingandcooling.com", "hello@otherbiz.com")
        with patch("tools._smtplib.SMTP", FakeSMTP):
            n1 = write_draft(ctx, "draft_20260924T000000_candchvac.com.md",
                             SEND_READY_DRAFT)
            tools.send_pitch(ctx, draft_name=n1)
            n2 = write_draft(ctx, "draft_20260924T000001_otherbiz.com.md", other)
            out = tools.send_pitch(ctx, draft_name=n2)
        self.assertIn("daily send cap", out)
        self.assertEqual(len(FakeSMTP.sent), 1)

    def test_rejects_path_traversal(self):
        ctx = make_ctx(self.tmp, auto_send=True)
        out = tools.send_pitch(ctx, draft_name="../../etc/passwd")
        self.assertIn("TOOL ERROR", out)


if __name__ == "__main__":
    unittest.main()
