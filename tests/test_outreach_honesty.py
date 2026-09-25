"""Outreach honesty gates: draft_pitch must refuse leads with no public
email (logging them for manual lookup), and stage_pitch must re-audit at
stage time and refuse stale findings.

Run: python3 tests/test_outreach_honesty.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools
from tools import ToolContext


def make_ctx(tmp: Path) -> ToolContext:
    (tmp / "outbox" / "submissions").mkdir(parents=True, exist_ok=True)
    return ToolContext(cfg={}, ledger=None, inventory=None, gigs=None,
                       data=tmp, tick=9)


def add_lead(tmp: Path, domain: str, emails, issues=("no_https",)):
    (tmp / "leads.json").write_text(json.dumps({"leads": [
        {"domain": domain, "score": 50, "issues": list(issues),
         "emails": list(emails)}]}))


class FakeLead:
    def __init__(self, issues):
        self.issues = issues


class TestDraftEmailGate(unittest.TestCase):
    def test_draft_refused_without_email(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); ctx = make_ctx(tmp)
            add_lead(tmp, "noemail.com", [])
            out = tools.draft_pitch(ctx, lead_domain="noemail.com",
                                    service="HTTPS fix", price_usd=25)
            self.assertIn("TOOL ERROR", out)
            self.assertIn("no public contact email", out)
            drafts = tmp / "outbox" / "drafts"
            self.assertFalse(drafts.exists() and any(drafts.iterdir()))

    def test_no_email_lead_logged_for_manual_lookup(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); ctx = make_ctx(tmp)
            add_lead(tmp, "noemail.com", [], issues=["slow"])
            tools.draft_pitch(ctx, lead_domain="noemail.com",
                              service="Speed fix", price_usd=25)
            doc = json.loads((tmp / "no_email_leads.json").read_text())
            self.assertEqual(doc["leads"][0]["domain"], "noemail.com")
            self.assertEqual(doc["leads"][0]["issues"], ["slow"])
            # second refusal does not duplicate the log entry
            tools.draft_pitch(ctx, lead_domain="noemail.com",
                              service="Speed fix", price_usd=25)
            doc = json.loads((tmp / "no_email_leads.json").read_text())
            self.assertEqual(len(doc["leads"]), 1)

    def test_draft_still_works_with_email(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); ctx = make_ctx(tmp)
            add_lead(tmp, "good.com", ["info@good.com"])
            out = tools.draft_pitch(ctx, lead_domain="good.com",
                                    service="HTTPS fix", price_usd=25)
            self.assertIn("Draft saved", out)
            txt = next((tmp / "outbox" / "drafts").glob("*.md")).read_text()
            self.assertIn("Send-ready: YES", txt)
            self.assertIn("info@good.com", txt)


class TestStageReverify(unittest.TestCase):
    def setUp(self):
        self._real = tools._audit_domain

    def tearDown(self):
        tools._audit_domain = self._real

    def test_stage_refused_when_finding_stale(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); ctx = make_ctx(tmp)
            add_lead(tmp, "stale.com", ["info@stale.com"], issues=["no_https"])
            tools._audit_domain = lambda dom: FakeLead(["not_mobile_friendly"])
            out = tools.stage_pitch(ctx, to="info@stale.com", subject="s",
                                    body="b", lead_domain="stale.com")
            self.assertIn("TOOL ERROR", out)
            self.assertIn("stale", out)
            self.assertEqual(list((tmp / "outbox" / "submissions").glob("*.json")), [])

    def test_stage_passes_when_finding_fresh(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); ctx = make_ctx(tmp)
            add_lead(tmp, "fresh.com", ["info@fresh.com"], issues=["no_https"])
            tools._audit_domain = lambda dom: FakeLead(["no_https", "slow"])
            out = tools.stage_pitch(ctx, to="info@fresh.com", subject="s",
                                    body="b", lead_domain="fresh.com")
            self.assertIn("Staged pitch", out)
            entry = json.loads(next(
                (tmp / "outbox" / "submissions").glob("*.json")).read_text())
            self.assertIn("re-verified", entry["notes"])

    def test_stage_refused_when_site_now_clean(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); ctx = make_ctx(tmp)
            add_lead(tmp, "clean.com", ["info@clean.com"],
                     issues=["not_mobile_friendly"])
            tools._audit_domain = lambda dom: FakeLead([])
            out = tools.stage_pitch(ctx, to="info@clean.com", subject="s",
                                    body="b", lead_domain="clean.com")
            self.assertIn("TOOL ERROR", out)

    def test_stage_skips_reaudit_without_lead_record(self):
        # No leads.json entry -> no network in unit tests, existing guards apply.
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); ctx = make_ctx(tmp)

            def boom(dom):
                raise AssertionError("re-audit must not run here")

            tools._audit_domain = boom
            out = tools.stage_pitch(ctx, to="office@example.com", subject="s",
                                    body="b", lead_domain="example.com")
            self.assertIn("Staged pitch", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
