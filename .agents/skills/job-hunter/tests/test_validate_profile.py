"""Unit tests for profile.yaml validation."""

from pathlib import Path

import yaml

from scripts import cli
from scripts.validate_profile import validate_profile


def test_validate_profile_success_and_missing_cv(tmp_path: Path) -> None:
    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF-1.4")
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        yaml.safe_dump(
            {
                "candidate": {
                    "full_name": "Alice Martin",
                    "email": "alice@example.com",
                    "cv_path": str(cv_path),
                },
                "defaults": {"contracts": ["alternance"], "queries": ["Développeur Web"]},
                "skills": {"languages": ["PHP"]},
                "highlights": ["Projet Phoenix"],
            }
        ),
        encoding="utf-8",
    )

    ok_report = validate_profile(profile)
    assert ok_report["valid"] is True
    assert ok_report["errors"] == []
    assert cli.main(["validate-profile", "--profile", str(profile)]) == 0

    cv_path.unlink()
    bad_report = validate_profile(profile)
    assert bad_report["valid"] is False
    assert any("CV file not found" in err for err in bad_report["errors"])
    assert cli.main(["validate-profile", "--profile", str(profile)]) == 1


def test_validate_profile_rejects_invalid_email_and_non_pdf_cv(tmp_path: Path) -> None:
    fake_pdf = tmp_path / "cv.pdf"
    fake_pdf.write_text("this is plain text, not a PDF", encoding="utf-8")
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        yaml.safe_dump(
            {
                "candidate": {
                    "full_name": "Alice Martin",
                    "email": "not-an-email",
                    "cv_path": str(fake_pdf),
                    "links": {"portfolio": "javascript:alert(1)"},
                },
                "defaults": {"contracts": ["alternance"], "queries": ["Développeur Web"]},
            }
        ),
        encoding="utf-8",
    )
    report = validate_profile(profile)
    assert report["valid"] is False
    assert any("not a valid email" in err for err in report["errors"])
    assert any("%PDF-" in err for err in report["errors"])
    assert any("candidate.links.portfolio" in w for w in report["warnings"])
