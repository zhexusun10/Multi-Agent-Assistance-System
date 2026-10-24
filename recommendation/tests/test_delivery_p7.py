"""P7 part 2 (delivery): upstream patch registration, retired comparison script, MIGRATION.md coverage,
final docs / INVENTORY / OPEN_ITEMS state and the recorded live smoke evidence.

Offline only (the autouse isolation fixture refuses live connections); the live smoke itself is recorded in
evaluation/recommendation-exploration-2026-10-23/p7/live-smoke.json and checked here as evidence.
"""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from recommendation.contracts.behavior import BehaviorContext, BookViewingListing, ListingViewDuration
from recommendation.contracts.feedback import ViewPayload, ViewingEvent
from recommendation.contracts.request import ColdStartInput, RecommendationRequest
from recommendation.contracts.response import Execution, RecommendationResponse, StateReference
from recommendation.contracts.summary import PoolSummary, ResolutionResult, SelectionSummary
from recommendation.models.candidate import RecommendationItem
from recommendation.models.score_breakdown import Contribution, ScoreBreakdown

ROOT = Path(__file__).resolve().parents[2]
EVALUATION = ROOT / "evaluation/recommendation-exploration-2026-10-23"
MANIFEST = ROOT / "contracts/releases/recommendation-upstream-907cfa8.json"
PATCHES = ROOT / "contracts/releases/recommendation-downstream-patches.json"
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()


# --- (d) upstream provenance ---------------------------------------------------------------------
def test_every_changed_manifest_file_is_declared_with_base_patched_issue_and_changes():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    declared = json.loads(PATCHES.read_text(encoding="utf-8"))
    assert declared["upstream_commit"] == manifest["upstream_commit"]
    ported = {e["target"]: e["ported_sha256"] for e in manifest["files"]}
    targets = [p["target"] for p in declared["files"]]
    assert len(targets) == len(set(targets)), "one entry per target"
    changed = {t for t, h in ported.items() if not (ROOT / t).is_file() or sha(ROOT / t) != h}
    assert changed == set(targets), {"undeclared": sorted(changed - set(targets)), "declared but unchanged": sorted(set(targets) - changed)}
    for patch in declared["files"]:
        path = ROOT / patch["target"]
        assert patch["base_ported_sha256"] == ported[patch["target"]], patch["target"]
        if patch.get("deleted"):
            # Declared deletion: absent on purpose, no patched bytes, with its reason.
            assert not path.exists() and patch["patched_sha256"] is None, patch["target"]
            assert patch["issue"].strip() and patch["changes"], patch["target"]
            continue
        assert patch["patched_sha256"] == sha(path) != patch["base_ported_sha256"], patch["target"]
        assert b"\r\n" not in path.read_bytes(), f"{patch['target']} must be hashed as LF bytes"
        assert patch["issue"].strip() and patch["changes"] and all(c.strip() for c in patch["changes"]), patch["target"]
    deleted = {p["target"] for p in declared["files"] if p.get("deleted")}
    assert all((ROOT / t).is_file() for t in ported if t not in deleted), "only declared deletions may be absent"
    assert deleted == {"recommendation/agent/model_bridge.ts"}


def test_upstream_check_passes_without_touching_the_manifest():
    before = MANIFEST.read_bytes()
    result = subprocess.run([sys.executable, str(ROOT / "scripts/import-recommendation-upstream.py"), "--check"],
                            capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr[-2000:]
    report = json.loads(result.stdout)
    assert report["mode"] == "manifest-check" and report["files"] == 71
    assert report["business_logic_changes"] == len(json.loads(PATCHES.read_text())["files"])
    assert MANIFEST.read_bytes() == before


def test_compare_upstream_script_is_retired(tmp_path):
    script = ROOT / "scripts/compare-recommendation-upstream.py"
    historical = ROOT / "evaluation/reports/native-upstream-parity.json"
    before = historical.read_bytes()
    plain = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, cwd=ROOT)
    assert plain.returncode == 2
    notice = json.loads(plain.stdout)
    assert notice["status"] == "retired" and notice["upstream"] == "907cfa8" and "not regenerated" in notice["historical_report"]
    # Even a fully specified historical re-run may never overwrite the kept evidence file.
    guarded = subprocess.run([sys.executable, str(script), "--reference", str(tmp_path), "--legacy-root", str(tmp_path),
                              "--output", str(historical)], capture_output=True, text=True, cwd=ROOT)
    assert guarded.returncode == 2 and "never overwritten" in guarded.stderr
    assert historical.read_bytes() == before


# --- (a) MIGRATION.md --------------------------------------------------------------------------
def _fields(*models):
    return {name for model in models for name in model.model_fields}


