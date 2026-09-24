"""
Hermes autonomous heartbeat daemon.

Every tick:
  1. Poll Stripe for completed gig payments -> credit ledger (treasury + commission).
  2. Check the agent's operating balance against the heartbeat cost.
     - Can't pay? Runway insurance covers one tick, else DEEP REST:
       the loop sleeps without waking the agent until funds arrive.
  3. Burn the heartbeat cost.
  4. Build the survival prompt (persona + ledger + gigs + effects) and
     invoke the agent runner.

Deep Rest is real: no prompt is built, no model is called, nothing burns.

Run:  python heartbeat.py --config config.yaml
Stop: Ctrl-C or `systemctl stop hermes-heartbeat` (see README for the unit).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "gig_seeker"))

from ledger import Ledger
from marketplace import Inventory
from prospector import Lead
from stripe_rails import GigStore, poll_completed_payments


def load_config(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _read_json(path: Path, key: str) -> list:
    try:
        import json
        return json.loads(path.read_text()).get(key, [])
    except Exception:
        return []


def build_prompt(cfg: dict, ledger: Ledger, inventory: Inventory,
                 gig_store: GigStore, tick: int) -> str:
    persona = Path(cfg["persona_path"]).read_text()
    s = ledger.summary()
    effects = inventory.active_effects()
    open_gigs = gig_store.open_gigs()
    recent = ledger.history("agent:operating", limit=8)
    data = Path(cfg["data_dir"])
    leads = _read_json(data / "leads.json", "leads")
    opps = _read_json(data / "opportunities.json", "opportunities")

    lines = [persona, "\n--- SYSTEM HEARTBEAT ---",
             f"Tick #{tick} at {time.strftime('%Y-%m-%d %H:%M:%S')}.",
             f"Operating balance: {s['agent_credits']} credits (${s['agent_usd']:.2f}).",
             f"Treasury (Aaron's): {s['treasury_credits']} credits (${s['treasury_usd']:.2f}).",
             f"Heartbeat cost: {cfg['heartbeat_cost_credits']} credits."]
    if open_gigs:
        lines.append("Open gigs awaiting payment:")
        for g in open_gigs:
            lines.append(f"  - {g['gig_id']}: ${g['amount_usd']:.2f} — {g['description']} [{g['status']}]")
    else:
        lines.append("No open gigs. Prospecting is your job.")
    if opps:
        lines.append(f"Bounty board opportunities ({len(opps)} new):")
        for o in opps[:10]:
            lines.append(f"  - [{o['board']}] {o['title']} — ${o['reward_usd']:.2f} {o['url']}")
    if leads:
        lines.append(f"Prospected leads ({len(leads)} with real issues):")
        for ld in leads[:10]:
            angle = Lead(domain=ld["domain"], url=ld["url"],
                         issues=ld["issues"]).pitch_angle()
            lines.append(f"  - {ld['domain']} (score {ld['score']}): {angle}")
        lines.append("Use draft_pitch() for honest outreach — only to leads with measured issues.")
    if effects.get("market_stall"):
        lines.append("Your market stall is ACTIVE: advertise it in outreach.")
    lines.append("Recent ledger activity:")
    for r in recent:
        lines.append(f"  {r['kind']} {r['delta']:+} — {r['memo']}")
    lines.append("\nAct now. Earn, spend wisely, survive. Report what you did.")
    return "\n".join(lines)


def run_agent(cfg: dict, prompt: str, tick: int) -> None:
    """Invoke the agent. Precedence:
    1. agent_command — shell command receiving the prompt on stdin (the real
       Hermes runner, when it exists).
    2. llm (enabled) — the free Step 3.7 Flash brain via the Nous portal.
    3. Otherwise the prompt is just logged to the outbox."""
    outbox = Path(cfg["outbox_dir"])
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / f"tick_{tick:06d}.md").write_text(prompt)
    cmd = cfg.get("agent_command")
    if cmd:
        subprocess.run(cmd, input=prompt.encode(), shell=True, check=False)
        return
    llm_cfg = cfg.get("llm") or {}
    if llm_cfg.get("enabled"):
        from llm import LLMConfig, LLMError, chat as llm_chat
        persona = Path(cfg["persona_path"]).read_text()
        try:
            reply = llm_chat(LLMConfig.from_dict(llm_cfg), persona, prompt)
        except LLMError as e:
            # A dead brain must never kill the body: record it, heartbeat logs it.
            (outbox / f"tick_{tick:06d}.llm_error.md").write_text(str(e))
            raise
        (outbox / f"tick_{tick:06d}.reply.md").write_text(reply)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()
    cfg = load_config(Path(args.config))

    data = Path(cfg["data_dir"])
    ledger = Ledger(data / "economy.db")
    gigs = GigStore(data / "economy.db")
    inventory = Inventory(ledger, data / "inventory.json")

    cost = cfg["heartbeat_cost_credits"]
    base_interval = cfg["heartbeat_interval_seconds"]
    deep_rest_check = cfg.get("deep_rest_check_seconds", 21600)
    commission = cfg["agent_commission_pct"]
    log = data / "heartbeat.log"

    def say(msg: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        print(line, flush=True)
        with open(log, "a") as f:
            f.write(line + "\n")

    tick = 0
    say("heartbeat online")
    while True:
        tick += 1
        # 1. Money in: Stripe poll (safe to re-run; ledger dedups).
        if cfg.get("stripe_enabled"):
            try:
                credited = poll_completed_payments(gigs, ledger, commission)
                for c in credited:
                    say(f"PAYMENT gig={c['gig_id']} ${c['amount_usd']:.2f} "
                        f"commission={c['commission']} owner_cut={c['owner_cut']}")
            except Exception as e:  # never let billing break the loop
                say(f"stripe poll failed: {e}")

        # 2. Can we afford this tick?
        balance = ledger.balance("agent:operating")
        if balance < cost:
            if inventory.consume_insurance():
                say("runway insurance consumed: tick covered")
            else:
                say(f"DEEP REST: balance {balance} < cost {cost}. Sleeping.")
                (data / "DEEP_REST").touch()
                time.sleep(deep_rest_check)
                (data / "DEEP_REST").unlink(missing_ok=True)
                continue

        # 3. Burn the tick.
        ledger.burn(cost, memo=f"heartbeat tick #{tick}")

        # 4. Wake the agent.
        effects = inventory.active_effects()
        interval = effects.get("interval", base_interval)
        try:
            prompt = build_prompt(cfg, ledger, inventory, gigs, tick)
            run_agent(cfg, prompt, tick)
            say(f"tick #{tick}: agent invoked (balance now {ledger.balance('agent:operating')})")
        except Exception as e:
            say(f"tick #{tick}: agent run failed: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    main()
