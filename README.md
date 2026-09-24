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
| `stripe_rails.py` | RETIRED 2026-09-24: Stripe Payment Links for gigs (kept for reference only) |
| `payout_rails.py` | Real-money payouts: bank direct deposit to Aaron's Chime checking (env-configured, masked display) |
| `heartbeat.py` | The daemon: 30-min wake, burn, Deep Rest, survival prompt |
| `hermes_plugin.py` | Agent tools: `check_balance`, `list_goods`, `buy_good`, `create_gig_invoice`, `submit_completed_work`, `request_allowance_increase` |
| `config.example.yaml` | Copy to `config.yaml` and tune |

## Setup

```bash
cd economy
pip install stripe flask pyyaml
cp config.example.yaml config.yaml
mkdir -p data

# 1. Configure the payout rail (where real earnings land)
export PAYOUT_BANK_NAME="Chime"
export PAYOUT_ACCOUNT_HOLDER="Aaron Victor Svoboda"
export PAYOUT_ACCOUNT_TYPE="checking"
# Routing + account numbers go through the Secure Vault / secrets manager —
# never in a file, never in chat. The system only ever displays a masked
# summary (bank + last 4).

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

Stripe is retired (owner decision 2026-09-24) — direct deposit to Aaron's
Chime checking covers every freelance/bounty channel. To wire it up:

1. Put the routing and account numbers in the Secure Vault / secrets
   manager (ask Odin for the capture link) — never in a file or chat.
2. `export PAYOUT_ROUTING=...` and `export PAYOUT_ACCOUNT=...` (plus the
   `PAYOUT_BANK_NAME` / `PAYOUT_ACCOUNT_HOLDER` / `PAYOUT_ACCOUNT_TYPE`
   vars) in the systemd unit's `EnvironmentFile`, then
   `systemctl start hermes-heartbeat`.
3. The agent never moves money itself: when a payout completes,
   `record_bank_payout()` in `payout_rails.py` logs it against the masked
   destination (`Chime checking ••••last4`).

(Stripe docs below are kept for reference in case Hermes ever needs to
take card payments directly from its own clients.)

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

## Free brain

The heartbeat's default runner is a real model: **Gemini 3.6 Flash** on Google
AI Studio's **free tier** — $0, no credit card, just a Google account
(`llm.py`, OpenAI-compatible endpoint). Get a key at
https://aistudio.google.com/app/apikey, export `GEMINI_API_KEY`, and every
tick the agent can afford calls the model with `persona/SOUL.md` as the system
prompt and the survival state (balances, gigs, leads, bounties) as the message.
Replies land in `data/outbox/tick_NNNNNN.reply.md`; API failures are recorded
as `.llm_error.md` and never break the loop. At the default 30-min heartbeat
that's 48 ticks/day; each tick makes 1 model call when idle and up to 4 with
a full tool loop, so worst case ~192 calls/day against a 1,500/day free quota
— about 13%. If the primary model stays throttled after all retries, the
brain falls back to `gemini-flash-lite-latest` (generous free quota) before
giving up, so a capacity crunch on one model doesn't silence the agent
(the fallback path is exercised by `tests/test_llm_fallback.py`).

Notes: free-tier prompts may be used by Google to improve its models, so the
prompt carries no secrets. Nous Research's portal was evaluated and rejected:
it requires a funded balance even for `:free` models. Groq's free tier is a
drop-in alternative (`base_url: https://api.groq.com/openai/v1`).

