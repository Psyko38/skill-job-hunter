"""Extract and prioritize recruiter contact emails from offers and company websites safely."""

from __future__ import annotations

import re
from urllib.parse import unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .http_headers import DEFAULT_HTTP_HEADERS
from .models import ApplyChannel, EnrichedOffer, JobOffer
from .security import is_safe_public_url, is_valid_email_syntax, safe_http_get

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Common human-readable obfuscations like "recrutement [at] acme [dot] fr" or "jobs (at) acme.com"
_OBFUSCATED_EMAIL_RE = re.compile(
    r"\b([A-Za-z0-9._%+-]{2,64})\s*(?:\[\s*(?:at|arobase)\s*\]|\(\s*(?:at|arobase)\s*\))\s*"
    r"([A-Za-z0-9-]+(?:\s*(?:\[\s*(?:dot|point)\s*\]|\(\s*(?:dot|point)\s*\)|\.)\s*[A-Za-z0-9-]+)+)\b",
    re.IGNORECASE,
)

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
    "security",
    "billing",
    "facturation",
    "compta",
    "comptabilite",
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
        "googleusercontent.com",
        "cloudflare.com",
    }
)

_NON_COMPANY_CRAWL_DOMAINS = frozenset(
    {
        "welcometothejungle.com",
        "wttj.co",
        "hellowork.com",
        "indeed.com",
        "linkedin.com",
        "facebook.com",
        "instagram.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "tiktok.com",
        "glassdoor.com",
        "glassdoor.fr",
        "apec.fr",
        "francetravail.fr",
        "pole-emploi.fr",
    }
)