def test_migration_doc_covers_every_contract_field_table_and_behavior_case():
    text = (EVALUATION / "MIGRATION.md").read_text(encoding="utf-8")
    fields = _fields(RecommendationRequest, ColdStartInput, BehaviorContext, ListingViewDuration, BookViewingListing,
                     ViewingEvent, ViewPayload, RecommendationResponse, StateReference, Execution, PoolSummary,
                     SelectionSummary, ResolutionResult, RecommendationItem, ScoreBreakdown, Contribution)
    missing = sorted(f for f in fields if f"`{f}`" not in text and not re.search(rf"\b{re.escape(f)}\b", text))
    assert not missing, f"MIGRATION.md does not mention: {missing}"
    removed = ["unverified_candidates", "feedback_version", "explicit_score", "implicit_score", "evidence_coverage",
               "weighted-v1", "FeedbackSnapshot", "previous_round", "feedback-sidecar.ts"]
    assert all(name in text for name in removed), [n for n in removed if n not in text]
    tables = set()
    for module in ("recommendation/state/store.py", "recommendation/state/pool_cache.py", "recommendation/app.py"):
        tables |= set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", (ROOT / module).read_text()))
    v2 = {t for t in tables if t.endswith("_v2") or t.startswith("pool_cache") or t == "traces"}
    assert v2 and all(f"`{t}" in text for t in v2), sorted(t for t in v2 if f"`{t}" not in text)
    assert all(legacy in text for legacy in ("request_identities", "conversations", "runs"))
    for topic in ("Merge with main c7dc536", "behavior_context", "listing_view_durations", "book_viewing_listings",
                  "viewing_draft_saved", "interest_eligible", "session-cumulative", "not scored",
                  "007-recommendation-cold-start.sql", "(evidence_id, criterion_id)",
                  "recommendation/settings/recommendation.yaml", "RECOMMENDATION_CONFIG",
                  "recommendation-downstream-patches.json", "compare-recommendation-upstream.py",
                  "unverified_candidates", "INVALID_FEEDBACK", "UNSUPPORTED_VERSION", "config/recommendation-full.json"):
        assert topic in text, topic


# --- (a) docs and (c) inventory ------------------------------------------------------------------
STALE = ("weighted-v1", "最多4", "explicit_weight", "implicit_weight", '"result_limit":4', "0.8 × explicit", "feedback_version", "unverified candidates are separate")


@pytest.mark.parametrize("path", ["README.md", "recommendation/README.md", "recommendation/contracts/README.md", "contracts/releases/README.md"])
def test_delivery_docs_have_no_contract_1_limits_or_scoring(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert [s for s in STALE if s in text] == []


def test_native_integration_doc_keeps_weighted_v1_only_in_the_labelled_history():
    text = (ROOT / "docs/Recommendation_Agent_原生接入说明.md").read_text(encoding="utf-8")
    current, history = text.split("下表是 2026-10-20 迁移", 1)
    # The current part may name feedback_version only to say that 2.0 removed it.
    assert [s for s in STALE + ("显式偏好优先", "log(121)") if s in current.replace("2.0 已删除 feedback_version", "")] == []
    assert "fused-v2" in current and "select-v1" in current and "MIGRATION.md" in current
    assert "（2026-10-20，历史）" in history


def test_inventory_has_no_open_entry_and_records_the_final_grep():
    text = (EVALUATION / "INVENTORY.md").read_text(encoding="utf-8")
    opened = [line for line in text.splitlines() if re.search(r"\bopen-P\d", line) and "Status values" not in line]
    assert opened == []
    assert "## F. P7 final grep check" in text and (EVALUATION / "p7/inventory_check.sh").is_file()
    assert "== end" in (EVALUATION / "p7/inventory-grep.txt").read_text(encoding="utf-8")


def test_open_items_final_section_lists_pending_decisions_and_known_effects():
    text = (EVALUATION / "OPEN_ITEMS.md").read_text(encoding="utf-8")
    final = text.split("## Final state (P7)", 1)[1].split("\n## ", 1)[0]
    for topic in ("destination_id", "explicit-station", "cannot be exited", "Single-reference normalisation", "40 %", "trace", "incident",
                  "not_done", "native-recommendation-live", "8010"):
        assert topic in final, topic


# --- (e) live smoke evidence -----------------------------------------------------------------------
def test_recorded_live_smoke_used_a_separate_service_and_met_its_expectations():
    report = json.loads((EVALUATION / "p7/live-smoke.json").read_text(encoding="utf-8"))
    assert report["live_state_untouched"] is True
    scripted, live = report["scripted"], report["live"]
    assert scripted["port"] not in (8010, 8091, 8092) and scripted["state"] == "temporary"
    assert all(scripted["expectations"].values()), scripted["expectations"]
    assert [r["name"] for r in scripted["runs"]] == ["r1_initial", "r2_feedback_rerank", "r3_soft_change_cache_hit"]
    assert 1 <= live["llm_runs"] <= 3 and all(r["execution"]["controller_mode"] == "live" for r in live["runs"])
    for run in scripted["runs"] + live["runs"]:
        assert run["status"] == "success" and run["execution"]["data_mode"] == "database"
        assert all(v is True for k, v in run["correctness"].items() if k != "pool_content_rows"), run["correctness"]
    assert all(r["execution"]["tokens"] for r in live["runs"])
    oracle = json.loads((EVALUATION / "p7/live-oracle-check.json").read_text(encoding="utf-8"))
    assert oracle["status"] == "passed" and all(not c.get("violations") for c in oracle["checks"])
