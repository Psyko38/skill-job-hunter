"""LinkedIn public guest jobs scraper (no login required)."""

from __future__ import annotations

import re
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from ..filters import is_contract_compatible
from ..models import ContractType, JobOffer, ScrapeResult
from .base import (
    DEFAULT_HTTP_HEADERS,
    BaseScraper,
    build_offer,
    extract_jsonld_job_posting,
    is_antibot_response,
    parse_jsonld_fields,
    polite_sleep,
)

_LI_SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
_LI_DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

# LinkedIn job type codes (`f_JT`):
# I = Internship, T = Temporary, C = Contract, F = Full-time, P = Part-time, O = Other.
# Alternance has no dedicated LinkedIn f_JT code, so we restrict to I,T,C (excluding Full-time CDI noise)
# while keeping "alternance" in keywords.
_CONTRACT_MAP: dict[ContractType, str] = {
    "stage": "I",
    "alternance": "I,T,C",
    "cdd": "C,T",
    "cdi": "F",
}


def build_linkedin_search_url(query: str, contract: ContractType, location: str, remote: bool) -> str:
    """Build a LinkedIn public guest search URL with f_JT contract filtering."""
    keywords = query
    if contract == "alternance" and "alternance" not in query.lower():
        keywords = f"{query} alternance"
    elif contract == "stage" and "stage" not in query.lower():
        keywords = f"{query} stage"

    url = f"{_LI_SEARCH_URL}?keywords={quote_plus(keywords)}&start=0"
    if location:
        url += f"&location={quote_plus(location)}"
    jt = _CONTRACT_MAP[contract]
    if jt:
        url += f"&f_JT={quote_plus(jt)}"
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
        time_el = card.select_one("time[datetime]")
        if not link_el or not title_el or not company_el:
            continue

        url = str(link_el.get("href", "")).split("?", 1)[0].strip()
        match = re.search(r"-(\d+)(?:/)?$", url) or re.search(r"/jobs/view/(\d+)", url)
        job_id = match.group(1) if match else ""
        title = title_el.get_text(" ", strip=True)
        company = company_el.get_text(" ", strip=True)
        loc = loc_el.get_text(" ", strip=True) if loc_el else ""
        published_at = str(time_el.get("datetime", "")).strip() if time_el else ""

        offer = build_offer(
            source="linkedin",
            title=title,
            company=company,
            location=loc,
            contract_type=contract,
            url=url,
            published_at=published_at,
        )
        results.append((job_id, offer))
        if len(results) >= limit:
            break

    return results


def parse_linkedin_detail_html(html: str) -> tuple[str, str]:
    """Return (description_text, external_apply_url) from a LinkedIn public job detail fragment."""
    soup = BeautifulSoup(html, "lxml")
    desc_el = soup.select_one(
        "div.show-more-less-html__markup, div.description__text, section.description div.show-more-less-html"
    )
    description = desc_el.get_text("\n", strip=True) if desc_el else ""
    if not description:
        fields = parse_jsonld_fields(extract_jsonld_job_posting(html))
        description = fields.get("description", "")

    apply_el = soup.select_one("a.apply-button, a[data-tracking-control-name*='apply']")
    apply_url = str(apply_el.get("href", "")).strip() if apply_el else ""
    if "/signup/" in apply_url.lower() or "cold-join" in apply_url.lower():
        apply_url = ""
    return description, apply_url


def _get_with_backoff(
    client: httpx.Client,
    url: str,
    *,
    max_retries: int = 2,
) -> httpx.Response:
    """GET a LinkedIn guest URL, retrying HTTP 429 with Retry-After / exponential backoff."""
    resp = client.get(url)
    for attempt in range(max_retries):
        if resp.status_code != 429:
            return resp
        retry_after_raw = resp.headers.get("retry-after", "").strip()
        if retry_after_raw.isdigit():
            delay = min(15.0, max(3.0, float(retry_after_raw)))
            polite_sleep(delay, delay + 1.0)
        else:
            base_delay = 4.0 * (attempt + 1)
            polite_sleep(base_delay, base_delay + 2.5)
        resp = client.get(url)
    return resp


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
                resp = _get_with_backoff(client, search_url, max_retries=2)
                if is_antibot_response(resp.status_code, resp.text, str(resp.url)):
                    return ScrapeResult(
                        source=self.source_name,
                        status="blocked_antibot",
                        error_message=f"Blocked on guest search endpoint (HTTP {resp.status_code})",
                    )

                # Parse up to 25 cards from the single search response, then keep only
                # contract-compatible titles before burning detail HTTP requests.
                raw_cards = parse_linkedin_search_cards(
                    resp.text,
                    contract=contract,
                    limit=max(25, limit * 2),
                )
                compatible_cards = [
                    (job_id, partial)
                    for job_id, partial in raw_cards
                    if is_contract_compatible(partial, contract)
                ][:limit]

                consecutive_429 = 0
                detail_429_count = 0
                for job_id, partial in compatible_cards:
                    if not job_id or consecutive_429 >= 2:
                        if consecutive_429 >= 2:
                            detail_429_count += 1
                        offers.append(partial)
                        continue

                    polite_sleep(1.8, 3.2)
                    try:
                        d_resp = _get_with_backoff(
                            client,
                            _LI_DETAIL_URL.format(job_id=job_id),
                            max_retries=2,
                        )
                    except httpx.HTTPError:
                        offers.append(partial)
                        continue

                    if d_resp.status_code == 429:
                        consecutive_429 += 1
                        detail_429_count += 1
                        offers.append(partial)
                        continue

                    if is_antibot_response(d_resp.status_code, d_resp.text, str(d_resp.url)):
                        offers.append(partial)
                        continue

                    consecutive_429 = 0
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
                            published_at=partial.published_at,
                        )
                    )

            warning = (
                f"Rate-limited (HTTP 429) on {detail_429_count}/{len(compatible_cards)} detail pages; "
                "some descriptions may be empty"
                if detail_429_count
                else ""
            )
            return ScrapeResult(
                source=self.source_name,
                status="ok",
                offers=offers,
                error_message=warning,
            )
        except Exception as exc:  # noqa: BLE001
            return ScrapeResult(
                source=self.source_name,
                status="error",
                offers=offers,
                error_message=str(exc),
            )