_ATS_DOMAINS = frozenset(
    {
        "greenhouse.io",
        "boards.greenhouse.io",
        "lever.co",
        "jobs.lever.co",
        "ashbyhq.com",
        "jobs.ashbyhq.com",
        "workable.com",
        "apply.workable.com",
        "smartrecruiters.com",
        "jobs.smartrecruiters.com",
        "teamtailor.com",
        "welcomekit.co",
        "taleez.com",
        "flatchr.io",
        "myworkdayjobs.com",
        "bamboohr.com",
        "recruitee.com",
        "jobvite.com",
        "breezy.hr",
        "personio.de",
        "personio.com",
        "comeet.co",
        "icims.com",
        "softy.pro",
        "beetween.com",
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

_CANDIDATE_PATHS = (
    "/",
    "/recrutement",
    "/careers",
    "/jobs",
    "/nous-rejoindre",
    "/contact",
    "/mentions-legales",
)

_CAREER_LINK_KEYWORDS = (
    "recrut",
    "career",
    "carriere",
    "job",
    "rejoindre",
    "contact",
    "mentions",
)


def _domain_matches(domain: str, targets: frozenset[str] | set[str]) -> bool:
    clean = domain.strip().lower().removeprefix("www.")
    return any(clean == target or clean.endswith(f".{target}") for target in targets)


def is_valid_recruiter_email(
    email: str,
    *,
    extra_blocked_domains: set[str] | None = None,
) -> bool:
    """Filter out invalid, automated, placeholder, image-like, jobboard, or blacklisted emails."""
    cleaned = email.strip().lower().rstrip(".")
    if not is_valid_email_syntax(cleaned):
        return False
    if cleaned.endswith((".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".css", ".js")):
        return False
    local, domain = cleaned.rsplit("@", 1)
    if any(local.startswith(prefix) for prefix in _IGNORED_LOCAL_PREFIXES):
        return False
    if _domain_matches(domain, _IGNORED_DOMAINS):
        return False
    if extra_blocked_domains and _domain_matches(domain, extra_blocked_domains):
        return False
    return "." in domain and len(domain) >= 4


def _deobfuscate_emails_in_text(text: str) -> list[str]:
    """Extract emails written with simple plain-text obfuscations like 'jobs [at] acme [dot] fr'."""
    results: list[str] = []
    for local, raw_domain in _OBFUSCATED_EMAIL_RE.findall(text or ""):
        domain = re.sub(
            r"\s*(?:\[\s*(?:dot|point)\s*\]|\(\s*(?:dot|point)\s*\))\s*",
            ".",
            raw_domain,
            flags=re.IGNORECASE,
        )
        domain = re.sub(r"\s*\.\s*", ".", domain).strip(".")
        candidate = f"{local.strip()}@{domain}".lower()
        if candidate not in results:
            results.append(candidate)
    return results


def extract_emails_from_text(
    text: str,
    *,
    extra_blocked_domains: set[str] | None = None,
) -> list[str]:
    """Return deduplicated, valid recruiter email candidates found in plain text."""
    seen: list[str] = []
    for match in _EMAIL_RE.findall(text or ""):
        lowered = match.lower().rstrip(".")
        if is_valid_recruiter_email(lowered, extra_blocked_domains=extra_blocked_domains) and lowered not in seen:
            seen.append(lowered)

    for candidate in _deobfuscate_emails_in_text(text or ""):
        if is_valid_recruiter_email(candidate, extra_blocked_domains=extra_blocked_domains) and candidate not in seen:
            seen.append(candidate)

    return seen


def extract_emails_from_html(
    html: str,
    *,
    extra_blocked_domains: set[str] | None = None,
) -> list[str]:
    """Extract emails from mailto links (with URL-decoding) and visible HTML text."""
    soup = BeautifulSoup(html, "lxml")
    candidates: list[str] = []

    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if href.lower().startswith("mailto:"):
            raw_addr = unquote(href[7:].split("?", 1)[0]).strip().lower()
            if (
                is_valid_recruiter_email(raw_addr, extra_blocked_domains=extra_blocked_domains)
                and raw_addr not in candidates
            ):
                candidates.append(raw_addr)

    for email in extract_emails_from_text(
        soup.get_text(" ", strip=True),
        extra_blocked_domains=extra_blocked_domains,
    ):
        if email not in candidates:
            candidates.append(email)

    return candidates


def _discover_same_origin_links(html: str, base_url: str, max_links: int = 3) -> list[str]:
    """Find same-origin career/contact page links on a company homepage."""
    base_host = (urlparse(base_url).hostname or "").lower().removeprefix("www.")
    if not base_host:
        return []
    soup = BeautifulSoup(html, "lxml")
    discovered: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(base_url, href).split("#", 1)[0]
        parsed = urlparse(full)
        if parsed.scheme not in {"http", "https"}:
            continue
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if host != base_host:
            continue
        label = f"{parsed.path} {anchor.get_text(' ', strip=True)}".lower()
        if any(kw in label for kw in _CAREER_LINK_KEYWORDS):
            if full not in discovered:
                discovered.append(full)
                if len(discovered) >= max_links:
                    break
    return discovered


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


def detect_apply_channel(apply_url: str, contact_email: str) -> ApplyChannel:
    """Classify the primary application channel: direct email, external ATS, or jobboard."""
    if contact_email:
        return "email"
    host = (urlparse(apply_url or "").hostname or "").lower().removeprefix("www.")
    if host and _domain_matches(host, _ATS_DOMAINS):
        return "ats"
    return "jobboard"


def _fetch_company_site_emails(
    company_url: str,
    client: httpx.Client,
    *,
    max_pages: int = 7,
    resolve_dns: bool = True,
    extra_blocked_domains: set[str] | None = None,
) -> tuple[str, str]:
    """Safely crawl up to max_pages public pages on company_url to find a recruiter email."""
    if not is_safe_public_url(company_url, resolve_dns=resolve_dns):
        return "", "none"

    parsed = urlparse(company_url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if not host or _domain_matches(host, _NON_COMPANY_CRAWL_DOMAINS):
        return "", "none"
    if extra_blocked_domains and _domain_matches(host, extra_blocked_domains):
        return "", "none"

    base_url = f"{parsed.scheme}://{parsed.netloc}"
    queue: list[str] = [urljoin(base_url, path) for path in _CANDIDATE_PATHS]
    seen_urls: set[str] = set()
    visited = 0

    while queue and visited < max_pages:
        target_url = queue.pop(0)
        if target_url in seen_urls:
            continue
        seen_urls.add(target_url)
        visited += 1

        fetched = safe_http_get(
            client,
            target_url,
            resolve_dns=resolve_dns,
        )
        if fetched is None:
            continue
        status, final_url, content_type, html = fetched
        if status != 200:
            continue
        if "html" not in content_type.lower() and "text" not in content_type.lower():
            continue

        found = extract_emails_from_html(html, extra_blocked_domains=extra_blocked_domains)
        if found:
            best = rank_emails(found, host)[0]
            return best, f"company_site:{final_url}"

        # On homepage, dynamically prioritize real same-origin career/contact links
        if visited == 1:
            discovered = _discover_same_origin_links(html, final_url, max_links=3)
            for link in reversed(discovered):
                if link not in seen_urls and link not in queue:
                    queue.insert(0, link)

    return "", "none"


def enrich_offer_contact(
    offer: JobOffer,
    client: httpx.Client | None = None,
    *,
    domain_cache: dict[str, tuple[str, str]] | None = None,
    extra_blocked_domains: set[str] | None = None,
) -> EnrichedOffer:
    """Resolve the best contact email and apply channel for a validated offer."""
    direct = list(offer.emails_found)
    for found in extract_emails_from_text(
        offer.description,
        extra_blocked_domains=extra_blocked_domains,
    ):
        if found not in direct:
            direct.append(found)
    direct = [
        e for e in direct if is_valid_recruiter_email(e, extra_blocked_domains=extra_blocked_domains)
    ]

    company_host = (urlparse(offer.company_url).hostname or "") if offer.company_url else ""
    if direct:
        best = rank_emails(direct, company_host)[0]
        return EnrichedOffer(
            offer=offer,
            contact_email=best,
            email_source="offer_text",
            apply_channel="email",
        )

    # Determine candidate company URL (fallback to non-jobboard/non-ATS apply_url if company_url missing)
    crawl_url = offer.company_url
    if not crawl_url and offer.apply_url:
        apply_host = (urlparse(offer.apply_url).hostname or "").lower().removeprefix("www.")
        if (
            apply_host
            and not _domain_matches(apply_host, _NON_COMPANY_CRAWL_DOMAINS)
            and not _domain_matches(apply_host, _ATS_DOMAINS)
        ):
            parsed_apply = urlparse(offer.apply_url)
            crawl_url = f"{parsed_apply.scheme}://{parsed_apply.netloc}"

    if crawl_url:
        cache_key = (urlparse(crawl_url).hostname or "").lower().removeprefix("www.")
        if domain_cache is not None and cache_key and cache_key in domain_cache:
            best, source = domain_cache[cache_key]
        else:
            if client is not None:
                is_mock = isinstance(getattr(client, "_transport", None), httpx.MockTransport)
                best, source = _fetch_company_site_emails(
                    crawl_url,
                    client,
                    resolve_dns=not is_mock,
                    extra_blocked_domains=extra_blocked_domains,
                )
            else:
                with httpx.Client(
                    timeout=5.0,
                    follow_redirects=False,
                    headers=DEFAULT_HTTP_HEADERS,
                ) as own_client:
                    best, source = _fetch_company_site_emails(
                        crawl_url,
                        own_client,
                        resolve_dns=True,
                        extra_blocked_domains=extra_blocked_domains,
                    )
            if domain_cache is not None and cache_key:
                domain_cache[cache_key] = (best, source)

        if best:
            return EnrichedOffer(
                offer=offer,
                contact_email=best,
                email_source=source,
                apply_channel="email",
            )

    channel = detect_apply_channel(offer.apply_url or offer.url, "")
    return EnrichedOffer(
        offer=offer,
        contact_email="",
        email_source="none",
        apply_channel=channel,
    )
