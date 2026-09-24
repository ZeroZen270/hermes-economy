"""
Hermes review dashboard: a single static HTML file regenerated every tick.

`data/dashboard.html` is committed back to the repo by the heartbeat's
persist step, so Aaron can review stats and every tick from GitHub with
zero infrastructure. The Flask app (webui.py) reuses gather_stats().
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

_TICK_RE = re.compile(r"^tick_(\d+)(?:\.(reply|llm_error|transcript))?$")


def _ticks(outbox: Path, limit: int = 12) -> list[dict]:
    nums: dict[int, dict] = {}
    if outbox.is_dir():
        for p in outbox.glob("tick_*.md"):
            m = _TICK_RE.match(p.stem)
            if not m:
                continue
            n = int(m.group(1))
            kind = m.group(2)  # None | reply | llm_error | transcript
            e = nums.setdefault(n, {"n": n, "reply": False, "error": False,
                                    "mtime": 0.0})
            if kind == "reply":
                e["reply"] = True
            elif kind == "llm_error":
                e["error"] = True
            elif kind is None:
                e["mtime"] = p.stat().st_mtime
    return sorted(nums.values(), key=lambda e: e["n"], reverse=True)[:limit]

import sys
sys.path.insert(0, str(Path(__file__).parent))

from ledger import Ledger
from marketplace import Inventory
from stripe_rails import GigStore


def _read_json(path: Path, key: str) -> list:
    try:
        return json.loads(path.read_text()).get(key, [])
    except Exception:
        return []



def _reply_preview(outbox: Path, n: int) -> str:
    p = outbox / f"tick_{n:06d}.reply.md"
    if not p.exists():
        return ""
    return p.read_text()[:300].replace("\n", " ")


def gather_stats(cfg: dict) -> dict:
    data = Path(cfg["data_dir"])
    outbox = Path(cfg["outbox_dir"])
    ledger = Ledger(data / "economy.db")
    inventory = Inventory(ledger, data / "inventory.json")
    gigs = GigStore(data / "economy.db")
    s = ledger.summary()
    cost = cfg.get("heartbeat_cost_credits", 50)
    ticks = _ticks(outbox)
    for t in ticks:
        t["preview"] = _reply_preview(outbox, t["n"])
        t["when"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(t["mtime"]))

    req_dir = data / "requests"
    requests_ = []
    if req_dir.is_dir():
        for p in sorted(req_dir.glob("*.json"), reverse=True)[:20]:
            try:
                r = json.loads(p.read_text())
                r["_file"] = p.name
                requests_.append(r)
            except Exception:
                continue

    drafts_dir = outbox / "drafts"
    drafts = []
    if drafts_dir.is_dir():
        for p in sorted(drafts_dir.glob("*.md"), reverse=True)[:20]:
            drafts.append({"file": p.name,
                           "mtime": time.strftime("%Y-%m-%d %H:%M",
                                                  time.localtime(p.stat().st_mtime))})

    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "deep_rest": (data / "DEEP_REST").exists(),
        "agent_credits": s["agent_credits"],
        "agent_usd": s["agent_usd"],
        "treasury_credits": s["treasury_credits"],
        "treasury_usd": s["treasury_usd"],
        "real_earned_credits": s["real_earned_credits"],
        "real_earned_usd": s["real_earned_usd"],
        "runway": s["agent_credits"] // cost if cost else 0,
        "cost": cost,
        "latest_tick": ticks[0]["n"] if ticks else 0,
        "ticks": ticks,
        "ledger_recent": ledger.history(None, limit=15),
        "open_gigs": gigs.open_gigs(),
        "opps": _read_json(data / "opportunities.json", "opportunities")[:10],
        "leads": _read_json(data / "leads.json", "leads")[:10],
        "drafts": drafts,
        "requests": requests_,
        "effects": inventory.active_effects(),
        "memory": (data / "agent_memory.md").read_text()[-2000:]
                   if (data / "agent_memory.md").exists() else "",
    }


CSS = """
:root{color-scheme:dark}
body{background:#0d1117;color:#e6edf3;font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:24px;max-width:1100px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px;color:#9fb3c8;text-transform:uppercase;letter-spacing:.06em}
.sub{color:#8b949e;font-size:13px;margin-bottom:18px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px}
.card .v{font-size:24px;font-weight:700}.card .l{font-size:12px;color:#8b949e;margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #21262d;vertical-align:top}
th{color:#8b949e;font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.05em}
a{color:#58a6ff;text-decoration:none}a:hover{text-decoration:underline}
.badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:12px;font-weight:600}
.ok{background:#1a3a24;color:#7ee2a0}.warn{background:#3a2a1a;color:#f0b35c}.err{background:#3a1a1a;color:#f08a8a}
.mut{color:#8b949e}.pend{background:#2a2a3a;color:#c9b8ff}
pre{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;overflow:auto;font-size:13px;white-space:pre-wrap}
"""


def _badge(text: str, cls: str) -> str:
    return f'<span class="badge {cls}">{text}</span>'


def render_dashboard(s: dict) -> str:
    status = _badge("DEEP REST", "err") if s["deep_rest"] else _badge("ONLINE", "ok")
    cards = f"""
    <div class="cards">
      <div class="card"><div class="v">{s['agent_credits']:,}</div><div class="l">Operating credits (${s['agent_usd']:.2f})</div></div>
      <div class="card"><div class="v">~{s['runway']}</div><div class="l">Ticks of runway ({s['cost']}/tick)</div></div>
      <div class="card"><div class="v">${s['treasury_usd']:.2f}</div><div class="l">Treasury — Aaron's ({s['treasury_credits']:,} cr)</div></div>
      <div class="card"><div class="v">${s['real_earned_usd']:.2f}</div><div class="l">Real money earned (goal: $100)</div></div>
      <div class="card"><div class="v">{s['latest_tick']}</div><div class="l">Latest tick</div></div>
      <div class="card"><div class="v">{len(s['open_gigs'])}</div><div class="l">Open gigs</div></div>
      <div class="card"><div class="v">{len(s['drafts'])} / {len([r for r in s['requests'] if r.get('status')=='pending_review'])}</div><div class="l">Drafts / pending requests</div></div>
    </div>"""

    tick_rows = []
    for t in s["ticks"]:
        n = t["n"]
        fn = f"tick_{n:06d}"
        if t["error"]:
            res = _badge("BRAIN ERROR", "err") + f' <a href="outbox/{fn}.llm_error.md">error</a>'
        elif t["reply"]:
            res = _badge("REPLIED", "ok")
        else:
            res = _badge("NO REPLY", "warn")
        links = f'<a href="outbox/{fn}.md">prompt</a>'
        if t["reply"]:
            links += f' · <a href="outbox/{fn}.reply.md">reply</a> · <a href="outbox/{fn}.transcript.md">transcript</a>'
        tick_rows.append(
            f"<tr><td>#{n}</td><td class='mut'>{t['when']}</td><td>{res}</td>"
            f"<td>{links}</td><td class='mut'>{t['preview'][:120]}</td></tr>")
    ticks_tbl = ("<table><tr><th>Tick</th><th>Time</th><th>Result</th><th>Files</th>"
                 "<th>Preview</th></tr>" + "".join(tick_rows) + "</table>"
                 if tick_rows else "<p class='mut'>No ticks yet.</p>")

    led_rows = "".join(
        f"<tr><td class='mut'>{time.strftime('%m-%d %H:%M', time.localtime(r.get('ts', 0)))}</td>"
        f"<td>{r.get('account','')}</td>"
        f"<td>{r.get('kind','')}</td><td>{r.get('delta','')}</td>"
        f"<td class='mut'>{r.get('memo','')}</td></tr>"
        for r in s["ledger_recent"])
    ledger_tbl = ("<table><tr><th>Time</th><th>Account</th><th>Kind</th><th>Δ</th>"
                  "<th>Memo</th></tr>" + led_rows + "</table>"
                  if led_rows else "<p class='mut'>No ledger activity.</p>")

    opp_rows = "".join(
        f"<tr><td>{o.get('board','')}</td><td>{o.get('title','')}</td>"
        f"<td>${o.get('reward_usd',0):.2f}</td>"
        f"<td><a href='{o.get('url','')}'>link</a></td></tr>" for o in s["opps"])
    lead_rows = "".join(
        f"<tr><td>{ld.get('domain','')}</td><td>{ld.get('score','')}</td>"
        f"<td class='mut'>{'; '.join(ld.get('issues',[]))}</td></tr>" for ld in s["leads"])

    draft_rows = "".join(
        f"<tr><td><a href='outbox/drafts/{d['file']}'>{d['file']}</a></td>"
        f"<td class='mut'>{d['mtime']}</td><td>{_badge('DRAFT — NOT SENT','pend')}</td></tr>"
        for d in s["drafts"])
    req_rows = "".join(
        f"<tr><td>{r.get('type','')}</td><td class='mut'>{r.get('created','')}</td>"
        f"<td>{r.get('amount_credits', r.get('price_credits',''))}</td>"
        f"<td class='mut'>{r.get('reason', r.get('sku',''))}</td>"
        f"<td>{_badge(r.get('status','').upper(), 'pend' if r.get('status')=='pending_review' else 'ok')}</td></tr>"
        for r in s["requests"])
    review_tbl = ("<table><tr><th>Item</th><th>Created</th><th>Amount/SKU</th>"
                  "<th>Detail</th><th>Status</th></tr>" + draft_rows + req_rows + "</table>"
                  if (draft_rows or req_rows)
                  else "<p class='mut'>Nothing awaiting review.</p>")

    gig_rows = "".join(
        f"<tr><td>{g['gig_id']}</td><td>${g['amount_usd']:.2f}</td>"
        f"<td class='mut'>{g['description']}</td><td>{g['status']}</td></tr>"
        for g in s["open_gigs"])

    memory = f"<h2>Agent memory</h2><pre>{s['memory']}</pre>" if s["memory"] else ""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hermes — survival dashboard</title><style>{CSS}</style></head><body>
<h1>Hermes — survival dashboard</h1>
<div class="sub">Generated {s['generated']} · {status}</div>
{cards}
<h2>Recent ticks</h2>{ticks_tbl}
<h2>Awaiting your review</h2>{review_tbl}
<h2>Ledger — recent activity</h2>{ledger_tbl}
<h2>Opportunities</h2>
{"<table><tr><th>Board</th><th>Title</th><th>Reward</th><th></th></tr>"+opp_rows+"</table>" if opp_rows else "<p class='mut'>No bounties on file.</p>"}
<h2>Leads</h2>
{"<table><tr><th>Domain</th><th>Score</th><th>Issues</th></tr>"+lead_rows+"</table>" if lead_rows else "<p class='mut'>No leads on file.</p>"}
<h2>Open gigs</h2>
{"<table><tr><th>ID</th><th>Amount</th><th>Description</th><th>Status</th></tr>"+gig_rows+"</table>" if gig_rows else "<p class='mut'>No open gigs.</p>"}
{memory}
<div class="sub" style="margin-top:32px">Hermes economy · full-survival persona · "
<a href="https://github.com/ZeroZen270/hermes-economy">repo</a></div>
</body></html>"""


def write_dashboard(cfg: dict) -> Path:
    data = Path(cfg["data_dir"])
    out = data / "dashboard.html"
    out.write_text(render_dashboard(gather_stats(cfg)))
    return out


if __name__ == "__main__":
    import argparse
    import yaml
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()
    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    p = write_dashboard(cfg)
    print(f"dashboard -> {p}")
