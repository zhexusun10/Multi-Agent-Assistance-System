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
V2 = "Recommendation v2 (request = main contract 1.0 + behavior_context + cold_start, response = native contract 2.0; spec evaluation/recommendation-exploration-2026-10-23, MIGRATION.md)"
EXAMPLES = ("Regenerated example (python -m recommendation.examples.generate_contract_examples): main-contract request with behavior_context, "
            "result_limit up to 10, pool / selection / resolution summaries, fused-v2 scores, no unverified_candidates")
TESTS = "Assertions changed one at a time for intended v2 rule changes, intent kept (listed per phase in VALIDATION.md)"
MPD = "Cold-start detector mpd-v1 (2026-10-24, MIGRATION.md section 0.5): "
# Commit ff40fb6 recorded this line by hand in the registry; kept here so regenerating does not drop it.
SCORING_REVISION = ("User-requested scoring revision: identity similarity 1, preserve informative hard-constrained features, "
                    "current-pool min/max normalization; update associated offline expectations.")

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
        "behavior_context checks: uncounted viewing signals rejected (INVALID_FEEDBACK); feedback events validated, recorded, never scored",
        "Profile records the behaviour snapshot (cumulative browsing, interest_eligible, viewing drafts) and the v2 browsing weights; weighted-v1 duration cap and event merging removed"]),
    "recommendation/examples/config.json": (V2 + ": settings experiment file", [
        "Rewritten in the v2 settings schema: identical to recommendation.yaml except cold_start.enabled false (not a production source)",
        SCORING_REVISION,
        MPD + "C/B/S thresholds, reenter_on_pool_rebuild and stability removed; cold_start.detector {candidate_window 30, min_mean_pairwise_difference 0.01}"]),
    "recommendation/examples/generate_contract_examples.py": (V2 + ": contract examples", [
        "Feedback-only and preference-update examples use a behavior_context built from the preceding response (fixed as_of); result_limit 10"]),
    "recommendation/examples/run.py": (V2 + ": example runner", ["Replays the example flow with a behavior_context instead of ViewingEvents"]),
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
        "Rewritten as the scalar fused-v2 reference (soft_utility, feature_similarity, pair_similarity) reading the settings; weighted-v1 constants removed",
        SCORING_REVISION]),
    "recommendation/README.md": (V2 + ": documentation", [
        "Main behavior_context request, pool cache / data_version / final gate, fused-v2 scoring, select-v1 selection and cold start described",
        "Settings section: single YAML source with confirmed / existing-default / experiment-result labels (provisional = not yet calibrated, pending) and TUNING.md; terminal debugging commands",
        SCORING_REVISION,
        MPD + "cold start described as D < threshold over the top candidate_window scores, re-evaluated every round; exit fields compat-only"]),
    "recommendation/repositories/geographic_repository.py": (V2 + ": native geography", [
        "Native catalog (LTA stations + exits, URA areas / regions, OSM places), resolver and Python haversine distances",
        "Neo4j NEAR_PLACE edges, point.distance and the 1 m tolerance comparison no longer used"]),
    "recommendation/repositories/listing_repository.py": (V2 + ": pool cache data version and by-id reads", [
        "Shared alias-free column list; data_version fingerprint inside the retrieve REPEATABLE READ transaction (retrieve_versioned)",
        "By-id lookup / lookup_versioned with ANY(CAST(:ids AS BIGINT[])) for the final gate"]),
    "recommendation/requirements.txt": (V2 + ": dependencies", ["PyYAML>=6,<7 (settings file) and numpy>=1.26,<3 (full-pool scoring)"]),
    "recommendation/schemas.py": (V2 + ": schema generation", ["behavior-context.schema.json added (main BehaviorContext); feedback.schema.json stays ViewingEvent"]),
    "recommendation/service.py": (V2 + ": service flow", [
        "Settings frozen per request; config_version / config_hash / selection_hash recorded; scoring_version fused-v2, selection_algorithm select-v1",
        "v2 flow: behaviour-input checks, pool summary, select-v1 selection, compact run snapshot (format 2, selection_input), no unverified list; web_detail / viewing-draft warnings",
        "Slim trace stage records (input_validation ... final_gate, response_assembly) with error location; replay via the stored selection_input"]),
    "recommendation/state/store.py": (V2 + ": state database", [
        "v2 tables request_identities_v2 / conversations_v2 / runs_v2 with direct state lookup; legacy 1.0 tables untouched and readable",
        "resolve_reference through tracing/slim.py; '<trace>/pool' from pool_cache; legacy v1 trace note"]),
    "recommendation/terminal/__main__.py": (V2 + ": terminal", [
        "Requirements / behavior_context flow (main contract); recommend always mints a new request_id (except retrying a failed pending one)",
        "Debug commands dispatched to terminal/debug.py; --config; staged cold_start / result_limit; response summary",
        MPD + "summary prints the detector status (reason, D, threshold, window); stored legacy C/B/S responses shown as stored; exit hint removed"]),
    "recommendation/terminal/acceptance.py": (V2 + ": acceptance companion", [
        "Ported to the merged request: behavior_context, the same snapshot resent under a new request ID (request-independent outputs compared), scoring summary",
        "--config defaults to RECOMMENDATION_CONFIG",
        MPD + "docstring: D is request-independent (no stability seed)"]),
    "recommendation/tests/test_policy_controller.py": (V2 + ": tests", [TESTS, SCORING_REVISION]),
    "recommendation/tests/test_recommendation.py": (V2 + ": tests", [TESTS, SCORING_REVISION]),
    "recommendation/tests/test_terminal_repositories.py": (V2 + ": tests", [TESTS]),
    "recommendation/tools/dispatcher.py": (V2 + ": tool dispatcher", [
        "Pipeline tools with stage dependencies and invalidation, typed per-tool arguments, resolution budgets and bind_entity application",
        "Pool built or reused through tools/pool_builder.py; full-pool fused-v2 scoring and select-v1 selection in rank_candidates",
        "Slim tool steps (arguments + compact result) and compact identity digests instead of full pool snapshots"]),
    "recommendation/validation/gate.py": (V2 + ": gates", [
        "final_gate: returned IDs equal the code selection, data_version re-checked (STALE_EVIDENCE), selected rows re-read by ID and hard criteria re-evaluated, selection replays from its input",
        "replay_gate: select-v1 recomputed from the stored selection_input; geographic provenance of returned listings recorded"]),
    "recommendation/contracts/feedback.py": (V2 + ": feedback contract", [
        "main's ViewingEvent (source sample | web_detail) kept; documented as validated, not scored (behavior_context holds the same time cumulatively)"]),
    "recommendation/contracts/request.py": (V2 + ": request contract", [
        "Main request contract 1.0 (2.0 bodies rejected) with feedback events and behavior_context; result_limit None -> settings default, max 10; recommendation-owned cold_start input; event conversation and unique IDs checked",
        MPD + "ColdStartInput docstring: accepted for compatibility, ignored by the detector"]),
    "recommendation/contracts/requirements.py": (V2 + ": requirements contract", [
        "A distance/commute criterion accepts a category alone, or a destination ID and/or a name (ID/name conflict -> ambiguous)"]),
    "recommendation/contracts/response.py": (V2 + ": response contract", [
        "contract_version 2.0, recommendations max 10, unverified_candidates and state.feedback_version removed",
        "resolution / pool / selection summaries; execution config and selection identity; evidence one entry per (evidence_id, criterion_id)"]),
    "recommendation/contracts/README.md": (V2 + ": documentation", ["Interface description updated (main request + behavior_context + cold_start, response 2.0 summaries, no unverified list); links fixed",
        MPD + "cold_start request field compat-only; selection.cold_start union (mpd-v1 or stored legacy C/B/S)"]),
    "recommendation/contracts/schemas/feedback.schema.json": (V2 + ": generated schema", ["Regenerated by python -m recommendation.schemas (ViewingEvent, sample | web_detail)"]),
    "recommendation/contracts/schemas/request.schema.json": (V2 + ": generated schema", ["Regenerated by python -m recommendation.schemas (main request 1.0 + behavior_context + cold_start, result_limit <= 10)",
        MPD + "ColdStartInput description"]),
    "recommendation/contracts/schemas/response.schema.json": (V2 + ": generated schema", ["Regenerated by python -m recommendation.schemas (response 2.0)",
        MPD + "selection.cold_start oneOf ColdStartDiagnostics (mpd-v1) / LegacyColdStartDiagnostics"]),
}
AGENT = "Dedicated Python LangChain agent (2026-10-24, MIGRATION.md section 0.6): "
for _target, _line in {
    "recommendation/agent/controller.py": "LiveController uses agent/llm.py (RECOMMENDATION_LLM_*, no Node subprocess); "
                                          "completion = current ranking_artifact_id (Completion), invalid/stale ones refused in the loop",
    "recommendation/models/tool_results.py": "ToolResult.selected_ids replaced by ranking_artifact_id",
    "recommendation/tools/dispatcher.py": "rank_candidates yields a code-managed ranking artifact with evidence coverage; "
                                          "read_pool; select_ranking (cold start + selection after the loop); discard_evidence",
    "recommendation/validation/gate.py": "final_gate(dispatcher, trace) validates the code selection of the current artifact; "
                                         "GateFailure with structured repair metadata",
    "recommendation/service.py": "select_and_validate: artifact check, code selection, final gate, code-controlled repair "
                                 "(FailureReport, invalidation, rebuild once, no-progress stop); agent inputs in the context",
    "recommendation/terminal/__main__.py": "check-connections reports RECOMMENDATION_LLM_* configuration",
    "recommendation/README.md": "Python LangChain agent, read_pool, ranking artifact handoff and structured repair described",
    "recommendation/requirements.txt": "langchain>=1.4,<2 and langchain-openai>=1.7,<2",
    "recommendation/tests/test_policy_controller.py": "fake model finishes with the ranking_artifact_id",
    "recommendation/tests/test_recommendation.py": "invented completion -> INVALID_SELECTION",
}.items():
    DESCRIPTIONS[_target][1].append(AGENT + _line)