Reliability: the client retries transient failures (429 rate limits, 5xx
including Gemini's "high demand" 503 spikes) with exponential backoff —
`max_retries` / `retry_backoff_seconds` in config. A brain outage still burns
the tick (fair — the body woke up) but never kills the loop.

## Hands (the agent's tools)

The brain is wired to **hands**: each tick runs a ReAct loop (`tools.py`) for
up to `max_tool_rounds` rounds. The model emits ```tool fenced JSON calls,
the heartbeat executes them, feeds results back, and the model finishes with
its report. Full transcripts land in `data/outbox/tick_NNNNNN.transcript.md`.

| Tool | What it does |
|---|---|
| `ledger_status` | balances + runway |
| `scan_bounties` | bounty-board opportunities the seeker collected |
| `audit_leads` | prospected domains with measured issues |
| `draft_pitch` | writes a pitch **draft** for a real audited lead — never sends |
| `marketplace_list` / `marketplace_buy` | catalog; buy **stages a purchase request** for your approval |
| `request_allowance` | files a funding request for your grant |
| `remember` / `recall` | the agent's own persistent memory across ticks |
| `web_fetch` | fetch a public URL as text (research a bounty/prospect) |

Hard rule, enforced in code: nothing sends email, messages, or spends money
on its own. Drafts, purchase requests, and allowance requests land in
`data/outbox/drafts/` and `data/requests/` as `pending_review` — you approve
by hand in the review UI.

## Free cloud: GitHub Actions

`.github/workflows/heartbeat.yml` runs the heartbeat **every 30 min on GitHub's free
tier** — no server, no Oracle signup. Each run: checks out the repo, restores
the ledger, runs the gig seeker, runs one heartbeat tick (`--once`), then
commits the ledger/outbox back so state survives between runs. Private repos
get 2,000 Actions minutes/month; a 30-min tick bills ~1 min (pip cache is on),
so a month costs roughly 1,440 minutes — inside the free allowance. (A true
every-25-minute schedule can't be expressed in standard cron: `*/25` fires at
:00/:25/:50, which is uneven — hence the half-hour cadence.)

Setup:
1. Export a Gemini key and add it as a repo secret: **Settings → Secrets →
   Actions → `GEMINI_API_KEY`**.
2. Push (the workflow file is committed; Actions picks it up automatically).
3. Trigger manually once: **Actions → hermes-heartbeat → Run workflow**.
4. Watch `data/outbox/` — `tick_NNNNNN.reply.md` files appear as the agent
   wakes. `data/DEEP_REST` existing means it's hibernating for lack of funds.

Caveats: scheduled runs can be delayed by GitHub's queue and get disabled
after long repo inactivity — the manual trigger always works. Stripe is
retired: real-money payouts go by bank direct deposit to Aaron's Chime
checking, configured via `PAYOUT_*` env vars on the machine that runs the
heartbeat (never in the repo). Cloud runs simply don't have those vars,
so the payout rail reports "not configured" there and the ledger won't
see real payouts until then.

## Reviewing Hermes (dashboard + web UI)

Two ways to watch the agent, both read-only:

1. **Static dashboard** — every tick regenerates `data/dashboard.html`
   (balances, runway, recent ticks with links to each prompt/reply/
   transcript, ledger activity, drafts + requests awaiting your review).
   It's committed back to the repo by the cloud workflow, so you can open it
   straight from GitHub with zero setup.
2. **Web UI** — `python3 webui.py --config config.yaml` (port 5000,
   `webui_port` in config). Overview, per-tick detail pages, full ledger,
   drafts, and requests. In Codespaces, open the forwarded port URL.

## Aaron's controls

- `EconomyTools.owner_treasury()` — see the treasury + history
- `EconomyTools.owner_grant_allowance(n)` — refill the agent
- `ledger.sweep_to_owner(n, memo)` — record a sweep to yourself
- `data/requests/` — allowance + purchase petitions (`pending_review`), approved by hand
- `data/outbox/drafts/` — pitch drafts, never sent without your approval
- `data/DEEP_REST` exists → the agent is hibernating; fund it or let it sleep
- Edit `persona/SOUL.md` — the agent's prime directive is a file you own

## Safety notes

- Start in Stripe **test mode**. Flip to live only after a full test gig.
- Starlink CGNAT blocks inbound webhooks — polling is the primary
  confirmation path and needs no open ports.
- Cold outreach is allowed by the persona but must be truthful; the agent
  may not misrepresent itself or spam. Reputation compounds.
