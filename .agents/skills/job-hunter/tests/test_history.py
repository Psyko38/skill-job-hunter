"""Unit tests for offer history deduplication, status updates, and CSV export."""

from pathlib import Path

from scripts.history import OfferHistory
from scripts.models import JobOffer


def test_dedup_by_canonical_url_and_cross_board_fingerprint(tmp_path: Path) -> None:
    history = OfferHistory(tmp_path / "history.json")
    offer_wttj = JobOffer(
        id="o1",
        source="wttj",
        title="Développeur Python (H/F)",
        company="Qonto",
        location="Paris",
        contract_type="cdi",
        url="https://www.welcometothejungle.com/fr/companies/qonto/jobs/python?utm_source=test",
    )
    assert not history.is_duplicate(offer_wttj)
    history.record_offer(offer_wttj, "drafted", contact_email="jobs@qonto.com")
    history.save()

    reloaded = OfferHistory(tmp_path / "history.json")
    same_url_diff_utm = JobOffer(
        id="o2",
        source="wttj",
        title="Autre titre",
        company="Qonto",
        location="Paris",
        contract_type="cdi",
        url="https://www.welcometothejungle.com/fr/companies/qonto/jobs/python?utm_campaign=x",
    )
    assert reloaded.is_duplicate(same_url_diff_utm)

    cross_board = JobOffer(
        id="o3",
        source="indeed",
        title="Developpeur Python (H/F)",
        company="qonto",
        location="Paris",
        contract_type="cdi",
        url="https://fr.indeed.com/viewjob?jk=9999",
    )
    assert reloaded.is_duplicate(cross_board)
    assert reloaded.update_status("o1", "sent")
    reloaded.save()

    csv_text = (tmp_path / "applications.csv").read_text(encoding="utf-8")
    assert "o1,sent,Qonto" in csv_text
