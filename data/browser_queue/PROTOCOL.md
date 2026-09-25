# Hermes Browser Queue Protocol

Hermes runs on GitHub Actions, which has no persistent browser, no logged-in
profiles, no 2FA device, and no human to click through captchas or device
prompts. This queue is the bridge: Hermes asks for browser work here, and the
local runner (Odin's assistant runtime, which owns a real Chromium with saved
sessions) executes it and posts results back. Fully automatic — no Aaron
interaction required for the standard path.

## Paths (repo: `ZeroZen270/hermes-economy`, branch `main`)

- Requests: `data/browser_queue/requests/<id>.json`
- Results:  `data/browser_queue/results/<id>.json`
- This file: `data/browser_queue/PROTOCOL.md`

## Request format (written by Hermes)

```json
{
  "id": "2026-09-24-clickworker-sweep",
  "created_tick": 125,
  "type": "sweep | submit | lookup | apply",
  "site": "clickworker",
  "url": "https://workplace.clickworker.com/",
  "task": "List available jobs with pay >= $1. Reserve nothing. Report titles, IDs, and pay.",
  "deliverable": "Job list with titles, IDs, pay amounts",
  "constraints": ["no payout/tax settings", "no account changes", "truthful answers only"],
  "status": "queued"
}
```

- `id`: unique, human-readable, prefixed with date.
- `status`: starts as `"queued"`. The runner flips it to `"done"`,
  `"failed"`, or `"blocked"` after execution.
- Requests are append-only from Hermes' side; the runner never deletes them.

## Result format (written by the runner)

```json
{
  "id": "2026-09-24-clickworker-sweep",
  "status": "done",
  "summary": "3 jobs found, none payable over $1. No reservations made.",
  "data": {"jobs": [{"title": "...", "id": "...", "pay": "..."}]},
  "completed_ts": "2026-09-25T03:45:00Z"
}
```

- `status`: `"done"` (completed as requested), `"failed"` (technical failure,
  safe to retry), or `"blocked"` (needs Aaron — include `reason`).
- For `"blocked"`, `summary` must say exactly what Aaron has to do
  (e.g. "needs Aaron's Google device tap: Yes + 58 on his Motorola Moto G").

## Security rules (non-negotiable)

1. Requests must NEVER contain raw credentials, passwords, API keys, tokens,
   session cookies, or one-time codes. Hermes references sessions by site name
   only ("use the saved Clickworker session").
2. Results must NEVER contain credential values, cookies, or tokens. Redact
   before writing.
3. Anything requiring Aaron's manual login, device verification, OTP, wallet
   signature, or CAPTCHA-solving stays `"blocked"` with a reason. The runner
   never works around a login gate.
4. The runner never touches payout, tax, banking, or identity/KYC settings.
5. The runner never registers new accounts, makes purchases, or changes
   account settings beyond what the request explicitly authorizes.
6. Stale requests (older than 24h, still queued) may be marked `"failed"` with
   reason `"expired"` so Hermes can re-issue.

## Cadence

The local runner drains the queue every 15 minutes. Hermes polls
`data/browser_queue/results/` for completed results and folds them into its
tick loop (leads, bounty entries, revenue events).
