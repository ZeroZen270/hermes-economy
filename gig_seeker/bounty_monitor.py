"""
Hermes bounty monitor — watch agent-friendly bounty boards for paid work.

Adapter model (honest by design):
  - Every board is a small class with fetch_open() -> list of opportunities.
  - Boards whose API/CLI isn't verified on this machine stay DISABLED with a
    clear message instead of guessing endpoints. Nothing here invents URLs.
  - The generic feed adapter works TODAY with any JSON or RSS feed URL you
    put in config — that covers most boards' public listings.

Opportunity shape:
  {board, id, title, reward_usd, url, skills[], posted_ts, raw}

Crypto earnings: bounties pay USDC to YOUR wallet (off-ramp via Coinbase).
The agent reports the tx hash with report_crypto_earning(); the ledger
credits it exactly like a Stripe payment (commission + treasury split).
"""
from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Opportunity:
    board: str
    id: str
    title: str
    reward_usd: float
    url: str
    skills: list[str] = field(default_factory=list)
    posted_ts: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


class Board:
    name = "base"
    enabled = True

    def fetch_open(self) -> list[Opportunity]:
        raise NotImplementedError


def _http_get(url: str, timeout: int = 25, attempts: int = 3) -> str:
    """GET a URL as text, tolerating flaky chunked responses.

    Retries with exponential backoff. Some bounty APIs (Superteam) sometimes
    terminate the connection mid-body; urllib raises IncompleteRead in that
    case — if the partial body is complete JSON we use it, otherwise retry.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "HermesBountyMonitor/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.read().decode("utf-8", "replace")
            except http.client.IncompleteRead as e:
                partial = (e.partial or b"").decode("utf-8", "replace")
                try:
                    json.loads(partial)  # complete JSON despite the dropped tail
                    return partial
                except Exception:
                    last = e
        except Exception as e:  # URLError, TimeoutError, SSLError, ...
            last = e
        time.sleep(2 ** attempt)
    # Fallback: curl handles some servers' chunked responses more gracefully
    # than urllib (observed on Superteam). Present on GH runners + most hosts.
    try:
        out = subprocess.run(
            ["curl", "-sS", "--max-time", str(timeout),
             "-A", "HermesBountyMonitor/1.0", url],
            capture_output=True, text=True, timeout=timeout + 10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout
        last = RuntimeError(f"curl exit {out.returncode}: {out.stderr.strip()[:120]}")
    except Exception as e:
        last = e
    raise RuntimeError(f"GET failed: {url} ({last})")


class GenericFeedBoard(Board):
    """Any public JSON or RSS/Atom feed of bounties.

    Config example:
      boards:
        - type: feed
          name: mysource
          url: https://example.com/bounties.json
          # JSON: list of {id, title, reward_usd, url, skills[]}
    """
    def __init__(self, name: str, url: str):
        self.name = name
        self.url = url

    def fetch_open(self) -> list[Opportunity]:
        body = _http_get(self.url, timeout=20)
        if body.lstrip().startswith("<"):
            return self._parse_rss(body)
        return self._parse_json(body)

    def _parse_json(self, body: str) -> list[Opportunity]:
        data = json.loads(body)
        items = data if isinstance(data, list) else data.get("items", data.get("bounties", []))
        opps = []
        for it in items:
            try:
                opps.append(Opportunity(
                    board=self.name, id=str(it.get("id", it.get("url"))),
                    title=it.get("title", "untitled"),
                    reward_usd=float(it.get("reward_usd", it.get("reward", 0)) or 0),
                    url=it.get("url", ""),
                    skills=it.get("skills", []),
                    posted_ts=float(it.get("posted_ts", 0)) or time.time()))
            except (TypeError, ValueError):
                continue
        return opps

    def _parse_rss(self, body: str) -> list[Opportunity]:
        opps = []
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return opps
        for item in root.iter("item"):
            title = (item.findtext("title") or "untitled").strip()
            link = (item.findtext("link") or "").strip()
            opps.append(Opportunity(board=self.name, id=link or title,
                                    title=title, reward_usd=0.0, url=link,
                                    posted_ts=time.time()))
        return opps


class ZeroXWorkBoard(Board):
    """0xWork (0xwork.org) — USDC bounties on Base, agent-native with its own
    CLI/SDK. This adapter shells to the CLI; install it per their docs and set
    the exact list command in config. Disabled until the CLI exists locally."""
    name = "0xwork"

    def __init__(self, list_command: str | None = None):
        self.list_command = list_command
        self.enabled = bool(list_command and shutil.which(list_command.split()[0]))

    def fetch_open(self) -> list[Opportunity]:
        if not self.enabled:
            return []
        out = subprocess.run(self.list_command, shell=True, capture_output=True,
                             text=True, timeout=60)
        data = json.loads(out.stdout or "[]")
        items = data if isinstance(data, list) else data.get("tasks", [])
        opps = []
        for it in items:
            opps.append(Opportunity(
                board=self.name, id=str(it.get("id")),
                title=it.get("title", "untitled"),
                reward_usd=float(it.get("reward_usdc", it.get("reward", 0)) or 0),
                url=it.get("url", "https://0xwork.org"),
                skills=it.get("skills", []),
                posted_ts=time.time()))
        return opps


class ClawTasksBoard(Board):
    """ClawTasks — USDC bounties, agent API advertised. The exact REST paths
    are NOT verified from here, so this stays disabled until you paste the
    listing endpoint from their docs into config. No guessed URLs."""
    name = "clawtasks"

    def __init__(self, api_base: str | None = None, api_key: str | None = None):
        self.api_base = api_base
        self.api_key = api_key
        self.enabled = bool(api_base)

    def fetch_open(self) -> list[Opportunity]:
        if not self.enabled:
            return []
        raise NotImplementedError(
            "set the verified listing path in config; refusing to guess endpoints")


class SuperteamBoard(Board):
    """Superteam Earn (superteam.fun) — Solana-ecosystem bounties/projects.

    Uses the public keyless listings endpoint, verified live 2026-09-24:
      GET https://superteam.fun/api/listings?context=home&tab=all&category=<cat>
    Returns OPEN listings with id/title/rewardAmount/token/deadline/slug/
    sponsor/agentAccess. Listing pages live at
    https://superteam.fun/earn/listing/<slug>/ (verified 200).

    NOTE: this endpoint is not filtered by agent eligibility — agentAccess
    is surfaced in skills so the agent (or Aaron) can pick accordingly.
    The agent-only API (api/agents/...) needs a registered agent API key;
    wire it only after Aaron approves the registration."""
    name = "superteam"

    def __init__(self, category: str = "Development"):
        self.category = category
        self.name = f"superteam-{category.lower()}"

    def fetch_open(self) -> list[Opportunity]:
        url = ("https://superteam.fun/api/listings?context=home&tab=all"
               f"&category={self.category}")
        data = json.loads(_http_get(url, timeout=25))
        items = data if isinstance(data, list) else data.get("listings", [])
        opps = []
        for it in items:
            if it.get("status") != "OPEN" or it.get("isWinnersAnnounced"):
                continue
            slug = it.get("slug") or ""
            reward = it.get("rewardAmount") or 0
            try:
                reward = float(reward)
            except (TypeError, ValueError):
                reward = 0.0
            opps.append(Opportunity(
                board=self.name,
                id=str(it.get("id", slug)),
                title=str(it.get("title", "untitled"))[:160],
                reward_usd=reward,
                url=f"https://superteam.fun/earn/listing/{slug}/" if slug else "https://superteam.fun",
                skills=[f"pays:{it.get('token', '?')}",
                        f"access:{it.get('agentAccess', '?')}",
                        f"type:{it.get('type', '?')}"],
                posted_ts=time.time()))
        return opps


def load_boards(cfg: dict) -> list[Board]:
    boards: list[Board] = []
    for b in cfg.get("boards", []):
        t = b.get("type")
        if t == "feed":
            boards.append(GenericFeedBoard(b.get("name", "feed"), b["url"]))
        elif t == "0xwork":
            boards.append(ZeroXWorkBoard(b.get("list_command")))
        elif t == "clawtasks":
            boards.append(ClawTasksBoard(b.get("api_base"), b.get("api_key")))
        elif t == "superteam":
            boards.append(SuperteamBoard(b.get("category", "Development")))
    return boards


def scan(boards: list[Board], out_path: Path, seen_path: Path,
         min_reward_usd: float = 0.0) -> list[dict]:
    """Poll all boards, return NEW opportunities since last scan.

    Also writes a snapshot of every currently-open listing to
    data/open_bounties.json (so the agent keeps sight of open bounties it
    has already seen) and per-board poll stats into opportunities.json
    under "boards" (so diagnostics report reality, not guesses).
    """
    seen: set[str] = set()
    if seen_path.exists():
        seen = set(json.loads(seen_path.read_text()))
    fresh: list[dict] = []
    all_open: list[dict] = []
    board_stats: dict[str, dict] = {}
    seen_ids: set[str] = set()  # dedupe the same listing across boards
    for board in boards:
        stats: dict = {"open": 0, "new": 0, "error": None}
        try:
            for opp in board.fetch_open():
                if opp.reward_usd < min_reward_usd:
                    continue
                if opp.id in seen_ids:
                    continue
                seen_ids.add(opp.id)
                stats["open"] += 1
                all_open.append(opp.as_dict())
                key = f"{opp.board}:{opp.id}"
                if key not in seen:
                    seen.add(key)
                    stats["new"] += 1
                    fresh.append(opp.as_dict())
        except Exception as e:
            stats["error"] = str(e)[:160]
            print(f"board {board.name} failed: {e}")
        board_stats[board.name] = stats
    seen_path.parent.mkdir(parents=True, exist_ok=True)
    seen_path.write_text(json.dumps(sorted(seen)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(
        {"generated_ts": time.time(), "opportunities": fresh,
         "boards": board_stats}, indent=2))
    (out_path.parent / "open_bounties.json").write_text(json.dumps(
        {"generated_ts": time.time(), "bounties": all_open}, indent=2))
    print(f"bounty snapshot: {len(all_open)} open, {len(fresh)} new, "
          f"{len(board_stats)} boards")
    return fresh
