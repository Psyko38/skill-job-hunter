"""CLI entry point for the job-hunter skill."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Any

import httpx
import yaml

from .contact_finder import enrich_offer_contact
from .draft_builder import validate_draft, write_application_draft
from .filters import (
    SchoolFilterRules,
    evaluate_offer,
    evaluate_profile_preferences,
    is_contract_compatible,
)
from .history import OfferHistory
from .http_headers import DEFAULT_HTTP_HEADERS
from .models import ContractType, DraftInput, EnrichedOffer, JobOffer
from .scorer import score_offer
from .scrapers import SCRAPER_REGISTRY
from .security import atomic_write_text, detect_prompt_injection
from .validate_profile import VALID_CONTRACTS, validate_profile

SKILL_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = (
    SKILL_ROOT.parent.parent.parent
    if SKILL_ROOT.parent.name == "skills" and SKILL_ROOT.parent.parent.name == ".agents"
    else Path.cwd()
)

DEFAULT_PROFILE_PATH = SKILL_ROOT / "references" / "profile.yaml"
DEFAULT_RULES_PATH = SKILL_ROOT / "references" / "school_blacklist.yaml"
DEFAULT_HISTORY_PATH = SKILL_ROOT / "data" / "history.json"
DEFAULT_RUNS_DIR = SKILL_ROOT / "data" / "runs"
DEFAULT_DRAFTS_DIR = WORKSPACE_ROOT / "drafts"

MIN_LIMIT = 1
MAX_LIMIT = 50


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _summarize_enriched(item: EnrichedOffer) -> dict[str, Any]:
    """Return a compact, low-token summary of an EnrichedOffer for agent triage."""
    offer = item.offer
    return {
        "offer_id": offer.id,
        "match_score": item.match_score,
        "company": offer.company,
        "title": offer.title,
        "contract_type": offer.contract_type,
        "source": offer.source,
        "location": offer.location,
        "remote": offer.remote,
        "language": offer.language,
        "apply_channel": item.apply_channel,
        "contact_email": item.contact_email,
        "matched_skills": item.matched_skills,
        "missing_skills": item.missing_skills,
        "security_warnings": item.security_warnings,
        "url": offer.url,
        "apply_url": offer.apply_url or offer.url,
    }


def _find_latest_candidates_path(runs_dir: Path | None = None) -> Path:
    """Locate the most recent non-empty run's candidates.json (or newest if all empty)."""
    base = runs_dir or DEFAULT_RUNS_DIR
    if not base.is_dir():
        raise SystemExit(f"No runs directory found at {base}. Run 'job-hunter search' first.")
    candidates = sorted(
        (p / "candidates.json" for p in base.iterdir() if p.is_dir() and (p / "candidates.json").is_file()),
        key=lambda p: p.parent.name,
        reverse=True,
    )
    if not candidates:
        raise SystemExit(f"No candidates.json found under {base}. Run 'job-hunter search' first.")
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list) and len(data) > 0:
                return path
        except Exception:  # noqa: BLE001
            continue
    return candidates[0]


def _resolve_candidates_path(candidates_arg: str, runs_dir: Path | None = None) -> Path:
    """Return explicit candidates path or resolve the latest run automatically."""
    if candidates_arg:
        path = Path(candidates_arg)
        if not path.is_file():
            raise SystemExit(f"Candidates file not found: {path}")
        return path
    return _find_latest_candidates_path(runs_dir)


def _check_playwright_chromium() -> dict[str, Any]:
    """Check whether Playwright Chromium binary is installed without launching a browser."""
    try:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        try:
            exe = Path(pw.chromium.executable_path)
            installed = exe.is_file()
            return {"installed": installed, "executable": str(exe)}
        finally:
            pw.stop()
    except Exception as exc:  # noqa: BLE001
        return {"installed": False, "executable": "", "error": str(exc)}


