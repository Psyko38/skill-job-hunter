"""Indeed France jobboard scraper."""

from __future__ import annotations

from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from ..models import ContractType, JobOffer, ScrapeResult
from .base import (
    BaseScraper,
    build_offer,
    extract_jsonld_job_posting,
    fetch_page_html,
    html_to_clean_text,
    is_antibot_response,
    playwright_context,
    polite_sleep,
)

_INDEED_BASE = "https://fr.indeed.com"
_CONTRACT_MAP: dict[ContractType, str] = {
    "stage": "internship",
    "alternance": "apprenticeship",
    "cdd": "contract",
    "cdi": "fulltime",
}


def build_indeed_search_url(query: str, contract: ContractType, location: str, remote: bool) -> str:
    """Build an Indeed France search URL."""
    q = f"{query} alternance" if contract == "alternance" and "alternance" not in query.lower() else query
    url = f"{_INDEED_BASE}/jobs?q={quote_plus(q)}&jt={_CONTRACT_MAP[contract]}"
    if location:
        url += f"&l={quote_plus(location)}"
    if remote:
        url += "&sc=0kf%3Aattr%28DSQF7%29%3B"
    return url


def parse_indeed_search_cards(
    html: str,
    *,
    contract: ContractType,
    limit: int,
    fallback_location: str = "",
) -> list[JobOffer]:
    """Parse summary job cards from an Indeed search results page."""
    soup = BeautifulSoup(html, "lxml")
    offers: list[JobOffer] = []

    cards = soup.select("div.job_seen_beacon, li.css-5lfssm, td.resultContent")
    seen_urls: set[str] = set()
    for card in cards:
        link_el = card.select_one("a[data-jk], h2.jobTitle a")
        if not link_el:
            continue
        jk = str(link_el.get("data-jk", "")).strip()
        href = str(link_el.get("href", "")).strip()
        url = f"{_INDEED_BASE}/viewjob?jk={jk}" if jk else urljoin(_INDEED_BASE, href)
        if url in seen_urls:
            continue
        title = link_el.get_text(" ", strip=True)

        company_el = card.select_one('[data-testid="company-name"], span.companyName')
        company = company_el.get_text(" ", strip=True) if company_el else ""

        loc_el = card.select_one('[data-testid="text-location"], div.companyLocation')
        loc = loc_el.get_text(" ", strip=True) if loc_el else fallback_location

        snippet_el = card.select_one(".job-snippet, div.underShelfFooter")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""

        if not title or not company or not url:
            continue

        seen_urls.add(url)
        offers.append(
            build_offer(
                source="indeed",
                title=title,
                company=company,
                location=loc,
                contract_type=contract,
                url=url,
                description=snippet,
            )
        )
        if len(offers) >= limit:
            break

    return offers


def parse_indeed_detail_description(html: str) -> str:
    """Extract full job description text from an Indeed viewjob page."""
    soup = BeautifulSoup(html, "lxml")
    desc_el = soup.select_one("#jobDescriptionText, div.jobsearch-jobDescriptionText")
    if desc_el:
        return desc_el.get_text("\n", strip=True)
    posting = extract_jsonld_job_posting(html)
    raw_desc = posting.get("description", "") if isinstance(posting, dict) else ""
    return html_to_clean_text(str(raw_desc)) if raw_desc else ""


class IndeedScraper(BaseScraper):
    """Playwright scraper for Indeed France."""

    source_name = "indeed"

    def search(
        self,
        *,
        query: str,
        contract: ContractType,
        location: str,
        remote: bool,
        limit: int,
    ) -> ScrapeResult:
        search_url = build_indeed_search_url(query, contract, location, remote)
        offers: list[JobOffer] = []
        try:
            with playwright_context() as context:
                page = context.new_page()
                status, final_url, html = fetch_page_html(page, search_url, wait_ms=2000)
                if is_antibot_response(status, html, final_url):
                    return ScrapeResult(
                        source=self.source_name,
                        status="blocked_antibot",
                        error_message=f"Blocked on search page (HTTP {status})",
                    )

                cards = parse_indeed_search_cards(
                    html,
                    contract=contract,
                    limit=limit,
                    fallback_location=location,
                )
                for card_offer in cards:
                    polite_sleep()
                    try:
                        d_status, d_url, d_html = fetch_page_html(page, card_offer.url, wait_ms=1000)
                        if is_antibot_response(d_status, d_html, d_url):
                            offers.append(card_offer)
                            continue
                        full_desc = parse_indeed_detail_description(d_html)
                        if full_desc:
                            card_offer = build_offer(
                                source="indeed",
                                title=card_offer.title,
                                company=card_offer.company,
                                location=card_offer.location,
                                contract_type=contract,
                                url=card_offer.url,
                                description=full_desc,
                            )
                    except Exception:  # noqa: BLE001
                        pass
                    offers.append(card_offer)
            return ScrapeResult(source=self.source_name, status="ok", offers=offers)
        except Exception as exc:  # noqa: BLE001
            return ScrapeResult(
                source=self.source_name,
                status="error",
                offers=offers,
                error_message=str(exc),
            )
