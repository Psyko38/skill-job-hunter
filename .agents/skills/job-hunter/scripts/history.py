"""Local offer deduplication and application tracking (JSON + CSV export)."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import JobOffer, OfferStatus
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
    "url",
    "apply_url",
    "reason",
    "draft_dir",
    "updated_at",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class OfferHistory:
    """Persistent store tracking seen, rejected, drafted, and sent offers."""

    def __init__(self, json_path: Path, csv_path: Path | None = None) -> None:
        self.json_path = json_path
        self.csv_path = csv_path or json_path.with_name("applications.csv")
        self.records: dict[str, dict[str, Any]] = {}
        self._url_index: set[str] = set()
        self._fp_index: set[str] = set()
        self.load()

    def load(self) -> None:
        """Load existing history from JSON if present."""
        if not self.json_path.exists():
            self.records = {}
            self._rebuild_indices()
            return
        data = json.loads(self.json_path.read_text(encoding="utf-8"))
        self.records = data.get("offers", {})
        self._rebuild_indices()

    def _rebuild_indices(self) -> None:
        self._url_index = {
            rec["canonical_url"]
            for rec in self.records.values()
            if rec.get("canonical_url")
        }
        self._fp_index = {
            rec["fingerprint"]
            for rec in self.records.values()
            if rec.get("fingerprint")
        }

    def is_duplicate(self, offer: JobOffer) -> bool:
        """Check whether an offer was already seen by URL or (company, title) fingerprint."""
        canon_url = canonicalize_url(offer.url)
        fingerprint = make_offer_fingerprint(offer.company, offer.title)
        return (canon_url in self._url_index) or (fingerprint in self._fp_index)

    def record_offer(
        self,
        offer: JobOffer,
        status: OfferStatus,
        *,
        contact_email: str = "",
        reason: str = "",
        draft_dir: str = "",
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
            "url": offer.url,
            "apply_url": offer.apply_url or offer.url,
            "canonical_url": canon_url,
            "fingerprint": fingerprint,
            "reason": reason or existing.get("reason", ""),
            "draft_dir": draft_dir or existing.get("draft_dir", ""),
            "created_at": existing.get("created_at", _now_iso()),
            "updated_at": _now_iso(),
        }
        self._rebuild_indices()

    def update_status(self, offer_id: str, status: OfferStatus, draft_dir: str = "") -> bool:
        """Update status for a tracked offer ID. Returns False if unknown."""
        if offer_id not in self.records:
            return False
        self.records[offer_id]["status"] = status
        if draft_dir:
            self.records[offer_id]["draft_dir"] = draft_dir
        self.records[offer_id]["updated_at"] = _now_iso()
        return True

    def save(self) -> None:
        """Persist JSON history atomically and refresh CSV export."""
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.json_path.with_suffix(".tmp")
        payload = {"updated_at": _now_iso(), "offers": self.records}
        tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(self.json_path)

        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(_CSV_FIELDS))
            writer.writeheader()
            for record in self.records.values():
                writer.writerow({field: record.get(field, "") for field in _CSV_FIELDS})
