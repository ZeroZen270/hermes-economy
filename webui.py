"""
Hermes review web UI: interactive companion to the static dashboard.

Read-only. Run locally or in Codespaces, then open the forwarded URL:

    python3 webui.py --config config.yaml [--port 5000]

Pages: / (overview), /tick/<n> (prompt/reply/transcript), /ledger,
/drafts, /requests.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))

import yaml
from flask import Flask, abort, render_template_string

from dashboard import CSS, gather_stats
from ledger import Ledger

app = Flask(__name__)
CFG: dict = {}


def page(title: str, body: str) -> str:
    nav = ('<div class="sub"><a href="/">overview</a> · '
           '<a href="/ledger">ledger</a> · <a href="/drafts">drafts</a> · '
           '<a href="/requests">requests</a></div>')
    return render_template_string(
        f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Hermes — {title}</title><style>{CSS}</style></head>"
        f"<body><h1>Hermes — {title}</h1>{nav}{body}</body></html>")


def _badge(text: str, cls: str) -> str:
    return f'<span class="badge {cls}">{text}</span>'


@app.route("/")
def index():
    s = gather_stats(CFG)
    status = _badge("DEEP REST", "err") if s["deep_rest"] else _badge("ONLINE", "ok")
    cards = f"""
    <div class="cards">
      <div class="card"><div class="v">{s['agent_credits']:,}</div><div class="l">Operating credits (${s['agent_usd']:.2f})</div></div>
      <div class="card"><div class="v">~{s['runway']}</div><div class="l">Ticks of runway</div></div>
      <div class="card"><div class="v">${s['treasury_usd']:.2f}</div><div class="l">Treasury — Aaron's</div></div>
      <div class="card"><div class="v">{s['latest_tick']}</div><div class="l">Latest tick</div></div>
    </div>
    <div class="sub" style="margin-top:12px">Updated {s['generated']} · {status}</div>"""
    rows = []
    for t in s["ticks"]:
        n = t["n"]
        res = _badge("BRAIN ERROR", "err") if t["error"] else (
            _badge("REPLIED", "ok") if t["reply"] else _badge("NO REPLY", "warn"))
        rows.append(f"<tr><td><a href='/tick/{n}'>#{n}</a></td>"
                    f"<td class='mut'>{t['when']}</td><td>{res}</td>"
                    f"<td class='mut'>{t['preview'][:100]}</td></tr>")
    pend = [r for r in s["requests"] if r.get("status") == "pending_review"]
    review = ""
    if s["drafts"] or pend:
        items = "".join(
            f"<tr><td>draft</td><td><a href='/drafts'>{d['file']}</a></td>"
            f"<td>{_badge('NOT SENT', 'pend')}</td></tr>" for d in s["drafts"])
        items += "".join(
            f"<tr><td>{r.get('type')}</td><td><a href='/requests'>{r.get('_file')}</a></td>"
            f"<td>{_badge('PENDING', 'pend')}</td></tr>" for r in pend)
        review = (f"<h2>Needs your review</h2><table><tr><th>Type</th><th>Item</th>"
                  f"<th>Status</th></tr>{items}</table>")
    body = (cards + review +
            "<h2>Recent ticks</h2><table><tr><th>Tick</th><th>Time</th><th>Result</th>"
            "<th>Preview</th></tr>" + "".join(rows) + "</table>")
    return page("review", body)


def _tick_files(n: int) -> dict:
    outbox = Path(CFG["outbox_dir"])
    base = outbox / f"tick_{n:06d}"
    return {"prompt": base.with_suffix(".md"),
            "reply": outbox / f"tick_{n:06d}.reply.md",
            "transcript": outbox / f"tick_{n:06d}.transcript.md",
            "error": outbox / f"tick_{n:06d}.llm_error.md"}


@app.route("/tick/<int:n>")
def tick(n: int):
    f = _tick_files(n)
    if not f["prompt"].exists():
        abort(404)
    def sec(title: str, p: Path) -> str:
        return f"<h2>{title}</h2><pre>{p.read_text() if p.exists() else '(none)'}</pre>"
    body = (sec("Prompt", f["prompt"]) + sec("Reply", f["reply"])
            + sec("Transcript", f["transcript"]) + sec("Error", f["error"]))
    return page(f"tick #{n}", body)


@app.route("/ledger")
def ledger():
    data = Path(CFG["data_dir"])
    rows = "".join(
        f"<tr><td class='mut'>{time.strftime('%Y-%m-%d %H:%M', time.localtime(r.get('ts', 0)))}</td>"
        f"<td>{r.get('account')}</td><td>{r.get('kind')}</td><td>{r.get('delta')}</td>"
        f"<td class='mut'>{r.get('memo')}</td><td class='mut'>{r.get('counterparty') or ''}</td></tr>"
        for r in Ledger(data / "economy.db").history(None, limit=200))
    return page("ledger", "<table><tr><th>Time</th><th>Account</th><th>Kind</th>"
                          "<th>Δ</th><th>Memo</th><th>Counterparty</th></tr>"
                          + rows + "</table>")


@app.route("/drafts")
def drafts():
    d = Path(CFG["outbox_dir"]) / "drafts"
    files = sorted(d.glob("*.md"), reverse=True) if d.is_dir() else []
    body = "".join(f"<h2>{p.name} {_badge('NOT SENT', 'pend')}</h2><pre>{p.read_text()}</pre>"
                   for p in files) or "<p class='mut'>No drafts.</p>"
    return page("pitch drafts", body)


@app.route("/requests")
def requests():
    d = Path(CFG["data_dir"]) / "requests"
    files = sorted(d.glob("*.json"), reverse=True) if d.is_dir() else []
    body = "".join(
        f"<h2>{p.name}</h2><pre>{json.dumps(json.loads(p.read_text()), indent=2)}</pre>"
        for p in files) or "<p class='mut'>No requests.</p>"
    return page("allowance & purchase requests", body)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()
    global CFG
    with open(args.config) as f:
        CFG = yaml.safe_load(f)
    port = args.port or CFG.get("webui_port", 5000)
    app.run(host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
