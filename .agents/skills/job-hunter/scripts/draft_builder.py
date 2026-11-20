"""Generate validated application summary Markdown files and RFC 5322 .eml drafts with CV PDF."""

from __future__ import annotations

import re
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from .contact_finder import is_valid_recruiter_email
from .filters import SchoolFilterRules, is_blacklisted_email_domain
from .history import OfferHistory
from .models import DraftInput, EnrichedOffer
from .security import (
    atomic_write_bytes,
    atomic_write_text,
    ensure_within_directory,
    is_valid_email_syntax,
    sanitize_header_value,
    sanitize_url,
    validate_pdf_file,
)
from .text_utils import slugify

_DATE_FOLDER_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_PLACEHOLDER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\[(?:pr[eé]nom(?:\s+nom)?|nom(?:\s+de\s+l'entreprise)?|intitul[eé][^\]]*|"
        r"disponibilit[eé]|rythme|full\s+name|job\s+title|availability|company|date|todo|insert[^\]]*)\]",
        re.IGNORECASE,
    ),
    re.compile(r"\{\{[^}]+\}\}"),
    re.compile(r"\b(?:À\s*COMPL[EÉ]TER|A\s*COMPLETER|TBD|FIXME|TODO:)\b", re.IGNORECASE),
    re.compile(r"<(?:INSERT|YOUR|NOM|ENTREPRISE)[^>]*>", re.IGNORECASE),
)


def _find_placeholders(text: str) -> list[str]:
    hits: list[str] = []
    for pattern in _PLACEHOLDER_PATTERNS:
        for match in pattern.findall(text or ""):
            token = match if isinstance(match, str) else str(match)
            if token not in hits:
                hits.append(token)
    return hits


def _safe_markdown_fence(body: str) -> str:
    """Choose a backtick fence longer than any backtick run in body to prevent markdown breakout."""
    runs = re.findall(r"`+", body or "")
    max_run = max((len(r) for r in runs), default=0)
    return "`" * max(3, max_run + 1)


def resolve_cv_path(cv_path_raw: str, profile_dir: Path | None = None) -> Path | None:
    """Resolve an absolute or relative CV PDF path against CWD or profile_dir."""
    cleaned = cv_path_raw.strip()
    if not cleaned:
        return None
    direct = Path(cleaned).expanduser()
    if direct.is_file():
        return direct
    if not direct.is_absolute() and profile_dir is not None:
        rel_to_profile = (profile_dir / direct).resolve()
        if rel_to_profile.is_file():
            return rel_to_profile
    return direct


def validate_draft(
    *,
    enriched: EnrichedOffer,
    draft: DraftInput,
    profile: dict[str, Any],
    profile_dir: Path | None = None,
    rules: SchoolFilterRules | None = None,
    history: OfferHistory | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Validate draft safety and quality before writing .eml/.md files."""
    errors: list[str] = []
    warnings: list[str] = []

    subject = (draft.subject or "").strip()
    body = (draft.body or "").strip()
    if not subject:
        errors.append("Draft subject is empty")
    if not body:
        errors.append("Draft body is empty")

    if any(ch in (draft.subject or "") for ch in ("\r", "\n")):
        errors.append("Draft subject contains CR/LF characters (header injection risk)")

    effective_to = (draft.to_email or enriched.contact_email or "").strip()
    if effective_to:
        if any(ch in effective_to for ch in ("\r", "\n")):
            errors.append("Recipient email contains CR/LF characters")
        elif not is_valid_email_syntax(effective_to):
            errors.append(f"Invalid recipient email syntax: {effective_to}")
        else:
            if not is_valid_recruiter_email(effective_to):
                errors.append(f"Recipient email is an automated/ignored address: {effective_to}")
            if rules is not None and is_blacklisted_email_domain(effective_to, rules):
                errors.append(f"Recipient email belongs to a blacklisted school/CFA domain: {effective_to}")

    placeholders = _find_placeholders(f"{subject}\n{body}")
    if placeholders:
        errors.append(f"Unreplaced template placeholders detected: {', '.join(placeholders[:5])}")

    if history is not None and not force:
        existing = history.records.get(enriched.offer.id) or history.find_existing_record(enriched.offer)
        if existing and existing.get("status") in {"sent", "interview", "offer"}:
            errors.append(
                f"Offer {enriched.offer.id} already has status '{existing.get('status')}' "
                f"(use --force to regenerate draft)"
            )

    candidate = profile.get("candidate", {}) or {}
    sender_email = str(candidate.get("email", "")).strip()
    if sender_email and "A COMPLETER" not in sender_email.upper() and "À COMPLÉTER" not in sender_email.upper():
        if not is_valid_email_syntax(sender_email):
            errors.append(f"Invalid candidate.email in profile: {sender_email}")

    cv_raw = str(candidate.get("cv_path", "")).strip()
    cv_path = resolve_cv_path(cv_raw, profile_dir=profile_dir) if cv_raw else None
    if cv_path is not None:
        ok_pdf, pdf_err = validate_pdf_file(cv_path)
        if not ok_pdf:
            warnings.append(f"CV will not be attached: {pdf_err}")
    else:
        warnings.append("No candidate.cv_path configured; draft.eml will have no CV attachment")

    word_count = len(re.findall(r"\S+", body))
    if word_count and word_count < 30:
        warnings.append(f"Draft body is very short ({word_count} words)")
    elif word_count > 350:
        warnings.append(f"Draft body is quite long ({word_count} words; recommended <= 250)")

    if enriched.security_warnings:
        warnings.append(
            f"Source offer had security warnings ({', '.join(enriched.security_warnings)}); review carefully"
        )

    return {
        "offer_id": enriched.offer.id,
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "word_count": word_count,
        "to_email": effective_to,
    }


def build_eml_message(
    *,
    sender_name: str,
    sender_email: str,
    to_email: str,
    subject: str,
    body: str,
    cv_path: Path | None = None,
) -> EmailMessage:
    """Build an editable RFC 5322 draft email (.eml) with optional validated PDF CV attachment."""
    msg = EmailMessage()
    clean_sender_email = sanitize_header_value(sender_email)
    clean_sender_name = sanitize_header_value(sender_name)
    clean_to = sanitize_header_value(to_email)
    clean_subject = sanitize_header_value(subject)

    if (
        clean_sender_email
        and "A COMPLETER" not in clean_sender_email.upper()
        and "À COMPLÉTER" not in clean_sender_email.upper()
        and is_valid_email_syntax(clean_sender_email)
    ):
        msg["From"] = f"{clean_sender_name} <{clean_sender_email}>" if clean_sender_name else clean_sender_email

    if clean_to and is_valid_email_syntax(clean_to):
        msg["To"] = clean_to

    msg["Subject"] = clean_subject
    msg["X-Unsent"] = "1"
    msg.set_content(body, subtype="plain", charset="utf-8")

    if cv_path is not None:
        ok_pdf, _ = validate_pdf_file(cv_path)
        if ok_pdf:
            pdf_bytes = cv_path.read_bytes()
            safe_filename = sanitize_header_value(cv_path.name) or "CV.pdf"
            msg.add_attachment(
                pdf_bytes,
                maintype="application",
                subtype="pdf",
                filename=safe_filename,
            )

    return msg


def render_application_markdown(
    enriched: EnrichedOffer,
    draft: DraftInput,
    cv_attached: bool,
    cv_path_str: str,
) -> str:
    """Render a clean summary Markdown document for reviewing the application."""
    offer = enriched.offer
    effective_to = draft.to_email or enriched.contact_email
    safe_offer_url = sanitize_url(offer.url) or offer.url
    safe_apply_url = sanitize_url(offer.apply_url) or safe_offer_url

    contact_line = (
        f"`{effective_to}` (source: `{enriched.email_source}`)"
        if effective_to
        else f"Aucun email trouvé — postuler via le formulaire : {safe_apply_url}"
    )
    cv_status = (
        f"Joint automatiquement dans `draft.eml` (`{cv_path_str}`)"
        if cv_attached
        else "Non joint (renseigner un PDF valide dans `candidate.cv_path` de `profile.yaml`)"
    )

    fence = _safe_markdown_fence(draft.body)

    lines = [
        f"# {offer.company} — {offer.title}",
        "",
        "## Informations de l'offre",
        f"- **ID** : `{offer.id}`",
        f"- **Contrat** : `{offer.contract_type}`",
        f"- **Source** : `{offer.source}`",
        f"- **Canal recommandé** : `{enriched.apply_channel}`",
        f"- **Score de pertinence** : `{enriched.match_score}/100`"
        + (f" (skills matchés : {', '.join(enriched.matched_skills)})" if enriched.matched_skills else ""),
        f"- **Lieu** : {offer.location or 'Non précisé'}"
        + (f" ({offer.remote})" if offer.remote else ""),
        f"- **Langue détectée** : `{offer.language}`",
        f"- **Salaire / Gratification** : {offer.salary or 'Non précisé'}",
        f"- **Lien offre** : {safe_offer_url}",
        f"- **Lien candidature directe** : {safe_apply_url}",
    ]

    if enriched.missing_skills:
        lines.append(f"- **Technos de l'offre absentes du profil** : {', '.join(enriched.missing_skills)}")
    if enriched.security_warnings:
        lines.append(
            f"- **Alertes sécurité (contenu externe suspect)** : `{', '.join(enriched.security_warnings)}`"
        )

    lines.extend(
        [
            "",
            "## Contact & Pièces jointes",
            f"- **Destinataire** : {contact_line}",
            f"- **CV PDF** : {cv_status}",
            "",
            "## Brouillon d'email",
            f"**Objet :** `{sanitize_header_value(draft.subject)}`",
            "",
            f"{fence}text",
            draft.body.rstrip(),
            fence,
            "",
        ]
    )
    return "\n".join(lines)


def write_application_draft(
    *,
    enriched: EnrichedOffer,
    draft: DraftInput,
    profile: dict[str, Any],
    output_root: Path,
    run_date: str | None = None,
    profile_dir: Path | None = None,
) -> Path:
    """Create application.md and draft.eml safely inside drafts/<date>/<company>_<title>/."""
    day_folder = run_date or date.today().isoformat()
    if not _DATE_FOLDER_RE.match(day_folder):
        raise ValueError(f"Invalid run_date format (expected YYYY-MM-DD): {day_folder}")

    offer = enriched.offer
    safe_id_suffix = slugify(offer.id[:6], 6)
    folder_name = f"{slugify(offer.company, 28)}_{slugify(offer.title, 36)}_{safe_id_suffix}"
    target_dir = ensure_within_directory(output_root / day_folder / folder_name, output_root)
    target_dir.mkdir(parents=True, exist_ok=True)

    candidate = profile.get("candidate", {}) or {}
    sender_name = str(candidate.get("full_name", "")).strip()
    sender_email = str(candidate.get("email", "")).strip()
    cv_path = resolve_cv_path(str(candidate.get("cv_path", "")), profile_dir=profile_dir)
    cv_valid = bool(cv_path and validate_pdf_file(cv_path)[0])

    effective_to = draft.to_email or enriched.contact_email
    msg = build_eml_message(
        sender_name=sender_name,
        sender_email=sender_email,
        to_email=effective_to,
        subject=draft.subject,
        body=draft.body,
        cv_path=cv_path if cv_valid else None,
    )

    eml_path = target_dir / "draft.eml"
    atomic_write_bytes(eml_path, msg.as_bytes())

    md_content = render_application_markdown(
        enriched=enriched,
        draft=draft,
        cv_attached=cv_valid,
        cv_path_str=str(cv_path) if cv_path else "",
    )
    atomic_write_text(target_dir / "application.md", md_content, encoding="utf-8")

    return target_dir
