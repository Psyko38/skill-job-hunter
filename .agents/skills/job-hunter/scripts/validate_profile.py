"""Validate job-hunter's profile.yaml and verify the attached CV PDF is safe and valid."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from .security import is_valid_email_syntax, sanitize_url, validate_pdf_file

DEFAULT_PROFILE_PATH = Path(__file__).resolve().parent.parent / "references" / "profile.yaml"
VALID_CONTRACTS = {"stage", "alternance", "cdd", "cdi"}
VALID_SOURCES = {"wttj", "hellowork", "indeed", "linkedin"}


def _is_placeholder(value: Any) -> bool:
    return isinstance(value, str) and "A COMPLETER" in value.upper().replace("À", "A").replace("É", "E")


def validate_profile(profile_path: Path) -> dict[str, Any]:
    """Load and validate profile.yaml, returning a structured status report."""
    errors: list[str] = []
    warnings: list[str] = []

    if not profile_path.is_file():
        return {
            "valid": False,
            "profile_path": str(profile_path),
            "errors": ["profile.yaml not found"],
            "warnings": [],
        }

    try:
        data = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return {
            "valid": False,
            "profile_path": str(profile_path),
            "errors": [f"Invalid YAML syntax: {exc}"],
            "warnings": [],
        }

    if not isinstance(data, dict):
        return {
            "valid": False,
            "profile_path": str(profile_path),
            "errors": ["profile.yaml root must be a mapping"],
            "warnings": [],
        }

    candidate = data.get("candidate") or {}
    if not isinstance(candidate, dict):
        errors.append("candidate section must be a mapping")
        candidate = {}

    for field in ("full_name", "email", "cv_path"):
        val = candidate.get(field, "")
        if not val or _is_placeholder(val):
            errors.append(f"candidate.{field} missing or placeholder")

    email_raw = str(candidate.get("email", "")).strip()
    if email_raw and not _is_placeholder(email_raw) and not is_valid_email_syntax(email_raw):
        errors.append(f"candidate.email is not a valid email address: {email_raw}")

    links = candidate.get("links") or {}
    if isinstance(links, dict):
        for link_name, link_val in links.items():
            if not link_val:
                continue
            cleaned_link = sanitize_url(str(link_val))
            if not cleaned_link:
                warnings.append(f"candidate.links.{link_name} is not a valid http(s) URL: {link_val}")

    cv_raw = str(candidate.get("cv_path", "")).strip()
    if cv_raw and not _is_placeholder(cv_raw):
        cv_file = Path(cv_raw).expanduser()
        if not cv_file.is_absolute():
            cv_file = (profile_path.parent / cv_file).resolve()
        if not cv_file.is_file():
            errors.append(f"CV file not found: {cv_file}")
        else:
            if cv_file.suffix.lower() != ".pdf":
                warnings.append(
                    f"CV file is not a PDF ({cv_file.suffix}); draft.eml only attaches valid PDFs"
                )
            ok_pdf, pdf_err = validate_pdf_file(cv_file)
            if not ok_pdf:
                errors.append(pdf_err)

    defaults = data.get("defaults") or {}
    if not isinstance(defaults, dict):
        errors.append("defaults section must be a mapping")
        defaults = {}

    contracts = defaults.get("contracts") or []
    if not contracts:
        errors.append("defaults.contracts is empty")
    else:
        invalid_contracts = [c for c in contracts if c not in VALID_CONTRACTS]
        if invalid_contracts:
            errors.append(f"Invalid contracts: {invalid_contracts}")

    if not (defaults.get("queries") or []):
        errors.append("defaults.queries is empty")

    sources = defaults.get("sources") or []
    invalid_sources = [s for s in sources if s not in VALID_SOURCES]
    if invalid_sources:
        warnings.append(f"Unknown sources in defaults.sources: {invalid_sources}")

    education = data.get("education") or {}
    if isinstance(education, dict):
        if "alternance" in contracts and not education.get("alternance_rhythm"):
            warnings.append("education.alternance_rhythm is empty (recommended for alternance applications)")
        if "stage" in contracts and not (education.get("stage_duration") or education.get("contract_duration")):
            warnings.append("education.stage_duration is empty (recommended for stage applications)")

    skills = data.get("skills") or {}
    if not isinstance(skills, dict) or not any(
        skills.get(k) for k in ("languages", "frameworks", "tools", "specialties")
    ):
        warnings.append("skills section has no technical skills listed")

    if not (data.get("highlights") or []):
        warnings.append("highlights section is empty (recommended for personalized emails)")

    preferences = data.get("preferences")
    if preferences is not None:
        if not isinstance(preferences, dict):
            errors.append("preferences section must be a mapping")
        else:
            for pref_key in ("exclude_companies", "exclude_keywords", "preferred_keywords"):
                pref_val = preferences.get(pref_key)
                if pref_val is not None and not isinstance(pref_val, list):
                    errors.append(f"preferences.{pref_key} must be a list of strings")

    return {
        "valid": len(errors) == 0,
        "profile_path": str(profile_path),
        "candidate_name": candidate.get("full_name", ""),
        "cv_path": cv_raw,
        "errors": errors,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for validating profile.yaml."""
    parser = argparse.ArgumentParser(description="Validate job-hunter profile.yaml")
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE_PATH))
    args = parser.parse_args(argv)
    report = validate_profile(Path(args.profile))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
