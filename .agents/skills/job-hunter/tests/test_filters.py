"""Unit tests for school/CFA filtering and language detection."""

from pathlib import Path

from scripts.filters import (
    SchoolFilterRules,
    detect_language,
    evaluate_offer,
    evaluate_profile_preferences,
    is_contract_compatible,
)
from scripts.models import JobOffer

RULES_PATH = Path(__file__).resolve().parent.parent / "references" / "school_blacklist.yaml"


def _make_offer(**kwargs: object) -> JobOffer:
    defaults: dict[str, object] = {
        "id": "abc123",
        "source": "wttj",
        "title": "Développeur Backend Python (H/F)",
        "company": "Alan",
        "location": "Paris",
        "contract_type": "alternance",
        "url": "https://www.welcometothejungle.com/fr/companies/alan/jobs/dev",
        "description": "Vous rejoignez notre équipe produit pour développer des APIs Python.",
    }
    defaults.update(kwargs)
    return JobOffer(**defaults)  # type: ignore[arg-type]


def test_rejects_known_blacklisted_school() -> None:
    rules = SchoolFilterRules.from_yaml(RULES_PATH)
    offer = _make_offer(company="ISCOD", title="Développeur Web en Alternance")
    decision = evaluate_offer(offer, rules)
    assert not decision.is_valid
    assert "blacklisted_company:iscod" in decision.reason


def test_rejects_generic_school_token_in_company_name() -> None:
    rules = SchoolFilterRules.from_yaml(RULES_PATH)
    offer = _make_offer(company="Campus Numérique Lyon")
    decision = evaluate_offer(offer, rules)
    assert not decision.is_valid
    assert "company_token" in decision.reason


def test_rejects_blacklisted_domain_and_phrases() -> None:
    rules = SchoolFilterRules.from_yaml(RULES_PATH)
    offer = _make_offer(
        company="Partenaire Recrutement",
        description="Rejoignez notre école et bénéficiez d'une aide à la recherche d'entreprise.",
    )
    decision = evaluate_offer(offer, rules)
    assert not decision.is_valid
    assert "blacklisted_phrase" in decision.reason

    domain_offer = _make_offer(company="Entreprise X", company_url="https://www.studi.fr")
    assert not evaluate_offer(domain_offer, rules).is_valid


def test_accepts_real_company_alternance_mentioning_master_and_ecole() -> None:
    rules = SchoolFilterRules.from_yaml(RULES_PATH)
    offer = _make_offer(
        company="Doctolib",
        title="Alternance - Software Engineer Python / React",
        description=(
            "Vous préparez un Master ou un diplôme d'école d'ingénieurs en informatique. "
            "Vous travaillerez avec nos équipes backend sur nos microservices à Paris."
        ),
    )
    decision = evaluate_offer(offer, rules)
    assert decision.is_valid
    assert decision.reason == ""


def test_detect_language_fr_vs_en() -> None:
    assert detect_language("Nous recherchons un développeur pour notre équipe à Paris.") == "fr"
    assert detect_language("Développeur Full Stack") == "fr"
    assert detect_language("We are looking for a backend engineer to join our remote team.") == "en"


def test_rejects_dawan_and_afpi() -> None:
    rules = SchoolFilterRules.from_yaml(RULES_PATH)
    assert not evaluate_offer(_make_offer(company="Dawan"), rules).is_valid
    assert not evaluate_offer(_make_offer(company="AFPI rhodanienne"), rules).is_valid


def test_is_contract_compatible() -> None:
    assert not is_contract_compatible(_make_offer(title="Stage PFE - Développeur Fullstack"), "alternance")
    assert not is_contract_compatible(_make_offer(title="Développeur Fullstack CDI"), "alternance")
    assert not is_contract_compatible(_make_offer(title="Stage Développeur Web"), "cdi")
    assert not is_contract_compatible(_make_offer(title="Alternance Développeur Web"), "cdd")
    assert is_contract_compatible(_make_offer(title="Stage ou Alternance - Développeur"), "alternance")
    assert is_contract_compatible(_make_offer(title="Développeur Full Stack"), "alternance")
    # Description-level mismatches
    assert not is_contract_compatible(
        _make_offer(
            title="Développeur Web",
            description="Offre de stage de 6 mois à pourvoir dès janvier.",
        ),
        "alternance",
    )
    assert not is_contract_compatible(
        _make_offer(
            title="Développeur Web",
            description="Stage uniquement, pas d'alternance possible.",
        ),
        "alternance",
    )
    assert is_contract_compatible(
        _make_offer(
            title="Développeur Web",
            description="Poste ouvert en stage ou en alternance (12 mois).",
        ),
        "alternance",
    )


def test_evaluate_profile_preferences_and_expanded_schools() -> None:
    rules = SchoolFilterRules.from_yaml(RULES_PATH)
    assert not evaluate_offer(_make_offer(company="M2i Formation"), rules).is_valid
    assert not evaluate_offer(_make_offer(company="La Plateforme"), rules).is_valid

    profile = {
        "preferences": {
            "exclude_companies": ["BadESN"],
            "exclude_keywords": ["COBOL", "WordPress"],
        }
    }
    assert not evaluate_profile_preferences(_make_offer(company="BadESN Lyon"), profile).is_valid
    assert not evaluate_profile_preferences(
        _make_offer(company="GoodCorp", description="Maintenance de sites WordPress"), profile
    ).is_valid
    assert evaluate_profile_preferences(
        _make_offer(company="GoodCorp", description="Stack Python et React"), profile
    ).is_valid