SELECT2 = "select-v2 cold-start selection (2026-10-24, MIGRATION.md section 0.7): "
for _target, _line in {
    "recommendation/validation/gate.py": "head = current ranking prefix; window = top exploration_window; returned = intermediate prefix",
    "recommendation/service.py": "Execution.strategy names the active selection algorithm",
    "recommendation/terminal/acceptance.py": "request-independent exploration identity for select-v2 summaries",
    "recommendation/README.md": "select-v2 window / intermediate / returned-prefix selection described",
    "recommendation/contracts/README.md": "selection.exploration union (select-v2 labelled counts / stored select-v1)",
    "recommendation/contracts/schemas/response.schema.json": "selection.exploration oneOf ExplorationV2 / Exploration",
    "recommendation/examples/config.json": "price_sampling exploration_window 30 + intermediate_size 10 (target_sample_size removed)",
}.items():
    DESCRIPTIONS[_target][1].append(SELECT2 + _line)

# Ported files removed on purpose (declared deletions: no patched bytes; the checker requires absence).
DELETIONS = {
    "recommendation/agent/model_bridge.ts": (
        "Dedicated Python LangChain recommendation agent (recommendation/agent/llm.py, RECOMMENDATION_LLM_*)",
        ["Node.js model bridge removed: the recommendation agent no longer calls src/model.ts or launches a "
         "subprocess for model requests; the outer application's TypeScript model factory is unchanged"]),
}
for name in ("clarification", "error", "feedback-only-update", "initial-search", "no-match", "preference-update", "success"):
    DESCRIPTIONS[f"recommendation/examples/contracts/{name}.json"] = (V2 + ": contract example", [EXAMPLES, MPD + "regenerated (new selection_hash; mpd-v1 cold_start)",
                                                                             SELECT2 + "regenerated (select-v2 identity)"])


def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
    entries, problems = [], []
    for entry in manifest["files"]:
        target = entry["target"]
        if not (ROOT / target).exists():
            if target not in DELETIONS:
                problems.append(f"deleted without a declaration: {target}")
                continue
            issue, changes = DELETIONS[target]
            entries.append({"target": target, "base_ported_sha256": entry["ported_sha256"], "patched_sha256": None,
                            "deleted": True, "issue": issue, "changes": changes})
            continue
        if target in DELETIONS:
            problems.append(f"declared deleted but present: {target}")
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
                      "(pool cache, native geography, fused-v2 full-pool scoring, select-v1 selection, slim trace), merged with main c7dc536 "
                      "(request = main contract 1.0 + behavior_context + cold_start; behavior_context scored natively), "
                      "branch feat/recommendation-exploration; see evaluation/recommendation-exploration-2026-10-23/MIGRATION.md. "
                      "Team review and final system signoff remain pending.",
           "files": entries}
    OUTPUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"declared": len(entries), "manifest_files": len(manifest["files"])}))


if __name__ == "__main__":
    main()
