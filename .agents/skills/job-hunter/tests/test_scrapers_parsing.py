"""Offline parser unit tests for all 4 jobboard scrapers using HTML fixtures."""

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
