"""LinkedIn public guest jobs scraper (no login required)."""

from __future__ import annotations

import re
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from ..models import ContractType, JobOffer, ScrapeResult
from .base import DEFAULT_HTTP_HEADERS, BaseScraper, build_offer, is_antibot_response, polite_sleep

_LI_SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
_LI_DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
_CONTRACT_MAP: dict[ContractType, str] = {
    "stage": "I",
    "alternance": "",
    "cdd": "C",
    "cdi": "F",
}


def build_linkedin_search_url(query: str, contract: ContractType, location: str, remote: bool) -> str:
    """Build a LinkedIn public guest search URL."""
    keywords = query
    if contract == "alternance" and "alternance" not in query.lower():
        keywords = f"{query} alternance"
    url = f"{_LI_SEARCH_URL}?keywords={quote_plus(keywords)}&start=0"
    if location:
        url += f"&location={quote_plus(location)}"
    jt = _CONTRACT_MAP[contract]
    if jt:
        url += f"&f_JT={jt}"
    if remote:
        url += "&f_WT=2"
    return url


def parse_linkedin_search_cards(
    html: str,
    *,
    contract: ContractType,
    limit: int,
) -> list[tuple[str, JobOffer]]:
    """Parse LinkedIn guest search HTML into (job_id, partial JobOffer) tuples."""
    soup = BeautifulSoup(html, "lxml")
    results: list[tuple[str, JobOffer]] = []

    for card in soup.select("li"):
        link_el = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
        title_el = card.select_one("h3.base-search-card__title")
        company_el = card.select_one("h4.base-search-card__subtitle")
        loc_el = card.select_one("span.job-search-card__location")
        if not link_el or not title_el or not company_el:
            continue

        url = str(link_el.get("href", "")).split("?", 1)[0].strip()
        match = re.search(r"-(\d+)(?:/)?$", url) or re.search(r"/jobs/view/(\d+)", url)
        job_id = match.group(1) if match else ""
        title = title_el.get_text(" ", strip=True)
        company = company_el.get_text(" ", strip=True)
        loc = loc_el.get_text(" ", strip=True) if loc_el else ""

        offer = build_offer(
            source="linkedin",
            title=title,
            company=company,
            location=loc,
            contract_type=contract,
            url=url,
        )
        results.append((job_id, offer))
        if len(results) >= limit:
            break

    return results


def parse_linkedin_detail_html(html: str) -> tuple[str, str]:
    """Return (description_text, external_apply_url) from a LinkedIn public job detail fragment."""
    soup = BeautifulSoup(html, "lxml")
    desc_el = soup.select_one("div.show-more-less-html__markup, div.description__text")
    description = desc_el.get_text("\n", strip=True) if desc_el else ""

    apply_el = soup.select_one("a.apply-button, a[data-tracking-control-name*='apply']")
    apply_url = str(apply_el.get("href", "")).strip() if apply_el else ""
    if "/signup/" in apply_url.lower() or "cold-join" in apply_url.lower():
        apply_url = ""
    return description, apply_url


class LinkedInScraper(BaseScraper):
    """HTTP scraper for LinkedIn public guest job endpoints."""

    source_name = "linkedin"

    def search(
        self,
        *,
        query: str,
        contract: ContractType,
        location: str,
        remote: bool,
        limit: int,
    ) -> ScrapeResult:
        search_url = build_linkedin_search_url(query, contract, location, remote)
        offers: list[JobOffer] = []
        headers = dict(DEFAULT_HTTP_HEADERS)
        try:
            with httpx.Client(timeout=12.0, follow_redirects=True, headers=headers) as client:
                resp = client.get(search_url)
                if resp.status_code == 429:
                    polite_sleep(3.0, 4.5)
                    resp = client.get(search_url)
                if is_antibot_response(resp.status_code, resp.text, str(resp.url)):
                    return ScrapeResult(
                        source=self.source_name,
                        status="blocked_antibot",
                        error_message=f"Blocked on guest search endpoint (HTTP {resp.status_code})",
                    )

                cards = parse_linkedin_search_cards(resp.text, contract=contract, limit=limit)
                rate_limited = False
                for job_id, partial in cards:
                    if not job_id or rate_limited:
                        offers.append(partial)
                        continue
                    polite_sleep(1.0, 2.0)
                    d_resp = client.get(_LI_DETAIL_URL.format(job_id=job_id))
                    if d_resp.status_code == 429:
                        rate_limited = True
                        offers.append(partial)
                        continue
                    if is_antibot_response(d_resp.status_code, d_resp.text, str(d_resp.url)):
                        offers.append(partial)
                        continue
                    desc, apply_url = parse_linkedin_detail_html(d_resp.text)
                    offers.append(
                        build_offer(
                            source="linkedin",
                            title=partial.title,
                            company=partial.company,
                            location=partial.location,
                            contract_type=contract,
                            url=partial.url,
                            apply_url=apply_url or partial.url,
                            description=desc,
                        )
                    )
            return ScrapeResult(source=self.source_name, status="ok", offers=offers)
        except Exception as exc:  # noqa: BLE001
            return ScrapeResult(
                source=self.source_name,
                status="error",
                offers=offers,
                error_message=str(exc),
            )
