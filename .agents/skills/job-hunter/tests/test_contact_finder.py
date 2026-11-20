"""Unit tests for recruiter email extraction and company site crawling."""

import httpx

from scripts.contact_finder import (
    enrich_offer_contact,
    extract_emails_from_text,
    is_valid_recruiter_email,
    rank_emails,
)
from scripts.models import JobOffer


def test_filters_out_noise_and_jobboard_emails() -> None:
    assert not is_valid_recruiter_email("noreply@company.fr")
    assert not is_valid_recruiter_email("support@hellowork.com")
    assert not is_valid_recruiter_email("avatar@2x.png")
    assert is_valid_recruiter_email("recrutement@acme-tech.fr")


def test_extract_and_rank_prioritizes_hr_on_company_domain() -> None:
    emails = extract_emails_from_text(
        "Contactez info@partner.io ou recrutement@acme.fr ou no-reply@acme.fr."
    )
    assert "no-reply@acme.fr" not in emails
    ranked = rank_emails(emails, preferred_domain="www.acme.fr")
    assert ranked[0] == "recrutement@acme.fr"


def test_enrich_offer_crawls_company_site_when_no_email_in_offer() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/recrutement":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<html><body><a href="mailto:talent@superstartup.fr">Postuler</a></body></html>',
            )
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html>Accueil</html>")

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    offer = JobOffer(
        id="id1",
        source="wttj",
        title="Backend Intern",
        company="SuperStartup",
        location="Paris",
        contract_type="stage",
        url="https://www.welcometothejungle.com/fr/companies/superstartup/jobs/backend",
        company_url="https://superstartup.fr",
        description="Join our engineering team!",
    )

    enriched = enrich_offer_contact(offer, client=client)
    assert enriched.contact_email == "talent@superstartup.fr"
    assert enriched.email_source == "company_site:https://superstartup.fr/recrutement"
    assert enriched.apply_channel == "email"


def test_contact_finder_blocks_ssrf_and_supports_deobfuscation_ats_and_cache() -> None:
    calls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<html><a href="/nous-rejoindre-equipe">Nous rejoindre</a></html>',
            )
        if request.url.path == "/nous-rejoindre-equipe":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<html>Écrivez à recrutement [at] acme-startup [dot] fr</html>",
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(_handler))

    # 1. SSRF URL must never hit network transport
    ssrf_offer = JobOffer(
        id="ssrf1",
        source="wttj",
        title="Dev",
        company="Evil",
        location="Paris",
        contract_type="cdi",
        url="https://www.welcometothejungle.com/fr/companies/evil/jobs/dev",
        company_url="http://169.254.169.254/latest/meta-data/",
        apply_url="https://jobs.lever.co/evil/123",
    )
    ssrf_res = enrich_offer_contact(ssrf_offer, client=client)
    assert ssrf_res.contact_email == ""
    assert ssrf_res.apply_channel == "ats"
    assert calls == []

    # 2. Discovers custom career link + de-obfuscates email + caches per domain
    cache: dict[str, tuple[str, str]] = {}
    good_offer = JobOffer(
        id="ok1",
        source="wttj",
        title="Dev",
        company="Acme Startup",
        location="Lyon",
        contract_type="alternance",
        url="https://www.welcometothejungle.com/fr/companies/acme/jobs/dev",
        company_url="https://acme-startup.fr",
    )
    res1 = enrich_offer_contact(good_offer, client=client, domain_cache=cache)
    assert res1.contact_email == "recrutement@acme-startup.fr"
    assert res1.apply_channel == "email"
    call_count_after_first = len(calls)

    # Second offer on same domain reuses cache without extra HTTP requests
    res2 = enrich_offer_contact(good_offer, client=client, domain_cache=cache)
    assert res2.contact_email == "recrutement@acme-startup.fr"
    assert len(calls) == call_count_after_first

    # 3. Blacklisted school email domains are rejected
    school_email_offer = JobOffer(
        id="sch1",
        source="wttj",
        title="Dev",
        company="Partner",
        location="Lyon",
        contract_type="alternance",
        url="https://example.com/job",
        description="Contact: jobs@iscod.fr",
    )
    res3 = enrich_offer_contact(school_email_offer, extra_blocked_domains={"iscod.fr"})
    assert res3.contact_email == ""
