"""Profile-aware relevance scoring and skill matching for job offers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .models import ApplyChannel, JobOffer
from .text_utils import normalize_text

# Common tech stack catalog used to highlight notable technologies requested in an offer
# that are not listed in the candidate's profile.
_TECH_CATALOG: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Python", ("python",)),
    ("JavaScript", ("javascript", "js")),
    ("TypeScript", ("typescript", "ts")),
    ("PHP", ("php",)),
    ("Java", ("java",)),
    ("C#", ("c#", "csharp")),
    ("C++", ("c++", "cpp")),
    ("Go", ("golang", "go")),
    ("Rust", ("rust",)),
    ("Ruby", ("ruby",)),
    ("Kotlin", ("kotlin",)),
    ("Swift", ("swift",)),
    ("SQL", ("sql",)),
    ("React", ("react", "reactjs", "react.js")),
    ("Next.js", ("next.js", "nextjs")),
    ("Vue.js", ("vue", "vue.js", "vuejs")),
    ("Angular", ("angular",)),
    ("Svelte", ("svelte", "sveltekit")),
    ("Node.js", ("node.js", "nodejs")),
    ("Laravel", ("laravel",)),
    ("Symfony", ("symfony",)),
    ("Django", ("django",)),
    ("FastAPI", ("fastapi",)),
    ("Spring Boot", ("spring boot", "springboot", "spring")),
    (".NET", (".net", "dotnet", "dotnetcore", ".net core", "asp.net")),
    ("Ruby on Rails", ("rails", "ruby on rails")),
    ("Tailwind CSS", ("tailwind", "tailwindcss")),
    ("PostgreSQL", ("postgresql", "postgres")),
    ("MySQL", ("mysql", "mariadb")),
    ("MongoDB", ("mongodb", "mongo")),
    ("Redis", ("redis",)),
    ("Elasticsearch", ("elasticsearch",)),
    ("GraphQL", ("graphql",)),
    ("Docker", ("docker",)),
    ("Kubernetes", ("kubernetes", "k8s")),
    ("AWS", ("aws", "amazon web services")),
    ("Azure", ("azure",)),
    ("GCP", ("gcp", "google cloud")),
    ("Terraform", ("terraform",)),
    ("Git", ("git", "github", "gitlab")),
    ("CI/CD", ("ci/cd", "cicd", "github actions", "gitlab ci", "jenkins")),
    ("Linux", ("linux",)),
    ("Flutter", ("flutter",)),
    ("React Native", ("react native",)),
)

_STOPWORDS = frozenset(
    {
        "de",
        "du",
        "des",
        "la",
        "le",
        "les",
        "et",
        "ou",
        "en",
        "un",
        "une",
        "pour",
        "avec",
        "sur",
        "par",
        "h",
        "f",
        "hf",
        "h/f",
        "f/h",
        "m/f",
        "the",
        "and",
        "or",
        "for",
        "with",
        "in",
        "a",
        "an",
        "alternance",
        "stage",
        "cdi",
        "cdd",
        "junior",
        "senior",
        "confirme",
    }
)

_REMOTE_RE = re.compile(
    r"\b(?:remote|teletravail|full\s*remote|hybride|hybrid|distanciel)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ScoreResult:
    """Structured relevance score for a single job offer."""

    score: int
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    breakdown: dict[str, int] = field(default_factory=dict)


def _skill_pattern(token: str) -> re.Pattern[str]:
    """Build a boundary-safe regex that works even for symbols like C++, C#, .NET, Node.js."""
    escaped = re.escape(token.strip().lower())
    return re.compile(rf"(?<![a-z0-9_+#.]){escaped}(?![a-z0-9_+#])")


