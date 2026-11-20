"""Generate application summary Markdown files and RFC 5322 .eml drafts with CV PDF."""

from __future__ import annotations

from datetime import date
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from .models import DraftInput, EnrichedOffer
from .text_utils import slugify


def build_eml_message(
    *,
    sender_name: str,
    sender_email: str,
    to_email: str,
    subject: str,
    body: str,
    cv_path: Path | None = None,
) -> EmailMessage:
    """Build an editable RFC 5322 draft email (.eml) with optional PDF CV attachment."""
    msg = EmailMessage()
    if sender_email and "A COMPLETER" not in sender_email.upper() and "À COMPLÉTER" not in sender_email.upper():
        msg["From"] = f"{sender_name} <{sender_email}>" if sender_name else sender_email
    if to_email:
        msg["To"] = to_email
    msg["Subject"] = subject
    msg["X-Unsent"] = "1"
    msg.set_content(body, subtype="plain", charset="utf-8")

    if cv_path and cv_path.is_file():
        pdf_bytes = cv_path.read_bytes()
        msg.add_attachment(
            pdf_bytes,
            maintype="application",
            subtype="pdf",
            filename=cv_path.name,
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
    contact_line = (
        f"`{effective_to}` (source: `{enriched.email_source}`)"
        if effective_to
        else f"Aucun email trouvé — postuler via le formulaire : {offer.apply_url or offer.url}"
    )
    cv_status = (
        f"Joint automatiquement dans `draft.eml` (`{cv_path_str}`)"
        if cv_attached
        else "Non joint (renseigner `candidate.cv_path` dans `profile.yaml`)"
    )

    lines = [
        f"# {offer.company} — {offer.title}",
        "",
        "## Informations de l'offre",
        f"- **ID** : `{offer.id}`",
        f"- **Contrat** : `{offer.contract_type}`",
        f"- **Source** : `{offer.source}`",
        f"- **Lieu** : {offer.location or 'Non précisé'}"
        + (f" ({offer.remote})" if offer.remote else ""),
        f"- **Langue détectée** : `{offer.language}`",
        f"- **Salaire / Gratification** : {offer.salary or 'Non précisé'}",
        f"- **Lien offre** : {offer.url}",
        f"- **Lien candidature directe** : {offer.apply_url or offer.url}",
        "",
        "## Contact & Pièces jointes",
        f"- **Destinataire** : {contact_line}",
        f"- **CV PDF** : {cv_status}",
        "",
        "## Brouillon d'email",
        f"**Objet :** `{draft.subject}`",
        "",
        "```text",
        draft.body.rstrip(),
        "```",
        "",
    ]
    return "\n".join(lines)


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


def write_application_draft(
    *,
    enriched: EnrichedOffer,
    draft: DraftInput,
    profile: dict[str, Any],
    output_root: Path,
    run_date: str | None = None,
    profile_dir: Path | None = None,
) -> Path:
    """Create application.md and draft.eml inside drafts/<date>/<company>_<title>/."""
    day_folder = run_date or date.today().isoformat()
    offer = enriched.offer
    folder_name = f"{slugify(offer.company, 28)}_{slugify(offer.title, 36)}_{offer.id[:6]}"
    target_dir = output_root / day_folder / folder_name
    target_dir.mkdir(parents=True, exist_ok=True)

    candidate = profile.get("candidate", {}) or {}
    sender_name = str(candidate.get("full_name", "")).strip()
    sender_email = str(candidate.get("email", "")).strip()
    cv_path = resolve_cv_path(str(candidate.get("cv_path", "")), profile_dir=profile_dir)
    cv_exists = bool(cv_path and cv_path.is_file())

    effective_to = draft.to_email or enriched.contact_email
    msg = build_eml_message(
        sender_name=sender_name,
        sender_email=sender_email,
        to_email=effective_to,
        subject=draft.subject,
        body=draft.body,
        cv_path=cv_path if cv_exists else None,
    )

    eml_path = target_dir / "draft.eml"
    eml_path.write_bytes(msg.as_bytes())

    md_content = render_application_markdown(
        enriched=enriched,
        draft=draft,
        cv_attached=cv_exists,
        cv_path_str=str(cv_path) if cv_path else "",
    )
    (target_dir / "application.md").write_text(md_content, encoding="utf-8")

    return target_dir