def _enrich_offers_parallel(
    offers: list[tuple[JobOffer, str]],
    *,
    profile: dict[str, Any],
    blacklisted_domains: set[str],
) -> list[EnrichedOffer]:
    """Enrich contacts in parallel with a shared domain cache, then score & inspect security."""
    if not offers:
        return []

    domain_cache: dict[str, tuple[str, str]] = {}
    cache_lock = threading.Lock()

    def _process(item: tuple[JobOffer, str]) -> EnrichedOffer:
        offer, query = item
        with httpx.Client(
            timeout=5.0,
            follow_redirects=False,
            headers=DEFAULT_HTTP_HEADERS,
        ) as client:
            with cache_lock:
                local_cache = dict(domain_cache)
            enriched = enrich_offer_contact(
                offer,
                client=client,
                domain_cache=local_cache,
                extra_blocked_domains=blacklisted_domains,
            )
            with cache_lock:
                domain_cache.update(local_cache)

        sec_warnings = detect_prompt_injection(
            f"{offer.title}\n{offer.company}\n{offer.description}"
        )
        score_res = score_offer(
            offer,
            profile,
            query=query,
            contact_email=enriched.contact_email,
            apply_channel=enriched.apply_channel,
            security_warnings=sec_warnings,
        )
        enriched.match_score = score_res.score
        enriched.matched_skills = score_res.matched_skills
        enriched.missing_skills = score_res.missing_skills
        enriched.score_breakdown = score_res.breakdown
        enriched.security_warnings = sec_warnings
        return enriched

    max_workers = min(4, len(offers))
    if max_workers <= 1:
        return [_process(item) for item in offers]

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(_process, offers))


def _resolve_search_plan(
    args: argparse.Namespace,
    profile: dict[str, Any],
) -> tuple[list[tuple[ContractType, str]], str, bool, list[str]]:
    """Build (contract, query) pairs and effective location/remote/sources."""
    defaults = profile.get("defaults") or {}

    if args.from_profile:
        if not profile:
            raise SystemExit(f"Profile not found for --from-profile: {args.profile}")
        contracts_raw = [args.contract] if args.contract else list(defaults.get("contracts") or [])
        queries_raw = [args.query] if args.query else list(defaults.get("queries") or [])
        contracts: list[ContractType] = [
            c for c in contracts_raw if c in VALID_CONTRACTS
        ]  # type: ignore[misc]
        queries = [str(q).strip() for q in queries_raw if str(q).strip()]
        if not contracts or not queries:
            raise SystemExit("Profile defaults.contracts and defaults.queries must be non-empty for --from-profile")
        location = args.location if args.location else str(defaults.get("location", ""))
        remote = bool(args.remote or defaults.get("remote_ok", False))
        if args.sources:
            sources = [s.strip().lower() for s in args.sources.split(",") if s.strip()]
        else:
            sources = [
                str(s).strip().lower()
                for s in (defaults.get("sources") or ["wttj", "hellowork", "indeed", "linkedin"])
                if str(s).strip()
            ]
        plan = [(c, q) for c in contracts for q in queries]
        return plan, location, remote, sources

    if not args.contract or not args.query:
        raise SystemExit("Both --contract and --query are required unless --from-profile is set.")

    sources_str = args.sources or "wttj,hellowork,indeed,linkedin"
    sources = [s.strip().lower() for s in sources_str.split(",") if s.strip()]
    return [(args.contract, args.query)], args.location, bool(args.remote), sources


