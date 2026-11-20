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
    "interview",
    "offer",
    "rejected",
    "ignored",
]
ScrapeStatus = Literal["ok", "blocked_antibot", "error"]
ApplyChannel = Literal["email", "ats", "jobboard"]


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
    """Result of deterministic school/CFA or preference filtering."""

    is_valid: bool
    reason: str = ""


@dataclass(slots=True)
class EnrichedOffer:
    """Validated offer enriched with contact resolution, relevance scoring, and security checks."""

    offer: JobOffer
    contact_email: str = ""
    email_source: str = "none"
    apply_channel: ApplyChannel = "jobboard"
    match_score: int = 0
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    score_breakdown: dict[str, int] = field(default_factory=dict)
    security_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "offer": self.offer.to_dict(),
            "contact_email": self.contact_email,
            "email_source": self.email_source,
            "apply_channel": self.apply_channel,
            "match_score": self.match_score,
            "matched_skills": list(self.matched_skills),
            "missing_skills": list(self.missing_skills),
            "score_breakdown": dict(self.score_breakdown),
            "security_warnings": list(self.security_warnings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnrichedOffer:
        """Deserialize an enriched offer dictionary (backward-compatible with older runs)."""
        return cls(
            offer=JobOffer.from_dict(data["offer"]),
            contact_email=data.get("contact_email", ""),
            email_source=data.get("email_source", "none"),
            apply_channel=data.get("apply_channel", "email" if data.get("contact_email") else "jobboard"),
            match_score=int(data.get("match_score", 0)),
            matched_skills=list(data.get("matched_skills", [])),
            missing_skills=list(data.get("missing_skills", [])),
            score_breakdown=dict(data.get("score_breakdown", {})),
            security_warnings=list(data.get("security_warnings", [])),
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
