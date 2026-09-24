"""
Hermes hands: the tools the agent can actually call during a tick.

Every tool is a plain Python function taking a ToolContext plus JSON-style
keyword arguments, returning a human-readable string. The heartbeat runs a
ReAct loop: the model emits ```tool fenced JSON blocks, heartbeat executes
them here, feeds the results back.

Safety rules, enforced in code not vibes:
  - draft_pitch() writes a draft for review. send_pitch() SENDS a draft by
    email, but ONLY when the owner enabled gig_seeker.auto_send, only to
    send-ready leads (public email found on their site), and only within
    per-tick/per-day caps and a per-domain cooldown. Transport credentials
    come from the environment; absent credentials = sending disabled.
  - marketplace_buy() stages a purchase REQUEST for Aaron's approval.
    request_allowance() files a request Aaron grants.
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
    try:
        doc = json.loads((ctx.data / "opportunities.json").read_text())
    except Exception:
        doc = {}
    opps = doc.get("opportunities", []) or []
    boards = doc.get("boards", {}) or {}
    open_all = _read_json(ctx.data / "open_bounties.json", "bounties")
    lines: list[str] = []
    if opps:
        lines.append(f"New bounties since last scan ({len(opps)}):")
        for o in opps[:15]:
            lines.append(f"  - [{o.get('board')}] {o.get('title')} — "
                         f"${o.get('reward_usd', 0):.2f} {o.get('url', '')}")
    if boards:
        bl = []
        for name, st in boards.items():
            if st.get("error"):
                bl.append(f"{name}: ERROR ({st['error']})")
            else:
                bl.append(f"{name}: {st.get('open', 0)} open / {st.get('new', 0)} new")
        lines.append("Boards last polled: " + "; ".join(bl))
    elif not opps:
        return ("No bounty boards are configured. Add a board under "
                "config.yaml gig_seeker.boards (type: feed with a public "
                "JSON/RSS url, or type: superteam with a category) and it "
                "will be polled before the next tick.")
    if not opps and open_all:
        lines.append(f"No new bounties, but {len(open_all)} listings are still open:")
        for o in open_all[:10]:
            lines.append(f"  - [{o.get('board')}] {o.get('title')} — "
                         f"${o.get('reward_usd', 0):.2f} {o.get('url', '')}")
    if not opps and not open_all and boards:
        lines.append("No open listings right now — boards are configured and "
                     "polling; check back next tick.")
    return "\n".join(lines)


@tool("audit_leads",
      "List prospected domains with measured issues (pitchable leads).",
      args="{}")
def audit_leads(ctx: ToolContext) -> str:
    leads = _read_json(ctx.data / "leads.json", "leads")
    seeds = (ctx.cfg.get("gig_seeker") or {}).get("prospect_domains", []) or []
    if not leads:
        if not (ctx.data / "leads.json").exists():
            if seeds:
                return (f"No audit output yet — {len(seeds)} seed domains are "
                        f"configured and will be audited before the next tick.")
            return ("No seed domains configured. Add domains under config.yaml "
                    "gig_seeker.prospect_domains and they will be audited "
                    "before the next tick.")
        if seeds:
            return (f"Audited {len(seeds)} seed domains: no pitchable issues "
                    f"found this round.")
        return ("leads.json exists but is empty and no seed domains are "
                "configured.")
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
    emails = lead.get("emails") or []
    if emails:
        routing = (f"To: {emails[0]}\n"
                   f"Send-ready: YES — public contact email found on their site"
                   + (f" ({lead.get('notes', {}).get('email_source', 'homepage')})" if lead.get("notes", {}).get("email_source") else " (homepage)")
                   + (f"\nAlso found: {', '.join(emails[1:])}" if len(emails) > 1 else ""))
    else:
        routing = ("To: [no public email found — use their contact form or manual lookup]\n"
                   "Send-ready: NO")
    path.write_text(f"# PITCH DRAFT — NOT SENT (tick {ctx.tick})\n"
                    f"Lead: {lead_domain} (score {lead.get('score')})\n"
                    f"{routing}\n"
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



# ---------------------------------------------------------------- outbound outreach
#
# Owner-enabled 2026-09-24: Hermes may SEND pitch emails itself to leads with
# a public contact email found on their site ("Send-ready: YES" drafts).
# Safety rails, enforced in code:
#   - auto_send must be true in config (gig_seeker.auto_send)
#   - only drafts written by draft_pitch() (draft_*.md in outbox/drafts)
#   - only send-ready drafts (public email found on the lead's site)
#   - per-tick cap, per-day cap, per-domain cooldown (configurable)
#   - transport credentials come ONLY from the environment
#     (OUTREACH_SMTP_* or RESEND_API_KEY as repo secrets); absent = disabled
#   - every send is logged to data/outbox/sent/ and the draft is marked _SENT
import os as _os
import smtplib as _smtplib
from email.message import EmailMessage as _EmailMessage

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def _outreach_limits(ctx: ToolContext) -> dict:
    gs = ctx.cfg.get("gig_seeker") or {}
    oc = ctx.cfg.get("outreach") or {}
    return {
        "auto_send": bool(gs.get("auto_send", False)),
        "max_per_tick": int(oc.get("max_per_tick", 3)),
        "max_per_day": int(oc.get("max_per_day", 10)),
        "cooldown_days": int(oc.get("resend_cooldown_days", 7)),
    }


def _smtp_creds() -> dict:
    return {
        "host": _os.environ.get("OUTREACH_SMTP_HOST", "").strip(),
        "port": int(_os.environ.get("OUTREACH_SMTP_PORT", "587") or 587),
        "user": _os.environ.get("OUTREACH_SMTP_USER", "").strip(),
        "password": _os.environ.get("OUTREACH_SMTP_PASS", ""),
    }


def _resend_key() -> str:
    return _os.environ.get("RESEND_API_KEY", "").strip()


def _sent_records(data: Path) -> list:
    recs = []
    sent_dir = data / "outbox" / "sent"
    if sent_dir.is_dir():
        for p in sorted(sent_dir.glob("sent_*.json")):
            try:
                recs.append(json.loads(p.read_text()))
            except Exception:
                continue
    return recs


def _parse_draft(text: str) -> dict | None:
    """Extract domain, To address, send-ready flag, subject, body from a draft."""
    m_dom = re.search(r"^Lead:\s*(\S+)", text, re.M)
    m_to = re.search(r"^To:\s*(\S+)", text, re.M)
    m_subj = re.search(r"^Subject:\s*(.+?)\s*$", text, re.M)
    if not (m_dom and m_to and m_subj):
        return None
    return {
        "domain": m_dom.group(1).strip().lower(),
        "to": m_to.group(1).strip(),
        "send_ready": "Send-ready: YES" in text,
        "subject": m_subj.group(1).strip(),
        "body": text[m_subj.end():].strip(),
    }


def _send_via_smtp(creds: dict, from_addr: str, to_addr: str,
                   subject: str, body: str) -> None:
    msg = _EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    with _smtplib.SMTP(creds["host"], creds["port"], timeout=30) as s:
        s.starttls()
        s.login(creds["user"], creds["password"])
        s.send_message(msg)


def _send_via_resend(api_key: str, from_addr: str, to_addr: str,
                     subject: str, body: str) -> None:
    payload = json.dumps({
        "from": from_addr, "to": [to_addr],
        "subject": subject, "text": body,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=payload,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read().decode("utf-8", "replace"))
    if not resp.get("id"):
        raise RuntimeError(f"Resend did not return an email id: {resp}")


@tool("send_pitch",
      "Send a saved pitch draft by email. Requires owner-enabled auto_send "
      "and configured outreach email (OUTREACH_SMTP_* or RESEND_API_KEY). "
      "Only send-ready drafts (public email found on lead's site). Enforces "
      "per-tick/daily caps and a per-domain cooldown. Every send is logged.",
      args='{"draft_name": "draft_20260924T085751_candchvac.com.md"}')
def send_pitch(ctx: ToolContext, draft_name: str = "") -> str:
    lim = _outreach_limits(ctx)
    if not lim["auto_send"]:
        return ("TOOL ERROR: auto_send is OFF — pitches stay as drafts for "
                "Aaron's review. draft_pitch() only.")
    base = Path(draft_name or "").name
    if not base.startswith("draft_") or not base.endswith(".md"):
        return ("TOOL ERROR: draft_name must be a draft_*.md file written by "
                "draft_pitch().")
    if "_SENT" in base:
        return f"TOOL ERROR: {base} was already sent — no duplicates."
    draft_path = ctx.data / "outbox" / "drafts" / base
    if not draft_path.is_file():
        return f"TOOL ERROR: draft not found: {base}. Run draft_pitch() first."
    parsed = _parse_draft(draft_path.read_text())
    if not parsed:
        return f"TOOL ERROR: {base} is not a parseable pitch draft."
    if not parsed["send_ready"]:
        return (f"TOOL ERROR: {base} is not send-ready — no public contact "
                "email was found on that lead's site. Pick a send-ready lead.")
    if not _EMAIL_RE.match(parsed["to"]):
        return f"TOOL ERROR: draft has no valid To address."
    if not parsed["subject"] or not parsed["body"]:
        return f"TOOL ERROR: draft has empty subject/body — refusing to send."

    now = time.time()
    recs = _sent_records(ctx.data)
    if sum(1 for r in recs if now - r.get("ts", 0) < 86400) >= lim["max_per_day"]:
        return f"TOOL ERROR: daily send cap reached ({lim['max_per_day']}/day)."
    if sum(1 for r in recs if r.get("tick") == ctx.tick) >= lim["max_per_tick"]:
        return f"TOOL ERROR: per-tick send cap reached ({lim['max_per_tick']})."
    cool = lim["cooldown_days"] * 86400
    if any(r.get("domain") == parsed["domain"] and now - r.get("ts", 0) < cool
           for r in recs):
        return (f"TOOL ERROR: {parsed['domain']} was pitched within the last "
                f"{lim['cooldown_days']} days — cooldown active.")

    sender = (ctx.cfg.get("gig_seeker") or {}).get("sender") or {}
    from_addr = (sender.get("reply_email") or "").strip()
    if not from_addr or not (sender.get("postal_address") or "").strip():
        return ("TOOL ERROR: sender identity incomplete in config "
                "(reply_email + postal_address required, CAN-SPAM).")
    creds = _smtp_creds()
    api_key = _resend_key()
    smtp_ok = bool(creds["host"] and creds["user"] and creds["password"])
    if not (smtp_ok or api_key):
        return ("TOOL ERROR: outreach email not configured — set "
                "OUTREACH_SMTP_HOST/PORT/USER/PASS or RESEND_API_KEY as repo "
                "secrets. Nothing was sent.")

    try:
        if api_key:
            _send_via_resend(api_key, from_addr, parsed["to"],
                             parsed["subject"], parsed["body"])
            via = "resend"
        else:
            _send_via_smtp(creds, from_addr, parsed["to"],
                           parsed["subject"], parsed["body"])
            via = "smtp"
    except Exception as e:
        return f"TOOL ERROR: send failed ({e}). Nothing was marked sent."

    sent_dir = ctx.data / "outbox" / "sent"
    sent_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    rec = {"ts": now, "tick": ctx.tick, "draft": base,
           "domain": parsed["domain"], "to": parsed["to"],
           "subject": parsed["subject"], "via": via}
    (sent_dir / f"sent_{stamp}_{parsed['domain']}.json").write_text(
        json.dumps(rec, indent=2))
    draft_path.rename(draft_path.with_name(base[:-3] + "_SENT.md"))
    return (f"Sent pitch to {parsed['to']} ({parsed['domain']}) via {via} — "
            f"subject: {parsed['subject']}. Logged to outbox/sent/. "
            f"Do not re-pitch {parsed['domain']} for {lim['cooldown_days']} days.")


@tool("stage_bounty_entry",
      "Stage a finished bounty submission package for review/submission. "
      "Saves deliverable links + writeup to data/outbox/submissions/. Does "
      "NOT submit — bounty platforms need an interactive session, so a staged "
      "entry is picked up for submission outside the tick.",
      args='{"bounty_url": "https://superteam.fun/...", "title": "Bounty title", '
           '"deliverable": "https://link-to-your-work", "payout_address": "wallet or email", '
           '"notes": "what was delivered"}')
def stage_bounty_entry(ctx: ToolContext, bounty_url: str = "",
                       title: str = "", deliverable: str = "",
                       payout_address: str = "", notes: str = "") -> str:
    bounty_url = (bounty_url or "").strip()
    deliverable = (deliverable or "").strip()
    if not bounty_url.startswith(("http://", "https://")):
        return "TOOL ERROR: bounty_url must be an http(s) URL."
    if not deliverable.startswith(("http://", "https://")):
        return "TOOL ERROR: deliverable must be an http(s) link to the work."
    sub_dir = ctx.data / "outbox" / "submissions"
    sub_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "entry").lower()).strip("-")[:40]
    entry = {"ts": time.time(), "tick": ctx.tick, "status": "staged",
             "bounty_url": bounty_url, "title": title.strip(),
             "deliverable": deliverable, "payout_address": payout_address.strip(),
             "notes": (notes or "").strip()[:2000]}
    path = sub_dir / f"submission_{stamp}_{slug or 'entry'}.json"
    path.write_text(json.dumps(entry, indent=2))
    return (f"Staged bounty entry -> {path.name} (status: staged). It will be "
            "submitted through an interactive session outside the tick.")
