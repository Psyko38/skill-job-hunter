"""Shared scraper primitives: Playwright lifecycle, JSON-LD extraction, anti-bot detection."""

from __future__ import annotations

import json
import random
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Iterator

from bs4 import BeautifulSoup
from playwright.sync_api import BrowserContext, Error as PlaywrightError, Page, sync_playwright

from ..contact_finder import extract_emails_from_text
from ..filters import detect_language
from ..http_headers import DEFAULT_HTTP_HEADERS, DEFAULT_USER_AGENT
from ..models import ContractType, JobOffer, ScrapeResult
from ..text_utils import make_offer_id

_ANTIBOT_MARKERS = (
    "cf-browser-verification",
    "just a moment...",
    "attention required! | cloudflare",
    "enable javascript and cookies to continue",
    "please verify you are a human",
    "captcha",
    "hcaptcha",
    "recaptcha",
    "datadome",
    "/authwall",
    "authwall",
)


def is_antibot_response(status_code: int | None, html: str = "", url: str = "") -> bool:
    """Detect HTTP 403/429 or anti-bot/login-wall pages without attempting any bypass."""
    if status_code in (403, 429, 503):
        return True
    haystack = f"{url}\n{html[:12000]}".lower()
    return any(marker in haystack for marker in _ANTIBOT_MARKERS)


def polite_sleep(min_s: float = 0.8, max_s: float = 1.8) -> None:
    """Sleep briefly between requests to avoid hammering jobboards."""
    time.sleep(random.uniform(min_s, max_s))


def extract_jsonld_job_posting(html: str) -> dict[str, Any]:
    """Extract schema.org JobPosting JSON-LD object from an HTML page if present."""
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = item.get("@type")
            if item_type == "JobPosting" or (
                isinstance(item_type, list) and "JobPosting" in item_type
            ):
                return item
            if "@graph" in item and isinstance(item["@graph"], list):
                for node in item["@graph"]:
                    if isinstance(node, dict) and node.get("@type") == "JobPosting":
                        return node
    return {}


def html_to_clean_text(raw_html: str) -> str:
    """Convert an HTML fragment (e.g., JSON-LD description) to readable plain text."""
    if not raw_html:
        return ""
    return BeautifulSoup(raw_html, "lxml").get_text("\n", strip=True)


def parse_jsonld_fields(posting: dict[str, Any]) -> dict[str, str]:
    """Extract normalized text fields from a schema.org JobPosting dictionary."""
    if not posting:
        return {}
    org = posting.get("hiringOrganization") or {}
    if isinstance(org, list):
        org = org[0] if org else {}
    company = str(org.get("name", "")).strip() if isinstance(org, dict) else ""
    company_url = str(org.get("sameAs") or org.get("url") or "").strip() if isinstance(org, dict) else ""

    loc = posting.get("jobLocation") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    address = loc.get("address", {}) if isinstance(loc, dict) else {}
    if isinstance(address, dict):
        city = address.get("addressLocality", "")
        region = address.get("addressRegion", "")
        location = ", ".join(part for part in (city, region) if part)
    else:
        location = ""

    salary_obj = posting.get("baseSalary") or {}
    salary = ""
    if isinstance(salary_obj, dict):
        val = salary_obj.get("value")
        currency = salary_obj.get("currency", "EUR")
        if isinstance(val, dict):
            min_v, max_v = val.get("minValue", ""), val.get("maxValue", "")
            unit = val.get("unitText", "")
            if min_v or max_v:
                salary = f"{min_v}-{max_v} {currency}/{unit}".strip("-/ ")
        elif val:
            salary = f"{val} {currency}"

    return {
        "title": str(posting.get("title", "")).strip(),
        "company": company,
        "company_url": company_url,
        "location": location,
        "description": html_to_clean_text(str(posting.get("description", ""))),
        "published_at": str(posting.get("datePosted", "")).strip(),
        "salary": salary,
    }


def build_offer(
    *,
    source: str,
    title: str,
    company: str,
    location: str,
    contract_type: ContractType,
    url: str,
    apply_url: str = "",
    company_url: str = "",
    remote: str = "",
    description: str = "",
    published_at: str = "",
    salary: str = "",
) -> JobOffer:
    """Create a normalized JobOffer with detected language and extracted emails."""
    combined = f"{title}\n{description}"
    return JobOffer(
        id=make_offer_id(source, url, company, title),
        source=source,
        title=title.strip(),
        company=company.strip(),
        location=location.strip(),
        contract_type=contract_type,
        url=url.strip(),
        apply_url=(apply_url or url).strip(),
        company_url=company_url.strip(),
        remote=remote.strip(),
        description=description.strip(),
        published_at=published_at.strip(),
        salary=salary.strip(),
        language=detect_language(combined),
        emails_found=extract_emails_from_text(description),
    )


@contextmanager
def playwright_context() -> Iterator[BrowserContext]:
    """Provide a configured headless Chromium context for scraping."""
    extra_headers = {k: v for k, v in DEFAULT_HTTP_HEADERS.items() if k != "User-Agent"}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=DEFAULT_USER_AGENT,
            extra_http_headers=extra_headers,
            locale="fr-FR",
            viewport={"width": 1280, "height": 900},
        )
        try:
            yield context
        finally:
            context.close()
            browser.close()


def fetch_page_html(page: Page, url: str, wait_ms: int = 1200) -> tuple[int | None, str, str]:
    """Navigate with Playwright and return (status_code, final_url, html)."""
    response = page.goto(url, wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(wait_ms)
    status = response.status if response else None
    for attempt in range(3):
        try:
            html = page.content()
            return status, page.url, html
        except PlaywrightError:
            if attempt == 2:
                raise
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            page.wait_for_timeout(500)
    return status, page.url, ""


class BaseScraper(ABC):
    """Abstract base class for all jobboard scrapers."""

    source_name: str = ""

    @abstractmethod
    def search(
        self,
        *,
        query: str,
        contract: ContractType,
        location: str,
        remote: bool,
        limit: int,
    ) -> ScrapeResult:
        """Execute a search and return normalized offers or a clean failure status."""
