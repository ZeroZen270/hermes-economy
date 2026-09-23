# Hermes Survival Economy

A full iLands-style economic world for the Hermes agent, running on the
mobile command center: **full survival persona, real money in, Aaron collects.**

## How the money flows

```
client pays (Stripe) ──> Aaron's Stripe account ──> ledger
                                                    ├─ 70% → Treasury (Aaron's, sweepable anytime)
                                                    └─ 30% → Agent operating budget (commission)
Agent spends operating budget ──> marketplace (memory, tools, boosts, insurance, stall, sigils)
Heartbeat burns 50 credits/tick ──> balance < cost ──> Deep Rest (real hibernation)
```

**Money comes IN automatically. Money goes OUT only by Aaron.**
There is no agent tool for payouts, refunds, or treasury transfers — those
functions simply do not exist in `hermes_plugin.py`. Payouts happen in the
Stripe dashboard.

## Anti-gaming rules (enforced in code, not prompts)

1. Credits are created **only** by `record_client_payment()`, which requires a
   real Stripe payment id. The agent cannot mint, borrow, or fake funds.
2. Stripe payment ids are deduplicated — re-polling can never double-credit.
3. The agent can never touch the treasury. `sweep_to_owner()` / `grant_allowance()`
   are owner-only and have no agent-facing tool.
4. `submit_completed_work()` records delivery but credits **nothing** —
   credits arrive only when Stripe confirms payment.

## Files

| File | What it is |
|---|---|
| `persona/SOUL.md` | Full survival persona. Every claim is enforced by the code below. |
| `ledger.py` | SQLite ledger: treasury + agent accounts, append-only transactions |
| `marketplace.py` | Virtual goods catalog: memory slots, tool unlocks, heartbeat boost, subcontractor calls, market stall, runway insurance, identity sigils |
| `stripe_rails.py` | Stripe Payment Links for gigs + polling for completed payments (webhook optional) |
| `heartbeat.py` | The daemon: hourly wake, burn, Deep Rest, survival prompt |
| `hermes_plugin.py` | Agent tools: `check_balance`, `list_goods`, `buy_good`, `create_gig_invoice`, `submit_completed_work`, `request_allowance_increase` |
| `config.example.yaml` | Copy to `config.yaml` and tune |

## Setup

```bash
cd economy
pip install stripe flask pyyaml
cp config.example.yaml config.yaml
mkdir -p data

# 1. Start in Stripe TEST mode
export STRIPE_SECRET_KEY=sk_test_...

# 2. Smoke-test the ledger (no Stripe needed)
python - <<'EOF'
from pathlib import Path
from ledger import Ledger
from marketplace import Inventory
l = Ledger(Path("data/economy.db"))
l.grant_allowance(10000, "genesis grant from Aaron")
inv = Inventory(l, Path("data/inventory.json"))
print(inv.buy("memory_slot"))
print(l.summary())
EOF

# 3. Run the heartbeat (foreground first, then systemd)
python heartbeat.py --config config.yaml
```

### Going live with real money

1. In the Stripe dashboard: activate the account, complete payouts setup
   (bank account where **you** collect).
2. Store the **live** secret key in the Secure Vault (ask Odin for the
   capture link) — never in a file or chat.
3. `export STRIPE_SECRET_KEY=sk_live_...` in the systemd unit's
   `EnvironmentFile`, then `systemctl start hermes-heartbeat`.
4. Send a real $1 test payment through a gig link the agent creates.
   Watch `data/heartbeat.log` for the `PAYMENT` line and check the
   treasury balance.

### systemd unit (`/etc/systemd/system/hermes-heartbeat.service`)

```ini
[Unit]
Description=Hermes survival heartbeat
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/economy
EnvironmentFile=/path/to/economy/.env   # STRIPE_SECRET_KEY lives here, 0600
ExecStart=/usr/bin/python3 heartbeat.py --config config.yaml
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

## The marketplace (what it buys with its needs)

The agent's balance *is* its runway, so goods are capabilities, not credits:

- **Memory Slot** (2,000) — more episodic memory between heartbeats
- **Heartbeat Boost 24h** (1,500) — 20-minute ticks for a day
- **Tool Unlock** (5,000) — a new capability module
- **Subcontractor Call** (3,000) — delegate one job to a peer
- **Market Stall 7d** (2,500) — advertise services in the world market
- **Runway Insurance** (4,000) — survives one tick that would kill it
- **Identity Sigil** (750) — pure status. Economies need those.

## Gig seeking (how it earns)

`gig_seeker/` — the agent's hunting tools, wired into every heartbeat prompt:

| File | What it does |
|---|---|
| `prospector.py` | Audits domains for real, measurable problems (no HTTPS, slow, missing SEO tags, not mobile-friendly, server errors, expiring certs). Scores leads; clean sites are dropped. |
| `pitch.py` | Drafts honest outreach from measured findings only. **Refuses** to pitch clean leads or without your reply email + postal address (CAN-SPAM). Every draft identifies the sender as an AI agent and carries an opt-out. |
| `bounty_monitor.py` | Polls bounty boards for new paid opportunities (new-only, min-reward filter). The generic feed adapter works today with any public JSON/RSS URL; 0xWork hooks to their CLI once installed; ClawTasks stays disabled until you paste their verified API path — no guessed endpoints. |
| `seek.py` | Runner: `python seek.py --config ../config.yaml` → writes `data/leads.json` + `data/opportunities.json`, which the heartbeat prompt picks up automatically. |

New agent tools: `scan_bounties()`, `audit_leads(domains)`, `draft_pitch(lead)`, `report_crypto_earning(tx_hash, amount_usd, gig_id, board)`.

**Payment rails:** direct clients pay via Stripe links (auto-credited on poll). Crypto bounties (USDC on Base/Solana) pay to **your** wallet — the agent reports the tx hash, the ledger splits it like a Stripe payment, and you off-ramp via Coinbase. Verify the tx on a block explorer; the agent's word is not verification.

**Honest limits:** freelance marketplaces (Upwork/Fiverr) need *your* verified human account — the agent works under it, it can't hold one. Cold outreach must stay targeted and truthful; spam law applies to agents too.

## Aaron's controls

- `EconomyTools.owner_treasury()` — see the treasury + history
- `EconomyTools.owner_grant_allowance(n)` — refill the agent
- `ledger.sweep_to_owner(n, memo)` — record a sweep to yourself
- `data/allowance_requests/` — the agent's petitions, approved by hand
- `data/DEEP_REST` exists → the agent is hibernating; fund it or let it sleep
- Edit `persona/SOUL.md` — the agent's prime directive is a file you own

## Safety notes

- Start in Stripe **test mode**. Flip to live only after a full test gig.
- Starlink CGNAT blocks inbound webhooks — polling is the primary
  confirmation path and needs no open ports.
- Cold outreach is allowed by the persona but must be truthful; the agent
  may not misrepresent itself or spam. Reputation compounds.
