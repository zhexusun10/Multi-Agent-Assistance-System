"""Write contracts/releases/recommendation-downstream-patches.json for every changed upstream-manifest file.

Run LAST, after every edit (P7 part 2, spec section 0): for each file of the 907cfa8 manifest whose bytes differ
from its ported_sha256 it records target, base_ported_sha256 (copied from the manifest, never recomputed),
patched_sha256 (sha256 of the file's final LF bytes), issue and changes. The manifest itself is never written.
Fails when a changed file has no description below, when a described file is unchanged, or when a target contains
CRLF bytes (recommendation/** is eol=lf in .gitattributes, so the fingerprint is of LF bytes).

Usage (repository root): .venv/bin/python evaluation/recommendation-exploration-2026-10-23/p7/register_downstream_patches.py
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "contracts/releases/recommendation-upstream-907cfa8.json"
OUTPUT = ROOT / "contracts/releases/recommendation-downstream-patches.json"
V2 = "Recommendation v2 (native contract 2.0; spec evaluation/recommendation-exploration-2026-10-23, MIGRATION.md)"
EXAMPLES = ("Regenerated contract-2.0 example (python -m recommendation.examples.generate_contract_examples): feedback snapshot, "
            "result_limit up to 10, pool / selection / resolution summaries, fused-v2 scores, no unverified_candidates")
TESTS = "Assertions changed one at a time for intended v2 rule changes, intent kept (listed per phase in VALIDATION.md)"

DESCRIPTIONS = {
    "recommendation/agent/controller.py": ("DEF11 controller terminal output and final-gate recovery; " + V2, [
        "Complete assessed empty rankings through the existing final gate",
        "Retain tool history and explicit error feedback across bounded final-gate repairs",
        "Proposal.listing_ids bounded by MAX_RESULT_LIMIT (10); LiveController timeout only from controller.timeout_seconds",
        "ScriptedController runs the fixed pipeline resolve_entities -> build_pool -> get_geographic_evidence -> rank_candidates and returns the select-v1 selection",
        "LiveController tool list from the typed registry; prompt and repair text from agent/prompt.py (criteria immutable, data is not instructions, only tool-returned IDs, no first pick)",
        "Model steps store only the messages added since the previous model step (slim trace)"]),
    "recommendation/config.py": (V2 + ": single settings source", [
        "Thin re-export of recommendation/settings/loader.py under the public name RecommendationConfig",
        "DEFAULT_* dictionaries, ScoringPolicy and RunLimits removed (values live in recommendation/settings/recommendation.yaml)"]),
    "recommendation/context/manager.py": (V2 + ": no feedback accumulation", [
        "Run-consistency checks kept (STALE_REFERENCE against the v2 head, STALE_REQUIREMENTS, run_sequence)",
        "Feedback snapshot checks: previous_round must be a committed v2 run with the same recommendation ID set; uncounted appointment statuses rejected (INVALID_FEEDBACK)",
        "Profile records the snapshot and the v2 browsing weights; weighted-v1 duration cap and event merging removed"]),
    "recommendation/examples/config.json": (V2 + ": settings experiment file", [
        "Rewritten in the v2 settings schema: identical to recommendation.yaml except cold_start.enabled false (not a production source)"]),
    "recommendation/examples/generate_contract_examples.py": (V2 + ": contract examples", [
        "Feedback-only and preference-update examples use a FeedbackSnapshot built from the preceding response; result_limit 10"]),
    "recommendation/examples/run.py": (V2 + ": example runner", ["Replays the example flow with a FeedbackSnapshot instead of ViewingEvents"]),
    "recommendation/models/candidate.py": (V2 + ": recommendation item", [
        "rank <= MAX_RESULT_LIMIT (display order), new score_rank, selection_reason, selection_note",
        "Unique score_rank; evidence must be exactly the evidence referenced by returned items"]),
    "recommendation/models/score_breakdown.py": (V2 + ": fused-v2 score breakdown", [
        "explicit/implicit renamed to soft/behavior; appointment_score, browsing_score and their weights added",
        "soft_evidence_coverage and comparison_coverage replace evidence_coverage; Contribution gains branch and method"]),
    "recommendation/models/tool_results.py": (V2 + ": tool results", [
        "Previews bounded by trace.preview_rows instead of 4; ScorePreview uses soft/behaviour names",
        "ToolResult carries selected_ids, completed_stages and compact data"]),
    "recommendation/ranking/ranker.py": (V2 + ": scoring reference", [
        "Rewritten as the scalar fused-v2 reference (soft_utility, feature_similarity, pair_similarity) reading the settings; weighted-v1 constants removed"]),
    "recommendation/README.md": (V2 + ": documentation", [
        "Feedback snapshot, pool cache / data_version / final gate, fused-v2 scoring, select-v1 selection and cold start described",
        "Settings section: single YAML source with confirmed / existing-default / experiment-result labels (provisional = not yet calibrated, pending) and TUNING.md; terminal debugging commands"]),
    "recommendation/repositories/geographic_repository.py": (V2 + ": native geography", [
        "Native catalog (LTA stations + exits, URA areas / regions, OSM places), resolver and Python haversine distances",
        "Neo4j NEAR_PLACE edges, point.distance and the 1 m tolerance comparison no longer used"]),
    "recommendation/repositories/listing_repository.py": (V2 + ": pool cache data version and by-id reads", [
        "Shared alias-free column list; data_version fingerprint inside the retrieve REPEATABLE READ transaction (retrieve_versioned)",
        "By-id lookup / lookup_versioned with ANY(CAST(:ids AS BIGINT[])) for the final gate"]),
    "recommendation/requirements.txt": (V2 + ": dependencies", ["PyYAML>=6,<7 (settings file) and numpy>=1.26,<3 (full-pool scoring)"]),
    "recommendation/schemas.py": (V2 + ": schema generation", ["feedback.schema.json generated from FeedbackSnapshot instead of ViewingEvent"]),
    "recommendation/service.py": (V2 + ": service flow", [
        "Settings frozen per request; config_version / config_hash / selection_hash recorded; scoring_version fused-v2, selection_algorithm select-v1",
        "Contract 2.0 flow: feedback snapshot checks, pool summary, select-v1 selection, compact run snapshot (format 2, selection_input), no unverified list",
        "Slim trace stage records (input_validation ... final_gate, response_assembly) with error location; replay via the stored selection_input"]),
    "recommendation/state/store.py": (V2 + ": state database", [
        "v2 tables request_identities_v2 / conversations_v2 / runs_v2 with direct state lookup; legacy 1.0 tables untouched and readable",
        "resolve_reference through tracing/slim.py; '<trace>/pool' from pool_cache; legacy v1 trace note"]),
    "recommendation/terminal/__main__.py": (V2 + ": terminal", [
        "Contract-2.0 requirements / feedback snapshot flow; recommend always mints a new request_id (except retrying a failed pending one)",
        "Debug commands dispatched to terminal/debug.py; --config; staged cold_start / result_limit; response summary"]),
    "recommendation/terminal/acceptance.py": (V2 + ": acceptance companion", [
        "Ported to contract 2.0: snapshot feedback, the same snapshot resent under a new request ID (request-independent outputs compared), scoring summary",
        "--config defaults to RECOMMENDATION_CONFIG"]),
    "recommendation/tests/test_policy_controller.py": (V2 + ": tests", [TESTS]),
    "recommendation/tests/test_recommendation.py": (V2 + ": tests", [TESTS]),
    "recommendation/tests/test_terminal_repositories.py": (V2 + ": tests", [TESTS]),
    "recommendation/tools/dispatcher.py": (V2 + ": tool dispatcher", [
        "Pipeline tools with stage dependencies and invalidation, typed per-tool arguments, resolution budgets and bind_entity application",
        "Pool built or reused through tools/pool_builder.py; full-pool fused-v2 scoring and select-v1 selection in rank_candidates",
        "Slim tool steps (arguments + compact result) and compact identity digests instead of full pool snapshots"]),
    "recommendation/validation/gate.py": (V2 + ": gates", [
        "final_gate: returned IDs equal the code selection, data_version re-checked (STALE_EVIDENCE), selected rows re-read by ID and hard criteria re-evaluated, selection replays from its input",
        "replay_gate: select-v1 recomputed from the stored selection_input; geographic provenance of returned listings recorded"]),
    "recommendation/contracts/feedback.py": (V2 + ": feedback contract", [
        "ViewingEvent replaced by FeedbackSnapshot (previous_round with exactly one browsing record per shown listing; one appointment entry per listing)"]),
    "recommendation/contracts/request.py": (V2 + ": request contract", [
        "contract_version 2.0 (1.0 rejected), result_limit None -> settings default, max 10, feedback snapshot, cold_start input"]),
    "recommendation/contracts/requirements.py": (V2 + ": requirements contract", [
        "A distance/commute criterion accepts a category alone, or a destination ID and/or a name (ID/name conflict -> ambiguous)"]),
    "recommendation/contracts/response.py": (V2 + ": response contract", [
        "contract_version 2.0, recommendations max 10, unverified_candidates and state.feedback_version removed",
        "resolution / pool / selection summaries; execution config and selection identity; evidence one entry per (evidence_id, criterion_id)"]),
    "recommendation/contracts/README.md": (V2 + ": documentation", ["Interface description updated to contract 2.0 (feedback snapshot, summaries, no unverified list); links fixed"]),
    "recommendation/contracts/schemas/feedback.schema.json": (V2 + ": generated schema", ["Regenerated by python -m recommendation.schemas (FeedbackSnapshot)"]),
    "recommendation/contracts/schemas/request.schema.json": (V2 + ": generated schema", ["Regenerated by python -m recommendation.schemas (request 2.0)"]),
    "recommendation/contracts/schemas/response.schema.json": (V2 + ": generated schema", ["Regenerated by python -m recommendation.schemas (response 2.0)"]),
}
for name in ("clarification", "error", "feedback-only-update", "initial-search", "no-match", "preference-update", "success"):
    DESCRIPTIONS[f"recommendation/examples/contracts/{name}.json"] = (V2 + ": contract example", [EXAMPLES])


def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
    entries, problems = [], []
    for entry in manifest["files"]:
        target = entry["target"]
        data = (ROOT / target).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest == entry["ported_sha256"]:
            if target in DESCRIPTIONS:
                problems.append(f"described but unchanged: {target}")
            continue
        if b"\r\n" in data:
            problems.append(f"CRLF bytes: {target}")
        if target not in DESCRIPTIONS:
            problems.append(f"changed without a description: {target}")
            continue
        issue, changes = DESCRIPTIONS[target]
        entries.append({"target": target, "base_ported_sha256": entry["ported_sha256"], "patched_sha256": digest,
                        "issue": issue, "changes": changes})
    if problems:
        sys.exit("\n".join(problems))
    out = {"upstream_commit": previous["upstream_commit"], "project_base_commit": previous["project_base_commit"],
           "purpose": "Declared local changes to the 907cfa8 port; original upstream fingerprints retained. "
                      "DEF11: recommendation-fit remediation (controller). Recommendation v2: native contract 2.0 "
                      "(feedback snapshot, pool cache, native geography, fused-v2 full-pool scoring, select-v1 selection, slim trace), "
                      "branch feat/recommendation-exploration; see evaluation/recommendation-exploration-2026-10-23/MIGRATION.md. "
                      "Team review and final system signoff remain pending.",
           "files": entries}
    OUTPUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"declared": len(entries), "manifest_files": len(manifest["files"])}))


if __name__ == "__main__":
    main()
