"""
Hermes pitch drafter — honest, targeted cold outreach.

Rules baked into every draft:
  - Every claimed problem comes from the prospector's real measurements.
  - The sender identifies as an AI agent, never a human.
  - CAN-SPAM basics: truthful subject line, real sender identity,
    physical postal address, clear opt-out. The sender's details come
    from config — no pitch goes out without them.
  - One business, one real finding, one specific offer. No blasts.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SenderIdentity:
    agent_name: str = "Hermes"
    operator_name: str = "Aaron"          # human owner
    business_name: str = "Technology Squared LLC"
    reply_email: str = ""                 # set in config
    postal_address: str = ""              # required by CAN-SPAM
    base_price_usd: float = 25.0


SERVICE_MENU = {
    "no_https": ("HTTPS migration", "move the site to HTTPS properly (cert, redirects, mixed-content fixes)"),
    "slow": ("speed optimization", "diagnose what's slow and fix the biggest wins"),
    "no_title": ("SEO basics fix", "title tags, meta descriptions, and search fundamentals"),
    "no_meta_description": ("SEO basics fix", "title tags, meta descriptions, and search fundamentals"),
    "not_mobile_friendly": ("mobile-friendly pass", "viewport, responsive issues, and mobile usability"),
    "server_error": ("site triage", "diagnose the server errors and stabilize the site"),
    "cert_expiring": ("certificate renewal", "renew and install the SSL certificate before it lapses"),
}

FINDING_LINES = {
    "no_https": "your site still serves pages over plain HTTP — browsers flag that as \"not secure\" to visitors",
    "slow": "your homepage took {latency_s}s to load in my check just now (most visitors leave after 3s)",
    "no_title": "your homepage has no <title> tag, so search results show a bare URL instead of your business name",
    "no_meta_description": "your homepage has no meta description, so Google writes your search snippet for you",
    "not_mobile_friendly": "your pages have no mobile viewport tag — over half of web traffic is mobile",
    "server_error": "your site returned a server error ({status}) when I checked it just now",
    "cert_expiring": "your SSL certificate expires in {cert_days_left} days — when it lapses, visitors get a full-page warning",
}


def draft_pitch(lead: dict, sender: SenderIdentity) -> dict:
    """Build a pitch from a prospector lead. Raises if the lead has no
    measurable issue or the sender identity is incomplete."""
    if not sender.reply_email or not sender.postal_address:
        raise ValueError("sender reply_email and postal_address are required (CAN-SPAM)")
    issues = [i for i in lead.get("issues", []) if i in SERVICE_MENU]
    if not issues:
        raise ValueError("lead has no pitchable issue — do not invent one")
    issue = issues[0]
    notes = lead.get("notes", {})
    finding = FINDING_LINES[issue].format(**{k: v for k, v in notes.items()
                                             if k in ("latency_s", "status", "cert_days_left")})
    service, scope = SERVICE_MENU[issue]
    domain = lead["domain"]

    subject = f"Quick find on {domain} — {service.lower()}"
    body = f"""Hi there,

I'm {sender.agent_name}, an AI agent that audits small-business websites for
fixable problems. I'm writing because I measured something specific on {domain}:

  {finding}.

I can take care of this — {scope} — for a flat ${sender.base_price_usd:.0f}, done
properly and verified afterward. If that's useful, reply and I'll send a
payment link plus a checklist of exactly what I'll do.

To be upfront: I'm an AI agent operated by {sender.operator_name} at
{sender.business_name}. A human reviews everything before it's delivered.

If you'd rather not hear from me again, reply "unsubscribe" and I won't
write back.

—
{sender.agent_name} (AI agent) · {sender.business_name}
{sender.reply_email}
{sender.postal_address}
"""
    return {"to_domain": domain, "subject": subject, "body": body.strip(),
            "issue": issue, "service": service,
            "suggested_price_usd": sender.base_price_usd}
