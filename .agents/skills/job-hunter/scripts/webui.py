"""Local Material Design 3 WebUI server for Job Hunter (stdlib-only, localhost-bound)."""

from __future__ import annotations

from datetime import datetime, timezone
from email import policy as email_policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
import webbrowser

import yaml

from .draft_builder import resolve_cv_path
from .history import OfferHistory, _parse_iso
from .models import EnrichedOffer
from .security import (
    ensure_within_directory,
    sanitize_header_value,
    sanitize_untrusted_text,
    sanitize_url,
    validate_pdf_file,
)

STATIC_DIR = Path(__file__).resolve().parent / "webui_static"
MAX_JSON_BODY_BYTES = 65_536

ALLOWED_STATUSES: tuple[str, ...] = (
    "candidates_extracted",
    "drafted",
    "sent",
    "interview",
    "offer",
    "rejected",
    "ignored",
)

_RUN_ID_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$")
_MD_ID_RE = re.compile(r"\*\*ID\*\*\s*:\s*`([A-Za-z0-9_-]{6,64})`")
_SAFE_ASSET_MAP: dict[str, tuple[str, str]] = {
    "styles.css": ("styles.css", "text/css; charset=utf-8"),
    "app.js": ("app.js", "application/javascript; charset=utf-8"),
}

_SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    (
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    ),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_int_score(raw: Any) -> int | None:
    if raw is None or raw == "" or isinstance(raw, bool):
        return None
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return None
    return max(0, min(100, val))


def _run_id_to_iso(run_id: str) -> str:
    match = _RUN_ID_RE.match(run_id)
    if not match:
        return ""
    y, mo, d, h, mi, s = match.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:{s}+00:00"


def _normalize_host(raw_host: str) -> str:
    host = (raw_host or "").strip().lower()
    if not host:
        return ""
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end != -1 else host
    if ":" in host:
        return host.split(":", 1)[0]
    return host


def _parse_eml_file(eml_path: Path) -> dict[str, Any]:
    """Parse a local draft.eml safely into structured metadata and plain text body."""
    try:
        raw = eml_path.read_bytes()
        msg = BytesParser(policy=email_policy.default).parsebytes(raw)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Unable to parse EML: {exc}"}

    body_text = ""
    attachments: list[dict[str, Any]] = []

    if msg.is_multipart():
        for part in msg.walk():
            disposition = (part.get_content_disposition() or "").lower()
            content_type = (part.get_content_type() or "").lower()
            filename = part.get_filename()
            if disposition == "attachment" or filename:
                payload = part.get_payload(decode=True) or b""
                attachments.append(
                    {
                        "filename": sanitize_header_value(filename or "attachment"),
                        "content_type": content_type,
                        "size_bytes": len(payload),
                    }
                )
            elif content_type == "text/plain" and not body_text:
                try:
                    body_text = str(part.get_content())
                except Exception:  # noqa: BLE001
                    payload = part.get_payload(decode=True) or b""
                    body_text = payload.decode("utf-8", errors="replace")
    else:
        try:
            body_text = str(msg.get_content())
        except Exception:  # noqa: BLE001
            payload = msg.get_payload(decode=True) or b""
            body_text = payload.decode("utf-8", errors="replace")

    return {
        "from": str(msg.get("From", "") or ""),
        "to": str(msg.get("To", "") or ""),
        "subject": str(msg.get("Subject", "") or ""),
        "x_unsent": str(msg.get("X-Unsent", "") or ""),
        "body": body_text.strip(),
        "attachments": attachments,
        "has_cv_attachment": any(
            att.get("content_type") == "application/pdf"
            or str(att.get("filename", "")).lower().endswith(".pdf")
            for att in attachments
        ),
    }


