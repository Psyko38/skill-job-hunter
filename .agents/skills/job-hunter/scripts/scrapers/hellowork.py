"""HelloWork jobboard scraper."""

from __future__ import annotations

from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from ..models import ContractType, JobOffer, ScrapeResult
from .base import (
    BaseScraper,
    build_offer,
    extract_jsonld_job_posting,
    fetch_page_html,
    is_antibot_response,
    parse_jsonld_fields,
    playwright_context,
    polite_sleep,
)

_HW_BASE = "https://www.hellowork.com"
_CONTRACT_MAP: dict[ContractType, str] = {
    "stage": "Stage",
    "alternance": "Alternance",
    "cdd": "CDD",
    "cdi": "CDI",
}


def build_hellowork_search_url(query: str, contract: ContractType, location: str) -> str:
    """Build a HelloWork search URL."""
    hw_contract = _CONTRACT_MAP[contract]
    url = (
        f"{_HW_BASE}/fr-fr/emploi/recherche.html"
        f"?k={quote_plus(query)}&c={quote_plus(hw_contract)}"
    )
    if location:
        url += f"&l={quote_plus(location)}"
    return url


def parse_hellowork_search_links(html: str, limit: int) -> list[str]:
    """Extract deduplicated job detail URLs from a HelloWork search results page."""
    soup = BeautifulSoup(html, "lxml")
    urls: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"])
        if "/fr-fr/emplois/" in href and href.split("?", 1)[0].endswith(".html"):
            full_url = urljoin(_HW_BASE, href.split("?", 1)[0])
            if full_url not in urls:
                urls.append(full_url)
                if len(urls) >= limit:
                    break
    return urls


def parse_hellowork_detail_html(
    html: str,
    *,
    url: str,
    contract: ContractType,
    fallback_location: str = "",
) -> JobOffer | None:
    """Parse a HelloWork job detail page via JSON-LD with HTML selector fallbacks."""
    fields = parse_jsonld_fields(extract_jsonld_job_posting(html))
    soup = BeautifulSoup(html, "lxml")

    title = fields.get("title", "")
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(" ", strip=True) if h1 else ""

    company = fields.get("company", "")
    if not company:
        comp_el = soup.select_one('[data-cy="companyName"], .company-name, h1 + a, h1 + span')
        company = comp_el.get_text(" ", strip=True) if comp_el else ""

    if not title or not company:
        return None

    description = fields.get("description", "")
    if not description:
        desc_el = soup.select_one('[data-cy="jobDescription"], section.tw-break-words, main')
        description = desc_el.get_text("\n", strip=True) if desc_el else ""

    return build_offer(
        source="hellowork",
        title=title,
        company=company,
        location=fields.get("location") or fallback_location,
        contract_type=contract,
        url=url,
        company_url=fields.get("company_url", ""),
        description=description,
        published_at=fields.get("published_at", ""),
        salary=fields.get("salary", ""),
    )


class HelloWorkScraper(BaseScraper):
    """Playwright scraper for HelloWork."""

    source_name = "hellowork"

    def search(
        self,
        *,
        query: str,
        contract: ContractType,
        location: str,
        remote: bool,
        limit: int,
    ) -> ScrapeResult:
        search_url = build_hellowork_search_url(query, contract, location)
        offers: list[JobOffer] = []
        try:
            with playwright_context() as context:
                page = context.new_page()
                status, final_url, html = fetch_page_html(page, search_url, wait_ms=1800)
                if is_antibot_response(status, html, final_url):
                    return ScrapeResult(
                        source=self.source_name,
                        status="blocked_antibot",
                        error_message=f"Blocked on search page (HTTP {status})",
                    )

                links = parse_hellowork_search_links(html, limit=limit)
                for link in links:
                    polite_sleep()
                    d_status, d_url, d_html = fetch_page_html(page, link, wait_ms=1000)
                    if is_antibot_response(d_status, d_html, d_url):
                        continue
                    offer = parse_hellowork_detail_html(
                        d_html,
                        url=link,
                        contract=contract,
                        fallback_location=location,
                    )
                    if offer:
                        offers.append(offer)
            return ScrapeResult(source=self.source_name, status="ok", offers=offers)
        except Exception as exc:  # noqa: BLE001
            return ScrapeResult(
                source=self.source_name,
                status="error",
                offers=offers,
                error_message=str(exc),
            )