def _extract_profile_skills(profile: dict[str, Any]) -> list[str]:
    """Extract normalized, deduplicated technical skill terms from profile.yaml."""
    skills_section = profile.get("skills") or {}
    if not isinstance(skills_section, dict):
        return []

    raw_items: list[str] = []
    for key in ("languages", "frameworks", "tools", "specialties"):
        entries = skills_section.get(key) or []
        if isinstance(entries, list):
            raw_items.extend(str(item) for item in entries if item)

    cleaned: list[str] = []
    seen_norm: set[str] = set()
    for entry in raw_items:
        # Split composite labels like "PHP / Laravel" or "TypeScript (React, Node.js)"
        parts = [entry.strip()]
        subparts = re.split(r"[,;/()|]+", entry)
        if len(subparts) > 1:
            parts.extend(p.strip() for p in subparts if p.strip())

        for part in parts:
            norm = normalize_text(part)
            if not norm or norm in _STOPWORDS or len(norm) < 2:
                continue
            if norm not in seen_norm:
                seen_norm.add(norm)
                cleaned.append(part.strip())

    return cleaned


def _contains_skill(haystack_norm: str, skill: str) -> bool:
    norm = normalize_text(skill)
    if not norm:
        return False
    # Very short tokens like "go" or "ts" require stricter matching to avoid noise
    if norm == "go":
        return bool(re.search(r"\b(?:golang|go\s+developer|developpeur\s+go|langage\s+go)\b", haystack_norm))
    return bool(_skill_pattern(norm).search(haystack_norm))


def _tokenize_title(text: str) -> set[str]:
    norm = normalize_text(text)
    # Normalize common French/English title variations
    norm = re.sub(r"\bfull[\s-]+stack\b", "fullstack", norm)
    norm = re.sub(r"\bback[\s-]+end\b", "backend", norm)
    norm = re.sub(r"\bfront[\s-]+end\b", "frontend", norm)
    norm = re.sub(r"\bdeveloppeu(?:r|se)(?:\(euse\))?\b", "developpeur", norm)
    norm = re.sub(r"\bingenieur(?:e|\(e\))?\b", "ingenieur", norm)
    return {
        tok
        for tok in re.findall(r"[a-z0-9+#.]+", norm)
        if len(tok) >= 2 and tok not in _STOPWORDS
    }


def _title_alignment_score(title: str, queries: list[str]) -> int:
    """Compute 0-25 alignment score between offer title and target queries."""
    title_tokens = _tokenize_title(title)
    if not title_tokens or not queries:
        return 10  # Neutral baseline when no query provided

    best_ratio = 0.0
    for q in queries:
        q_tokens = _tokenize_title(q)
        if not q_tokens:
            continue
        overlap = len(title_tokens & q_tokens)
        ratio = overlap / len(q_tokens)
        best_ratio = max(best_ratio, ratio)

    return min(25, round(best_ratio * 25))


def _location_remote_score(offer: JobOffer, profile: dict[str, Any]) -> int:
    """Compute 0-15 score for location and remote preference alignment."""
    defaults = profile.get("defaults") or {}
    target_loc = normalize_text(str(defaults.get("location", "")))
    remote_ok = bool(defaults.get("remote_ok", False))

    offer_loc = normalize_text(offer.location)
    remote_text = normalize_text(f"{offer.remote} {offer.location} {offer.title}")
    has_remote = bool(_REMOTE_RE.search(remote_text))

    score = 0
    if target_loc:
        target_parts = [p for p in re.split(r"[\s,/]+", target_loc) if len(p) >= 3]
        if any(part in offer_loc for part in target_parts):
            score += 12
        elif has_remote and remote_ok:
            score += 10
        elif "france" in offer_loc:
            score += 6
    else:
        score += 10

    if remote_ok and has_remote:
        score += 3

    return min(15, score)