def _run_doctor(args: argparse.Namespace) -> int:
    profile_report = validate_profile(Path(args.profile))
    rules_path = Path(args.rules)
    rules_ok = False
    rules_error = ""
    if rules_path.is_file():
        try:
            SchoolFilterRules.from_yaml(rules_path)
            rules_ok = True
        except Exception as exc:  # noqa: BLE001
            rules_error = str(exc)
    else:
        rules_error = f"Rules file not found: {rules_path}"

    pw_status = _check_playwright_chromium() if not args.skip_browser else {"installed": True, "skipped": True}

    next_actions: list[str] = []
    if not profile_report["valid"]:
        next_actions.append("Run profile-setup to initialize/fix references/profile.yaml and CV PDF")
    if not pw_status.get("installed"):
        next_actions.append("Run: uv run --project .agents/skills/job-hunter playwright install chromium")
    if not rules_ok:
        next_actions.append(f"Fix rules file: {rules_error}")

    ready = bool(profile_report["valid"] and rules_ok and pw_status.get("installed"))
    report = {
        "ready": ready,
        "profile": profile_report,
        "rules": {"valid": rules_ok, "path": str(rules_path), "error": rules_error},
        "playwright_chromium": pw_status,
        "next_actions": next_actions,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if ready else 1


def _run_search(args: argparse.Namespace) -> int:
    if not (MIN_LIMIT <= args.limit <= MAX_LIMIT):
        raise SystemExit(f"--limit must be between {MIN_LIMIT} and {MAX_LIMIT} (got {args.limit})")
    if not (0 <= args.min_score <= 100):
        raise SystemExit(f"--min-score must be between 0 and 100 (got {args.min_score})")

    profile = _load_yaml(Path(args.profile)) if args.profile else {}
    rules = SchoolFilterRules.from_yaml(Path(args.rules))
    history = OfferHistory(Path(args.history))

    plan, location, remote, sources = _resolve_search_plan(args, profile)
    unknown = [s for s in sources if s not in SCRAPER_REGISTRY]
    if unknown:
        raise SystemExit(f"Unknown sources: {', '.join(unknown)}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(args.out_run_dir) if args.out_run_dir else DEFAULT_RUNS_DIR / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    source_reports: list[dict[str, Any]] = []
    rejected_schools: list[dict[str, str]] = []
    rejected_contract_mismatch = 0
    rejected_preferences = 0
    filtered_low_score = 0
    pending_enrichment: list[tuple[JobOffer, str]] = []
    seen_in_run: set[str] = set()
    total_scraped = 0
    duplicates_count = 0
    ignore_statuses = {"candidates_extracted"} if args.include_seen else None

    for contract, query in plan:
        for source_name in sources:
            scraper = SCRAPER_REGISTRY[source_name]()
            result = scraper.search(
                query=query,
                contract=contract,
                location=location,
                remote=remote,
                limit=args.limit,
            )
            source_reports.append(
                {
                    "source": source_name,
                    "contract": contract,
                    "query": query,
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
                    rejected_contract_mismatch += 1
                    continue

                pref_decision = evaluate_profile_preferences(offer, profile)
                if not pref_decision.is_valid:
                    rejected_preferences += 1
                    continue

                if history.is_duplicate(offer, ignore_statuses=ignore_statuses):
                    duplicates_count += 1
                    continue

                pending_enrichment.append((offer, query))

    enriched_all = _enrich_offers_parallel(
        pending_enrichment,
        profile=profile,
        blacklisted_domains=rules.blacklisted_domains,
    )

    kept_offers: list[EnrichedOffer] = []
    for enriched in enriched_all:
        if enriched.match_score < args.min_score:
            filtered_low_score += 1
            continue
        kept_offers.append(enriched)
        history.record_offer(
            enriched.offer,
            "candidates_extracted",
            contact_email=enriched.contact_email,
            match_score=enriched.match_score,
        )

    kept_offers.sort(
        key=lambda item: (-item.match_score, item.contact_email == "", item.offer.company.lower())
    )

    history.save()

    candidates_path = run_dir / "candidates.json"
    atomic_write_text(
        candidates_path,
        json.dumps([item.to_dict() for item in kept_offers], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    summaries = [_summarize_enriched(item) for item in kept_offers]
    summary_path = run_dir / "summary.json"
    atomic_write_text(
        summary_path,
        json.dumps(summaries, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    security_flagged = sum(1 for item in kept_offers if item.security_warnings)
    with_direct_email = sum(1 for item in kept_offers if item.contact_email)
    with_ats = sum(1 for item in kept_offers if item.apply_channel == "ats")

    report = {
        "run_dir": str(run_dir),
        "candidates_path": str(candidates_path),
        "summary_path": str(summary_path),
        "query": plan[0][1] if len(plan) == 1 else [q for _, q in plan],
        "contract": plan[0][0] if len(plan) == 1 else sorted({c for c, _ in plan}),
        "location": location,
        "remote": remote,
        "sources": source_reports,
        "stats": {
            "scraped": total_scraped,
            "rejected_schools": len(rejected_schools),
            "duplicates": duplicates_count,
            "kept": len(kept_offers),
        },
        "extended_stats": {
            "rejected_contract_mismatch": rejected_contract_mismatch,
            "rejected_preferences": rejected_preferences,
            "filtered_low_score": filtered_low_score,
            "security_flagged": security_flagged,
            "with_direct_email": with_direct_email,
            "with_ats": with_ats,
        },
        "top_candidates": summaries[:20],
        "rejected_school_details": rejected_schools,
    }
    report_path = run_dir / "report.json"
    atomic_write_text(report_path, json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def _run_show(args: argparse.Namespace) -> int:
    candidates_path = _resolve_candidates_path(
        args.candidates,
        runs_dir=Path(args.runs_dir) if args.runs_dir else None,
    )
    raw_candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    enriched_list = [EnrichedOffer.from_dict(entry) for entry in raw_candidates]

    selected: list[EnrichedOffer]
    if args.offer_id:
        wanted = set(args.offer_id)
        by_id = {item.offer.id: item for item in enriched_list}
        missing = [oid for oid in args.offer_id if oid not in by_id]
        if missing:
            raise SystemExit(f"Offer ID(s) not found in {candidates_path}: {', '.join(missing)}")
        selected = [by_id[oid] for oid in args.offer_id if oid in wanted]
    else:
        selected = enriched_list[: max(1, args.top)]

    payload: dict[str, Any] = {
        "candidates_path": str(candidates_path),
        "count": len(selected),
        "offers": [item.to_dict() for item in selected],
    }
    if args.with_profile:
        profile = _load_yaml(Path(args.profile))
        payload["candidate_profile"] = {
            "candidate": profile.get("candidate", {}),
            "education": profile.get("education", {}),
            "skills": profile.get("skills", {}),
            "highlights": profile.get("highlights", []),
        }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _execute_draft_batch(
    *,
    candidates_path: Path,
    raw_drafts: list[dict[str, Any]],
    profile_path: Path,
    rules_path: Path | None,
    history_path: Path,
    output_root: Path,
    dry_run: bool,
    force: bool,
) -> int:
    profile = _load_yaml(profile_path)
    history = OfferHistory(history_path)
    rules = SchoolFilterRules.from_yaml(rules_path) if rules_path and rules_path.is_file() else None

    raw_candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    candidates_by_id: dict[str, EnrichedOffer] = {}
    for entry in raw_candidates:
        enriched = EnrichedOffer.from_dict(entry)
        candidates_by_id[enriched.offer.id] = enriched

    validated_items: list[tuple[EnrichedOffer, DraftInput, dict[str, Any]]] = []
    validation_reports: list[dict[str, Any]] = []
    has_errors = False

    for item in raw_drafts:
        offer_id = str(item.get("offer_id", "")).strip()
        if offer_id not in candidates_by_id:
            raise SystemExit(f"Offer ID not found in {candidates_path}: {offer_id}")

        enriched = candidates_by_id[offer_id]
        draft = DraftInput(
            offer_id=offer_id,
            subject=str(item.get("subject", "")),
            body=str(item.get("body", "")),
            to_email=str(item.get("to_email", "")),
        )
        v_report = validate_draft(
            enriched=enriched,
            draft=draft,
            profile=profile,
            profile_dir=profile_path.parent,
            rules=rules,
            history=history,
            force=force,
        )
        validation_reports.append(v_report)
        if not v_report["valid"]:
            has_errors = True
        validated_items.append((enriched, draft, v_report))

    if dry_run or has_errors:
        payload = {
            "dry_run": dry_run,
            "valid": not has_errors,
            "candidates_path": str(candidates_path),
            "validations": validation_reports,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 1 if has_errors else 0

    created: list[dict[str, Any]] = []
    for enriched, draft, v_report in validated_items:
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
            match_score=enriched.match_score,
        )
        created.append(
            {
                "offer_id": enriched.offer.id,
                "company": enriched.offer.company,
                "title": enriched.offer.title,
                "draft_dir": str(draft_dir),
                "eml_path": str(draft_dir / "draft.eml"),
                "md_path": str(draft_dir / "application.md"),
                "warnings": v_report["warnings"],
            }
        )

    history.save()
    print(json.dumps({"created": created}, indent=2, ensure_ascii=False))
    return 0


def _run_build_drafts(args: argparse.Namespace) -> int:
    candidates_path = _resolve_candidates_path(
        args.candidates,
        runs_dir=Path(args.runs_dir) if args.runs_dir else None,
    )
    raw_drafts = json.loads(Path(args.drafts_json).read_text(encoding="utf-8"))
    if not isinstance(raw_drafts, list):
        raise SystemExit("drafts-json must contain a JSON array of draft objects")

    return _execute_draft_batch(
        candidates_path=candidates_path,
        raw_drafts=raw_drafts,
        profile_path=Path(args.profile).resolve(),
        rules_path=Path(args.rules) if args.rules else DEFAULT_RULES_PATH,
        history_path=Path(args.history),
        output_root=Path(args.out_dir),
        dry_run=bool(args.dry_run),
        force=bool(args.force),
    )


def _run_draft(args: argparse.Namespace) -> int:
    candidates_path = _resolve_candidates_path(
        args.candidates,
        runs_dir=Path(args.runs_dir) if args.runs_dir else None,
    )
    body = args.body
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")

    raw_drafts = [
        {
            "offer_id": args.offer_id,
            "subject": args.subject,
            "body": body,
            "to_email": args.to_email,
        }
    ]
    return _execute_draft_batch(
        candidates_path=candidates_path,
        raw_drafts=raw_drafts,
        profile_path=Path(args.profile).resolve(),
        rules_path=Path(args.rules) if args.rules else DEFAULT_RULES_PATH,
        history_path=Path(args.history),
        output_root=Path(args.out_dir),
        dry_run=bool(args.dry_run),
        force=bool(args.force),
    )


def _run_mark_status(args: argparse.Namespace) -> int:
    history = OfferHistory(Path(args.history))
    if not history.update_status(args.offer_id, args.status, notes=args.notes or ""):
        raise SystemExit(f"Offer ID not found in history: {args.offer_id}")
    history.save()
    print(json.dumps({"offer_id": args.offer_id, "status": args.status, "notes": args.notes or ""}))
    return 0


def _run_list_history(args: argparse.Namespace) -> int:
    history = OfferHistory(Path(args.history))
    records = list(history.records.values())
    if args.status:
        records = [r for r in records if r.get("status") == args.status]
    if args.format == "summary":
        summary = [
            {
                "id": r.get("id", ""),
                "status": r.get("status", ""),
                "company": r.get("company", ""),
                "title": r.get("title", ""),
                "match_score": r.get("match_score", ""),
                "contact_email": r.get("contact_email", ""),
                "updated_at": r.get("updated_at", ""),
            }
            for r in records
        ]
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(records, indent=2, ensure_ascii=False))
    return 0


def _run_stats(args: argparse.Namespace) -> int:
    history = OfferHistory(Path(args.history))
    stats = history.get_stats(follow_up_days=max(1, args.follow_up_days))
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    return 0


def _run_score(args: argparse.Namespace) -> int:
    profile = _load_yaml(Path(args.profile))
    candidates_path = _resolve_candidates_path(
        args.candidates,
        runs_dir=Path(args.runs_dir) if args.runs_dir else None,
    )
    raw_candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    rescored: list[EnrichedOffer] = []

    for entry in raw_candidates:
        enriched = EnrichedOffer.from_dict(entry)
        sec_warnings = detect_prompt_injection(
            f"{enriched.offer.title}\n{enriched.offer.company}\n{enriched.offer.description}"
        )
        res = score_offer(
            enriched.offer,
            profile,
            query=args.query,
            contact_email=enriched.contact_email,
            apply_channel=enriched.apply_channel,
            security_warnings=sec_warnings,
        )
        enriched.match_score = res.score
        enriched.matched_skills = res.matched_skills
        enriched.missing_skills = res.missing_skills
        enriched.score_breakdown = res.breakdown
        enriched.security_warnings = sec_warnings
        if enriched.match_score >= args.min_score:
            rescored.append(enriched)

    rescored.sort(
        key=lambda item: (-item.match_score, item.contact_email == "", item.offer.company.lower())
    )

    if args.in_place:
        atomic_write_text(
            candidates_path,
            json.dumps([item.to_dict() for item in rescored], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        atomic_write_text(
            candidates_path.with_name("summary.json"),
            json.dumps([_summarize_enriched(item) for item in rescored], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    print(json.dumps([_summarize_enriched(item) for item in rescored], indent=2, ensure_ascii=False))
    return 0


def _run_validate_profile(args: argparse.Namespace) -> int:
    report = validate_profile(Path(args.profile))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["valid"] else 1


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""
    parser = argparse.ArgumentParser(prog="job-hunter", description="Job Hunter skill CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    doc_p = sub.add_parser("doctor", help="Check profile, CV PDF, rules, and Playwright Chromium in one command")
    doc_p.add_argument("--profile", default=str(DEFAULT_PROFILE_PATH))
    doc_p.add_argument("--rules", default=str(DEFAULT_RULES_PATH))
    doc_p.add_argument("--skip-browser", action="store_true", help="Skip Playwright Chromium binary check")
    doc_p.set_defaults(func=_run_doctor)

    search_p = sub.add_parser("search", help="Scrape offers, filter schools, enrich contacts, and score")
    search_p.add_argument("--contract", choices=["stage", "alternance", "cdd", "cdi"], default=None)
    search_p.add_argument("--query", default="")
    search_p.add_argument("--from-profile", action="store_true", help="Use contracts/queries/location from profile.yaml")
    search_p.add_argument("--location", default="")
    search_p.add_argument("--remote", action="store_true")
    search_p.add_argument("--sources", default="")
    search_p.add_argument("--limit", type=int, default=10)
    search_p.add_argument("--min-score", type=int, default=0, help="Keep only offers with match_score >= N (0-100)")
    search_p.add_argument("--include-seen", action="store_true", help="Re-include previously extracted (undrafted) offers")
    search_p.add_argument("--profile", default=str(DEFAULT_PROFILE_PATH))
    search_p.add_argument("--rules", default=str(DEFAULT_RULES_PATH))
    search_p.add_argument("--history", default=str(DEFAULT_HISTORY_PATH))
    search_p.add_argument("--out-run-dir", default="")
    search_p.set_defaults(func=_run_search)

    show_p = sub.add_parser("show", help="Show full offer details (and optional profile) for selected offer IDs")
    show_p.add_argument("--offer-id", nargs="+", default=[], help="One or more offer IDs to inspect")
    show_p.add_argument("--top", type=int, default=5, help="Show top N offers if --offer-id is omitted")
    show_p.add_argument("--with-profile", action="store_true", help="Include candidate profile/skills/highlights in output")
    show_p.add_argument("--candidates", default="", help="Path to candidates.json (defaults to latest run)")
    show_p.add_argument("--runs-dir", default="")
    show_p.add_argument("--profile", default=str(DEFAULT_PROFILE_PATH))
    show_p.set_defaults(func=_run_show)

    draft_p = sub.add_parser("draft", help="Validate and generate .md + .eml for a single offer in one command")
    draft_p.add_argument("--offer-id", required=True)
    draft_p.add_argument("--subject", required=True)
    draft_p.add_argument("--body", default="")
    draft_p.add_argument("--body-file", default="", help="Optional path to a UTF-8 file containing the email body")
    draft_p.add_argument("--to-email", default="")
    draft_p.add_argument("--candidates", default="", help="Path to candidates.json (defaults to latest run)")
    draft_p.add_argument("--runs-dir", default="")
    draft_p.add_argument("--profile", default=str(DEFAULT_PROFILE_PATH))
    draft_p.add_argument("--rules", default=str(DEFAULT_RULES_PATH))
    draft_p.add_argument("--history", default=str(DEFAULT_HISTORY_PATH))
    draft_p.add_argument("--out-dir", default=str(DEFAULT_DRAFTS_DIR))
    draft_p.add_argument("--dry-run", action="store_true", help="Validate without writing files or updating history")
    draft_p.add_argument("--force", action="store_true", help="Allow regenerating draft for an already-sent offer")
    draft_p.set_defaults(func=_run_draft)

    drafts_p = sub.add_parser("build-drafts", help="Validate and generate .md and .eml drafts in batch")
    drafts_p.add_argument("--candidates", default="", help="Path to candidates.json (defaults to latest run)")
    drafts_p.add_argument("--latest", action="store_true", help="Use latest run's candidates.json (default when --candidates omitted)")
    drafts_p.add_argument("--runs-dir", default="")
    drafts_p.add_argument("--drafts-json", required=True)
    drafts_p.add_argument("--profile", default=str(DEFAULT_PROFILE_PATH))
    drafts_p.add_argument("--rules", default=str(DEFAULT_RULES_PATH))
    drafts_p.add_argument("--history", default=str(DEFAULT_HISTORY_PATH))
    drafts_p.add_argument("--out-dir", default=str(DEFAULT_DRAFTS_DIR))
    drafts_p.add_argument("--dry-run", action="store_true", help="Validate drafts without writing files or updating history")
    drafts_p.add_argument("--force", action="store_true", help="Allow regenerating drafts for already-sent offers")
    drafts_p.set_defaults(func=_run_build_drafts)

    mark_p = sub.add_parser("mark-status", help="Update offer status in history")
    mark_p.add_argument("--offer-id", required=True)
    mark_p.add_argument(
        "--status",
        required=True,
        choices=["drafted", "sent", "interview", "offer", "rejected", "ignored"],
    )
    mark_p.add_argument("--notes", default="")
    mark_p.add_argument("--history", default=str(DEFAULT_HISTORY_PATH))
    mark_p.set_defaults(func=_run_mark_status)

    list_p = sub.add_parser("list-history", help="List tracked offers")
    list_p.add_argument("--status", default="")
    list_p.add_argument("--format", choices=["json", "summary"], default="json")
    list_p.add_argument("--history", default=str(DEFAULT_HISTORY_PATH))
    list_p.set_defaults(func=_run_list_history)

    stats_p = sub.add_parser("stats", help="Show application funnel stats and follow-ups due")
    stats_p.add_argument("--follow-up-days", type=int, default=7)
    stats_p.add_argument("--history", default=str(DEFAULT_HISTORY_PATH))
    stats_p.set_defaults(func=_run_stats)

    score_p = sub.add_parser("score", help="Re-score an existing candidates.json against profile.yaml")
    score_p.add_argument("--candidates", default="", help="Path to candidates.json (defaults to latest run)")
    score_p.add_argument("--latest", action="store_true", help="Use latest run's candidates.json (default when --candidates omitted)")
    score_p.add_argument("--runs-dir", default="")
    score_p.add_argument("--profile", default=str(DEFAULT_PROFILE_PATH))
    score_p.add_argument("--query", default="")
    score_p.add_argument("--min-score", type=int, default=0)
    score_p.add_argument("--in-place", action="store_true")
    score_p.set_defaults(func=_run_score)

    val_p = sub.add_parser("validate-profile", help="Validate profile.yaml and CV PDF")
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
