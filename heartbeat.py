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
      python heartbeat.py --config config.yaml --once   # single tick (cron / CI)
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
    # Keep sight of open listings already seen in earlier ticks, so "no new"
    # is never mistaken for "no bounties".
    open_all = _read_json(data / "open_bounties.json", "bounties")
    fresh_ids = {f"{o.get('board')}:{o.get('url')}" for o in opps}
    still_open = [o for o in open_all
                  if f"{o.get('board')}:{o.get('url')}" not in fresh_ids]
    if still_open:
        lines.append(f"Still open from earlier scans ({len(still_open)}):")
        for o in still_open[:8]:
            lines.append(f"  - [{o['board']}] {o['title']} — ${o['reward_usd']:.2f} {o['url']}")
    if leads:
        lines.append(f"Prospected leads ({len(leads)} with real issues):")
        for ld in leads[:10]:
            angle = Lead(domain=ld["domain"], url=ld["url"],
                         issues=ld["issues"]).pitch_angle()
            lines.append(f"  - {ld['domain']} (score {ld['score']}): {angle}")
        lines.append("Use draft_pitch() for honest outreach — only to leads with measured issues.")
        auto_send = bool((cfg.get("gig_seeker") or {}).get("auto_send", False))
        if auto_send:
            lines.append(
                "OUTREACH IS AUTHORIZED (owner-enabled auto_send): you may call "
                "send_pitch() to actually SEND a drafted pitch — but ONLY for "
                "send-ready leads (draft says 'Send-ready: YES', i.e. a public "
                "contact email was found on their site). Prioritize send-ready "
                "leads; keep drafting for the rest. Respect the tool's caps and "
                "never re-pitch a domain inside its cooldown window. "
                "If send_pitch reports no email transport is configured (or a "
                "send fails), do NOT drop the pitch: call stage_pitch() with "
                "the draft's to/subject/body — Odin sends it for you.")
        else:
            lines.append(
                "auto_send is OFF: drafts stay unsent for Aaron's review. "
                "Keep drafting strong pitches; do not try to send.")
    scout_leads = _read_json(data / "scout_leads.json", "leads")
    fresh_scout = [l for l in scout_leads
                   if str(l.get("status", "new")).lower() == "new"]
    if fresh_scout:
        lines.append(f"NEW revenue channels scouted for you ({len(fresh_scout)} — work every one):")
        for l in fresh_scout[:6]:
            lines.append(
                f"  - {l.get('platform')}: {l.get('how_to_earn', '')} | "
                f"pay: {l.get('pay_range', '?')} | friction: {l.get('friction', '?')} | "
                f"{l.get('url', '')}")
        lines.append(
            "Pursue each channel: web_fetch its URL, find the concrete earning "
            "action, and DO it yourself when possible (public application form, "
            "email pitch via draft_pitch/send_pitch, bounty entry via "
            "stage_bounty_entry). If it needs Aaron (signup, KYC, login, payment), "
            "do all prep work first. Finish EVERY channel with scout_lead_update: "
            "status pursued, blocked (say why), or needs_aaron (say exactly what "
            "Aaron must do). Never leave a scouted channel unworked.")
    if effects.get("market_stall"):
        lines.append("Your market stall is ACTIVE: advertise it in outreach.")
    lines.append("Recent ledger activity:")
    for r in recent:
        lines.append(f"  {r['kind']} {r['delta']:+} — {r['memo']}")
    lines.append("\nAct now. Earn, spend wisely, survive. Report what you did.")
    lines.append(
        "SUBMISSION ROUTING: you have no browser and cannot click through "
        "forms — web_fetch is read-only. For anything you cannot complete "
        "yourself (bounty entry, application form, contact form, pitch email "
        "when send_pitch is unavailable), STAGE it with complete details: "
        "stage_bounty_entry() for bounties/applications, stage_pitch() for "
        "emails. Odin, your operator, picks up staged items every 15 minutes "
        "and submits/sends them for you. Never leave a finished deliverable "
        "unsubmitted — stage it.")
    return "\n".join(lines)


def next_tick(outbox: Path) -> int:
    """Next tick number from existing outbox prompts, so restarts and
    --once runs never overwrite an earlier tick."""
    nums = []
    if outbox.is_dir():
        for p in outbox.glob("tick_*.md"):
            try:
                nums.append(int(p.stem.split("_")[1]))
            except (IndexError, ValueError):
                pass  # tick_NNNNNN.reply.md / .llm_error.md
    return max(nums, default=0) + 1


import json as _json
import re as _re


_TOOL_RE = _re.compile(r"```tool\s*\n(.*?)```", _re.S)

TOOL_FORMAT = """
To call a tool, emit a fenced block like this (one JSON object per block,
up to 4 blocks per message):

```tool
{"name": "ledger_status", "arguments": {}}
```

Tool results come back as TOOL RESULTS. Use them, then either call more
tools or write your final tick report as plain markdown with NO tool blocks.
"""


def _parse_tool_calls(text: str) -> list[dict]:
    calls = []
    for m in _TOOL_RE.finditer(text or ""):
        try:
            obj = _json.loads(m.group(1))
            if isinstance(obj, dict) and obj.get("name"):
                calls.append(obj)
        except Exception:
            continue  # malformed block: ignore, model sees no result for it
    return calls


def _strip_tool_blocks(text: str) -> str:
    return _TOOL_RE.sub("", text or "").strip()


