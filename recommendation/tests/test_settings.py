"""P1: single settings file, loader validation and config adapter (weighted-v1 parity retired in P5)."""

# conftest.py isolates every test; the app keeps caller-set RECOMMENDATION_* selections over .env.
import recommendation.app  # noqa: F401
from .isolation import subprocess_env
import argparse, json, os, re, subprocess, sys
from pathlib import Path
import pytest
import yaml
from annotated_types import Le, MaxLen
from pydantic import ValidationError
from recommendation.config import RecommendationConfig, ConfigError, SETTINGS_PATH
from recommendation.settings import loader
from recommendation.contracts.request import RecommendationRequest
from recommendation.models.candidate import RecommendationItem, Response
from recommendation.agent.controller import Proposal, ScriptedController
from recommendation.repositories.connections import repositories
from recommendation.service import RecommendationService
from recommendation.state.store import StateStore
from recommendation.ranking import scoring_v2
from .test_scoring_v2 import pool_scores

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "recommendation"
# P7: calibrated values are experiment-result; values P7 could not calibrate keep the spec R2 label
# "provisional" (listed as pending decisions; test_analysis_p7 checks that).
SOURCE_TAG = re.compile(r"#.*\b(confirmed|existing-default|experiment-result|provisional)\b")


@pytest.fixture(autouse=True)
def clean_settings_env(monkeypatch):
    """Tests start from the committed YAML whatever the developer's shell exports."""
    monkeypatch.delenv("RECOMMENDATION_TRACE_MODE", raising=False)
    monkeypatch.delenv("RECOMMENDATION_CONFIG", raising=False)


def default():
    return RecommendationConfig.default()


def data(**overrides):
    result = default().to_data()
    loader.merge_overrides(result, overrides)
    return result


