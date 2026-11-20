"""Offline parser unit tests for all 4 jobboard scrapers using HTML fixtures."""

import httpx
import pytest

from scripts.http_headers import DEFAULT_HTTP_HEADERS, DEFAULT_USER_AGENT
from scripts.scrapers.hellowork import (
    parse_hellowork_detail_html,
    parse_hellowork_search_links,
)
from scripts.scrapers.indeed import (
    parse_indeed_detail_description,
    parse_indeed_search_cards,
)
from scripts.scrapers.linkedin import (
    LinkedInScraper,
    build_linkedin_search_url,
    parse_linkedin_detail_html,
    parse_linkedin_search_cards,
)
from scripts.scrapers.wttj import (
    parse_wttj_detail_html,
    parse_wttj_search_links,
)


def test_wttj_parsers() -> None:
    search_html = """
    <html><body>
      <a href="/fr/companies/alan/jobs/backend-python_paris?o=1">Job 1</a>
      <a href="/fr/companies/alan/jobs/backend-python_paris?o=2">Job 1 dup</a>
      <a href="/fr/companies/qonto/jobs/fullstack_paris">Job 2</a>
    </body></html>
    """
    links = parse_wttj_search_links(search_html, limit=5)
    assert links == [
        "https://www.welcometothejungle.com/fr/companies/alan/jobs/backend-python_paris",
        "https://www.welcometothejungle.com/fr/companies/qonto/jobs/fullstack_paris",
    ]

    detail_html = """
    <html><head>
      <script type="application/ld+json">
        {
          "@type": "JobPosting",
          "title": "Backend Engineer Python",
          "hiringOrganization": {"name": "Alan", "sameAs": "https://alan.com"},
          "jobLocation": {"address": {"addressLocality": "Paris"}},
          "description": "<p>Build health APIs. Write to jobs@alan.com.</p>"
        }
      </script>
    </head><body><h1>Backend Engineer Python</h1></body></html>
    """
    offer = parse_wttj_detail_html(
        detail_html,
        url=links[0],
        contract="cdi",
    )
    assert offer is not None
    assert offer.company == "Alan"
    assert offer.company_url == "https://alan.com"
    assert "jobs@alan.com" in offer.emails_found


def test_hellowork_parsers() -> None:
    search_html = """
    <html><body>
      <a href="/fr-fr/emplois/123456.html">Dev Alternance</a>
      <a href="/fr-fr/emplois/654321.html?utm_source=x">Dev Stage</a>
    </body></html>
    """
    links = parse_hellowork_search_links(search_html, limit=5)
    assert links == [
        "https://www.hellowork.com/fr-fr/emplois/123456.html",
        "https://www.hellowork.com/fr-fr/emplois/654321.html",
    ]

    detail_html = """
    <html><head>
      <script type="application/ld+json">
        {
          "@type": "JobPosting",
          "title": "Développeur React Alternance",
          "hiringOrganization": {"name": "Swile"},
          "description": "<p>Rejoignez Swile à Montpellier.</p>"
        }
      </script>
    </head><body></body></html>
    """
    offer = parse_hellowork_detail_html(detail_html, url=links[0], contract="alternance")
    assert offer is not None
    assert offer.company == "Swile"
    assert offer.contract_type == "alternance"


def test_indeed_parsers() -> None:
    search_html = """
    <html><body>
      <div class="job_seen_beacon">
        <td class="resultContent">
          <h2 class="jobTitle"><a data-jk="abc987" href="/rc/clk?jk=abc987">Stage Data Engineer</a></h2>
          <span data-testid="company-name">Datadog</span>
          <div data-testid="text-location">Paris</div>
        </td>
      </div>
    </body></html>
    """
    cards = parse_indeed_search_cards(search_html, contract="stage", limit=5)
    assert len(cards) == 1
    assert cards[0].url == "https://fr.indeed.com/viewjob?jk=abc987"
    assert cards[0].company == "Datadog"

    detail_html = '<div id="jobDescriptionText"><p>Python &amp; SQL internship.</p></div>'
    assert "Python & SQL internship." in parse_indeed_detail_description(detail_html)