def _freshness_score(published_at: str) -> int:
    """Return 0-3 points for recently posted offers (within 14 days)."""
    if not published_at:
        return 0
    cleaned = published_at.strip()[:10]
    try:
        pub_date = datetime.strptime(cleaned, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return 0
    age_days = (datetime.now(timezone.utc) - pub_date).days
    if age_days <= 3:
        return 3
    if age_days <= 14:
        return 2
    if age_days <= 30:
        return 1
    return 0


def score_offer(
    offer: JobOffer,
    profile: dict[str, Any] | None = None,
    *,
    query: str = "",
    contact_email: str = "",
    apply_channel: ApplyChannel = "jobboard",
    security_warnings: list[str] | None = None,
) -> ScoreResult:
    """Compute a deterministic 0-100 relevance score and matched/missing tech skills."""
    prof = profile or {}
    title_norm = normalize_text(offer.title)
    desc_norm = normalize_text(offer.description)
    combined_norm = f"{title_norm}\n{desc_norm}"

    # 1. Technical skills match (0-45 pts)
    profile_skills = _extract_profile_skills(prof)
    matched_skills: list[str] = []
    title_hits = 0
    for skill in profile_skills:
        if _contains_skill(combined_norm, skill):
            matched_skills.append(skill)
            if _contains_skill(title_norm, skill):
                title_hits += 1

    if profile_skills:
        # Reward matching several skills (diminishing returns past 6 matches) + title matches
        coverage_pts = min(35, len(matched_skills) * 7)
        title_bonus = min(10, title_hits * 5)
        skills_pts = min(45, coverage_pts + title_bonus)
    else:
        skills_pts = 20  # Neutral when profile has no skills configured

    # Identify notable tech skills in the offer that are not in the candidate's profile
    matched_norm_set = {normalize_text(s) for s in matched_skills}
    profile_norm_set = {normalize_text(s) for s in profile_skills}
    missing_skills: list[str] = []
    for canonical_name, aliases in _TECH_CATALOG:
        canon_norm = normalize_text(canonical_name)
        if canon_norm in profile_norm_set or any(a in profile_norm_set for a in aliases):
            continue
        if any(_contains_skill(combined_norm, alias) for alias in aliases):
            if canon_norm not in matched_norm_set:
                missing_skills.append(canonical_name)
        if len(missing_skills) >= 8:
            break

    # 2. Title/query alignment (0-25 pts)
    defaults = prof.get("defaults") or {}
    target_queries = [q for q in (defaults.get("queries") or []) if isinstance(q, str) and q.strip()]
    if query and query not in target_queries:
        target_queries = [query, *target_queries]
    title_pts = _title_alignment_score(offer.title, target_queries)

    # 3. Location / remote alignment (0-15 pts)
    loc_pts = _location_remote_score(offer, prof)

    # 4. Contact / channel actionability (0-10 pts)
    if contact_email:
        contact_pts = 10
    elif apply_channel == "ats":
        contact_pts = 5
    else:
        contact_pts = 2

    # 5. Description quality & freshness (0-5 pts)
    quality_pts = 0
    if len(offer.description) >= 200:
        quality_pts += 2
    quality_pts += _freshness_score(offer.published_at)
    quality_pts = min(5, quality_pts)

    # 6. Optional preferred keywords bonus (0-5 pts)
    prefs = prof.get("preferences") or {}
    pref_keywords = [str(k) for k in (prefs.get("preferred_keywords") or []) if k]
    pref_hits = sum(1 for kw in pref_keywords if _contains_skill(combined_norm, kw))
    pref_pts = min(5, pref_hits * 2)

    # 7. Penalties (missing description or prompt injection detected)
    penalty = 0
    if len(offer.description) < 40:
        penalty -= 10
    if security_warnings:
        penalty -= 20

    raw_total = skills_pts + title_pts + loc_pts + contact_pts + quality_pts + pref_pts + penalty
    final_score = max(0, min(100, raw_total))

    breakdown = {
        "skills": skills_pts,
        "title": title_pts,
        "location_remote": loc_pts,
        "contact": contact_pts,
        "quality_freshness": quality_pts,
        "preferred_keywords": pref_pts,
        "penalty": penalty,
    }
    return ScoreResult(
        score=final_score,
        matched_skills=matched_skills,
        missing_skills=missing_skills,
        breakdown=breakdown,
    )