def leaves(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from leaves(child, f"{prefix}{key}.")
    else:
        yield prefix.rstrip("."), value


def rejected(overrides, key):
    with pytest.raises(ConfigError) as error:
        RecommendationConfig.from_data(data(**overrides), "fixture")
    assert key in str(error.value), str(error.value)
    return str(error.value)


# --- valid configuration ----------------------------------------------------------------
def test_committed_yaml_loads_with_identity():
    config = default()
    assert config.source == str(SETTINGS_PATH) and config.config_version == 1
    assert re.fullmatch(r"[0-9a-f]{64}", config.config_hash)
    assert re.fullmatch(r"[0-9a-f]{64}", config.selection_hash)
    assert config.identity()["config_hash"] == config.config_hash
    # R2: provisional non-null values; the only null is the explicit "uncapped" duration cap.
    nulls = [key for key, value in leaves(config.to_data()) if value is None]
    assert nulls == ["ranking.browsing.duration_cap_seconds"]
    assert config.ranking.similarity.feature_weights.as_dict() == {
        "price": 0.20, "property_type": 0.20, "bedrooms": 0.16, "floor_area_sqft": 0.20, "geography": 0.24,
    }
    assert config.recommendation.default_result_limit == config.recommendation.max_result_limit == 10
    assert (config.ranking.soft_weight, config.ranking.behavior_weight) == (0.60, 0.40)
    # A set, stored sorted (the YAML lists MANUAL_SENT, MANUAL_REPLIED, CONFIRMED).
    assert config.ranking.behavior.counted_appointment_signals == ("viewing_draft_saved",)
    assert config.ranking.behavior.browsing_requires_interest_eligible is True
    assert RecommendationConfig.from_file(None) is config and RecommendationConfig.from_file("") is config


def test_every_yaml_value_is_annotated_with_its_source():
    stack = []
    for number, line in enumerate(SETTINGS_PATH.read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        tagged = bool(SOURCE_TAG.search(line)) or any(t for _, t in stack)
        key, _, value = line.partition("#")[0].partition(":")
        if value.strip():
            assert tagged or key.strip() == "config_version", f"line {number} lacks a source tag: {line}"
        stack.append((indent, bool(SOURCE_TAG.search(line))))


def test_yaml_and_json_alternates_share_schema_and_hash(tmp_path):
    config = default()
    as_json, as_yaml = tmp_path / "alt.json", tmp_path / "alt.yml"
    as_json.write_text(json.dumps(config.to_data()))
    as_yaml.write_text(yaml.safe_dump(config.to_data()))
    for path in (as_json, as_yaml):
        loaded = RecommendationConfig.from_file(path)
        assert loaded.config_hash == config.config_hash and loaded.source == str(path)
    (tmp_path / "alt.toml").write_text("")
    with pytest.raises(ConfigError, match="suffix"):
        RecommendationConfig.from_file(tmp_path / "alt.toml")
    (tmp_path / "dup.yaml").write_text("config_version: 1\nconfig_version: 1\n")
    with pytest.raises(ConfigError, match="duplicate key"):
        RecommendationConfig.from_file(tmp_path / "dup.yaml")
    (tmp_path / "dup.json").write_text('{"config_version": 1, "config_version": 1}')
    with pytest.raises(ConfigError, match="duplicate key"):
        RecommendationConfig.from_file(tmp_path / "dup.json")
    with pytest.raises(ConfigError, match="cannot read"):
        RecommendationConfig.from_file(tmp_path / "missing.yaml")


def test_examples_config_is_v2_experiment_with_cold_start_disabled():
    example = RecommendationConfig.from_file(PACKAGE / "examples/config.json")
    assert example.cold_start.enabled is False
    expected = default().to_data()
    expected["cold_start"]["enabled"] = False
    assert example.to_data() == expected
    assert example.config_hash != default().config_hash


def test_hash_identity_and_trace_mode_env(monkeypatch, tmp_path):
    config = default()
    debug = config.with_overrides({"trace.mode": "debug"})
    assert debug.selection_hash == config.selection_hash and debug.config_hash != config.config_hash
    tie = config.with_overrides({"selection.tie_max_score_span": 0.03})
    assert tie.selection_hash != config.selection_hash
    path = tmp_path / "alt.json"
    path.write_text(json.dumps(config.to_data()))
    monkeypatch.setenv("RECOMMENDATION_TRACE_MODE", "debug")
    assert RecommendationConfig.from_file(path).trace.mode == "debug"
    assert RecommendationConfig.default().trace.mode == "debug"
    monkeypatch.setenv("RECOMMENDATION_TRACE_MODE", "verbose")
    with pytest.raises(ConfigError, match="trace.mode"):
        RecommendationConfig.from_file(path)


def test_counted_signals_are_canonical_and_the_interest_gate_is_result_affecting():
    config = default()
    same = config.with_overrides({"ranking.behavior.counted_appointment_signals": ["viewing_draft_saved"]})
    assert (same.config_hash, same.selection_hash) == (config.config_hash, config.selection_hash)
    ungated = config.with_overrides({"ranking.behavior.browsing_requires_interest_eligible": False})
    assert ungated.selection_hash != config.selection_hash


def test_settings_are_frozen_per_request():
    config = default()
    with pytest.raises(ValidationError):
        config.controller.max_tool_calls = 3
    with pytest.raises(ValidationError):
        config.ranking = None
    bounds = config.ranking.soft.numeric_bounds.as_dict()
    bounds["price"] = (1, 2)
    assert config.ranking.soft.numeric_bounds.price == (0.0, 20000.0)


# --- rejected configurations (key named in the message) ------------------------------------
@pytest.mark.parametrize(
    "overrides,key",
    [
        ({"ranking.soft_weight": 0.7}, "ranking.soft_weight + ranking.behavior_weight"),
        ({"ranking.behavior.browsing_weight": 0.5}, "ranking.behavior.appointment_weight + ranking.behavior.browsing_weight"),
        ({"ranking.similarity.feature_weights.price": 0.3}, "ranking.similarity.feature_weights must sum to 1"),
        ({"ranking.similarity.feature_weights.price": -0.1}, "ranking.similarity.feature_weights.price"),
        ({"ranking.soft_weight": float("nan")}, "ranking.soft_weight"),
        ({"ranking.behavior_weight": float("inf")}, "ranking.behavior_weight"),
        ({"ranking.soft_weight": "0.6"}, "ranking.soft_weight"),
        ({"ranking.behavior.missing_branch_contribution": 0.1}, "ranking.behavior.missing_branch_contribution"),
        ({"ranking.behavior.redistribute_missing_branch_weight": True}, "ranking.behavior.redistribute_missing_branch_weight"),
    ],
)
def test_bad_weights_rejected(overrides, key):
    rejected(overrides, key)


@pytest.mark.parametrize(
    "overrides,key",
    [
        ({"cold_start.detector.candidate_window": 1}, "cold_start.detector.candidate_window"),
        ({"cold_start.detector.candidate_window": 0}, "cold_start.detector.candidate_window"),
        ({"cold_start.detector.candidate_window": loader.DETECTOR_WINDOW_CEILING + 1}, "cold_start.detector.candidate_window"),
        ({"cold_start.detector.candidate_window": True}, "cold_start.detector.candidate_window"),
        ({"cold_start.detector.candidate_window": 2.5}, "cold_start.detector.candidate_window"),
        ({"cold_start.detector.min_mean_pairwise_difference": -0.01}, "cold_start.detector.min_mean_pairwise_difference"),
        ({"cold_start.detector.min_mean_pairwise_difference": "0.01"}, "cold_start.detector.min_mean_pairwise_difference"),
        ({"cold_start.detector.min_mean_pairwise_difference": True}, "cold_start.detector.min_mean_pairwise_difference"),
        ({"cold_start.detector.min_mean_pairwise_difference": float("inf")}, "cold_start.detector.min_mean_pairwise_difference"),
        ({"cold_start.price_sampling.target_sample_size": 5}, "cold_start.price_sampling.target_sample_size"),
        ({"cold_start.price_sampling.quantile_bins": 0}, "cold_start.price_sampling.quantile_bins"),
        ({"selection.tie_max_score_span": -0.01}, "selection.tie_max_score_span"),
        ({"recommendation.max_result_limit": 11}, "recommendation.max_result_limit"),
        ({"recommendation.default_result_limit": 0}, "recommendation.default_result_limit"),
        ({"recommendation.max_result_limit": 8}, "recommendation.default_result_limit must not exceed"),
        ({"controller.max_model_turns": 17}, "controller.max_model_turns"),
        ({"controller.max_tool_calls": 41}, "controller.max_tool_calls"),
        ({"controller.max_tool_calls": 4}, "controller.max_resolution_tool_calls must not exceed"),
        ({"controller.model_result_candidates": 9}, "controller.model_result_candidates must be >="),
        ({"controller.timeout_seconds": 0}, "controller.timeout_seconds"),
        ({"controller.repairs": 3}, "controller.repairs"),
        ({"ranking.similarity.geographic_scale_m": 0}, "ranking.similarity.geographic_scale_m"),
        ({"ranking.similarity.min_comparison_coverage": 2}, "ranking.similarity.min_comparison_coverage"),
        ({"ranking.browsing.time_scale_seconds": 0}, "ranking.browsing.time_scale_seconds"),
        ({"ranking.browsing.duration_cap_seconds": -1}, "ranking.browsing.duration_cap_seconds"),
        ({"ranking.soft.numeric_bounds.bedrooms": [5, 5]}, "ranking.soft.numeric_bounds.bedrooms"),
        ({"ranking.soft.numeric_bounds.price": [-1, 10]}, "ranking.soft.numeric_bounds.price"),
        ({"trace.debug_max_rows": 5}, "trace.debug_max_rows"),
        ({"cache.ttl_seconds": -1}, "cache.ttl_seconds"),
        ({"cache.pool_retention_per_conversation": 1}, "cache.pool_retention_per_conversation"),
        ({"ranking.behavior.counted_appointment_signals": []}, "ranking.behavior.counted_appointment_signals"),
        *[({"ranking.behavior.counted_appointment_signals": ["viewing_draft_saved", signal]}, f"uncountable signal(s) ['{signal}']")
          for signal in ("manual_sent", "confirmed", "draft_opened")],
        ({"ranking.behavior.counted_appointment_signals": ["VIEWING_DRAFT_SAVED"]}, "ranking.behavior.counted_appointment_signals"),
        ({"ranking.behavior.counted_appointment_signals": ["viewing_draft_saved", "viewing_draft_saved"]}, "duplicates"),
        ({"ranking.behavior.browsing_requires_interest_eligible": "yes"}, "ranking.behavior.browsing_requires_interest_eligible"),
        # enum values
        ({"ranking.similarity.numeric_scaling": "invalid"}, "ranking.similarity.numeric_scaling"),
        ({"ranking.similarity.suppression": "soft_criteria"}, "ranking.similarity.suppression"),
        ({"ranking.behavior.reference_scope": "all"}, "ranking.behavior.reference_scope"),
        ({"ranking.browsing.transform": "linear"}, "ranking.browsing.transform"),
        ({"geography.station_distance_method": "exit"}, "geography.station_distance_method"),
        ({"selection.random_policy": "exposure_weighted"}, "selection.random_policy"),
        ({"trace.mode": "verbose"}, "trace.mode"),
        ({"config_version": 2}, "config_version"),
        # unknown keys
        ({"ranking.similarity.feature_weights.unknown": 1}, "ranking.similarity.feature_weights.unknown"),
        ({"cold_start.exit_after_rounds": 2}, "cold_start.exit_after_rounds"),
        # keys of the superseded C/B/S detector and remembered exit (removed 2026-10-24)
        *[({f"cold_start.{key}": value}, f"cold_start.{key}: unknown key (removed with the C/B/S cold-start detector")
          for key, value in (("reenter_on_pool_rebuild", True), ("min_evidence_coverage", 0.8),
                             ("max_boundary_tie_group_size", 5), ("min_top_k_stability", 0.9),
                             ("stability", {"trials": 20, "weight_perturbation_fraction": 0.2}))],
        ({"cold_start.stability.trials": 20}, "cold_start.stability: unknown settings section"),
    ],
)
def test_illegal_ranges_enums_and_unknown_keys_rejected(overrides, key):
    rejected(overrides, key)


REQUIRED = [
    (key, "ranking.behavior_weight > 0") for key in loader.SIMILARITY_KEYS
] + [
    ("ranking.browsing.time_scale_seconds", "browsing_weight > 0"),
    ("cold_start.price_sampling.target_sample_size", "cold_start.enabled"),
    ("cold_start.price_sampling.quantile_bins", "cold_start.enabled"),
    ("cold_start.detector.candidate_window", "cold_start.enabled"),
    ("cold_start.detector.min_mean_pairwise_difference", "cold_start.enabled"),
    ("cache.ttl_seconds", "cache.enabled"),
    ("selection.tie_max_score_span", "always required"),
    ("entity_resolution.max_search_attempts", "always required"),
    ("entity_resolution.max_candidates", "always required"),
    ("controller.max_model_turns", "always required"),
    ("controller.max_tool_calls", "always required"),
    ("controller.max_resolution_tool_calls", "always required"),
    ("trace.preview_rows", "always required"),
    ("trace.debug_max_rows", "always required"),
]


@pytest.mark.parametrize("key,reason", REQUIRED)
def test_missing_required_parameter_rejected_with_key(key, reason):
    message = rejected({key: None}, f"{key} is null but required")
    assert reason in message


@pytest.mark.parametrize(
    "path", ["controller.repairs", "ranking.similarity", "selection.seed_namespace", "cold_start", "cold_start.detector"]
)
def test_absent_required_key_rejected(path):
    raw = default().to_data()
    *parents, leaf = path.split(".")
    target = raw
    for part in parents:
        target = target[part]
    del target[leaf]
    with pytest.raises(ConfigError, match=re.escape(f"{path}: missing required key")):
        RecommendationConfig.from_data(raw)


def test_unreachable_parameters_may_be_null():
    no_behavior = {"ranking.soft_weight": 1.0, "ranking.behavior_weight": 0.0}
    nulls = {key: None for key in loader.SIMILARITY_KEYS}
    config = RecommendationConfig.from_data(
        data(**no_behavior, **nulls, **{"ranking.browsing.time_scale_seconds": None})
    )
    assert not scoring_v2.behaviour_enabled(config)
    RecommendationConfig.from_data(
        data(**{"ranking.behavior.appointment_weight": 1.0, "ranking.behavior.browsing_weight": 0.0,
                "ranking.browsing.time_scale_seconds": None})
    )
    RecommendationConfig.from_data(
        data(**{"cold_start.enabled": False, "cold_start.price_sampling": {"target_sample_size": None, "quantile_bins": None},
                "cold_start.detector": {"candidate_window": None, "min_mean_pairwise_difference": None}})
    )
    RecommendationConfig.from_data(data(**{"cache.enabled": False, "cache.ttl_seconds": None}))
    # Disabled behaviour branch: the fused-v2 scorer produces no behaviour signal (P5: was the
    # weighted-v1 ranker's implicit signal).
    listing, geo = repositories("fixture")
    try:
        a, b = listing.retrieve()[:2]
        assert pool_scores([a, b], browsing=[(b.listing_id, 90)], config=config).breakdown(a.listing_id).behavior_score is None
        assert pool_scores([a, b], browsing=[(b.listing_id, 90)]).breakdown(a.listing_id).behavior_score is not None
    finally:
        listing.close()
        geo.close()


# --- secret-like keys --------------------------------------------------------------------
def test_committed_settings_contain_no_secret_like_keys():
    for path in (SETTINGS_PATH, PACKAGE / "examples/config.json"):
        raw = loader.read_settings_file(path)
        assert loader.secret_like_entries(raw) == []
        for key, _ in leaves(raw):
            for part in key.split("."):
                assert not loader.is_secret_like_key(part), key


@pytest.mark.parametrize(
    "overrides,key",
    [
        ({"password": "x"}, "password"),
        ({"controller.api_token": "x"}, "controller.api_token"),
        ({"cache.database_url": "x"}, "cache.database_url"),
        ({"cache.dsn": "x"}, "cache.dsn"),
        ({"trace.secret_key": "x"}, "trace.secret_key"),
        ({"entity_resolution.neo4j_credentials": {"user": "x"}}, "entity_resolution.neo4j_credentials"),
        ({"selection.seed_namespace": "postgresql://user@host/db"}, "selection.seed_namespace"),
        # camelCase / run-together names and a scheme-less DSN value
        ({"cache.databaseUrl": "x"}, "cache.databaseUrl"),
        ({"controller.accessKey": "x"}, "controller.accessKey"),
        ({"trace.privatekey": "x"}, "trace.privatekey"),
        ({"entity_resolution.neo4jPass": "x"}, "entity_resolution.neo4jPass"),
        ({"pwd": "x"}, "pwd"),
        ({"cache.authUri": "x"}, "cache.authUri"),
        ({"selection.seed_namespace": "user:pass@host:5432/db"}, "selection.seed_namespace"),
    ],
)
def test_secret_like_keys_rejected(overrides, key):
    message = rejected(overrides, key)
    assert "not allowed in recommendation settings" in message


# --- no duplicated hard-coded parameters ---------------------------------------------------
OLD_LITERALS = re.compile(
    r"DEFAULT_(SCALES|WEIGHTS|BOUNDS)|ScoringPolicy|RunLimits|config\.limits|config\.scoring"
    r"|\b20000\b|\b3000\b|500\.0|\b0\.8\b|\b0\.2\b|\b0\.25\b|\b0\.30?\b|\b120\b|log\(121\)"
    r"|timeout=90|le=8\b|le=20\b|ge=4, le=500"
    r"|\b4000\b"  # P7: former tracing/slim.py TEXT_LIMIT, now trace.max_string_chars only
)


def test_no_duplicate_hard_coded_parameters_outside_settings():
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        # P7: the offline analysis package (experiment grids and scenario inputs, never imported
        # by the runtime; test_analysis_p7 checks that) is exempt like the tests.
        if "tests" in path.parts or "analysis" in path.parts:
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if OLD_LITERALS.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert offenders == []
    # P5: the weighted-v1 legacy constants module is gone (no exemption left).
    assert not (PACKAGE / "ranking/legacy.py").exists()


# --- keys validated but not yet in effect (migration bookkeeping) --------------------------
MARKER = re.compile(r"\[not in effect until (P[2-7])\b[^\]]*\]")


def yaml_key_lines():
    """(dotted key, line) for every key line of the committed YAML."""
    stack = []
    for line in SETTINGS_PATH.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        key = line.partition("#")[0].partition(":")[0].strip()
        stack.append((indent, key))
        yield ".".join(k for _, k in stack), line


def test_not_in_effect_markers_match_pending_table():
    from recommendation.settings import pending

    marked = {}
    for dotted, line in yaml_key_lines():
        match = MARKER.search(line)
        if match:
            marked[dotted] = match.group(1)
    assert set(marked) == set(pending.NOT_IN_EFFECT)
    for key, phase in marked.items():
        assert pending.NOT_IN_EFFECT[key].startswith(f"{phase}:"), (key, phase, pending.NOT_IN_EFFECT[key])
    # Keys the runtime does read stay unmarked (and are not covered by a section entry).
    for key in ("ranking.soft.numeric_bounds.price", "ranking.similarity.geographic_scale_m",
                "ranking.similarity.feature_weights.bedrooms", "controller.max_tool_calls",
                "controller.timeout_seconds", "controller.repairs", "controller.model_result_candidates",
                "controller.max_model_turns"):
        assert pending.covering_entry(key) is None, key
    # P5: fused-v2 reads every ranking key that weighted-v1 legacy constants used to override,
    # so none of them is declared not in effect any more (was: all of them were).
    for key in ("ranking.soft_weight", "ranking.browsing.duration_cap_seconds", "ranking.browsing.time_scale_seconds",
                "ranking.similarity.suppression", "ranking.similarity.numeric_scaling",
                "ranking.similarity.feature_weights.price", "ranking.behavior.appointment_weight"):
        assert pending.covering_entry(key) is None, key
    assert not any(key.startswith("ranking") for key in pending.NOT_IN_EFFECT)
    # P6: the slim trace reads every trace key (was: trace.mode and trace.debug_max_rows pending).
    for key in ("trace.mode", "trace.preview_rows", "trace.debug_max_rows"):
        assert pending.covering_entry(key) is None, key


def test_trace_records_settings_not_in_effect(tmp_path):
    from recommendation.settings import pending

    service = make(tmp_path)
    try:
        response = service.recommend(request())
        assert response.status == "success"
        trace = service.store.trace("cfg")
    finally:
        service.close()
    policy = trace["context"]["scoring_policy"]
    assert policy["settings_not_in_effect"] == pending.NOT_IN_EFFECT
    # P5: the configured suppression mode is in effect (weighted-v1 forced all_criteria).
    assert policy["similarity"]["suppression"] == default().ranking.similarity.suppression == "none"


def test_requirements_declare_yaml_and_numpy():
    lines = (PACKAGE / "requirements.txt").read_text().splitlines()
    assert "PyYAML>=6,<7" in lines and "numpy>=1.26,<3" in lines


# --- contract maximum vs configured maximum ------------------------------------------------
# Every layer that carries the native result cap: the Python models, the generated JSON
# schemas and the strict TS zod mirror. (Tool-result previews are not a contract maximum:
# they become trace.preview_rows in P6, plan Step 8 "Preview 数量独立配置".)
SCHEMAS = PACKAGE / "contracts/schemas"
TS_MIRROR = ROOT / "src/contracts/recommendation-native.ts"
TS_PATTERNS = {
    "ts.const.NATIVE_MAX_RESULTS": r"\bexport const NATIVE_MAX_RESULTS=(\d+)",
    "ts.item.rank": r"\brank:z\.number\(\)\.int\(\)\.min\(1\)\.max\((\d+|NATIVE_MAX_RESULTS)\)",
    "ts.response.recommendations": r"\brecommendations:z\.array\(nativeItemSchema\)\.max\((\d+|NATIVE_MAX_RESULTS)\)",
}


def _limit(field, kind):
    return next(getattr(m, kind) for m in field.metadata if hasattr(m, kind))


def _schema_bound(node, kind):
    """``kind`` of a JSON-schema property, looking through anyOf (optional fields)."""
    if kind in node:
        return node[kind]
    found = [_schema_bound(option, kind) for option in node.get("anyOf", [])]
    found = [value for value in found if value is not None]
    return found[0] if len(found) == 1 else None


def native_contract_maxima():
    request_schema = json.loads((SCHEMAS / "request.schema.json").read_text())
    response_schema = json.loads((SCHEMAS / "response.schema.json").read_text())
    ts = TS_MIRROR.read_text()
    maxima = {
        "py.request.result_limit": _limit(RecommendationRequest.model_fields["result_limit"], "le"),
        "py.response.recommendations": _limit(Response.model_fields["recommendations"], "max_length"),
        "py.item.rank": _limit(RecommendationItem.model_fields["rank"], "le"),
        "py.proposal.listing_ids": _limit(Proposal.model_fields["listing_ids"], "max_length"),
        "schema.request.result_limit": _schema_bound(request_schema["properties"]["result_limit"], "maximum"),
        "schema.response.recommendations": _schema_bound(response_schema["properties"]["recommendations"], "maxItems"),
        "schema.item.rank": _schema_bound(response_schema["$defs"]["RecommendationItem"]["properties"]["rank"], "maximum"),
    }
    for name, pattern in TS_PATTERNS.items():
        matches = re.findall(pattern, ts)
        assert len(matches) == 1, f"{name}: expected one match of {pattern} in {TS_MIRROR}, found {matches}"
        maxima[name] = matches[0]
    # The zod bounds name the exported constant; resolve it to its literal value.
    constant = int(maxima["ts.const.NATIVE_MAX_RESULTS"])
    return {k: constant if v == "NATIVE_MAX_RESULTS" else int(v) for k, v in maxima.items()}


def test_native_contract_layers_agree_on_result_maximum():
    """Python models, generated JSON schemas and the TS zod mirror carry one result cap."""
    maxima = native_contract_maxima()
    assert None not in maxima.values(), maxima
    assert len(set(maxima.values())) == 1, maxima


def test_contract_max_equals_config_max():
    assert set(native_contract_maxima().values()) == {default().recommendation.max_result_limit}
    # The loader's ceiling is derived from the contract maximum, not a second literal.
    from recommendation.contracts.limits import MAX_RESULT_LIMIT

    assert loader.RESULT_LIMIT_CEILING == MAX_RESULT_LIMIT == 10
    with pytest.raises(ConfigError):
        default().with_overrides({"recommendation.max_result_limit": MAX_RESULT_LIMIT + 1})


# --- loading by terminal / acceptance / app ------------------------------------------------
def custom_file(tmp_path):
    config = default().with_overrides({"ranking.similarity.geographic_scale_m": 2500})
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(config.to_data()))
    return config, path


def test_terminal_loads_yaml_by_default_and_alternate_file(tmp_path):
    from recommendation.terminal.__main__ import Terminal

    base = dict(conversation_id="cfg", data_mode="fixture", controller="scripted")
    terminal = Terminal(argparse.Namespace(state_path=str(tmp_path / "a.sqlite"), **base))
    assert terminal.config.source == str(SETTINGS_PATH)
    terminal.store.close()
    config, path = custom_file(tmp_path)
    terminal = Terminal(argparse.Namespace(state_path=str(tmp_path / "b.sqlite"), config=str(path), **base))
    assert terminal.config.config_hash == config.config_hash
    terminal.store.close()


def test_acceptance_env_alternate_recorded(tmp_path):
    config, path = custom_file(tmp_path)
    env = subprocess_env(RECOMMENDATION_CONFIG=path, PYTHONPATH=ROOT)
    out = tmp_path / "acceptance"
    subprocess.run(
        [sys.executable, "-m", "recommendation.terminal.acceptance", "--data-mode", "fixture",
         "--controller", "scripted", "--output", str(out)],
        cwd=ROOT, env=env, check=True, capture_output=True, text=True,
    )
    report = json.loads((out / "acceptance.json").read_text())
    assert report["status"] == "passed"
    assert report["settings"]["config_hash"] == config.config_hash
    assert report["settings"]["config_source"] == str(path)


def test_app_loads_yaml_by_default_and_env_alternate(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from recommendation.app import app  # module already imported at collection (see top)

    monkeypatch.setenv("RECOMMENDATION_SERVICE_TOKEN", "test-only-credential")
    monkeypatch.setenv("RECOMMENDATION_STATE_PATH", str(tmp_path / "state.sqlite"))
    monkeypatch.setenv("RECOMMENDATION_DATA_MODE", "fixture")
    monkeypatch.setenv("RECOMMENDATION_CONTROLLER", "scripted")
    monkeypatch.delenv("RECOMMENDATION_CONFIG", raising=False)
    requirements = json.loads((PACKAGE / "examples/requirements.json").read_text())
    headers = {"x-recommendation-token": "test-only-credential"}
    config, path = custom_file(tmp_path)
    with TestClient(app) as client:
        for conversation, expected in (("app-default", default()), ("app-alternate", config)):
            if expected is config:
                monkeypatch.setenv("RECOMMENDATION_CONFIG", str(path))
            body = {"request_id": conversation, "conversation_id": conversation,
                    "requirements_version": 1, "requirements": requirements}
            response = client.post("/recommend", json=body, headers=headers).json()
            assert response["status"] == "success"
            assert response["execution"]["scoring_version"] == scoring_v2.scoring_identifier(expected)
            # R2: both identities in response.execution and in the run snapshot.
            assert response["execution"]["config_hash"] == expected.config_hash
            assert response["execution"]["selection_hash"] == expected.selection_hash
            store = StateStore(tmp_path / "state.sqlite")
            try:
                trace = store.trace(conversation)
                assert trace["config_hash"] == expected.config_hash
                assert trace["configuration"] == expected.to_data()
                assert store.current(conversation)["selection_hash"] == expected.selection_hash
            finally:
                store.close()


# --- service behaviour ---------------------------------------------------------------------
def make(tmp_path, config=None):
    listing, geo = repositories("fixture")
    return RecommendationService(listing, geo, StateStore(tmp_path / "state.sqlite"), ScriptedController(), config=config)


def request(**changes):
    body = dict(request_id="cfg-1", conversation_id="cfg", requirements_version=1,
                requirements=json.loads((PACKAGE / "examples/requirements.json").read_text()))
    body.update(changes)
    return RecommendationRequest(**body)


def test_service_rejects_untyped_config(tmp_path):
    with pytest.raises(TypeError):
        make(tmp_path, config={"controller": {}})


def test_config_change_never_alters_replay_of_old_request(tmp_path):
    first_service = make(tmp_path)
    first = first_service.recommend(request())
    first_service.close()
    custom = default().with_overrides({"ranking.similarity.geographic_scale_m": 2500})
    service = make(tmp_path, custom)
    try:
        assert service.recommend(request()) == first  # stored response, original policy
        second = service.recommend(request(request_id="cfg-2", previous_state_id=first.state.state_id))
        assert second.execution.scoring_version == scoring_v2.scoring_identifier(custom)
        assert second.execution.scoring_version != first.execution.scoring_version
        assert service.store.current("cfg")["config_hash"] == custom.config_hash
    finally:
        service.close()


# --- weighted-v1 parity (retired in P5) ----------------------------------------------------
# test_weighted_v1_score_parity_all_pairs_and_suppression_sets and
# test_weighted_v1_scores_identical_to_baseline_fixtures were removed with
# recommendation/ranking/legacy.py: fused-v2 intentionally changes every score. The 4d36507
# oracle (evaluation/recommendation-exploration-2026-10-23/baseline/score-parity.json and the
# baseline responses) stays as historical evidence and is no longer asserted
# (VALIDATION.md P5).


def test_ts_counted_appointment_signals_equal_yaml_and_main_contract():
    """Cross-layer: the TS mirror's counted viewing signals equal the native
    ranking.behavior.counted_appointment_signals (committed YAML) and the main contract's only
    book_viewing_listings signal (src/contracts/behavior-context.ts)."""
    match = re.search(r"COUNTED_APPOINTMENT_SIGNALS=\[([^\]]*)\]", TS_MIRROR.read_text())
    assert match, "COUNTED_APPOINTMENT_SIGNALS not found in the TS mirror"
    ts = [s.strip().strip("'\"") for s in match.group(1).split(",") if s.strip()]
    yaml_list = yaml.safe_load(SETTINGS_PATH.read_text())["ranking"]["behavior"]["counted_appointment_signals"]
    assert ts == sorted(ts) and set(ts) == set(yaml_list) and len(ts) == len(yaml_list)
    assert list(default().ranking.behavior.counted_appointment_signals) == ts
    main = (PACKAGE.parent / "src/contracts/behavior-context.ts").read_text()
    assert re.findall(r"signal:z\.literal\('([a-z_]+)'\)", main) == ts
