"""Unit tests for .eml MIME draft building and application.md rendering."""

from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest

from scripts.draft_builder import resolve_cv_path, validate_draft, write_application_draft
from scripts.filters import SchoolFilterRules
from scripts.history import OfferHistory
from scripts.models import DraftInput, EnrichedOffer, JobOffer


def test_resolve_cv_path_relative_to_profile_dir(tmp_path: Path) -> None:
    refs_dir = tmp_path / "references"
    refs_dir.mkdir()
    pdf = refs_dir / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    resolved = resolve_cv_path("./cv.pdf", profile_dir=refs_dir)
    assert resolved == pdf.resolve()


def test_writes_md_and_eml_with_pdf_attachment(tmp_path: Path) -> None:
    cv_file = tmp_path / "CV_Matteo.pdf"
    cv_file.write_bytes(b"%PDF-1.4 fake pdf content")

    offer = JobOffer(
        id="abcdef123456",
        source="wttj",
        title="Alternance Développeur Backend",
        company="Mistral AI",
        location="Paris",
        remote="Hybride",
        contract_type="alternance",
        url="https://www.welcometothejungle.com/fr/companies/mistral/jobs/dev",
        apply_url="https://jobs.mistral.ai/apply",
        language="fr",
    )
    enriched = EnrichedOffer(
        offer=offer,
        contact_email="talent@mistral.ai",
        email_source="offer_text",
    )
    draft = DraftInput(
        offer_id=offer.id,
        subject="Candidature Alternance Développeur Backend – Matteo",
        body="Bonjour,\n\nJe souhaite rejoindre vos équipes en alternance.\n\nCordialement,\nMatteo",
    )
    profile = {
        "candidate": {
            "full_name": "Matteo",
            "email": "matteo@example.org",
            "cv_path": str(cv_file),
        }
    }

    out_dir = write_application_draft(
        enriched=enriched,
        draft=draft,
        profile=profile,
        output_root=tmp_path / "drafts",
        run_date="2026-11-20",
    )

    md_text = (out_dir / "application.md").read_text(encoding="utf-8")
    assert "Mistral AI — Alternance Développeur Backend" in md_text
    assert "`talent@mistral.ai`" in md_text
    assert "Joint automatiquement dans `draft.eml`" in md_text

    parsed = BytesParser(policy=policy.default).parsebytes((out_dir / "draft.eml").read_bytes())
    assert parsed["Subject"] == "Candidature Alternance Développeur Backend – Matteo"
    assert parsed["To"] == "talent@mistral.ai"
    assert parsed["From"] == "Matteo <matteo@example.org>"
    assert parsed["X-Unsent"] == "1"

    attachments = list(parsed.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "CV_Matteo.pdf"
    assert attachments[0].get_payload(decode=True) == b"%PDF-1.4 fake pdf content"


def test_validate_draft_blocks_placeholders_crlf_school_email_and_already_sent(tmp_path: Path) -> None:
    rules_path = Path(__file__).resolve().parent.parent / "references" / "school_blacklist.yaml"
    rules = SchoolFilterRules.from_yaml(rules_path)
    history = OfferHistory(tmp_path / "history.json")
    offer = JobOffer(
        id="offer99",
        source="wttj",
        title="Dev Python",
        company="Alan",
        location="Paris",
        contract_type="alternance",
        url="https://www.welcometothejungle.com/fr/companies/alan/jobs/dev",
    )
    history.record_offer(offer, "sent")
    enriched = EnrichedOffer(offer=offer, contact_email="jobs@iscod.fr", email_source="offer_text")
    bad_draft = DraftInput(
        offer_id=offer.id,
        subject="Candidature [Intitulé exact]\r\nBcc: evil@attacker.com",
        body="Bonjour [Prénom Nom], voici {{TODO}}.",
    )
    profile = {"candidate": {"full_name": "Matteo", "email": "matteo@example.org", "cv_path": ""}}

    report = validate_draft(
        enriched=enriched,
        draft=bad_draft,
        profile=profile,
        rules=rules,
        history=history,
    )
    assert not report["valid"]
    joined = " | ".join(report["errors"])
    assert "CR/LF" in joined
    assert "blacklisted school/CFA" in joined
    assert "placeholders" in joined
    assert "already has status 'sent'" in joined


def test_write_draft_rejects_date_traversal_and_escapes_markdown_fence(tmp_path: Path) -> None:
    offer = JobOffer(
        id="abcdef123456",
        source="wttj",
        title="Dev",
        company="Acme",
        location="Lyon",
        contract_type="cdi",
        url="https://acme.fr/job",
    )
    enriched = EnrichedOffer(offer=offer, contact_email="jobs@acme.fr", match_score=82, matched_skills=["Python"])
    draft = DraftInput(
        offer_id=offer.id,
        subject="Candidature CDI Dev",
        body="Voici un exemple :\n```python\nprint('hi')\n```",
    )
    profile = {"candidate": {"full_name": "Matteo", "email": "matteo@example.org", "cv_path": ""}}

    with pytest.raises(ValueError):
        write_application_draft(
            enriched=enriched,
            draft=draft,
            profile=profile,
            output_root=tmp_path / "drafts",
            run_date="../../etc",
        )

    out_dir = write_application_draft(
        enriched=enriched,
        draft=draft,
        profile=profile,
        output_root=tmp_path / "drafts",
        run_date="2026-11-20",
    )
    md_text = (out_dir / "application.md").read_text(encoding="utf-8")
    assert "````text" in md_text
    assert "`82/100`" in md_text
