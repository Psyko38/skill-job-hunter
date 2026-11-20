"""End-to-end CLI test with monkeypatched scrapers (offline)."""

import json
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest
import yaml

from scripts import cli
from scripts.models import JobOffer, ScrapeResult
from scripts.scrapers.base import BaseScraper


class _FakeScraper(BaseScraper):
    source_name = "fake"

    def search(self, *, query, contract, location, remote, limit):  # type: ignore[override]
        return ScrapeResult(
            source="fake",
            status="ok",
            offers=[
                JobOffer(
                    id="school01",
                    source="fake",
                    title="Développeur Alternance",
                    company="ISCOD",
                    location="Paris",
                    contract_type=contract,
                    url="https://example.org/school",
                    description="Formation financée par l'OPCO",
                ),
                JobOffer(
                    id="real01",
                    source="fake",
                    title="Développeur Python Alternance",
                    company="Alan",
                    location="Paris",
                    contract_type=contract,
                    url="https://example.org/alan-python",
                    description="Rejoignez Alan. Contact: jobs@alan.com",
                    emails_found=["jobs@alan.com"],
                    language="fr",
                ),
            ],
        )


def test_cli_search_build_drafts_mark_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(cli.SCRAPER_REGISTRY, "fake", _FakeScraper)
    rules_path = Path(__file__).resolve().parent.parent / "references" / "school_blacklist.yaml"
    history_path = tmp_path / "history.json"
    run_dir = tmp_path / "run1"
    drafts_dir = tmp_path / "drafts"
    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF-1.4 test")
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(
        yaml.safe_dump({"candidate": {"full_name": "Matteo", "email": "m@test.fr", "cv_path": str(cv_path)}}),
        encoding="utf-8",
    )

    rc = cli.main(
        [
            "search",
            "--contract",
            "alternance",
            "--query",
            "python",
            "--location",
            "Paris",
            "--sources",
            "fake",
            "--rules",
            str(rules_path),
            "--history",
            str(history_path),
            "--out-run-dir",
            str(run_dir),
        ]
    )
    assert rc == 0

    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert report["stats"] == {"scraped": 2, "rejected_schools": 1, "duplicates": 0, "kept": 1}

    candidates = json.loads((run_dir / "candidates.json").read_text(encoding="utf-8"))
    assert len(candidates) == 1
    assert candidates[0]["offer"]["id"] == "real01"
    assert candidates[0]["contact_email"] == "jobs@alan.com"

    drafts_input = tmp_path / "drafts_input.json"
    drafts_input.write_text(
        json.dumps(
            [
                {
                    "offer_id": "real01",
                    "subject": "Candidature Alternance Python – Matteo",
                    "body": "Bonjour l'équipe Alan,\n\nVoici ma candidature.",
                }
            ]
        ),
        encoding="utf-8",
    )

    rc2 = cli.main(
        [
            "build-drafts",
            "--candidates",
            str(run_dir / "candidates.json"),
            "--drafts-json",
            str(drafts_input),
            "--profile",
            str(profile_path),
            "--history",
            str(history_path),
            "--out-dir",
            str(drafts_dir),
        ]
    )
    assert rc2 == 0

    eml_files = list(drafts_dir.rglob("draft.eml"))
    assert len(eml_files) == 1
    parsed = BytesParser(policy=policy.default).parsebytes(eml_files[0].read_bytes())
    assert parsed["To"] == "jobs@alan.com"
    assert len(list(parsed.iter_attachments())) == 1

    assert cli.main(["mark-status", "--offer-id", "real01", "--status", "sent", "--history", str(history_path)]) == 0
    history_data = json.loads(history_path.read_text(encoding="utf-8"))
    assert history_data["offers"]["real01"]["status"] == "sent"
