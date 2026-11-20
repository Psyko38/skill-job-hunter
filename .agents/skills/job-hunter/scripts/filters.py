"""Deterministic rule-based filtering (schools/CFAs vs real jobs) and language detection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from .models import ContractType, FilterDecision, JobOffer, LanguageCode
from .text_utils import normalize_text

_FR_STOPWORDS = frozenset(
    {
        "le",
        "la",
        "les",
        "des",
        "une",
        "dans",
        "avec",
        "pour",
        "sur",
        "vous",
        "nous",
        "votre",
        "notre",
        "entreprise",
        "mission",
        "profil",
        "recherche",
        "competences",
        "experience",
        "equipe",
        "poste",
        "alternance",
        "stage",
    }
)

_EN_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "you",
        "our",
        "your",
        "will",
        "work",
        "team",
        "role",
        "company",
        "experience",
        "skills",
        "requirements",
        "responsibilities",
        "looking",
        "join",
        "we",
        "are",
        "internship",
        "remote",
    }
)


@dataclass(slots=True)
class SchoolFilterRules:
    """Compiled rule set loaded from school_blacklist.yaml."""

    blacklisted_companies: list[str]
    company_tokens: list[re.Pattern[str]]
    whitelisted_companies: set[str]
    blacklisted_domains: set[str]
    blacklisted_phrases: list[re.Pattern[str]]

    @classmethod
    def from_yaml(cls, path: Path) -> SchoolFilterRules:
        """Load and compile rules from a YAML configuration file."""
        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        companies = [normalize_text(c) for c in raw.get("blacklisted_companies", []) if c]
        tokens = [
            re.compile(rf"\b{re.escape(normalize_text(tok))}\b")
            for tok in raw.get("company_tokens", [])
            if tok
        ]
        whitelist = {normalize_text(c) for c in raw.get("whitelisted_companies", []) if c}
        domains = {normalize_text(d) for d in raw.get("blacklisted_domains", []) if d}
        phrases = [
            re.compile(normalize_text(pattern))
            for pattern in raw.get("blacklisted_phrases", [])
            if pattern
        ]
        return cls(
            blacklisted_companies=companies,
            company_tokens=tokens,
            whitelisted_companies=whitelist,
            blacklisted_domains=domains,
            blacklisted_phrases=phrases,
        )


def _extract_domain(url: str) -> str:
    if not url:
        return ""
    host = urlparse(url).netloc.lower()
    return host.removeprefix("www.")


def evaluate_offer(offer: JobOffer, rules: SchoolFilterRules) -> FilterDecision:
    """Return a strict reject/accept decision for a job offer against school/CFA rules."""
    company_norm = normalize_text(offer.company)
    if company_norm and company_norm not in rules.whitelisted_companies:
        for blacklisted in rules.blacklisted_companies:
            if re.search(rf"\b{re.escape(blacklisted)}\b", company_norm):
                return FilterDecision(False, f"blacklisted_company:{blacklisted}")

        for token_re in rules.company_tokens:
            if token_re.search(company_norm):
                return FilterDecision(False, f"company_token:{token_re.pattern}")

    for candidate_url in (offer.company_url, offer.apply_url, offer.url):
        domain = _extract_domain(candidate_url)
        if any(
            domain == blocked or domain.endswith(f".{blocked}")
            for blocked in rules.blacklisted_domains
        ):
            return FilterDecision(False, f"blacklisted_domain:{domain}")

    for email in offer.emails_found:
        email_domain = email.rsplit("@", 1)[-1].lower()
        if any(
            email_domain == blocked or email_domain.endswith(f".{blocked}")
            for blocked in rules.blacklisted_domains
        ):
            return FilterDecision(False, f"blacklisted_email_domain:{email_domain}")

    body_norm = normalize_text(f"{offer.title} {offer.description}")
    for phrase_re in rules.blacklisted_phrases:
        if phrase_re.search(body_norm):
            return FilterDecision(False, f"blacklisted_phrase:{phrase_re.pattern}")

    return FilterDecision(True, "")


_STAGE_ONLY_RE = re.compile(r"\b(?:stage|stagiaire|pfe|internship|intern)\b")
_ALTERNANCE_RE = re.compile(r"\b(?:alternance|alternant|apprentissage|apprenti|professionnalisation|apprenticeship)\b")


def is_contract_compatible(offer: JobOffer, requested_contract: ContractType) -> bool:
    """Reject obvious contract mismatches (e.g., stage-only titles during alternance search)."""
    title_norm = normalize_text(offer.title)
    if requested_contract == "alternance":
        if _STAGE_ONLY_RE.search(title_norm) and not _ALTERNANCE_RE.search(title_norm):
            return False
    elif requested_contract == "stage":
        if _ALTERNANCE_RE.search(title_norm) and not _STAGE_ONLY_RE.search(title_norm):
            return False
    return True


def detect_language(text: str) -> LanguageCode:
    """Detect whether job text is primarily French or English."""
    words = re.findall(r"\b[a-z]+\b", normalize_text(text))
    if not words:
        return "fr"
    fr_hits = sum(1 for word in words if word in _FR_STOPWORDS)
    en_hits = sum(1 for word in words if word in _EN_STOPWORDS)
    return "en" if en_hits > fr_hits else "fr"