def test_linkedin_parsers() -> None:
    search_html = """
    <ul><li>
      <a class="base-card__full-link" href="https://fr.linkedin.com/jobs/view/dev-fullstack-41002233?trk=x">Link</a>
      <h3 class="base-search-card__title">Développeur Fullstack</h3>
      <h4 class="base-search-card__subtitle">Pennylane</h4>
      <span class="job-search-card__location">Paris, Île-de-France</span>
    </li></ul>
    """
    cards = parse_linkedin_search_cards(search_html, contract="cdd", limit=5)
    assert len(cards) == 1
    job_id, offer = cards[0]
    assert job_id == "41002233"
    assert offer.company == "Pennylane"

    detail_html = """
    <div class="show-more-less-html__markup">We use Ruby and React.</div>
    <a class="apply-button" href="https://jobs.pennylane.com/apply">Apply</a>
    """
    desc, apply_url = parse_linkedin_detail_html(detail_html)
    assert "Ruby and React" in desc
    assert apply_url == "https://jobs.pennylane.com/apply"

    signup_html = """
    <div class="show-more-less-html__markup">Backend role.</div>
    <a class="apply-button" href="https://www.linkedin.com/signup/cold-join?source=jobs_registration">Apply</a>
    """
    _, signup_apply = parse_linkedin_detail_html(signup_html)
    assert signup_apply == ""


def test_indeed_jsonld_fallback_and_windows_headers() -> None:
    jsonld_html = """
    <html><head><script type="application/ld+json">
      {"@type": "JobPosting", "description": "<p>Stack PHP Laravel et Svelte.</p>"}
    </script></head><body></body></html>
    """
    assert "Stack PHP Laravel et Svelte." in parse_indeed_detail_description(jsonld_html)
    assert "Windows NT 10.0" in DEFAULT_USER_AGENT
    assert "Linux" not in DEFAULT_USER_AGENT
    assert DEFAULT_HTTP_HEADERS["Sec-CH-UA-Platform"] == '"Windows"'


def test_linkedin_contract_urls_prefilter_and_429_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "f_JT=I%2CT%2CC" in build_linkedin_search_url("Dev Web", "alternance", "Lyon", False)
    assert "alternance" in build_linkedin_search_url("Dev Web", "alternance", "Lyon", False)
    assert "f_JT=I" in build_linkedin_search_url("Dev Web", "stage", "Lyon", False)
    assert "f_JT=C%2CT" in build_linkedin_search_url("Dev Web", "cdd", "Lyon", False)
    assert "f_JT=F" in build_linkedin_search_url("Dev Web", "cdi", "Lyon", False)

    search_html = """
    <ul>
      <li>
        <a class="base-card__full-link" href="https://fr.linkedin.com/jobs/view/stage-dev-111">Link</a>
        <h3 class="base-search-card__title">Stage Développeur Fullstack</h3>
        <h4 class="base-search-card__subtitle">StageCorp</h4>
        <span class="job-search-card__location">Lyon</span>
      </li>
      <li>
        <a class="base-card__full-link" href="https://fr.linkedin.com/jobs/view/alt-dev-222">Link</a>
        <h3 class="base-search-card__title">Alternance Développeur PHP</h3>
        <h4 class="base-search-card__subtitle">AltCorp</h4>
        <span class="job-search-card__location">Lyon</span>
        <time datetime="2026-11-19">1 day ago</time>
      </li>
    </ul>
    """
    detail_attempts: dict[str, int] = {"222": 0}
    called_urls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        called_urls.append(url)
        if "seeMoreJobPostings" in url:
            return httpx.Response(200, text=search_html)
        if "jobPosting/222" in url:
            detail_attempts["222"] += 1
            if detail_attempts["222"] == 1:
                return httpx.Response(429, headers={"retry-after": "3"})
            return httpx.Response(
                200,
                text='<div class="show-more-less-html__markup">Alternance PHP Laravel à Lyon.</div>',
            )
        return httpx.Response(404)

    monkeypatch.setattr("scripts.scrapers.linkedin.polite_sleep", lambda *args, **kwargs: None)
    real_client_cls = httpx.Client

    def _mock_client(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(_handler)
        return real_client_cls(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("scripts.scrapers.linkedin.httpx.Client", _mock_client)

    res = LinkedInScraper().search(
        query="Développeur PHP",
        contract="alternance",
        location="Lyon",
        remote=False,
        limit=5,
    )
    assert res.status == "ok"
    assert res.error_message == ""
    assert len(res.offers) == 1
    assert res.offers[0].company == "AltCorp"
    assert res.offers[0].published_at == "2026-11-19"
    assert "Alternance PHP Laravel" in res.offers[0].description
    # Stage card 111 must NEVER trigger a detail HTTP call
    assert not any("111" in u for u in called_urls)
    assert detail_attempts["222"] == 2
