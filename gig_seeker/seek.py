"""
Hermes gig seeker runner — one command the heartbeat (or a cron) runs.

  python seek.py --config ../config.yaml

Does:
  1. Scans bounty boards -> data/opportunities.json (new since last scan)
  2. Audits configured seed domains -> data/leads.json (scored, pitchable)

The heartbeat prompt builder picks both files up automatically, so the
agent wakes up to fresh leads and bounties every tick.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from bounty_monitor import load_boards, scan
from prospector import audit_list


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="../config.yaml")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config))
    gs = cfg.get("gig_seeker", {})
    data = Path(cfg["data_dir"])

    # 1. bounties
    boards = load_boards(gs)
    fresh = scan(boards, data / "opportunities.json", data / "seen_opps.json",
                 min_reward_usd=gs.get("min_bounty_usd", 5.0))
    print(f"bounties: {len(boards)} boards, {len(fresh)} new opportunities")

    # 2. leads (seed domains from config; the agent can also call audit_leads()
    #    directly with domains it finds while browsing)
    seeds = gs.get("prospect_domains", [])
    if seeds:
        leads = audit_list(seeds, data / "leads.json")
        print(f"prospects: {len(seeds)} audited, {len(leads)} with issues")
    else:
        print("prospects: no seed domains configured")


if __name__ == "__main__":
    main()
