"""Deterministic rule-based filtering (schools/CFAs, contracts, preferences) and language detection."""

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
    host = (urlparse(url).hostname or "").lower()
    return host.removeprefix("www.")


def is_blacklisted_email_domain(email: str, rules: SchoolFilterRules) -> bool:
    """Return True if an email address belongs to a blacklisted school/CFA domain."""
    if not email or "@" not in email:
        return False
    email_domain = email.rsplit("@", 1)[-1].strip().lower()
    return any(
        email_domain == blocked or email_domain.endswith(f".{blocked}")
        for blocked in rules.blacklisted_domains
    )


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
        if domain and any(
            domain == blocked or domain.endswith(f".{blocked}")
            for blocked in rules.blacklisted_domains
        ):
            return FilterDecision(False, f"blacklisted_domain:{domain}")

    for email in offer.emails_found:
        if is_blacklisted_email_domain(email, rules):
            email_domain = email.rsplit("@", 1)[-1].lower()
            return FilterDecision(False, f"blacklisted_email_domain:{email_domain}")

    body_norm = normalize_text(f"{offer.title} {offer.description}")
    for phrase_re in rules.blacklisted_phrases:
        if phrase_re.search(body_norm):
            return FilterDecision(False, f"blacklisted_phrase:{phrase_re.pattern}")

    return FilterDecision(True, "")


def evaluate_profile_preferences(offer: JobOffer, profile: dict[str, Any] | None) -> FilterDecision:
    """Filter offers against optional user preferences (exclude_companies / exclude_keywords)."""
    if not profile or not isinstance(profile, dict):
        return FilterDecision(True, "")
    prefs = profile.get("preferences") or {}
    if not isinstance(prefs, dict):
        return FilterDecision(True, "")

    company_norm = normalize_text(offer.company)
    for raw_company in prefs.get("exclude_companies") or []:
        target = normalize_text(str(raw_company))
        if target and re.search(rf"\b{re.escape(target)}\b", company_norm):
            return FilterDecision(False, f"excluded_company:{target}")

    text_norm = normalize_text(f"{offer.title} {offer.description}")
    for raw_kw in prefs.get("exclude_keywords") or []:
        kw = normalize_text(str(raw_kw))
        if kw and re.search(rf"(?<![a-z0-9_+#.]){re.escape(kw)}(?![a-z0-9_+#])", text_norm):
            return FilterDecision(False, f"excluded_keyword:{kw}")

    return FilterDecision(True, "")


_STAGE_RE = re.compile(r"\b(?:stage|stagiaire|pfe|internship|intern)\b")
_ALTERNANCE_RE = re.compile(
    r"\b(?:alternance|alternant|alternante|apprentissage|apprenti|apprentie|"
    r"professionnalisation|apprenticeship)\b"
)
_CDI_RE = re.compile(r"\bcdi\b")
_CDD_RE = re.compile(r"\bcdd\b")

_NO_ALTERNANCE_DESC_RE = re.compile(
    r"\b(?:pas d[' ]alternance|sans alternance|stage uniquement|uniquement en stage|"
    r"aucun(?:e)? (?:offre|poste)? ?en alternance|ne (?:recrutons|proposons|prenons) pas d[' ]alternance)\b"
)
_NO_STAGE_DESC_RE = re.compile(
    r"\b(?:pas de stage|sans stage|alternance uniquement|uniquement en alternance|"
    r"ne (?:recrutons|proposons|prenons) pas de stagiaire)\b"
)


def is_contract_compatible(offer: JobOffer, requested_contract: ContractType) -> bool:
    """Reject obvious contract mismatches in title or unambiguous description statements."""
    title_norm = normalize_text(offer.title)
    has_stage = bool(_STAGE_RE.search(title_norm))
    has_alt = bool(_ALTERNANCE_RE.search(title_norm))
    has_cdi = bool(_CDI_RE.search(title_norm))
    has_cdd = bool(_CDD_RE.search(title_norm))

    if requested_contract == "alternance":
        if (has_stage or has_cdi or has_cdd) and not has_alt:
            return False
    elif requested_contract == "stage":
        if (has_alt or has_cdi or has_cdd) and not has_stage:
            return False
    elif requested_contract == "cdi":
        if (has_stage or has_alt or has_cdd) and not has_cdi:
            return False
    elif requested_contract == "cdd":
        if (has_stage or has_alt or has_cdi) and not has_cdd:
            return False

    desc_norm = normalize_text(offer.description)
    if not desc_norm:
        return True

    combined_norm = f"{title_norm}\n{desc_norm}"
    if requested_contract == "alternance":
        if _NO_ALTERNANCE_DESC_RE.search(desc_norm):
            return False
        if _STAGE_RE.search(desc_norm) and not _ALTERNANCE_RE.search(combined_norm):
            return False
    elif requested_contract == "stage":
        if _NO_STAGE_DESC_RE.search(desc_norm):
            return False
        if _ALTERNANCE_RE.search(desc_norm) and not _STAGE_RE.search(combined_norm):
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
