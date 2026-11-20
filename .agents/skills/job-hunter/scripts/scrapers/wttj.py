"""Welcome to the Jungle (WTTJ) scraper."""

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

_WTTJ_BASE = "https://www.welcometothejungle.com"
_CONTRACT_MAP: dict[ContractType, str] = {
    "stage": "internship",
    "alternance": "apprenticeship",
    "cdd": "temporary",
    "cdi": "full_time",
}


def build_wttj_search_url(query: str, contract: ContractType, location: str, remote: bool) -> str:
    """Build a Welcome to the Jungle search URL."""
    wttj_contract = _CONTRACT_MAP[contract]
    url = (
        f"{_WTTJ_BASE}/fr/jobs?query={quote_plus(query)}"
        f"&refinementList%5Bcontract_type%5D%5B%5D={wttj_contract}"
    )
    if location:
        url += f"&aroundQuery={quote_plus(location)}"
    if remote:
        url += "&refinementList%5Bremote%5D%5B%5D=fulltime&refinementList%5Bremote%5D%5B%5D=partial"
    return url


def parse_wttj_search_links(html: str, limit: int) -> list[str]:
    """Extract deduplicated job detail URLs from a WTTJ search results page."""
    soup = BeautifulSoup(html, "lxml")
    urls: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"])
        if "/companies/" in href and "/jobs/" in href:
            full_url = urljoin(_WTTJ_BASE, href.split("?", 1)[0])
            if full_url not in urls:
                urls.append(full_url)
                if len(urls) >= limit:
                    break
    return urls


def parse_wttj_detail_html(
    html: str,
    *,
    url: str,
    contract: ContractType,
    fallback_location: str = "",
) -> JobOffer | None:
    """Parse a WTTJ job detail page via JSON-LD with HTML selector fallbacks."""
    fields = parse_jsonld_fields(extract_jsonld_job_posting(html))
    soup = BeautifulSoup(html, "lxml")

    title = fields.get("title") or (
        soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else ""
    )
    company = fields.get("company", "")
    if not company:
        org_el = soup.select_one('a[href*="/companies/"] span, [data-testid="job-company-name"]')
        company = org_el.get_text(" ", strip=True) if org_el else ""

    if not title or not company:
        return None

    description = fields.get("description", "")
    if not description:
        desc_el = soup.select_one('[data-testid="job-section-description"], main')
        description = desc_el.get_text("\n", strip=True) if desc_el else ""

    company_url = fields.get("company_url", "")
    if not company_url:
        for anchor in soup.select('a[rel*="noopener"][target="_blank"]'):
            href = str(anchor.get("href", ""))
            if href.startswith("http") and "welcometothejungle" not in href:
                company_url = href
                break

    return build_offer(
        source="wttj",
        title=title,
        company=company,
        location=fields.get("location") or fallback_location,
        remote=fields.get("remote", ""),
        contract_type=contract,
        url=url,
        company_url=company_url,
        description=description,
        published_at=fields.get("published_at", ""),
        salary=fields.get("salary", ""),
    )


class WTTJScraper(BaseScraper):
    """Playwright scraper for Welcome to the Jungle."""

    source_name = "wttj"

    def search(
        self,
        *,
        query: str,
        contract: ContractType,
        location: str,
        remote: bool,
        limit: int,
    ) -> ScrapeResult:
        search_url = build_wttj_search_url(query, contract, location, remote)
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

                links = parse_wttj_search_links(html, limit=limit)
                for link in links:
                    polite_sleep()
                    try:
                        d_status, d_url, d_html = fetch_page_html(page, link, wait_ms=1000)
                        if is_antibot_response(d_status, d_html, d_url):
                            continue
                        offer = parse_wttj_detail_html(
                            d_html,
                            url=link,
                            contract=contract,
                            fallback_location=location,
                        )
                        if offer:
                            offers.append(offer)
                    except Exception:  # noqa: BLE001
                        continue
            return ScrapeResult(source=self.source_name, status="ok", offers=offers)
        except Exception as exc:  # noqa: BLE001
            return ScrapeResult(
                source=self.source_name,
                status="error",
                offers=offers,
                error_message=str(exc),
            )
