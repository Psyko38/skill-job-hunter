"""Shared text, URL, and identifier normalization helpers."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "ref",
        "trk",
        "trkinfo",
        "refid",
        "trackingid",
        "fbclid",
        "gclid",
    }
)


def normalize_text(text: str) -> str:
    """Lowercase text, strip accents, and collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", stripped).strip().lower()


def slugify(text: str, max_length: int = 48) -> str:
    """Convert text into a filesystem-safe lowercase slug."""
    normalized = normalize_text(text)
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return (slug[:max_length].strip("-")) or "item"


def canonicalize_url(url: str) -> str:
    """Normalize a URL by removing tracking query params and fragments."""
    if not url:
        return ""
    parsed = urlparse(url.strip())
    kept_params = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS
    ]
    cleaned = parsed._replace(
        scheme=parsed.scheme.lower() or "https",
        netloc=parsed.netloc.lower(),
        path=parsed.path.rstrip("/") or "/",
        query=urlencode(sorted(kept_params)),
        fragment="",
    )
    return urlunparse(cleaned)


def make_offer_fingerprint(company: str, title: str) -> str:
    """Compute a stable cross-board deduplication key for (company, title)."""
    key = f"{normalize_text(company)}::{normalize_text(title)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def make_offer_id(source: str, url: str, company: str, title: str) -> str:
    """Compute a short deterministic identifier for an offer."""
    canonical = canonicalize_url(url) or f"{source}:{company}:{title}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
