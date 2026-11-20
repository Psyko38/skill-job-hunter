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