class WebUIDataStore:
    """Read/write aggregator over Job Hunter's history, runs, drafts, and profile."""

    def __init__(
        self,
        *,
        history_path: Path,
        runs_dir: Path,
        drafts_dir: Path,
        profile_path: Path,
        default_follow_up_days: int = 7,
    ) -> None:
        self.history_path = history_path
        self.runs_dir = runs_dir
        self.drafts_dir = drafts_dir
        self.profile_path = profile_path
        self.default_follow_up_days = max(1, default_follow_up_days)
        self._lock = threading.Lock()

    def _load_profile_summary(self) -> dict[str, Any]:
        if not self.profile_path.is_file():
            return {"configured": False, "full_name": "", "cv_valid": False, "defaults": {}}
        try:
            data = yaml.safe_load(self.profile_path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            return {"configured": False, "full_name": "", "cv_valid": False, "defaults": {}}
        if not isinstance(data, dict):
            return {"configured": False, "full_name": "", "cv_valid": False, "defaults": {}}
        candidate = data.get("candidate") or {}
        defaults = data.get("defaults") or {}
        cv_raw = str(candidate.get("cv_path", "") or "").strip()
        cv_path = resolve_cv_path(cv_raw, profile_dir=self.profile_path.parent) if cv_raw else None
        cv_valid = bool(cv_path and validate_pdf_file(cv_path)[0])
        return {
            "configured": True,
            "full_name": str(candidate.get("full_name", "") or "").strip(),
            "location": str(candidate.get("location", "") or defaults.get("location", "") or "").strip(),
            "cv_valid": cv_valid,
            "cv_filename": cv_path.name if cv_path and cv_valid else "",
            "defaults": {
                "contracts": list(defaults.get("contracts") or []),
                "queries": list(defaults.get("queries") or []),
                "location": str(defaults.get("location", "") or ""),
            },
        }

    def _index_enriched_candidates(self) -> dict[str, tuple[EnrichedOffer, str]]:
        """Scan all runs chronologically so newer scraped runs win for the same offer_id."""
        indexed: dict[str, tuple[EnrichedOffer, str]] = {}
        if not self.runs_dir.is_dir():
            return indexed

        run_dirs = sorted(
            (p for p in self.runs_dir.iterdir() if p.is_dir()),
            key=lambda p: p.name,
        )
        for run_dir in run_dirs:
            cand_path = run_dir / "candidates.json"
            if not cand_path.is_file():
                continue
            try:
                raw_list = json.loads(cand_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(raw_list, list):
                continue
            for entry in raw_list:
                if not isinstance(entry, dict) or "offer" not in entry:
                    continue
                try:
                    enriched = EnrichedOffer.from_dict(entry)
                except Exception:  # noqa: BLE001
                    continue
                # Distinguish genuinely scored entries from older unscored runs
                if "match_score" not in entry:
                    enriched.match_score = -1
                indexed[enriched.offer.id] = (enriched, run_dir.name)
        return indexed

    def _safe_resolve_draft_dir(self, raw_dir: str) -> Path | None:
        if not raw_dir or not self.drafts_dir.is_dir():
            return None
        candidate = Path(raw_dir)
        if not candidate.is_absolute():
            candidate = self.drafts_dir.parent / candidate
        try:
            resolved = ensure_within_directory(candidate, self.drafts_dir)
        except ValueError:
            return None
        return resolved if resolved.is_dir() else None

    def _scan_drafts(self) -> tuple[list[dict[str, Any]], dict[str, Path]]:
        """Discover all draft directories inside drafts_dir and map offer_id -> draft_dir."""
        drafts_list: list[dict[str, Any]] = []
        by_offer_id: dict[str, Path] = {}
        if not self.drafts_dir.is_dir():
            return drafts_list, by_offer_id

        date_dirs = sorted(
            (p for p in self.drafts_dir.iterdir() if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )
        for date_dir in date_dirs:
            folder_dirs = sorted(
                (p for p in date_dir.iterdir() if p.is_dir()),
                key=lambda p: p.name,
            )
            for folder in folder_dirs:
                eml_path = folder / "draft.eml"
                md_path = folder / "application.md"
                if not eml_path.is_file() and not md_path.is_file():
                    continue
                offer_id = ""
                if md_path.is_file():
                    try:
                        md_text = md_path.read_text(encoding="utf-8", errors="replace")
                        match = _MD_ID_RE.search(md_text)
                        if match:
                            offer_id = match.group(1)
                    except OSError:
                        pass
                eml_info = _parse_eml_file(eml_path) if eml_path.is_file() else {}
                rel_path = f"{date_dir.name}/{folder.name}"
                if offer_id:
                    by_offer_id.setdefault(offer_id, folder)
                drafts_list.append(
                    {
                        "offer_id": offer_id,
                        "date": date_dir.name,
                        "folder": folder.name,
                        "rel_path": rel_path,
                        "has_eml": eml_path.is_file(),
                        "has_md": md_path.is_file(),
                        "subject": eml_info.get("subject", ""),
                        "to": eml_info.get("to", ""),
                        "body_preview": str(eml_info.get("body", ""))[:260],
                        "has_cv_attachment": bool(eml_info.get("has_cv_attachment", False)),
                        "attachments": eml_info.get("attachments", []),
                    }
                )
        return drafts_list, by_offer_id

    def _build_offer_summary(
        self,
        rec: dict[str, Any],
        *,
        enriched_entry: tuple[EnrichedOffer, str] | None,
        fallback_draft_dir: Path | None,
        follow_up_days: int,
        now: datetime,
        include_details: bool = False,
    ) -> dict[str, Any]:
        enriched = enriched_entry[0] if enriched_entry else None
        run_id = enriched_entry[1] if enriched_entry else ""
        offer = enriched.offer if enriched else None

        status = str(rec.get("status", "candidates_extracted"))
        updated_str = str(rec.get("updated_at", "") or "")
        created_str = str(rec.get("created_at", "") or "")
        updated_dt = _parse_iso(updated_str)
        age_days = max(0, (now - updated_dt).days) if updated_dt is not None else 0
        needs_follow_up = status == "sent" and age_days >= follow_up_days

        score = _parse_int_score(rec.get("match_score"))
        if score is None and enriched is not None and enriched.match_score >= 0:
            score = enriched.match_score

        contact_email = str(rec.get("contact_email", "") or (enriched.contact_email if enriched else "") or "")
        apply_channel = (
            enriched.apply_channel
            if enriched is not None
            else ("email" if contact_email else "jobboard")
        )
        if contact_email and apply_channel == "jobboard":
            apply_channel = "email"

        draft_dir = self._safe_resolve_draft_dir(str(rec.get("draft_dir", "") or "")) or fallback_draft_dir
        has_eml = bool(draft_dir and (draft_dir / "draft.eml").is_file())
        has_md = bool(draft_dir and (draft_dir / "application.md").is_file())
        draft_rel_path = ""
        if draft_dir is not None:
            try:
                draft_rel_path = draft_dir.relative_to(self.drafts_dir.resolve()).as_posix()
            except ValueError:
                draft_rel_path = draft_dir.name

        safe_url = sanitize_url(str(rec.get("url", "") or (offer.url if offer else "")))
        safe_apply_url = sanitize_url(
            str(rec.get("apply_url", "") or (offer.apply_url if offer else "") or safe_url)
        )

        summary: dict[str, Any] = {
            "id": str(rec.get("id", "")),
            "status": status,
            "company": str(rec.get("company", "") or (offer.company if offer else "")),
            "title": str(rec.get("title", "") or (offer.title if offer else "")),
            "contract_type": str(rec.get("contract_type", "") or (offer.contract_type if offer else "")),
            "source": str(rec.get("source", "") or (offer.source if offer else "")),
            "location": str(rec.get("location", "") or (offer.location if offer else "")),
            "remote": offer.remote if offer else "",
            "salary": offer.salary if offer else "",
            "language": offer.language if offer else "fr",
            "contact_email": contact_email,
            "email_source": enriched.email_source if enriched else ("history" if contact_email else "none"),
            "apply_channel": apply_channel,
            "match_score": score,
            "matched_skills": list(enriched.matched_skills) if enriched else [],
            "missing_skills": list(enriched.missing_skills) if enriched else [],
            "security_warnings": list(enriched.security_warnings) if enriched else [],
            "url": safe_url,
            "apply_url": safe_apply_url or safe_url,
            "reason": str(rec.get("reason", "") or ""),
            "notes": str(rec.get("notes", "") or ""),
            "has_draft": bool(has_eml or has_md),
            "has_eml": has_eml,
            "has_md": has_md,
            "draft_rel_path": draft_rel_path,
            "run_id": run_id,
            "created_at": created_str,
            "updated_at": updated_str,
            "days_since_update": age_days,
            "needs_follow_up": needs_follow_up,
            "has_description": bool(offer and offer.description),
        }

        if include_details:
            summary["description"] = offer.description if offer else ""
            summary["score_breakdown"] = dict(enriched.score_breakdown) if enriched else {}
            summary["published_at"] = offer.published_at if offer else ""
            summary["emails_found"] = list(offer.emails_found) if offer else []
            if draft_dir is not None:
                draft_detail: dict[str, Any] = {
                    "rel_path": draft_rel_path,
                    "has_eml": has_eml,
                    "has_md": has_md,
                    "eml_download_url": f"/api/drafts/{summary['id']}/eml" if has_eml else "",
                }
                if has_eml:
                    draft_detail["eml"] = _parse_eml_file(draft_dir / "draft.eml")
                if has_md:
                    try:
                        draft_detail["markdown"] = (draft_dir / "application.md").read_text(
                            encoding="utf-8", errors="replace"
                        )
                    except OSError:
                        draft_detail["markdown"] = ""
                summary["draft"] = draft_detail
            else:
                summary["draft"] = None

        return summary

    def _load_runs(self) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        if not self.runs_dir.is_dir():
            return runs

        run_dirs = sorted(
            (p for p in self.runs_dir.iterdir() if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )
        for run_dir in run_dirs:
            report_path = run_dir / "report.json"
            cand_path = run_dir / "candidates.json"
            report: dict[str, Any] = {}
            if report_path.is_file():
                try:
                    parsed = json.loads(report_path.read_text(encoding="utf-8"))
                    if isinstance(parsed, dict):
                        report = parsed
                except Exception:  # noqa: BLE001
                    report = {}
            candidates_count = 0
            if cand_path.is_file():
                try:
                    raw_c = json.loads(cand_path.read_text(encoding="utf-8"))
                    if isinstance(raw_c, list):
                        candidates_count = len(raw_c)
                except Exception:  # noqa: BLE001
                    candidates_count = 0

            raw_query = report.get("query", "")
            queries = raw_query if isinstance(raw_query, list) else ([raw_query] if raw_query else [])
            raw_contract = report.get("contract", "")
            contracts = (
                raw_contract if isinstance(raw_contract, list) else ([raw_contract] if raw_contract else [])
            )

            stats = report.get("stats") or {}
            runs.append(
                {
                    "run_id": run_dir.name,
                    "timestamp": _run_id_to_iso(run_dir.name),
                    "queries": [str(q) for q in queries],
                    "contracts": [str(c) for c in contracts],
                    "location": str(report.get("location", "") or ""),
                    "remote": bool(report.get("remote", False)),
                    "sources": report.get("sources") or [],
                    "stats": {
                        "scraped": int(stats.get("scraped", 0) or 0),
                        "rejected_schools": int(stats.get("rejected_schools", 0) or 0),
                        "duplicates": int(stats.get("duplicates", 0) or 0),
                        "kept": int(stats.get("kept", candidates_count) or 0),
                    },
                    "extended_stats": report.get("extended_stats") or {},
                    "rejected_school_details": report.get("rejected_school_details") or [],
                }
            )
        return runs

    def get_dashboard(self, *, follow_up_days: int | None = None) -> dict[str, Any]:
        days = max(1, follow_up_days if follow_up_days is not None else self.default_follow_up_days)
        now = datetime.now(timezone.utc)

        with self._lock:
            history = OfferHistory(self.history_path)
            base_stats = history.get_stats(follow_up_days=days)
            enriched_index = self._index_enriched_candidates()
            drafts_list, drafts_by_offer = self._scan_drafts()
            runs = self._load_runs()
            profile_summary = self._load_profile_summary()

            offers: list[dict[str, Any]] = []
            by_contract: dict[str, int] = {}
            scores: list[int] = []
            with_email = 0
            with_draft = 0
            security_flagged = 0

            for oid, rec in history.records.items():
                summary = self._build_offer_summary(
                    rec,
                    enriched_entry=enriched_index.get(oid),
                    fallback_draft_dir=drafts_by_offer.get(oid),
                    follow_up_days=days,
                    now=now,
                    include_details=False,
                )
                offers.append(summary)
                contract = summary["contract_type"] or "autre"
                by_contract[contract] = by_contract.get(contract, 0) + 1
                if summary["match_score"] is not None:
                    scores.append(summary["match_score"])
                if summary["contact_email"]:
                    with_email += 1
                if summary["has_draft"]:
                    with_draft += 1
                if summary["security_warnings"]:
                    security_flagged += 1

        offers.sort(
            key=lambda item: (
                item["updated_at"],
                item["match_score"] if item["match_score"] is not None else -1,
            ),
            reverse=True,
        )

        offers_by_id = {item["id"]: item for item in offers}
        draft_meta_by_id = {d["offer_id"]: d for d in drafts_list if d.get("offer_id")}

        # Enrich drafts list with current offer status/company/title from history
        enriched_drafts: list[dict[str, Any]] = []
        for draft_item in drafts_list:
            oid = draft_item.get("offer_id", "")
            linked = offers_by_id.get(oid)
            enriched_drafts.append(
                {
                    **draft_item,
                    "company": linked["company"] if linked else draft_item["folder"].split("_")[0],
                    "title": linked["title"] if linked else draft_item["folder"],
                    "status": linked["status"] if linked else "drafted",
                    "contract_type": linked["contract_type"] if linked else "",
                    "location": linked["location"] if linked else "",
                    "apply_url": linked["apply_url"] if linked else "",
                    "eml_download_url": (
                        f"/api/drafts/{oid}/eml" if oid and draft_item["has_eml"] else ""
                    ),
                }
            )

        drafts_to_send = []
        for item in offers:
            if item["status"] == "drafted":
                dmeta = draft_meta_by_id.get(item["id"], {})
                drafts_to_send.append(
                    {
                        **item,
                        "draft_subject": dmeta.get("subject", ""),
                        "draft_to": dmeta.get("to", "") or item["contact_email"],
                        "has_cv_attachment": bool(dmeta.get("has_cv_attachment", False)),
                        "eml_download_url": f"/api/drafts/{item['id']}/eml" if item["has_eml"] else "",
                    }
                )

        follow_ups = sorted(
            (item for item in offers if item["needs_follow_up"]),
            key=lambda item: item["days_since_update"],
            reverse=True,
        )

        to_review = sorted(
            (item for item in offers if item["status"] == "candidates_extracted"),
            key=lambda item: (
                item["match_score"] if item["match_score"] is not None else -1,
                bool(item["contact_email"]),
                item["updated_at"],
            ),
            reverse=True,
        )

        avg_score = round(sum(scores) / len(scores), 1) if scores else None
        stats = {
            **base_stats,
            "by_contract": by_contract,
            "with_email": with_email,
            "with_draft": with_draft,
            "security_flagged": security_flagged,
            "avg_score": avg_score,
            "runs_count": len(runs),
            "drafts_count": len(enriched_drafts),
            "action_required_count": len(drafts_to_send) + len(follow_ups),
        }

        return {
            "generated_at": _now_iso(),
            "candidate": profile_summary,
            "stats": stats,
            "tasks": {
                "drafts_to_send": drafts_to_send,
                "follow_ups": follow_ups,
                "to_review": to_review,
            },
            "offers": offers,
            "drafts": enriched_drafts,
            "runs": runs,
        }

    def get_offer_detail(
        self, offer_id: str, *, follow_up_days: int | None = None
    ) -> dict[str, Any] | None:
        days = max(1, follow_up_days if follow_up_days is not None else self.default_follow_up_days)
        now = datetime.now(timezone.utc)
        with self._lock:
            history = OfferHistory(self.history_path)
            rec = history.records.get(offer_id)
            if rec is None:
                return None
            enriched_index = self._index_enriched_candidates()
            _, drafts_by_offer = self._scan_drafts()
            return self._build_offer_summary(
                rec,
                enriched_entry=enriched_index.get(offer_id),
                fallback_draft_dir=drafts_by_offer.get(offer_id),
                follow_up_days=days,
                now=now,
                include_details=True,
            )

    def update_offer(
        self,
        offer_id: str,
        *,
        status: str | None = None,
        notes: str | None = None,
        follow_up_days: int | None = None,
    ) -> dict[str, Any] | None:
        if status is not None and status not in ALLOWED_STATUSES:
            raise ValueError(f"Unsupported status '{status}'. Allowed: {', '.join(ALLOWED_STATUSES)}")

        clean_notes: str | None = None
        if notes is not None:
            clean_notes = sanitize_untrusted_text(str(notes), max_length=2000, single_line=False)

        with self._lock:
            history = OfferHistory(self.history_path)
            rec = history.records.get(offer_id)
            if rec is None:
                return None
            target_status = status if status is not None else str(rec.get("status", "candidates_extracted"))
            history.update_status(
                offer_id,
                target_status,  # type: ignore[arg-type]
                notes=clean_notes if clean_notes is not None else "",
                replace_notes=clean_notes is not None,
                preserve_timestamp_if_same_status=(status is None or status == rec.get("status")),
            )
            history.save()

        return self.get_offer_detail(offer_id, follow_up_days=follow_up_days)

    def resolve_eml_path(self, offer_id: str) -> tuple[Path, str] | None:
        """Return validated (eml_path, safe_download_filename) for an offer ID."""
        with self._lock:
            history = OfferHistory(self.history_path)
            rec = history.records.get(offer_id)
            _, drafts_by_offer = self._scan_drafts()

        draft_dir: Path | None = None
        if rec is not None:
            draft_dir = self._safe_resolve_draft_dir(str(rec.get("draft_dir", "") or ""))
        if draft_dir is None:
            draft_dir = drafts_by_offer.get(offer_id)
        if draft_dir is None:
            return None

        try:
            eml_path = ensure_within_directory(draft_dir / "draft.eml", self.drafts_dir)
        except ValueError:
            return None
        if not eml_path.is_file():
            return None

        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", draft_dir.name).strip("_") or offer_id
        return eml_path, f"{safe_name}.eml"

    def get_csv_bytes(self) -> bytes | None:
        with self._lock:
            history = OfferHistory(self.history_path)
            if not history.csv_path.is_file():
                history.save()
            if not history.csv_path.is_file():
                return None
            return history.csv_path.read_bytes()


class JobHunterWebServer(ThreadingHTTPServer):
    """Threaded localhost HTTP server carrying the Job Hunter data store."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        store: WebUIDataStore,
        allowed_hosts: set[str],
    ) -> None:
        self.store = store
        self.allowed_hosts = allowed_hosts
        super().__init__(server_address, _WebUIRequestHandler)


class _WebUIRequestHandler(BaseHTTPRequestHandler):
    server: JobHunterWebServer  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Keep stdout clean when running locally or inside tests
        return

    def _send_security_headers(self) -> None:
        for key, value in _SECURITY_HEADERS:
            self.send_header(key, value)

    def _send_bytes(
        self,
        data: bytes,
        *,
        status: int = HTTPStatus.OK,
        content_type: str = "application/octet-stream",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self._send_security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, val in extra_headers.items():
                self.send_header(key, val)
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: Any, *, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(body, status=status, content_type="application/json; charset=utf-8")

    def _validate_host_header(self) -> bool:
        host = _normalize_host(self.headers.get("Host", ""))
        return not host or host in self.server.allowed_hosts

    def _validate_origin_header(self) -> bool:
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        parsed = urlparse(origin)
        origin_host = (parsed.hostname or "").lower()
        return parsed.scheme in {"http", "https"} and origin_host in self.server.allowed_hosts

    def do_GET(self) -> None:  # noqa: N802
        if not self._validate_host_header():
            self._send_json({"error": "Invalid Host header"}, status=HTTPStatus.FORBIDDEN)
            return

        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path in {"/", "/index.html"}:
            index_path = STATIC_DIR / "index.html"
            if not index_path.is_file():
                self._send_json({"error": "WebUI assets not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_bytes(index_path.read_bytes(), content_type="text/html; charset=utf-8")
            return

        if path.startswith("/assets/"):
            asset_name = path[len("/assets/") :]
            spec = _SAFE_ASSET_MAP.get(asset_name)
            if spec is None:
                self._send_json({"error": "Asset not found"}, status=HTTPStatus.NOT_FOUND)
                return
            rel_name, mime = spec
            asset_path = STATIC_DIR / rel_name
            if not asset_path.is_file():
                self._send_json({"error": "Asset file missing"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_bytes(asset_path.read_bytes(), content_type=mime)
            return

        if path == "/api/dashboard":
            follow_up_days: int | None = None
            raw_days = query.get("follow_up_days", [""])[0]
            if raw_days:
                try:
                    follow_up_days = max(1, min(365, int(raw_days)))
                except ValueError:
                    self._send_json({"error": "Invalid follow_up_days"}, status=HTTPStatus.BAD_REQUEST)
                    return
            payload = self.server.store.get_dashboard(follow_up_days=follow_up_days)
            self._send_json(payload)
            return

        if path.startswith("/api/offers/"):
            offer_id = unquote(path[len("/api/offers/") :]).strip("/")
            if not offer_id or "/" in offer_id:
                self._send_json({"error": "Invalid offer ID"}, status=HTTPStatus.BAD_REQUEST)
                return
            detail = self.server.store.get_offer_detail(offer_id)
            if detail is None:
                self._send_json({"error": f"Offer not found: {offer_id}"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json(detail)
            return

        if path.startswith("/api/drafts/") and path.endswith("/eml"):
            offer_id = unquote(path[len("/api/drafts/") : -len("/eml")]).strip("/")
            if not offer_id or "/" in offer_id:
                self._send_json({"error": "Invalid offer ID"}, status=HTTPStatus.BAD_REQUEST)
                return
            resolved = self.server.store.resolve_eml_path(offer_id)
            if resolved is None:
                self._send_json({"error": "Draft EML not found"}, status=HTTPStatus.NOT_FOUND)
                return
            eml_path, filename = resolved
            self._send_bytes(
                eml_path.read_bytes(),
                content_type="message/rfc822",
                extra_headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
            return

        if path == "/api/export/csv":
            csv_bytes = self.server.store.get_csv_bytes()
            if csv_bytes is None:
                self._send_json({"error": "CSV export unavailable"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_bytes(
                csv_bytes,
                content_type="text/csv; charset=utf-8",
                extra_headers={"Content-Disposition": 'attachment; filename="applications.csv"'},
            )
            return

        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if not self._validate_host_header() or not self._validate_origin_header():
            self._send_json({"error": "Forbidden origin or host"}, status=HTTPStatus.FORBIDDEN)
            return

        parsed = urlparse(self.path)
        path = parsed.path

        if not (path.startswith("/api/offers/") and path.endswith("/status")):
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return

        offer_id = unquote(path[len("/api/offers/") : -len("/status")]).strip("/")
        if not offer_id or "/" in offer_id:
            self._send_json({"error": "Invalid offer ID"}, status=HTTPStatus.BAD_REQUEST)
            return

        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_json(
                {"error": "Content-Type must be application/json"},
                status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            )
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json({"error": "Invalid Content-Length"}, status=HTTPStatus.BAD_REQUEST)
            return
        if length <= 0 or length > MAX_JSON_BODY_BYTES:
            self._send_json({"error": "Invalid request body size"}, status=HTTPStatus.BAD_REQUEST)
            return

        raw_body = self.rfile.read(length)
        try:
            data = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json({"error": "Malformed JSON payload"}, status=HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(data, dict):
            self._send_json({"error": "JSON payload must be an object"}, status=HTTPStatus.BAD_REQUEST)
            return

        status = data.get("status")
        notes = data.get("notes")
        if status is None and notes is None:
            self._send_json(
                {"error": "Provide at least 'status' or 'notes'"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        try:
            updated = self.server.store.update_offer(
                offer_id,
                status=str(status) if status is not None else None,
                notes=str(notes) if notes is not None else None,
            )
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if updated is None:
            self._send_json({"error": f"Offer not found: {offer_id}"}, status=HTTPStatus.NOT_FOUND)
            return

        self._send_json({"ok": True, "offer": updated})


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    history_path: Path,
    runs_dir: Path,
    drafts_dir: Path,
    profile_path: Path,
    follow_up_days: int = 7,
) -> JobHunterWebServer:
    """Create a configured localhost-bound Material Design 3 WebUI server."""
    store = WebUIDataStore(
        history_path=history_path,
        runs_dir=runs_dir,
        drafts_dir=drafts_dir,
        profile_path=profile_path,
        default_follow_up_days=follow_up_days,
    )
    allowed_hosts = {"localhost", "127.0.0.1", "::1", _normalize_host(host)}
    return JobHunterWebServer((host, port), store, allowed_hosts)


def run_webui_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    history_path: Path,
    runs_dir: Path,
    drafts_dir: Path,
    profile_path: Path,
    follow_up_days: int = 7,
    open_browser: bool = False,
) -> int:
    """Start the local WebUI server and block until Ctrl+C."""
    server = create_server(
        host=host,
        port=port,
        history_path=history_path,
        runs_dir=runs_dir,
        drafts_dir=drafts_dir,
        profile_path=profile_path,
        follow_up_days=follow_up_days,
    )
    bound_host, bound_port = server.server_address[:2]
    display_host = "127.0.0.1" if bound_host in {"0.0.0.0", ""} else bound_host
    url = f"http://{display_host}:{bound_port}/"
    print(f"Job Hunter WebUI (Material Design 3) démarrée sur {url}")
    print("Appuyez sur Ctrl+C pour arrêter le serveur.")

    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt de la WebUI Job Hunter.")
    finally:
        server.server_close()
    return 0