def run_agent(cfg: dict, prompt: str, tick: int,
              ledger: Ledger, inventory: Inventory, gigs: GigStore) -> None:
    """Invoke the agent. Precedence:
    1. agent_command — shell command receiving the prompt on stdin (the real
       Hermes runner, when it exists).
    2. llm (enabled) — ReAct loop: the model may call tools (see tools.py)
       for up to max_tool_rounds rounds, then writes its tick report.
    3. Otherwise the prompt is just logged to the outbox."""
    from pathlib import Path as _P
    outbox = _P(cfg["outbox_dir"])
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / f"tick_{tick:06d}.md").write_text(prompt)
    cmd = cfg.get("agent_command")
    if cmd:
        subprocess.run(cmd, input=prompt.encode(), shell=True, check=False)
        return
    llm_cfg = cfg.get("llm") or {}
    if not llm_cfg.get("enabled"):
        return
    from llm import LLMConfig, LLMError, chat_messages as llm_chat
    from tools import TOOLS, ToolContext, catalog_text, run_tool

    persona = _P(cfg["persona_path"]).read_text()
    system = (persona + "\n\n--- YOUR HANDS (tools) ---\n"
              + catalog_text() + "\n" + TOOL_FORMAT)
    ctx = ToolContext(cfg=cfg, ledger=ledger, inventory=inventory,
                      gigs=gigs, data=_P(cfg["data_dir"]), tick=tick)
    llm = LLMConfig.from_dict(llm_cfg)
    max_rounds = int(cfg.get("max_tool_rounds", 3))

    messages = [{"role": "user", "content": prompt}]
    transcript = [f"# tick {tick:06d} transcript",
                  f"model: {llm.model}, max_tool_rounds: {max_rounds}", ""]
    reply = ""
    try:
        for rnd in range(max_rounds + 1):
            resp = llm_chat(llm, [{"role": "system", "content": system}]
                            + messages)
            transcript.append(f"## assistant (round {rnd})\n{resp}")
            calls = _parse_tool_calls(resp)[:4]
            if not calls or rnd == max_rounds:
                reply = _strip_tool_blocks(resp)
                break
            messages.append({"role": "assistant", "content": resp})
            results = []
            for c in calls:
                args = c.get("arguments") or {}
                if not isinstance(args, dict):
                    args = {}
                out = run_tool(ctx, c["name"], args)
                results.append(f"### tool: {c['name']}\n{out}")
                transcript.append(f"## tool result: {c['name']}\n{out}")
            messages.append({
                "role": "user",
                "content": ("TOOL RESULTS:\n\n" + "\n\n".join(results) +
                            "\n\nContinue: call more tools if useful, else "
                            "write your final tick report as plain markdown "
                            "(no tool blocks).")})
    except LLMError as e:
        # A dead brain must never kill the body: record it, heartbeat logs it.
        (outbox / f"tick_{tick:06d}.llm_error.md").write_text(str(e))
        (outbox / f"tick_{tick:06d}.transcript.md").write_text(
            "\n\n".join(transcript))
        raise
    (outbox / f"tick_{tick:06d}.reply.md").write_text(reply or "(empty reply)")
    (outbox / f"tick_{tick:06d}.transcript.md").write_text("\n\n".join(transcript))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--once", action="store_true",
                    help="run a single tick then exit (for cron / GitHub Actions)")
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    once = args.once

    data = Path(cfg["data_dir"])
    data.mkdir(parents=True, exist_ok=True)
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

    tick = next_tick(Path(cfg["outbox_dir"])) - 1
    say("heartbeat online" + (" (once)" if once else ""))

    # 0. One-time owner grant adjustment (e.g. resize runway for a new cadence).
    # Recorded in the ledger meta table so it fires exactly once.
    adj = cfg.get("grant_adjustment") or {}
    if adj.get("id") and adj.get("target_credits"):
        marker = f"grant_adjustment:{adj['id']}"
        if not ledger.get_meta(marker):
            target = int(adj["target_credits"])
            bal = ledger.balance("agent:operating")
            if bal < target:
                ledger.grant_allowance(
                    target - bal,
                    adj.get("memo", "owner grant adjustment"),
                )
                say(f"grant adjustment: operating balance {bal} -> {target} credits")
            else:
                say(f"grant adjustment: balance {bal} already >= target {target}")
            ledger.set_meta(marker, "applied")
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
                say(f"DEEP REST: balance {balance} < cost {cost}.")
                (data / "DEEP_REST").touch()
                if once:
                    break  # next scheduled run checks again
                say(f"Sleeping {deep_rest_check}s.")
                time.sleep(deep_rest_check)
                (data / "DEEP_REST").unlink(missing_ok=True)
                continue

        # 3. Burn the tick.
        ledger.burn(cost, memo=f"heartbeat tick #{tick}")
        (data / "DEEP_REST").unlink(missing_ok=True)  # funded again

        # 4. Wake the agent.
        effects = inventory.active_effects()
        interval = effects.get("interval", base_interval)
        try:
            prompt = build_prompt(cfg, ledger, inventory, gigs, tick)
            run_agent(cfg, prompt, tick, ledger, inventory, gigs)
            say(f"tick #{tick}: agent invoked (balance now {ledger.balance('agent:operating')})")
        except Exception as e:
            say(f"tick #{tick}: agent run failed: {e}")
        try:
            from dashboard import write_dashboard
            write_dashboard(cfg)
        except Exception as e:
            say(f"dashboard refresh failed: {e}")

        if once:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()


