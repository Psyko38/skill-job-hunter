"""Extract and prioritize recruiter contact emails from offers and company websites."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .http_headers import DEFAULT_HTTP_HEADERS
from .models import EnrichedOffer, JobOffer

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

_IGNORED_LOCAL_PREFIXES = (
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "bounce",
    "mailer-daemon",
    "privacy",
    "dpo",
    "rgpd",
    "abuse",
    "postmaster",
    "webmaster",
    "support",
)

_IGNORED_DOMAINS = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "sentry.io",
        "welcometothejungle.com",
        "wttj.co",
        "hellowork.com",
        "indeed.com",
        "indeedemail.com",
        "linkedin.com",
        "schema.org",
        "w3.org",
    }
)

_PRIORITY_LOCAL_PARTS = (
    "recrutement",
    "recruitment",
    "talent",
    "talents",
    "jobs",
    "job",
    "careers",
    "career",
    "carriere",
    "carrieres",
    "rh",
    "hr",
    "hiring",
    "bonjour",
    "hello",
    "contact",
)

_CANDIDATE_PATHS = ("/", "/recrutement", "/careers", "/jobs", "/contact", "/mentions-legales")


def is_valid_recruiter_email(email: str) -> bool:
    """Filter out automated, placeholder, image-like, or jobboard emails."""
    cleaned = email.strip().lower().rstrip(".")
    if "@" not in cleaned or cleaned.endswith((".png", ".jpg", ".jpeg", ".svg", ".webp")):
        return False
    local, domain = cleaned.rsplit("@", 1)
    if any(local.startswith(prefix) for prefix in _IGNORED_LOCAL_PREFIXES):
        return False
    if any(domain == bad or domain.endswith(f".{bad}") for bad in _IGNORED_DOMAINS):
        return False
    return "." in domain and len(domain) >= 4


def extract_emails_from_text(text: str) -> list[str]:
    """Return deduplicated, valid recruiter email candidates found in plain text."""
    seen: list[str] = []
    for match in _EMAIL_RE.findall(text or ""):
        lowered = match.lower().rstrip(".")
        if is_valid_recruiter_email(lowered) and lowered not in seen:
            seen.append(lowered)
    return seen


def extract_emails_from_html(html: str) -> list[str]:
    """Extract emails from mailto links and visible HTML text."""
    soup = BeautifulSoup(html, "lxml")
    candidates: list[str] = []

    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if href.lower().startswith("mailto:"):
            raw_addr = href[7:].split("?", 1)[0].strip().lower()
            if is_valid_recruiter_email(raw_addr) and raw_addr not in candidates:
                candidates.append(raw_addr)

    for email in extract_emails_from_text(soup.get_text(" ", strip=True)):
        if email not in candidates:
            candidates.append(email)

    return candidates


def rank_emails(emails: list[str], preferred_domain: str = "") -> list[str]:
    """Sort candidate emails so HR/recruitment addresses on the company domain come first."""
    clean_pref = preferred_domain.lower().removeprefix("www.")

    def _score(addr: str) -> tuple[int, int, str]:
        local, domain = addr.rsplit("@", 1)
        domain_penalty = 0 if (not clean_pref or domain == clean_pref or domain.endswith(f".{clean_pref}")) else 1
        local_rank = len(_PRIORITY_LOCAL_PARTS)
        for idx, prefix in enumerate(_PRIORITY_LOCAL_PARTS):
            if local == prefix or local.startswith(f"{prefix}.") or local.startswith(f"{prefix}-"):
                local_rank = idx
                break
        return (domain_penalty, local_rank, addr)

    return sorted(set(emails), key=_score)


def _fetch_company_site_emails(
    company_url: str,
    client: httpx.Client,
    max_pages: int = 4,
) -> tuple[str, str]:
    """Crawl up to max_pages public pages on company_url to find a recruiter email."""
    parsed = urlparse(company_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "", "none"

    base_url = f"{parsed.scheme}://{parsed.netloc}"
    visited = 0
    for path in _CANDIDATE_PATHS:
        if visited >= max_pages:
            break
        target_url = urljoin(base_url, path)
        visited += 1
        try:
            response = client.get(target_url)
            if response.status_code != 200:
                continue
            content_type = response.headers.get("content-type", "")
            if "html" not in content_type and "text" not in content_type:
                continue
            found = extract_emails_from_html(response.text)
            if found:
                best = rank_emails(found, parsed.netloc)[0]
                return best, f"company_site:{target_url}"
        except httpx.HTTPError:
            continue

    return "", "none"


def enrich_offer_contact(
    offer: JobOffer,
    client: httpx.Client | None = None,
) -> EnrichedOffer:
    """Resolve the best contact email for a validated offer."""
    direct = list(offer.emails_found)
    for found in extract_emails_from_text(offer.description):
        if found not in direct:
            direct.append(found)
    direct = [e for e in direct if is_valid_recruiter_email(e)]

    company_host = urlparse(offer.company_url).netloc if offer.company_url else ""
    if direct:
        best = rank_emails(direct, company_host)[0]
        return EnrichedOffer(offer=offer, contact_email=best, email_source="offer_text")

    if offer.company_url:
        if client is not None:
            best, source = _fetch_company_site_emails(offer.company_url, client)
        else:
            with httpx.Client(
                timeout=5.0,
                follow_redirects=True,
                headers=DEFAULT_HTTP_HEADERS,
            ) as own_client:
                best, source = _fetch_company_site_emails(offer.company_url, own_client)
        if best:
            return EnrichedOffer(offer=offer, contact_email=best, email_source=source)

    return EnrichedOffer(offer=offer, contact_email="", email_source="none")
