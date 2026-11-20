"""Unit tests for profile-aware relevance scoring and skill matching."""

from scripts.models import JobOffer
from scripts.scorer import score_offer


def _sample_profile() -> dict:
    return {
        "defaults": {
            "contracts": ["alternance"],
            "queries": ["Développeur Fullstack", "Développeur Web"],
            "location": "Lyon",
            "remote_ok": True,
        },
        "skills": {
            "languages": ["PHP", "JavaScript", "TypeScript", "SQL", "C#"],
            "frameworks": ["Laravel", "React", "Node.js", ".NET"],
            "tools": ["Docker", "Git"],
            "specialties": ["REST API"],
        },
        "preferences": {
            "preferred_keywords": ["SaaS"],
        },
    }


def test_high_score_for_strong_skill_and_location_match() -> None:
    offer = JobOffer(
        id="o1",
        source="wttj",
        title="Développeur(euse) Full-Stack PHP / React (Alternance)",
        company="SuperSaaS",
        location="Lyon, Auvergne-Rhône-Alpes",
        remote="Hybride",
        contract_type="alternance",
        url="https://example.com/jobs/1",
        description=(
            "Rejoignez notre produit SaaS ! Stack : PHP, Laravel, TypeScript, React, "
            "Docker, Git, PostgreSQL et Kubernetes. " + ("Mission passionnante. " * 15)
        ),
    )
    res = score_offer(
        offer,
        _sample_profile(),
        query="Développeur Fullstack",
        contact_email="jobs@supersaas.fr",
        apply_channel="email",
    )
    assert res.score >= 85
    assert "PHP" in res.matched_skills
    assert "React" in res.matched_skills
    assert "Laravel" in res.matched_skills
    assert "PostgreSQL" in res.missing_skills
    assert "Kubernetes" in res.missing_skills


def test_special_tech_tokens_and_security_penalty() -> None:
    offer = JobOffer(
        id="o2",
        source="linkedin",
        title="Développeur C# / .NET Core",
        company="Corp",
        location="Paris",
        contract_type="alternance",
        url="https://example.com/jobs/2",
        description="Développement d'APIs en C# et .NET avec Node.js.",
    )
    clean_res = score_offer(offer, _sample_profile(), query="Développeur Fullstack")
    assert "C#" in clean_res.matched_skills
    assert ".NET" in clean_res.matched_skills
    assert "Node.js" in clean_res.matched_skills

    flagged_res = score_offer(
        offer,
        _sample_profile(),
        query="Développeur Fullstack",
        security_warnings=["prompt_injection:ignore_instructions"],
    )
    assert flagged_res.score == max(0, clean_res.score - 20)
