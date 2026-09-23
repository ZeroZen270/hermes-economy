"""
Hermes economy tool plugin — the tools the agent itself may call.

What the agent CAN do:
  - check its balance, list/buy marketplace goods
  - create gig payment links (money IN: always allowed)
  - mark work delivered, petition Aaron for allowance

What the agent CANNOT do (no function exists for these):
  - mint credits, move treasury funds, issue real-money payouts/refunds
  - approve its own allowance requests

Wire these into the Hermes tool registry. All money movement is enforced
by ledger.py, not by prompt promises.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from ledger import Ledger, AGENT, TREASURY
from marketplace import Inventory
from stripe_rails import GigStore, create_gig_payment_link

import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent / "gig_seeker"))
from bounty_monitor import load_boards, scan
from prospector import audit_domain
from pitch import SenderIdentity, draft_pitch


class EconomyTools:
    def __init__(self, data_dir: str | Path, commission_pct: float,
                 heartbeat_cost: int, stripe_enabled: bool = True):
        data = Path(data_dir)
        self.ledger = Ledger(data / "economy.db")
        self.gigs = GigStore(data / "economy.db")
        self.inventory = Inventory(self.ledger, data / "inventory.json")
        self.requests_dir = data / "allowance_requests"
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        self.commission_pct = commission_pct
        self.heartbeat_cost = heartbeat_cost
        self.stripe_enabled = stripe_enabled

    # -- self-knowledge ------------------------------------------------------
    def check_balance(self) -> dict:
        s = self.ledger.summary()
        runway_ticks = s["agent_credits"] // self.heartbeat_cost if self.heartbeat_cost else 0
        return {
            "operating_credits": s["agent_credits"],
            "operating_usd": round(s["agent_usd"], 2),
            "runway_ticks": runway_ticks,
            "runway_hours": round(runway_ticks, 1),
            "treasury_usd": round(s["treasury_usd"], 2),
            "active_effects": self.inventory.active_effects(),
        }

    def ledger_history(self, limit: int = 20) -> list[dict]:
        return self.ledger.history(AGENT, limit=limit)

    # -- marketplace ---------------------------------------------------------
    def list_goods(self) -> list[dict]:
        return self.inventory.list_catalog()

    def buy_good(self, sku: str) -> dict:
        good = self.inventory.buy(sku)
        return {"bought": good.name, "price_credits": good.price_credits,
                "new_balance": self.ledger.balance(AGENT)}

    # -- earning: money IN ----------------------------------------------------
    def create_gig_invoice(self, amount_usd: float, description: str,
                           client_email: str | None = None) -> dict:
        """Create a Stripe payment link for a gig. Send the URL to the client.
        Money lands in Aaron's Stripe account; your commission credits on payment."""
        if not self.stripe_enabled:
            raise RuntimeError("stripe is not enabled in config")
        if amount_usd <= 0 or amount_usd > 10000:
            raise ValueError("amount must be between $0.01 and $10,000")
        gig_id = f"gig-{uuid.uuid4().hex[:8]}"
        link = create_gig_payment_link(self.gigs, gig_id, amount_usd,
                                       description, client_email)
        link["commission_pct"] = self.commission_pct
        link["your_cut_usd"] = round(amount_usd * self.commission_pct / 100, 2)
        return link

    def submit_completed_work(self, gig_id: str, proof: str) -> dict:
        """Record that you delivered. Does NOT credit you — only a real
        Stripe payment does that (confirmed by the heartbeat poller)."""
        (self.requests_dir / f"delivery_{gig_id}.txt").write_text(
            f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{proof}\n")
        return {"gig_id": gig_id, "status": "delivery recorded",
                "note": "credits arrive when the client pays via Stripe"}

    # -- petitioning the owner --------------------------------------------------
    def request_allowance_increase(self, credits: int, reason: str) -> dict:
        """Ask Aaron for more operating budget. He approves manually —
        this only files the request."""
        if credits <= 0:
            raise ValueError("credits must be positive")
        req_id = f"req-{uuid.uuid4().hex[:8]}"
        (self.requests_dir / f"{req_id}.txt").write_text(
            f"credits: {credits}\nreason: {reason}\n"
            f"at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        return {"request_id": req_id, "status": "pending Aaron's approval"}

    # -- owner-only helpers (Aaron runs these, never the agent) ------------------
    def owner_grant_allowance(self, credits: int, memo: str = "owner grant") -> dict:
        self.ledger.grant_allowance(credits, memo)
        return {"granted": credits, "new_balance": self.ledger.balance(AGENT)}

    # -- gig seeking ------------------------------------------------------------
    def scan_bounties(self, boards_cfg: list[dict], min_reward_usd: float = 5.0) -> list[dict]:
        """Poll bounty boards for new paid opportunities. Returns only ones
        not seen before."""
        data = Path(self.ledger.path).parent
        boards = load_boards({"boards": boards_cfg})
        return scan(boards, data / "opportunities.json", data / "seen_opps.json",
                    min_reward_usd=min_reward_usd)

    def audit_leads(self, domains: list[str]) -> list[dict]:
        """Audit candidate domains for real, measurable problems. Only
        scored leads (with issues) are returned — never pitch a clean site."""
        from prospector import audit_list
        data = Path(self.ledger.path).parent
        return audit_list(domains, data / "leads.json")

    def draft_pitch(self, lead: dict, sender_cfg: dict) -> dict:
        """Draft honest outreach for a prospected lead. Requires complete
        sender identity (reply email + postal address) or it refuses."""
        sender = SenderIdentity(**sender_cfg)
        return draft_pitch(lead, sender)

    def report_crypto_earning(self, tx_hash: str, amount_usd: float,
                              gig_id: str, board: str) -> dict:
        """Report a confirmed on-chain bounty payout to YOUR wallet.
        Credits the ledger like a Stripe payment. Verify the tx yourself
        on a block explorer — the agent's word is not verification."""
        split = self.ledger.record_crypto_payment(
            amount_usd, tx_hash, gig_id, board, self.commission_pct)
        return {"gig_id": gig_id, "board": board, "tx": tx_hash, **split}

    def owner_treasury(self) -> dict:
        s = self.ledger.summary()
        return {"treasury_credits": s["treasury_credits"],
                "treasury_usd": round(s["treasury_usd"], 2),
                "history": self.ledger.history(TREASURY, limit=20)}
