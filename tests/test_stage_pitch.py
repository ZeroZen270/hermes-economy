"""stage_pitch verification (stdlib unittest, no pytest needed).

Run:  python3 tests/test_stage_pitch.py
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
                       data=tmp, tick=7)


def staged_files(tmp: Path):
    d = tmp / "outbox" / "submissions"
    return sorted(d.glob("submission_*_pitch_*.json")) if d.is_dir() else []


class TestStagePitch(unittest.TestCase):
    def test_valid_pitch_staged(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            out = tools.stage_pitch(
                make_ctx(tmp), to="office@example.com",
                subject="Quick SEO win for Example",
                body="Hi, I found a real issue on your site...",
                lead_domain="example.com", note="send-ready lead")
            self.assertIn("Staged pitch", out)
            files = staged_files(tmp)
            self.assertEqual(len(files), 1)
            entry = json.loads(files[0].read_text())
            self.assertEqual(entry["status"], "staged")
            self.assertEqual(entry["kind"], "pitch")
            self.assertEqual(entry["to"], "office@example.com")
            self.assertEqual(entry["subject"], "Quick SEO win for Example")
            self.assertIn("real issue", entry["body"])
            self.assertEqual(entry["tick"], 7)

    def test_bad_email_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            out = tools.stage_pitch(make_ctx(tmp), to="not-an-email",
                                    subject="s", body="b")
            self.assertIn("TOOL ERROR", out)
            self.assertEqual(staged_files(tmp), [])

    def test_empty_subject_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            out = tools.stage_pitch(make_ctx(tmp), to="a@b.com",
                                    subject="", body="b")
            self.assertIn("TOOL ERROR", out)
            self.assertEqual(staged_files(tmp), [])

    def test_empty_body_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            out = tools.stage_pitch(make_ctx(tmp), to="a@b.com",
                                    subject="s", body="  ")
            self.assertIn("TOOL ERROR", out)
            self.assertEqual(staged_files(tmp), [])

    def test_two_pitches_do_not_collide(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            tools.stage_pitch(make_ctx(tmp), to="a@x.com", subject="s1",
                              body="b1", lead_domain="x.com")
            tools.stage_pitch(make_ctx(tmp), to="b@y.com", subject="s2",
                              body="b2", lead_domain="y.com")
            self.assertEqual(len(staged_files(tmp)), 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
