"""CLI entry point for the job-hunter skill."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .contact_finder import enrich_offer_contact
from .draft_builder import write_application_draft
from .filters import SchoolFilterRules, evaluate_offer, is_contract_compatible
from .history import OfferHistory
from .models import ContractType, DraftInput, EnrichedOffer
from .scrapers import SCRAPER_REGISTRY
from .validate_profile import validate_profile

SKILL_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = (
    SKILL_ROOT.parent.parent.parent
    if SKILL_ROOT.parent.name == "skills" and SKILL_ROOT.parent.parent.name == ".agents"
    else Path.cwd()
)

DEFAULT_PROFILE_PATH = SKILL_ROOT / "references" / "profile.yaml"
DEFAULT_RULES_PATH = SKILL_ROOT / "references" / "school_blacklist.yaml"
DEFAULT_HISTORY_PATH = SKILL_ROOT / "data" / "history.json"
DEFAULT_DRAFTS_DIR = WORKSPACE_ROOT / "drafts"


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _run_search(args: argparse.Namespace) -> int:
    rules = SchoolFilterRules.from_yaml(Path(args.rules))
    history = OfferHistory(Path(args.history))

    sources = [s.strip().lower() for s in args.sources.split(",") if s.strip()]
    unknown = [s for s in sources if s not in SCRAPER_REGISTRY]
    if unknown:
        raise SystemExit(f"Unknown sources: {', '.join(unknown)}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(args.out_run_dir) if args.out_run_dir else SKILL_ROOT / "data" / "runs" / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    contract: ContractType = args.contract
    source_reports: list[dict[str, Any]] = []
    rejected_schools: list[dict[str, str]] = []
    kept_offers: list[EnrichedOffer] = []
    seen_in_run: set[str] = set()
    total_scraped = 0
    duplicates_count = 0

    for source_name in sources:
        scraper = SCRAPER_REGISTRY[source_name]()
        result = scraper.search(
            query=args.query,
            contract=contract,
            location=args.location,
            remote=args.remote,
            limit=args.limit,
        )
        source_reports.append(
            {
                "source": source_name,
                "status": result.status,
                "scraped": len(result.offers),
                "error": result.error_message,
            }
        )
        total_scraped += len(result.offers)

        for offer in result.offers:
            if offer.id in seen_in_run:
                duplicates_count += 1
                continue
            seen_in_run.add(offer.id)

            decision = evaluate_offer(offer, rules)
            if not decision.is_valid:
                rejected_schools.append(
                    {
                        "id": offer.id,
                        "source": offer.source,
                        "company": offer.company,
                        "title": offer.title,
                        "reason": decision.reason,
                    }
                )
                history.record_offer(offer, "rejected_school", reason=decision.reason)
                continue

            if not is_contract_compatible(offer, contract):
                continue

            if history.is_duplicate(offer):
                duplicates_count += 1
                continue

            enriched = enrich_offer_contact(offer)
            kept_offers.append(enriched)
            history.record_offer(
                offer,
                "candidates_extracted",
                contact_email=enriched.contact_email,
            )

    history.save()

    candidates_path = run_dir / "candidates.json"
    candidates_path.write_text(
        json.dumps([item.to_dict() for item in kept_offers], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    report = {
        "run_dir": str(run_dir),
        "candidates_path": str(candidates_path),
        "query": args.query,
        "contract": contract,
        "location": args.location,
        "remote": args.remote,
        "sources": source_reports,
        "stats": {
            "scraped": total_scraped,
            "rejected_schools": len(rejected_schools),
            "duplicates": duplicates_count,
            "kept": len(kept_offers),
        },
        "rejected_school_details": rejected_schools,
    }
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def _run_build_drafts(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile).resolve()
    profile = _load_yaml(profile_path)
    history = OfferHistory(Path(args.history))
    raw_candidates = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    candidates_by_id: dict[str, EnrichedOffer] = {}
    for entry in raw_candidates:
        enriched = EnrichedOffer.from_dict(entry)
        candidates_by_id[enriched.offer.id] = enriched

    raw_drafts = json.loads(Path(args.drafts_json).read_text(encoding="utf-8"))
    output_root = Path(args.out_dir)
    created: list[dict[str, str]] = []

    for item in raw_drafts:
        offer_id = item["offer_id"]
        if offer_id not in candidates_by_id:
            raise SystemExit(f"Offer ID not found in candidates.json: {offer_id}")

        enriched = candidates_by_id[offer_id]
        draft = DraftInput(
            offer_id=offer_id,
            subject=item["subject"],
            body=item["body"],
            to_email=item.get("to_email", ""),
        )
        draft_dir = write_application_draft(
            enriched=enriched,
            draft=draft,
            profile=profile,
            output_root=output_root,
            profile_dir=profile_path.parent,
        )
        history.record_offer(
            enriched.offer,
            "drafted",
            contact_email=draft.to_email or enriched.contact_email,
            draft_dir=str(draft_dir),
        )
        created.append(
            {
                "offer_id": offer_id,
                "company": enriched.offer.company,
                "title": enriched.offer.title,
                "draft_dir": str(draft_dir),
            }
        )

    history.save()
    print(json.dumps({"created": created}, indent=2, ensure_ascii=False))
    return 0


def _run_mark_status(args: argparse.Namespace) -> int:
    history = OfferHistory(Path(args.history))
    if not history.update_status(args.offer_id, args.status):
        raise SystemExit(f"Offer ID not found in history: {args.offer_id}")
    history.save()
    print(json.dumps({"offer_id": args.offer_id, "status": args.status}))
    return 0


def _run_list_history(args: argparse.Namespace) -> int:
    history = OfferHistory(Path(args.history))
    records = list(history.records.values())
    if args.status:
        records = [r for r in records if r.get("status") == args.status]
    print(json.dumps(records, indent=2, ensure_ascii=False))
    return 0


def _run_validate_profile(args: argparse.Namespace) -> int:
    report = validate_profile(Path(args.profile))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["valid"] else 1


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""
    parser = argparse.ArgumentParser(prog="job-hunter", description="Job Hunter skill CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    search_p = sub.add_parser("search", help="Scrape offers, filter schools, enrich contacts")
    search_p.add_argument("--contract", required=True, choices=["stage", "alternance", "cdd", "cdi"])
    search_p.add_argument("--query", required=True)
    search_p.add_argument("--location", default="")
    search_p.add_argument("--remote", action="store_true")
    search_p.add_argument("--sources", default="wttj,hellowork,indeed,linkedin")
    search_p.add_argument("--limit", type=int, default=10)
    search_p.add_argument("--rules", default=str(DEFAULT_RULES_PATH))
    search_p.add_argument("--history", default=str(DEFAULT_HISTORY_PATH))
    search_p.add_argument("--out-run-dir", default="")
    search_p.set_defaults(func=_run_search)

    drafts_p = sub.add_parser("build-drafts", help="Generate .md and .eml drafts")
    drafts_p.add_argument("--candidates", required=True)
    drafts_p.add_argument("--drafts-json", required=True)
    drafts_p.add_argument("--profile", default=str(DEFAULT_PROFILE_PATH))
    drafts_p.add_argument("--history", default=str(DEFAULT_HISTORY_PATH))
    drafts_p.add_argument("--out-dir", default=str(DEFAULT_DRAFTS_DIR))
    drafts_p.set_defaults(func=_run_build_drafts)

    mark_p = sub.add_parser("mark-status", help="Update offer status in history")
    mark_p.add_argument("--offer-id", required=True)
    mark_p.add_argument("--status", required=True, choices=["drafted", "sent", "ignored"])
    mark_p.add_argument("--history", default=str(DEFAULT_HISTORY_PATH))
    mark_p.set_defaults(func=_run_mark_status)

    list_p = sub.add_parser("list-history", help="List tracked offers")
    list_p.add_argument("--status", default="")
    list_p.add_argument("--history", default=str(DEFAULT_HISTORY_PATH))
    list_p.set_defaults(func=_run_list_history)

    val_p = sub.add_parser("validate-profile", help="Validate profile.yaml and CV path")
    val_p.add_argument("--profile", default=str(DEFAULT_PROFILE_PATH))
    val_p.set_defaults(func=_run_validate_profile)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI main entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
