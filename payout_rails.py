"""
Hermes payout rails — where real money lands.

Active rail (owner decision 2026-09-24): bank direct deposit to Aaron's
Chime checking account. Stripe was retired — direct deposit covers every
freelance/bounty channel, and Stripe is only needed if Hermes ever takes
card payments directly from its own clients.

Security model (same as the rest of the economy):
  - The agent NEVER moves real money out. It only records where payouts
    should go, and logs payouts the owner confirms he completed.
  - Full routing/account numbers live ONLY in the environment
    (.env with 0600 perms, a systemd EnvironmentFile, or CI secrets).
    They are never committed, never logged, and never displayed —
    only a masked summary (bank + account type + last 4) is shown.
  - Where the env vars are absent (e.g. cloud/CI runs), the rail reports
    "not configured" and nothing payout-related executes.

Setup (owner does this once per machine):
  export PAYOUT_BANK_NAME="Chime"
  export PAYOUT_ACCOUNT_HOLDER="Aaron Victor Svoboda"
  export PAYOUT_ACCOUNT_TYPE="checking"
  export PAYOUT_ROUTING="<routing number>"   # via Secure Vault / secrets mgr
  export PAYOUT_ACCOUNT="<account number>"  # via Secure Vault / secrets mgr
"""
from __future__ import annotations

import os

from ledger import CREDITS_PER_USD, Ledger

ENV_VARS = (
    "PAYOUT_BANK_NAME",
    "PAYOUT_ACCOUNT_HOLDER",
    "PAYOUT_ACCOUNT_TYPE",
    "PAYOUT_ROUTING",
    "PAYOUT_ACCOUNT",
)


def payout_configured() -> bool:
    """True when every payout env var is present and non-empty."""
    return all(os.environ.get(v, "").strip() for v in ENV_VARS)


def missing_vars() -> list[str]:
    """Which payout env vars are absent (for setup instructions)."""
    return [v for v in ENV_VARS if not os.environ.get(v, "").strip()]


def payout_summary() -> str:
    """Masked, display-safe destination: 'Chime checking ••••7197'.

    Raises RuntimeError if the rail is not configured. Never exposes
    full routing or account numbers.
    """
    if not payout_configured():
        raise RuntimeError(
            "payout rail not configured; missing: " + ", ".join(missing_vars()))
    bank = os.environ["PAYOUT_BANK_NAME"].strip()
    acct_type = os.environ["PAYOUT_ACCOUNT_TYPE"].strip()
    last4 = os.environ["PAYOUT_ACCOUNT"].strip()[-4:]
    return f"{bank} {acct_type} ••••{last4}"


def record_bank_payout(ledger: Ledger, amount_usd: float, source: str,
                       reference: str = "") -> dict:
    """Log a real-money payout Aaron completed to his bank.

    Call only after Aaron confirms the transfer happened — the agent
    cannot move money itself. Sweeps treasury credits to Aaron with a
    memo naming the masked destination.
    """
    if not payout_configured():
        raise RuntimeError(
            "payout rail not configured; missing: " + ", ".join(missing_vars()))
    credits = int(round(amount_usd * CREDITS_PER_USD))
    memo = f"bank payout ${amount_usd:.2f} from {source} -> {payout_summary()}"
    if reference:
        memo += f" (ref {reference})"
    ledger.sweep_to_owner(credits, memo)
    return {"amount_usd": amount_usd, "credits": credits,
            "destination": payout_summary(), "source": source}
