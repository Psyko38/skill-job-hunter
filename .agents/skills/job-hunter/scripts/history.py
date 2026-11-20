"""Local offer deduplication and application tracking (JSON + CSV export)."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import JobOffer, OfferStatus
from .security import atomic_write_text, sanitize_csv_cell
from .text_utils import canonicalize_url, make_offer_fingerprint

_CSV_FIELDS = (
    "id",
    "status",
    "company",
    "title",
    "contract_type",
    "source",
    "location",
    "contact_email",
    "match_score",
    "url",
    "apply_url",
    "reason",
    "notes",
    "draft_dir",
    "updated_at",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


class OfferHistory:
    """Persistent store tracking seen, rejected, drafted, sent, and interview/offer outcomes."""

    def __init__(self, json_path: Path, csv_path: Path | None = None) -> None:
        self.json_path = json_path
        self.csv_path = csv_path or json_path.with_name("applications.csv")
        self.records: dict[str, dict[str, Any]] = {}
        self._url_index: dict[str, str] = {}
        self._fp_index: dict[str, str] = {}
        self.load()

    def load(self) -> None:
        """Load existing history from JSON if present."""
        if not self.json_path.exists():
            self.records = {}
            self._rebuild_indices()
            return
        try:
            data = json.loads(self.json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Failed to read history file {self.json_path}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("offers", {}), dict):
            raise RuntimeError(f"Invalid history file structure in {self.json_path}")
        self.records = data.get("offers", {})
        self._rebuild_indices()

    def _rebuild_indices(self) -> None:
        self._url_index = {}
        self._fp_index = {}
        for oid, rec in self.records.items():
            canon_url = rec.get("canonical_url")
            if canon_url:
                self._url_index[canon_url] = oid
            fp = rec.get("fingerprint")
            if fp:
                self._fp_index[fp] = oid

    def find_existing_record(self, offer: JobOffer) -> dict[str, Any] | None:
        """Return an existing tracked record matching URL or (company, title) fingerprint."""
        canon_url = canonicalize_url(offer.url)
        if canon_url in self._url_index:
            return self.records.get(self._url_index[canon_url])
        fingerprint = make_offer_fingerprint(offer.company, offer.title)
        if fingerprint in self._fp_index:
            return self.records.get(self._fp_index[fingerprint])
        return None

    def is_duplicate(
        self,
        offer: JobOffer,
        *,
        ignore_statuses: set[str] | None = None,
    ) -> bool:
        """Check whether an offer was already seen by URL or (company, title) fingerprint."""
        existing = self.find_existing_record(offer)
        if existing is None:
            return False
        if ignore_statuses and existing.get("status") in ignore_statuses:
            return False
        return True

    def record_offer(
        self,
        offer: JobOffer,
        status: OfferStatus,
        *,
        contact_email: str = "",
        reason: str = "",
        draft_dir: str = "",
        match_score: int | None = None,
        notes: str = "",
    ) -> None:
        """Insert or update an offer record in history."""
        canon_url = canonicalize_url(offer.url)
        fingerprint = make_offer_fingerprint(offer.company, offer.title)
        existing = self.records.get(offer.id, {})
        self.records[offer.id] = {
            "id": offer.id,
            "status": status,
            "company": offer.company,
            "title": offer.title,
            "contract_type": offer.contract_type,
            "source": offer.source,
            "location": offer.location,
            "contact_email": contact_email or existing.get("contact_email", ""),
            "match_score": match_score if match_score is not None else existing.get("match_score", ""),
            "url": offer.url,
            "apply_url": offer.apply_url or offer.url,
            "canonical_url": canon_url,
            "fingerprint": fingerprint,
            "reason": reason or existing.get("reason", ""),
            "notes": notes or existing.get("notes", ""),
            "draft_dir": draft_dir or existing.get("draft_dir", ""),
            "created_at": existing.get("created_at", _now_iso()),
            "updated_at": _now_iso(),
        }
        self._rebuild_indices()

    def update_status(
        self,
        offer_id: str,
        status: OfferStatus,
        draft_dir: str = "",
        notes: str = "",
    ) -> bool:
        """Update status (and optional draft_dir/notes) for a tracked offer ID."""
        if offer_id not in self.records:
            return False
        self.records[offer_id]["status"] = status
        if draft_dir:
            self.records[offer_id]["draft_dir"] = draft_dir
        if notes:
            self.records[offer_id]["notes"] = notes
        self.records[offer_id]["updated_at"] = _now_iso()
        return True

    def get_stats(self, *, follow_up_days: int = 7) -> dict[str, Any]:
        """Compute funnel statistics and list sent applications due for follow-up."""
        by_status: dict[str, int] = {}
        by_source: dict[str, int] = {}
        follow_up_due: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)

        for rec in self.records.values():
            status = str(rec.get("status", "unknown"))
            source = str(rec.get("source", "unknown"))
            by_status[status] = by_status.get(status, 0) + 1
            by_source[source] = by_source.get(source, 0) + 1

            if status == "sent":
                updated = _parse_iso(str(rec.get("updated_at", "")))
                if updated is not None:
                    age_days = (now - updated).days
                    if age_days >= follow_up_days:
                        follow_up_due.append(
                            {
                                "id": rec.get("id", ""),
                                "company": rec.get("company", ""),
                                "title": rec.get("title", ""),
                                "contact_email": rec.get("contact_email", ""),
                                "days_since_sent": age_days,
                                "apply_url": rec.get("apply_url", "") or rec.get("url", ""),
                            }
                        )

        follow_up_due.sort(key=lambda item: item["days_since_sent"], reverse=True)
        sent_total = (
            by_status.get("sent", 0)
            + by_status.get("interview", 0)
            + by_status.get("offer", 0)
            + by_status.get("rejected", 0)
        )
        positive_responses = by_status.get("interview", 0) + by_status.get("offer", 0)
        response_rate = round((positive_responses / sent_total) * 100, 1) if sent_total > 0 else 0.0

        return {
            "total_tracked": len(self.records),
            "by_status": by_status,
            "by_source": by_source,
            "applied_total": sent_total,
            "positive_responses": positive_responses,
            "positive_rate_pct": response_rate,
            "follow_up_days_threshold": follow_up_days,
            "follow_up_due": follow_up_due,
        }

    def save(self) -> None:
        """Persist JSON history and CSV export atomically with CSV formula injection protection."""
        payload = {"updated_at": _now_iso(), "offers": self.records}
        atomic_write_text(
            self.json_path,
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(_CSV_FIELDS))
        writer.writeheader()
        for record in self.records.values():
            writer.writerow(
                {field: sanitize_csv_cell(record.get(field, "")) for field in _CSV_FIELDS}
            )
        atomic_write_text(self.csv_path, buf.getvalue(), encoding="utf-8")
