"""scout_lead_update verification (stdlib unittest, no pytest needed).

Run:  python3 tests/test_scout_lead.py
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
    return ToolContext(cfg={}, ledger=None, inventory=None, gigs=None,
                       data=tmp, tick=42)


def seed(tmp: Path) -> None:
    (tmp / "scout_leads.json").write_text(json.dumps({"leads": [
        {"platform": "ExampleGigs", "url": "https://example.com",
         "how_to_earn": "do tasks", "pay_range": "$5-50",
         "friction": "email signup", "found_ts": 1758780000,
         "status": "new"},
    ]}))


class TestScoutLeadUpdate(unittest.TestCase):
    def test_pursued_updates_status(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            seed(tmp)
            out = tools.scout_lead_update(make_ctx(tmp), platform="examplegigs",
                                          status="pursued", note="applied")
            self.assertIn("status -> pursued", out)
            leads = json.loads((tmp / "scout_leads.json").read_text())["leads"]
            self.assertEqual(leads[0]["status"], "pursued")
            self.assertEqual(leads[0]["agent_note"], "applied")
            self.assertEqual(leads[0]["worked_tick"], 42)

    def test_needs_aaron(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            seed(tmp)
            out = tools.scout_lead_update(make_ctx(tmp), platform="ExampleGigs",
                                          status="needs_aaron",
                                          note="needs KYC selfie")
            self.assertIn("status -> needs_aaron", out)

    def test_bad_status_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            seed(tmp)
            out = tools.scout_lead_update(make_ctx(tmp), platform="ExampleGigs",
                                          status="maybe", note="x")
            self.assertIn("TOOL ERROR", out)
            leads = json.loads((tmp / "scout_leads.json").read_text())["leads"]
            self.assertEqual(leads[0]["status"], "new")

    def test_unknown_platform(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            seed(tmp)
            out = tools.scout_lead_update(make_ctx(tmp), platform="Nope",
                                          status="blocked", note="gone")
            self.assertIn("TOOL ERROR", out)

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as t:
            out = tools.scout_lead_update(make_ctx(Path(t)), platform="X",
                                          status="pursued", note="x")
            self.assertIn("TOOL ERROR", out)

    def test_registered_in_catalog(self):
        self.assertIn("scout_lead_update", tools.TOOLS)
        self.assertIn("scout_leads.json", tools.TOOLS["scout_lead_update"]["description"])


if __name__ == "__main__":
    unittest.main()
