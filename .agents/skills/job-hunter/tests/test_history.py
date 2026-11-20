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


def test_history_csv_injection_protection_stats_and_ignore_statuses(tmp_path: Path) -> None:
    history = OfferHistory(tmp_path / "history.json")
    evil_offer = JobOffer(
        id="evil1",
        source="wttj",
        title="=HYPERLINK(\"http://evil.com\",\"Click\")",
        company="+EvilCorp",
        location="Lyon",
        contract_type="alternance",
        url="https://example.com/evil",
    )
    history.record_offer(evil_offer, "candidates_extracted", match_score=40)
    assert history.is_duplicate(evil_offer)
    assert not history.is_duplicate(evil_offer, ignore_statuses={"candidates_extracted"})

    sent_offer = JobOffer(
        id="old_sent",
        source="linkedin",
        title="Dev Fullstack",
        company="Alan",
        location="Paris",
        contract_type="alternance",
        url="https://example.com/alan",
    )
    history.record_offer(sent_offer, "sent", contact_email="jobs@alan.com")
    # Simulate an offer sent 10 days ago
    history.records["old_sent"]["updated_at"] = "2020-01-01T10:00:00+00:00"
    history.save()

    csv_text = (tmp_path / "applications.csv").read_text(encoding="utf-8")
    assert "'+EvilCorp" in csv_text
    assert "'=HYPERLINK" in csv_text

    stats = history.get_stats(follow_up_days=7)
    assert stats["total_tracked"] == 2
    assert stats["applied_total"] == 1
    assert len(stats["follow_up_due"]) == 1
    assert stats["follow_up_due"][0]["id"] == "old_sent"
