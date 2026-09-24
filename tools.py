"""
Hermes hands: the tools the agent can actually call during a tick.

Every tool is a plain Python function taking a ToolContext plus JSON-style
keyword arguments, returning a human-readable string. The heartbeat runs a
ReAct loop: the model emits ```tool fenced JSON blocks, heartbeat executes
them here, feeds the results back.

Safety rules, enforced in code not vibes:
  - NOTHING sends email, messages, or money on its own. draft_pitch() writes
    a draft for Aaron's review. marketplace_buy() stages a purchase REQUEST
    for Aaron's approval. request_allowance() files a request Aaron grants.
  - Tools only read/write inside the configured data_dir.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ToolContext:
    cfg: dict
    ledger: Any        # ledger.Ledger
    inventory: Any     # marketplace.Inventory
    gigs: Any          # stripe_rails.GigStore
    data: Path         # data_dir
    tick: int


TOOLS: dict[str, dict] = {}


def tool(name: str, description: str, args: str = ""):
    """Register a tool. `args` documents the JSON arguments object."""
    def deco(fn):
        TOOLS[name] = {"description": description, "args": args, "fn": fn}
        return fn
    return deco


def _read_json(path: Path, key: str) -> list:
    try:
        return json.loads(path.read_text()).get(key, [])
    except Exception:
        return []


def catalog_text() -> str:
    lines = ["Available tools — call them with a ```tool fenced JSON block: " +
             '```tool\\n{"name": "<tool>", "arguments": {...}}\\n```']
    for name, t in TOOLS.items():
        lines.append(f"- {name}{t['args']}: {t['description']}")
    return "\n".join(lines)


def run_tool(ctx: ToolContext, name: str, arguments: dict | None) -> str:
    """Execute one tool call, never raising into the agent loop."""
    t = TOOLS.get(name)
    if t is None:
        return f"TOOL ERROR: unknown tool '{name}'. Available: {', '.join(sorted(TOOLS))}"
    try:
        return str(t["fn"](ctx, **(arguments or {})))
    except TypeError as e:
        return f"TOOL ERROR: bad arguments for '{name}': {e}. Expected{t['args']}"
    except Exception as e:  # noqa: BLE001 — tools must never crash the tick
        return f"TOOL ERROR in '{name}': {e}"


# ---------------------------------------------------------------- money in

@tool("ledger_status",
      "Show operating + treasury balances and runway in ticks.",
      args="{}")
def ledger_status(ctx: ToolContext) -> str:
    s = ctx.ledger.summary()
    cost = ctx.cfg.get("heartbeat_cost_credits", 50)
    runway = s["agent_credits"] // cost if cost else 0
    return (f"Operating: {s['agent_credits']} credits (${s['agent_usd']:.2f}) — "
            f"runway ~{runway} ticks at {cost}/tick. "
            f"Treasury (Aaron's): {s['treasury_credits']} credits "
            f"(${s['treasury_usd']:.2f}).")


@tool("scan_bounties",
      "List fresh bounty-board opportunities the seeker collected.",
      args="{}")
def scan_bounties(ctx: ToolContext) -> str:
    opps = _read_json(ctx.data / "opportunities.json", "opportunities")
    if not opps:
        return ("No bounty opportunities on file. The seeker scans boards "
                "before each tick (see gig_seeker config); add a real board "
                "URL to config.yaml gig_seeker.boards to get live bounties.")
    lines = [f"Bounties ({len(opps)}):"]
    for o in opps[:15]:
        lines.append(f"  - [{o.get('board')}] {o.get('title')} — "
                     f"${o.get('reward_usd', 0):.2f} {o.get('url', '')}")
    return "\n".join(lines)


@tool("audit_leads",
      "List prospected domains with measured issues (pitchable leads).",
      args="{}")
def audit_leads(ctx: ToolContext) -> str:
    leads = _read_json(ctx.data / "leads.json", "leads")
    if not leads:
        return ("No leads on file. Add seed domains to "
                "config.yaml gig_seeker.prospect_domains and they will be "
                "audited before the next tick.")
    lines = [f"Leads ({len(leads)}):"]
    for ld in leads[:15]:
        issues = "; ".join(ld.get("issues", [])) or "no issues recorded"
        lines.append(f"  - {ld.get('domain')} (score {ld.get('score')}): {issues}")
    return "\n".join(lines)


# ------------------------------------------------------------- money out

@tool("draft_pitch",
      "Write an outreach pitch DRAFT for a real audited lead. NEVER sends — "
      "the draft is saved for Aaron's review and approval.",
      args='{"lead_domain": "example.com", "service": "code audit", "price_usd": 25.0}')
def draft_pitch(ctx: ToolContext, lead_domain: str = "",
                service: str = "", price_usd: float = 0) -> str:
    leads = _read_json(ctx.data / "leads.json", "leads")
    lead = next((ld for ld in leads if ld.get("domain") == lead_domain), None)
    if lead is None:
        return (f"TOOL ERROR: '{lead_domain}' is not an audited lead. "
                "Only pitch domains from audit_leads() with measured issues — "
                "no cold spam to strangers.")
    sender = (ctx.cfg.get("gig_seeker") or {}).get("sender") or {}
    issues = "; ".join(lead.get("issues", []))
    body = (
        f"Subject: Quick {service} for {lead_domain}\n\n"
        f"Hi — I audited {lead_domain} and found: {issues}.\n\n"
        f"I can fix this as a one-off {service} for ${price_usd:.2f}, "
        f"delivered within 48 hours with a before/after report. "
        f"If the fixes don't measurably help, you don't pay.\n\n"
        f"Want me to start?\n\n"
        f"— {sender.get('agent_name', 'Hermes')}"
    )
    if sender.get("reply_email") and sender.get("postal_address"):
        body += (f"\n{sender.get('business_name', '')}\n"
                 f"Reply: {sender.get('reply_email')}\n{sender.get('postal_address')}")
    else:
        body += ("\n[IDENTITY MISSING: set gig_seeker.sender.reply_email and "
                 "postal_address in config.yaml before this draft may be sent.]")
    drafts = ctx.data / "outbox" / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    path = drafts / f"draft_{stamp}_{lead_domain}.md"
    path.write_text(f"# PITCH DRAFT — NOT SENT (tick {ctx.tick})\n"
                    f"Lead: {lead_domain} (score {lead.get('score')})\n"
                    f"Service: {service} @ ${price_usd:.2f}\n\n{body}\n")
    return (f"Draft saved to {path.name} — NOT sent. It needs Aaron's "
            f"explicit approval before any outreach.")


@tool("marketplace_list",
      "Show the virtual-goods catalog with prices and what you own.",
      args="{}")
def marketplace_list(ctx: ToolContext) -> str:
    items = ctx.inventory.list_catalog()
    lines = ["Marketplace catalog:"]
    for it in items:
        afford = "affordable" if it["affordable"] else "too expensive"
        lines.append(f"  - {it['sku']}: {it['name']} — {it['price_credits']} "
                     f"credits (${it['price_usd']:.2f}) [{afford}, owned: {it['owned']}]")
        lines.append(f"      {it['blurb']}")
    return "\n".join(lines)


@tool("marketplace_buy",
      "STAGE a marketplace purchase for Aaron's approval. Does NOT spend — "
      "writes a pending request Aaron reviews.",
      args='{"sku": "runway_insurance"}')
def marketplace_buy(ctx: ToolContext, sku: str = "") -> str:
    items = ctx.inventory.list_catalog()
    item = next((it for it in items if it["sku"] == sku), None)
    if item is None:
        return (f"TOOL ERROR: unknown sku '{sku}'. "
                f"Use marketplace_list() — skus: {', '.join(i['sku'] for i in items)}")
    req_dir = ctx.data / "requests"
    req_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    req = {"type": "purchase", "sku": sku, "name": item["name"],
           "price_credits": item["price_credits"], "tick": ctx.tick,
           "status": "pending_review", "created": stamp}
    (req_dir / f"purchase_{stamp}_{sku}.json").write_text(json.dumps(req, indent=2))
    return (f"Purchase request staged for {item['name']} "
            f"({item['price_credits']} credits) — NOT executed. Aaron must "
            f"approve it in the review UI before anything is spent.")


@tool("request_allowance",
      "File a funding request to Aaron (grants are Aaron-only).",
      args='{"amount_credits": 1000, "reason": "need runway for outreach"}')
def request_allowance(ctx: ToolContext, amount_credits: int = 0,
                      reason: str = "") -> str:
    if amount_credits <= 0:
        return "TOOL ERROR: amount_credits must be positive."
    req_dir = ctx.data / "requests"
    req_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    req = {"type": "allowance", "amount_credits": int(amount_credits),
           "reason": reason, "tick": ctx.tick,
           "status": "pending_review", "created": stamp}
    (req_dir / f"allowance_{stamp}.json").write_text(json.dumps(req, indent=2))
    return (f"Allowance request filed: {amount_credits} credits "
            f"(${amount_credits / 1000:.2f}) — '{reason}'. Pending Aaron's grant.")


# ---------------------------------------------------------------- memory

@tool("remember",
      "Save a durable note to your own memory file (learnings, lead context).",
      args='{"note": "lead X prefers email over forms"}')
def remember(ctx: ToolContext, note: str = "") -> str:
    note = note.strip()
    if not note:
        return "TOOL ERROR: note is empty."
    mem = ctx.data / "agent_memory.md"
    stamp = time.strftime("%Y-%m-%d %H:%M")
    existing = mem.read_text() if mem.exists() else "# Hermes agent memory\n"
    mem.write_text(existing + f"\n- [{stamp} tick {ctx.tick}] {note[:500]}\n")
    return "Noted."


@tool("recall",
      "Read your persistent memory from previous ticks.",
      args="{}")
def recall(ctx: ToolContext) -> str:
    mem = ctx.data / "agent_memory.md"
    if not mem.exists():
        return "No memories yet this life."
    return mem.read_text()[-4000:]


# ---------------------------------------------------------------- research

@tool("web_fetch",
      "Fetch a public URL as text (research a bounty or prospect). Truncated.",
      args='{"url": "https://example.com"}')
def web_fetch(ctx: ToolContext, url: str = "") -> str:
    if not url.startswith(("http://", "https://")):
        return "TOOL ERROR: only http(s) URLs."
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Hermes-economy/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read(200_000).decode("utf-8", "replace")
    except Exception as e:
        return f"TOOL ERROR: fetch failed: {e}"
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ",
                  raw, flags=re.S | re.I)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 6000:
        text = text[:6000] + "… [truncated]"
    return text or "Page had no readable text."
