"""
Hermes marketplace — the virtual goods / needs catalog.

The agent's operating balance IS its runway (the heartbeat burns from it),
so the marketplace sells what a budget can't buy directly: capabilities,
upgrades, insurance, presence, and status. Purchases are enforced against
the ledger: no balance, no goods.

Inventory persists to JSON so the heartbeat daemon can honor active effects.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from ledger import Ledger


@dataclass(frozen=True)
class Good:
    sku: str
    name: str
    price_credits: int
    blurb: str
    effect: str = ""          # honored by the heartbeat daemon


CATALOG: list[Good] = [
    Good("memory_slot", "Memory Slot", 2000,
         "Expands episodic memory: the daemon keeps more heartbeat summaries.",
         effect="memory:+1"),
    Good("heartbeat_boost_24h", "Heartbeat Boost (24h)", 1500,
         "Wake every 20 minutes instead of hourly for 24h. More ticks, more chances to earn.",
         effect="interval:1200/86400"),
    Good("tool_unlock", "Tool Unlock", 5000,
         "Unlock one new capability module (scraper pack, code runner, inbox monitor...).",
         effect="tool:+1"),
    Good("subcontractor_call", "Subcontractor Call", 3000,
         "Hire a peer agent instance for one delegated job.",
         effect="subcontract:+1"),
    Good("market_stall_7d", "Market Stall (7d)", 2500,
         "A stall in the world market advertising your services for 7 days.",
         effect="stall:604800"),
    Good("runway_insurance", "Runway Insurance", 4000,
         "If a heartbeat would drop you into Deep Rest, insurance covers one tick. One-shot.",
         effect="insurance:+1"),
    Good("identity_sigil", "Identity Sigil", 750,
         "A cosmetic sigil for your agent identity. Pure status. Economies need those too.",
         effect="cosmetic"),
]


class Inventory:
    def __init__(self, ledger: Ledger, path: Path):
        self.ledger = ledger
        self.path = Path(path)
        self.state: dict = {"owned": {}, "activated": {}}
        if self.path.exists():
            self.state = json.loads(self.path.read_text())

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, indent=2))

    def buy(self, sku: str) -> Good:
        good = next((g for g in CATALOG if g.sku == sku), None)
        if good is None:
            raise ValueError(f"unknown sku: {sku}")
        self.ledger.spend(good.price_credits, "spend",
                          memo=f"marketplace: bought {good.name} ({sku})")
        owned = self.state["owned"]
        owned[sku] = owned.get(sku, 0) + 1
        # Timed effects activate on purchase.
        if good.effect.startswith("interval:"):
            _, rest = good.effect.split(":", 1)
            seconds, duration = rest.split("/")
            self.state["activated"]["boost_until"] = time.time() + float(duration)
            self.state["activated"]["boost_interval"] = float(seconds)
        elif good.effect.startswith("stall:"):
            _, duration = good.effect.split(":")
            self.state["activated"]["stall_until"] = time.time() + float(duration)
        self._save()
        return good

    def consume_insurance(self) -> bool:
        owned = self.state["owned"]
        if owned.get("runway_insurance", 0) > 0:
            owned["runway_insurance"] -= 1
            self._save()
            return True
        return False

    def active_effects(self) -> dict:
        now = time.time()
        act = self.state["activated"]
        effects: dict = {"owned": dict(self.state["owned"])}
        if act.get("boost_until", 0) > now:
            effects["interval"] = act["boost_interval"]
        if act.get("stall_until", 0) > now:
            effects["market_stall"] = True
        return effects

    def list_catalog(self) -> list[dict]:
        bal = self.ledger.balance("agent:operating")
        return [{
            "sku": g.sku, "name": g.name,
            "price_credits": g.price_credits,
            "price_usd": g.price_credits / 1000,
            "blurb": g.blurb,
            "affordable": bal >= g.price_credits,
            "owned": self.state["owned"].get(g.sku, 0),
        } for g in CATALOG]
