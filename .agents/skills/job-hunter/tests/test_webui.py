"""Tests for the local Material Design 3 WebUI server and data store."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import pytest
import yaml

from scripts.draft_builder import write_application_draft
from scripts.history import OfferHistory
from scripts.models import DraftInput, EnrichedOffer, JobOffer
from scripts.webui import WebUIDataStore, create_server


def _seed_workspace(tmp_path: Path) -> dict[str, Path]:
    history_path = tmp_path / "data" / "history.json"
    runs_dir = tmp_path / "data" / "runs"
    drafts_dir = tmp_path / "drafts"
    profile_path = tmp_path / "references" / "profile.yaml"
    cv_path = tmp_path / "references" / "cv.pdf"
    cv_path.parent.mkdir(parents=True, exist_ok=True)
    cv_path.write_bytes(b"%PDF-1.4 dummy cv content")

    profile = {
        "candidate": {
            "full_name": "Alex Martin",
            "email": "alex@example.com",
            "location": "Lyon",
            "cv_path": str(cv_path),
        },
        "defaults": {
            "contracts": ["alternance"],
            "queries": ["Développeur Fullstack"],
            "location": "Lyon",
        },
    }
    profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")

    history = OfferHistory(history_path)

    offer_drafted = JobOffer(
        id="draft001",
        source="indeed",
        title="Développeur Full Stack",
        company="2emotion",
        location="Lyon",
        contract_type="alternance",
        url="https://example.com/2emotion",
        apply_url="https://example.com/2emotion/apply",
        description="Super poste Full Stack PHP / JS à Lyon.",
    )
    enriched_drafted = EnrichedOffer(
        offer=offer_drafted,
        contact_email="jobs@2emotion.com",
        email_source="offer_text",
        apply_channel="email",
        match_score=84,
        matched_skills=["php", "javascript"],
        missing_skills=["docker"],
    )
    draft_dir = write_application_draft(
        enriched=enriched_drafted,
        draft=DraftInput(
            offer_id="draft001",
            subject="Candidature Alternance Full Stack – Alex Martin",
            body="Bonjour,\n\nJe souhaite rejoindre 2emotion en alternance.",
            to_email="jobs@2emotion.com",
        ),
        profile=profile,
        output_root=drafts_dir,
        run_date="2026-11-20",
        profile_dir=profile_path.parent,
    )
    history.record_offer(
        offer_drafted,
        "drafted",
        contact_email="jobs@2emotion.com",
        draft_dir=str(draft_dir),
        match_score=84,
    )

    offer_sent_old = JobOffer(
        id="sent001",
        source="linkedin",
        title="Développeur Backend Python",
        company="Alan",
        location="Paris",
        contract_type="alternance",
        url="https://example.com/alan",
        description="Backend Python API.",
    )
    history.record_offer(offer_sent_old, "sent", contact_email="rh@alan.com", match_score=76)
    history.records["sent001"]["updated_at"] = "2020-01-01T10:00:00+00:00"

    offer_candidate = JobOffer(
        id="cand001",
        source="hellowork",
        title="Alternance Dev Web",
        company="SolarCare",
        location="Villeurbanne",
        contract_type="alternance",
        url="https://example.com/solarcare",
        description="Poste web avec Svelte et Laravel.",
    )
    history.record_offer(offer_candidate, "candidates_extracted")

    offer_school = JobOffer(
        id="school01",
        source="hellowork",
        title="Alternance Dev",
        company="ISCOD",
        location="Lyon",
        contract_type="alternance",
        url="https://example.com/iscod",
    )
    history.record_offer(offer_school, "rejected_school", reason="blacklisted_company:iscod")
    history.save()

    run_dir = runs_dir / "20261120T093729Z"
    run_dir.mkdir(parents=True, exist_ok=True)
    # Mix a scored entry and a legacy unscored entry
    candidates_payload = [
        enriched_drafted.to_dict(),
        {
            "offer": offer_candidate.to_dict(),
            "contact_email": "",
            "email_source": "none",
        },
    ]
    (run_dir / "candidates.json").write_text(
        json.dumps(candidates_payload, ensure_ascii=False), encoding="utf-8"
    )
    (run_dir / "report.json").write_text(
        json.dumps(
            {
                "query": "Développeur Fullstack",
                "contract": "alternance",
                "location": "Lyon",
                "sources": [
                    {"source": "wttj", "status": "blocked_antibot", "scraped": 0, "error": "HTTP 403"},
                    {"source": "linkedin", "status": "ok", "scraped": 5, "error": ""},
                ],
                "stats": {"scraped": 5, "rejected_schools": 1, "duplicates": 0, "kept": 2},
                "rejected_school_details": [
                    {
                        "id": "school01",
                        "source": "hellowork",
                        "company": "ISCOD",
                        "title": "Alternance Dev",
                        "reason": "blacklisted_company:iscod",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return {
        "history_path": history_path,
        "runs_dir": runs_dir,
        "drafts_dir": drafts_dir,
        "profile_path": profile_path,
    }


def test_datastore_dashboard_detail_and_updates(tmp_path: Path) -> None:
    paths = _seed_workspace(tmp_path)
    store = WebUIDataStore(**paths, default_follow_up_days=7)

    dash = store.get_dashboard()
    assert dash["candidate"]["full_name"] == "Alex Martin"
    assert dash["candidate"]["cv_valid"] is True
    assert dash["stats"]["total_tracked"] == 4
    assert dash["stats"]["action_required_count"] == 2  # 1 drafted + 1 follow-up due
    assert len(dash["tasks"]["drafts_to_send"]) == 1
    assert dash["tasks"]["drafts_to_send"][0]["has_cv_attachment"] is True
    assert len(dash["tasks"]["follow_ups"]) == 1
    assert dash["tasks"]["follow_ups"][0]["id"] == "sent001"
    assert len(dash["tasks"]["to_review"]) == 1
    assert dash["tasks"]["to_review"][0]["match_score"] is None  # legacy unscored run preserved as None
    assert len(dash["runs"]) == 1
    assert len(dash["drafts"]) == 1

    detail = store.get_offer_detail("draft001")
    assert detail is not None
    assert "Super poste Full Stack" in detail["description"]
    assert detail["draft"]["eml"]["has_cv_attachment"] is True
    assert detail["draft"]["eml"]["to"] == "jobs@2emotion.com"

    # Updating only notes on a sent offer must NOT reset updated_at (preserves follow-up age)
    updated_sent = store.update_offer("sent001", status="sent", notes="Relance prévue lundi")
    assert updated_sent is not None
    assert updated_sent["notes"] == "Relance prévue lundi"
    assert updated_sent["updated_at"] == "2020-01-01T10:00:00+00:00"
    assert updated_sent["needs_follow_up"] is True

    # Clearing notes works
    cleared = store.update_offer("sent001", notes="")
    assert cleared is not None
    assert cleared["notes"] == ""

    # Invalid status rejected
    with pytest.raises(ValueError):
        store.update_offer("draft001", status="invalid_status")


def test_webui_http_endpoints_and_security_guards(tmp_path: Path) -> None:
    paths = _seed_workspace(tmp_path)
    server = create_server(host="127.0.0.1", port=0, **paths)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=base_url, timeout=5.0, trust_env=False) as client:
            # HTML & static assets + security headers
            r_index = client.get("/")
            assert r_index.status_code == 200
            assert "Job Hunter" in r_index.text
            assert r_index.headers["X-Content-Type-Options"] == "nosniff"
            assert r_index.headers["X-Frame-Options"] == "DENY"
            assert "default-src 'self'" in r_index.headers["Content-Security-Policy"]

            assert client.get("/assets/styles.css").status_code == 200
            assert client.get("/assets/app.js").status_code == 200
            # Path traversal blocked
            assert client.get("/assets/../webui.py").status_code == 404

            # Dashboard API
            r_dash = client.get("/api/dashboard")
            assert r_dash.status_code == 200
            assert r_dash.json()["stats"]["total_tracked"] == 4

            # Offer detail API
            r_offer = client.get("/api/offers/draft001")
            assert r_offer.status_code == 200
            assert r_offer.json()["draft"]["eml"]["subject"].startswith("Candidature Alternance")
            assert client.get("/api/offers/nonexistent").status_code == 404

            # EML download
            r_eml = client.get("/api/drafts/draft001/eml")
            assert r_eml.status_code == 200
            assert "message/rfc822" in r_eml.headers["Content-Type"]
            assert b"X-Unsent: 1" in r_eml.content

            # CSV export
            r_csv = client.get("/api/export/csv")
            assert r_csv.status_code == 200
            assert "2emotion" in r_csv.text

            # CSRF Origin guard blocks foreign origins
            r_csrf = client.post(
                "/api/offers/draft001/status",
                json={"status": "sent"},
                headers={"Origin": "https://evil.example.com"},
            )
            assert r_csrf.status_code == 403

            # DNS rebinding Host guard blocks foreign Host header
            r_host = client.get("/api/dashboard", headers={"Host": "evil.example.com:8765"})
            assert r_host.status_code == 403

            # Valid status update
            r_post = client.post(
                "/api/offers/draft001/status",
                json={"status": "sent", "notes": "Envoyé via Thunderbird"},
                headers={"Origin": base_url},
            )
            assert r_post.status_code == 200
            body = r_post.json()
            assert body["ok"] is True
            assert body["offer"]["status"] == "sent"
            assert body["offer"]["notes"] == "Envoyé via Thunderbird"

            # Path traversal via tampered draft_dir in history.json cannot leak external files
            secret_file = tmp_path / "secret_outside" / "draft.eml"
            secret_file.parent.mkdir(parents=True, exist_ok=True)
            secret_file.write_text("TOP SECRET", encoding="utf-8")
            hist = OfferHistory(paths["history_path"])
            hist.records["cand001"]["draft_dir"] = str(secret_file.parent)
            hist.save()
            assert client.get("/api/drafts/cand001/eml").status_code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
