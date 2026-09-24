"""
Hermes lead prospector — find businesses with detectable, fixable problems.

The agent supplies candidate domains (from its own research/browsing);
the prospector audits each one and scores concrete issues the agent can
pitch honestly: no HTTPS, slow responses, missing titles/meta, not
mobile-friendly, server errors. Every finding in a pitch is one the
prospector actually measured — no invented problems.

Needs: pip install requests
"""
from __future__ import annotations

import json
import re
import socket
import ssl
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import requests

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
META_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]*>', re.I)
VIEWPORT_RE = re.compile(
    r'<meta[^>]+name=["\']viewport["\'][^>]*>', re.I)
MAILTO_RE = re.compile(r'mailto:([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', re.I)
EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')

# Addresses we never treat as outreach targets.
EMAIL_BLOCKLIST_EXACT = {"techsquaredllc42071@gmail.com"}  # our own sender
EMAIL_BLOCKLIST_SUBSTR = ("noreply", "no-reply", "donotreply", "do-not-reply",
                          "example.com", "example.org", "example.net",
                          "test.com", ".png", ".jpg", ".jpeg",
                          ".gif", ".webp", ".svg", "sentry", "schema.org",
                          "w3.org")
# Domains the owner has permanently removed — never audit, score, or pitch these.
# Edit this set (not leads.json) to kill a dead lead for good.
SUPPRESSED_DOMAINS = {"kentpriceplumbing.com"}
# Likely contact pages worth one polite fetch each.
CONTACT_PATHS = ("/contact", "/contact-us", "/contact.html",
                 "/about/contact-us", "/about-us", "/about")
UA = {"User-Agent": "HermesSiteAudit/1.0"}


def _clean_email(raw: str) -> str | None:
    e = raw.strip().strip(".,;:!?\"'()[]<>").lower()
    if not EMAIL_RE.fullmatch(e):
        return None
    if e in EMAIL_BLOCKLIST_EXACT:
        return None
    if any(b in e for b in EMAIL_BLOCKLIST_SUBSTR):
        return None
    return e


def extract_emails(html: str, domain: str) -> list[str]:
    """Pull candidate outreach emails from page HTML.

    mailto: links first (explicit contact intent), then plain-text matches.
    Same-domain addresses rank first — they're the most likely legitimate
    business inboxes. Returns at most 5, deduplicated."""
    found: list[str] = []
    for raw in MAILTO_RE.findall(html):
        e = _clean_email(raw.split("?")[0])
        if e and e not in found:
            found.append(e)
    text = re.sub(r"<[^>]+>", " ", html)
    for raw in EMAIL_RE.findall(text):
        e = _clean_email(raw)
        if e and e not in found:
            found.append(e)
    same = [e for e in found if e.endswith("@" + domain)]
    other = [e for e in found if not e.endswith("@" + domain)]
    return (same + other)[:5]


@dataclass
class Lead:
    domain: str
    url: str
    issues: list[str] = field(default_factory=list)
    notes: dict = field(default_factory=dict)
    score: int = 0
    checked_ts: float = field(default_factory=time.time)
    emails: list[str] = field(default_factory=list)  # public contact emails found on-site

    def pitch_angle(self) -> str:
        if not self.issues:
            return ""
        top = self.issues[0]
        angles = {
            "no_https": "their site still serves over plain HTTP",
            "slow": "their site loads slowly",
            "no_title": "their homepage has no title tag (hurts search)",
            "no_meta_description": "their homepage has no meta description (hurts click-through)",
            "not_mobile_friendly": "their site lacks a mobile viewport tag",
            "server_error": "their site is throwing server errors",
            "unreachable": "their site is unreachable",
        }
        return angles.get(top, top)


def _cert_days_left(host: str) -> int | None:
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=8) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ss:
                cert = ss.getpeercert()
        import datetime
        exp = datetime.datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
        return (exp - datetime.datetime.utcnow()).days
    except Exception:
        return None


def audit_domain(domain: str, timeout: int = 12) -> Lead:
    domain = domain.strip().lower().removeprefix("http://").removeprefix("https://").split("/")[0]
    lead = Lead(domain=domain, url=f"https://{domain}")
    t0 = time.time()
    try:
        r = requests.get(f"https://{domain}", timeout=timeout, allow_redirects=True,
                         headers=UA)
        elapsed = time.time() - t0
        lead.notes["status"] = r.status_code
        lead.notes["latency_s"] = round(elapsed, 2)
        lead.notes["final_url"] = r.url
        if r.status_code >= 500:
            lead.issues.append("server_error")
        if elapsed > 4:
            lead.issues.append("slow")
        html = r.text[:200_000]
        if not TITLE_RE.search(html):
            lead.issues.append("no_title")
        if not META_DESC_RE.search(html):
            lead.issues.append("no_meta_description")
        if not VIEWPORT_RE.search(html):
            lead.issues.append("not_mobile_friendly")
        lead.emails = extract_emails(html, domain)
        # One polite fetch per likely contact page — businesses often publish
        # an email there even when the homepage doesn't.
        if not lead.emails:
            base = f"{urlparse(r.url).scheme}://{urlparse(r.url).hostname}"
            for path in CONTACT_PATHS:
                try:
                    cr = requests.get(base + path, timeout=timeout,
                                      allow_redirects=True, headers=UA)
                    time.sleep(0.5)
                    if cr.status_code == 200 and "text/html" in cr.headers.get("Content-Type", ""):
                        lead.emails = extract_emails(cr.text[:200_000], domain)
                        if lead.emails:
                            lead.notes["email_source"] = path
                            break
                except Exception:
                    continue
        days = _cert_days_left(urlparse(r.url).hostname or domain)
        if days is not None:
            lead.notes["cert_days_left"] = days
            if days < 14:
                lead.issues.append("cert_expiring")
    except requests.exceptions.SSLError:
        # Try plain HTTP to distinguish "no HTTPS" from "dead site".
        try:
            r = requests.get(f"http://{domain}", timeout=timeout,
                             headers=UA)
            lead.issues.append("no_https")
            lead.notes["http_status"] = r.status_code
            lead.url = f"http://{domain}"
        except Exception:
            lead.issues.append("unreachable")
    except Exception as e:
        lead.issues.append("unreachable")
        lead.notes["error"] = str(e)[:120]

    weights = {"no_https": 5, "server_error": 5, "unreachable": 0,
               "slow": 3, "no_title": 2, "no_meta_description": 2,
               "not_mobile_friendly": 2, "cert_expiring": 3}
    lead.score = sum(weights.get(i, 1) for i in lead.issues)
    return lead


def audit_list(domains: list[str], out_path: Path) -> list[dict]:
    leads = []
    for d in domains:
        if not d.strip():
            continue
        if d.strip().lower().removeprefix("http://").removeprefix("https://").split("/")[0] in SUPPRESSED_DOMAINS:
            continue
        try:
            lead = audit_domain(d)
        except Exception as e:
            lead = Lead(domain=d, issues=["unreachable"], notes={"error": str(e)[:120]})
        if lead.score > 0:
            leads.append(asdict(lead))
        time.sleep(1)  # be polite
    leads.sort(key=lambda l: l["score"], reverse=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(
        {"generated_ts": time.time(), "leads": leads}, indent=2))
    return leads


if __name__ == "__main__":
    import sys
    domains = sys.argv[1:]
    if not domains:
        print("usage: prospector.py domain1 domain2 ...")
        raise SystemExit(1)
    leads = audit_list(domains, Path("data/leads.json"))
    for l in leads:
        print(f"{l['score']:>2}  {l['domain']:<30} {', '.join(l['issues'])}")
