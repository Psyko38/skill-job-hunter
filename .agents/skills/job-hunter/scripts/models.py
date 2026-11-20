"""Typed data models for the job-hunter pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ContractType = Literal["stage", "alternance", "cdd", "cdi"]
LanguageCode = Literal["fr", "en"]
OfferStatus = Literal[
    "rejected_school",
    "candidates_extracted",
    "drafted",
    "sent",
    "ignored",
]
ScrapeStatus = Literal["ok", "blocked_antibot", "error"]


@dataclass(slots=True)
class JobOffer:
    """Normalized job offer extracted from a jobboard."""

    id: str
    source: str
    title: str
    company: str
    location: str
    contract_type: ContractType
    url: str
    apply_url: str = ""
    company_url: str = ""
    remote: str = ""
    description: str = ""
    published_at: str = ""
    salary: str = ""
    language: LanguageCode = "fr"
    emails_found: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the offer to a JSON-compatible dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobOffer:
        """Deserialize an offer dictionary."""
        return cls(
            id=data["id"],
            source=data["source"],
            title=data["title"],
            company=data["company"],
            location=data.get("location", ""),
            contract_type=data["contract_type"],
            url=data["url"],
            apply_url=data.get("apply_url", ""),
            company_url=data.get("company_url", ""),
            remote=data.get("remote", ""),
            description=data.get("description", ""),
            published_at=data.get("published_at", ""),
            salary=data.get("salary", ""),
            language=data.get("language", "fr"),
            emails_found=list(data.get("emails_found", [])),
        )


@dataclass(slots=True)
class FilterDecision:
    """Result of deterministic school/CFA filtering."""

    is_valid: bool
    reason: str = ""


@dataclass(slots=True)
class EnrichedOffer:
    """Validated offer enriched with contact resolution metadata."""

    offer: JobOffer
    contact_email: str = ""
    email_source: str = "none"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "offer": self.offer.to_dict(),
            "contact_email": self.contact_email,
            "email_source": self.email_source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnrichedOffer:
        """Deserialize an enriched offer dictionary."""
        return cls(
            offer=JobOffer.from_dict(data["offer"]),
            contact_email=data.get("contact_email", ""),
            email_source=data.get("email_source", "none"),
        )


@dataclass(slots=True)
class ScrapeResult:
    """Result returned by a single jobboard scraper."""

    source: str
    status: ScrapeStatus
    offers: list[JobOffer] = field(default_factory=list)
    error_message: str = ""


@dataclass(slots=True)
class DraftInput:
    """Agent-authored email payload used to generate .md and .eml files."""

    offer_id: str
    subject: str
    body: str
    to_email: str = ""
