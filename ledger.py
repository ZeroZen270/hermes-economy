"""
Hermes economy ledger — SQLite-backed double-entry-ish ledger.

Accounts:
  treasury          Aaron's wallet. Receives 100% of client payments (in credits).
  agent:operating   The agent's spending money: commission on gigs + approved allowances.

Anti-gaming rules (enforced here, not in prompts):
  - Credits are created ONLY via record_client_payment(), which requires a
    Stripe payment id. The agent can never mint credits.
  - The agent can never move money out of the treasury. Only Aaron (owner) can.
  - Every mutation is an append-only transaction row with a timestamp.

1,000 credits = $1.00 USD.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

CREDITS_PER_USD = 1000

TREASURY = "treasury"
AGENT = "agent:operating"

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL    NOT NULL,
    account       TEXT    NOT NULL,
    delta         INTEGER NOT NULL,   -- credits, signed
    kind          TEXT    NOT NULL,   -- payment, commission, allowance, spend, burn, sweep, refund
    memo          TEXT    NOT NULL DEFAULT '',
    stripe_id     TEXT,               -- set only for real-money payments
    counterparty  TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class Ledger:
    path: Path

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    # -- low-level ---------------------------------------------------------
    def _add(self, account: str, delta: int, kind: str, memo: str = "",
             stripe_id: str | None = None, counterparty: str | None = None) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO transactions (ts, account, delta, kind, memo, stripe_id, counterparty)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (time.time(), account, delta, kind, memo, stripe_id, counterparty),
            )
            return cur.lastrowid

    def balance(self, account: str) -> int:
        with self._conn() as c:
            row = c.execute(
                "SELECT COALESCE(SUM(delta), 0) AS b FROM transactions WHERE account = ?",
                (account,),
            ).fetchone()
            return int(row["b"])

    # -- one-time owner adjustments (grant resizes, etc.) --------------------
    def get_meta(self, key: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def history(self, account: str | None = None, limit: int = 50) -> list[dict]:
        q = "SELECT * FROM transactions"
        args: list = []
        if account:
            q += " WHERE account = ?"
            args.append(account)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]

    # -- money in: Stripe-confirmed payments only ---------------------------
    def record_client_payment(self, amount_usd: float, stripe_id: str,
                              gig_id: str, client: str,
                              agent_commission_pct: float) -> dict:
        """Credit a real, Stripe-confirmed client payment.

        Splits: agent commission -> agent:operating, remainder -> treasury.
        Raises if this stripe_id was already recorded (no double-spend).
        """
        if self._stripe_seen(stripe_id):
            raise ValueError(f"stripe payment {stripe_id} already recorded")
        credits = int(round(amount_usd * CREDITS_PER_USD))
        commission = int(round(credits * agent_commission_pct / 100.0))
        owner_cut = credits - commission
        self._add(TREASURY, owner_cut, "payment",
                  memo=f"gig {gig_id}: owner cut of ${amount_usd:.2f}",
                  stripe_id=stripe_id, counterparty=client)
        self._add(AGENT, commission, "commission",
                  memo=f"gig {gig_id}: {agent_commission_pct}% commission on ${amount_usd:.2f}",
                  stripe_id=stripe_id, counterparty=client)
        return {"credits": credits, "commission": commission, "owner_cut": owner_cut}

    def _stripe_seen(self, stripe_id: str) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM transactions WHERE stripe_id = ? LIMIT 1",
                (stripe_id,),
            ).fetchone()
            return row is not None

    # -- money in: crypto bounties (off-ramp to USD happens outside) --------
    def record_crypto_payment(self, amount_usd: float, tx_hash: str,
                              gig_id: str, board: str,
                              agent_commission_pct: float) -> dict:
        """Credit a confirmed on-chain bounty payout (USDC etc., valued in USD
        at receipt). Same split rules as Stripe. tx_hash dedups like stripe_id."""
        if self._stripe_seen(tx_hash):
            raise ValueError(f"tx {tx_hash} already recorded")
        credits = int(round(amount_usd * CREDITS_PER_USD))
        commission = int(round(credits * agent_commission_pct / 100.0))
        owner_cut = credits - commission
        self._add(TREASURY, owner_cut, "payment",
                  memo=f"crypto bounty {gig_id} on {board}: owner cut of ${amount_usd:.2f}",
                  stripe_id=tx_hash, counterparty=board)
        self._add(AGENT, commission, "commission",
                  memo=f"crypto bounty {gig_id}: {agent_commission_pct}% commission",
                  stripe_id=tx_hash, counterparty=board)
        return {"credits": credits, "commission": commission, "owner_cut": owner_cut}

    # -- money within the agent's world --------------------------------------
    def grant_allowance(self, credits: int, memo: str) -> None:
        """Owner-to-agent grant. Only Aaron calls this (never the agent)."""
        if credits <= 0:
            raise ValueError("allowance must be positive")
        self._add(AGENT, credits, "allowance", memo=memo, counterparty="aaron")

    def spend(self, credits: int, kind: str, memo: str) -> None:
        """Agent spends operating budget (marketplace, tool costs)."""
        if credits <= 0:
            raise ValueError("spend must be positive")
        if self.balance(AGENT) < credits:
            raise ValueError("insufficient operating balance")
        self._add(AGENT, -credits, kind, memo=memo)

    def burn(self, credits: int, memo: str) -> None:
        """Unavoidable cost: heartbeat tick, compute. Can drive balance negative
        only via burn — this is what triggers Deep Rest, not debt."""
        self._add(AGENT, -credits, "burn", memo=memo)

    # -- money out: Aaron only -----------------------------------------------
    def sweep_to_owner(self, credits: int, memo: str) -> None:
        """Move treasury funds to Aaron's real payout. Owner-only.
        The agent has no tool that calls this."""
        if credits <= 0:
            raise ValueError("sweep must be positive")
        if self.balance(TREASURY) < credits:
            raise ValueError("insufficient treasury balance")
        self._add(TREASURY, -credits, "sweep", memo=memo, counterparty="aaron")

    # -- reporting --------------------------------------------------------------
    def summary(self) -> dict:
        tb = self.balance(TREASURY)
        ab = self.balance(AGENT)
        with self._conn() as c:
            row = c.execute(
                "SELECT COALESCE(SUM(delta), 0) FROM transactions "
                "WHERE kind = 'payment'"
            ).fetchone()
        real_earned = int(row[0] or 0)
        return {
            "treasury_credits": tb,
            "treasury_usd": tb / CREDITS_PER_USD,
            "agent_credits": ab,
            "agent_usd": ab / CREDITS_PER_USD,
            "real_earned_credits": real_earned,
            "real_earned_usd": real_earned / CREDITS_PER_USD,
        }
