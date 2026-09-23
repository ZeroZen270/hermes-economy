"""
Hermes Stripe rails — real money in, automatically.

Design (Starlink-friendly):
  - The agent CREATES payment links for gigs (inbound money: always safe).
  - Confirmation happens by POLLING the Stripe API for completed checkouts —
    no inbound webhook required, which matters behind Starlink CGNAT.
  - A Flask webhook handler is included as an option for when inbound
    connectivity exists, but polling is the primary path.
  - The agent can NEVER move real money out. Payouts happen in the Stripe
    dashboard by Aaron. There is deliberately no payout function here.

Setup:
  pip install stripe flask
  export STRIPE_SECRET_KEY=sk_test_...   # start in test mode!
  # live key goes through the Secure Vault when Aaron wires it for real.

Gig flow:
  1. agent calls create_gig_payment_link(25.00, gig_id, "Website audit")
  2. agent sends the URL to the client
  3. client pays -> money lands in Aaron's Stripe account
  4. heartbeat poller calls poll_completed_payments() -> ledger.record_client_payment()
     splits commission to the agent, owner cut to the treasury
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from ledger import Ledger

GIG_SCHEMA = """
CREATE TABLE IF NOT EXISTS gigs (
    gig_id      TEXT PRIMARY KEY,
    amount_usd  REAL NOT NULL,
    description TEXT NOT NULL,
    client      TEXT,
    payment_url TEXT,
    stripe_link_id TEXT,
    status      TEXT NOT NULL DEFAULT 'open',  -- open, paid, cancelled
    created_ts  REAL NOT NULL,
    paid_ts     REAL
);
"""


def _stripe():
    try:
        import stripe
    except ImportError as e:
        raise RuntimeError("pip install stripe  (and flask for the webhook)") from e
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is not set")
    stripe.api_key = key
    return stripe


class GigStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        with sqlite3.connect(self.db_path) as c:
            c.executescript(GIG_SCHEMA)

    def add(self, gig_id: str, amount_usd: float, description: str,
            client: str | None, payment_url: str, stripe_link_id: str) -> None:
        with sqlite3.connect(self.db_path) as c:
            c.execute(
                "INSERT INTO gigs (gig_id, amount_usd, description, client, payment_url,"
                " stripe_link_id, created_ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (gig_id, amount_usd, description, client, payment_url,
                 stripe_link_id, time.time()),
            )

    def mark_paid(self, gig_id: str) -> None:
        with sqlite3.connect(self.db_path) as c:
            c.execute(
                "UPDATE gigs SET status='paid', paid_ts=? WHERE gig_id=?",
                (time.time(), gig_id),
            )

    def open_gigs(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in
                    c.execute("SELECT * FROM gigs WHERE status='open'").fetchall()]


def create_gig_payment_link(gig_store: GigStore, gig_id: str, amount_usd: float,
                            description: str,
                            client_email: str | None = None) -> dict:
    """Create a Stripe Payment Link for a gig. Returns the URL to send the client."""
    stripe = _stripe()
    price = stripe.Price.create(
        unit_amount=int(round(amount_usd * 100)),
        currency="usd",
        product_data={"name": f"Hermes gig {gig_id}: {description}"},
        metadata={"gig_id": gig_id},
    )
    link = stripe.PaymentLink.create(
        line_items=[{"price": price.id, "quantity": 1}],
        metadata={"gig_id": gig_id},
    )
    gig_store.add(gig_id, amount_usd, description, client_email,
                  link.url, link.id)
    return {"url": link.url, "gig_id": gig_id, "amount_usd": amount_usd}


def poll_completed_payments(gig_store: GigStore, ledger: Ledger,
                            commission_pct: float,
                            lookback_seconds: int = 86400) -> list[dict]:
    """Poll Stripe for completed checkout sessions tied to our gigs.

    Idempotent: ledger.record_client_payment() rejects duplicate stripe ids,
    so re-polling is safe. Call this from the heartbeat.
    """
    stripe = _stripe()
    since = int(time.time()) - lookback_seconds
    credited = []
    # Payment Links produce Checkout Sessions; find completed ones.
    sessions = stripe.checkout.Session.list(
        created={"gte": since}, status="complete", limit=100,
    )
    for s in sessions.auto_paging_iter():
        gig_id = (s.get("metadata") or {}).get("gig_id")
        if not gig_id:
            continue
        pi = s.get("payment_intent")
        stripe_id = pi if isinstance(pi, str) else s.get("id")
        amount_usd = (s.get("amount_total") or 0) / 100.0
        client = s.get("customer_details", {}).get("email") if s.get("customer_details") else None
        try:
            split = ledger.record_client_payment(
                amount_usd, stripe_id, gig_id, client or "unknown", commission_pct)
        except ValueError:
            continue  # already recorded
        gig_store.mark_paid(gig_id)
        credited.append({"gig_id": gig_id, "amount_usd": amount_usd, **split})
    return credited


# -- Optional webhook (only useful with inbound connectivity) -----------------
def make_webhook_app(gig_store: GigStore, ledger: Ledger, commission_pct: float):
    """Flask app: POST /stripe/webhook. Needs STRIPE_WEBHOOK_SECRET set."""
    from flask import Flask, request, abort
    stripe = _stripe()
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET is not set")
    app = Flask(__name__)

    @app.post("/stripe/webhook")
    def webhook():
        payload = request.data
        sig = request.headers.get("Stripe-Signature", "")
        try:
            event = stripe.Webhook.construct_event(payload, sig, secret)
        except Exception:
            abort(400)
        if event["type"] == "checkout.session.completed":
            s = event["data"]["object"]
            gig_id = (s.get("metadata") or {}).get("gig_id")
            if gig_id:
                pi = s.get("payment_intent")
                stripe_id = pi if isinstance(pi, str) else s["id"]
                amount_usd = (s.get("amount_total") or 0) / 100.0
                try:
                    ledger.record_client_payment(
                        amount_usd, stripe_id, gig_id, "webhook", commission_pct)
                    gig_store.mark_paid(gig_id)
                except ValueError:
                    pass  # duplicate delivery
        return {"ok": True}

    return app
