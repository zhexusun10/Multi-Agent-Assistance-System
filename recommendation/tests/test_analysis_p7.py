"""P7 part 1: calibration / evaluation tooling, trace.max_string_chars, TUNING.md provenance.

The analysis package runs here on the synthetic fixture data only (fixture mode, scripted
controller, temporary state; the isolation guard refuses live connections); the live runs are
recorded in evaluation/recommendation-exploration-2026-10-23/{CALIBRATION,PERFORMANCE}.md.
"""

import ast
import json
import math
import re
from pathlib import Path

import pytest

from recommendation.analysis import calibrate, simulate, stability, stats
from recommendation.analysis.live import Data
from recommendation.config import ConfigError, RecommendationConfig
from recommendation.settings.loader import SETTINGS_PATH, lookup
from recommendation.tracing import slim

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "recommendation"
EVALUATION = ROOT / "evaluation/recommendation-exploration-2026-10-23"
# The three P7 sources plus the spec R2 label "provisional" for values P7 could not calibrate (not a
# calibrated source: each one must be a pending user decision and the P7 item reported partial).
SOURCES = {"confirmed", "existing-default", "experiment-result", "provisional"}


# --- pure statistics ---------------------------------------------------------------------------
def test_gini_includes_zero_exposure_listings():
    assert stats.gini([5, 5, 5, 5]) == pytest.approx(0.0)
    assert stats.gini([0, 0, 0, 12]) == pytest.approx(0.75)  # (n-1)/n: everything on one listing
    # Never-shown listings raise inequality: the same shown counts over a larger pool.
    assert stats.gini([3, 3, 0, 0, 0, 0]) > stats.gini([3, 3])
    assert stats.gini([]) == 0.0 and stats.gini([0, 0]) == 0.0
    with pytest.raises(ValueError):
        stats.gini([1, -1])


def test_coverage_counts_listings_shown_at_least_once():
    assert stats.coverage({"a": 2, "b": 0, "x": 4}, ["a", "b", "c", "d"]) == 0.25
    assert stats.coverage({}, []) == 0.0


def test_tie_profile_matches_the_select_v1_cutoff_rule():
    from recommendation.ranking.selection import tie_group

    scores = [0.9, 0.8, 0.7] + [0.5] * 27 + [0.1] * 5
    profile = stats.tie_profile(scores, 10, (0.0, 0.02))
    assert profile["exact_ties"] == 27 and profile["per_epsilon"]["0"] == {"B": 27, "leaders": 3, "m": 7}
    for eps in (0.0, 0.01, 0.25, 0.5):
        assert stats.tie_group_bounds(scores, 10, eps) == tie_group(scores, 10, eps)
    assert stats.tie_profile([0.5] * 4, 10, (0.02,)) == {"pool": 4, "k": 10, "small_pool": True}


def test_quantiles_duplicates_outliers_and_bin_balance():
    values = [1000] * 6 + [2000, 3000, 128_000_000, None]
    summary = stats.summarize(values)
    assert summary["n"] == 10 and summary["missing"] == 1 and summary["distinct"] == 4
    dup = stats.duplicates(values, top=1)
    assert dup["top"] == [[1000, 6]] and dup["shared_value_share"] == pytest.approx(6 / 9)
    out = stats.outliers(values, thresholds=(100000,))
    assert out["max"] == 128_000_000 and out["above"]["100000"] == 1 and out["above_tukey_far"] == 1
    assert stats.bin_balance([5, 5, 5])["entropy"] == pytest.approx(1.0)
    assert stats.bin_balance([10, 0])["empty"] == 1


def test_browsing_curves_keep_increasing_beyond_120_seconds():
    curves = stats.browsing_curves((10, 30, 60), (5, 60, 120, 240, 600))
    for scale, curve in curves.items():
        w = curve["weights"]
        assert w["240"] > w["120"] and curve["gain_120_to_240"] > 0
        assert math.isclose(w["60"], math.log1p(60 / float(scale)))
    # A larger scale discounts glances more and lets long dwell count relatively more.
    assert curves["60"]["glance_over_read"] < curves["10"]["glance_over_read"]
    assert curves["60"]["long_over_read"] > curves["10"]["long_over_read"]


def test_estimate_spread_and_p50_p95():
    assert stats.estimate_spread([0.9, 0.9, 0.9])["std"] == 0.0
    assert stats.p50_p95([1, 2, 3, 4, 100]) == {"n": 5, "p50": 3.0, "p95": pytest.approx(80.8)}


# --- calibration and evaluation entry points (fixture data) ------------------------------------
@pytest.fixture(scope="module")
def fixture_report(tmp_path_factory):
    from recommendation.tests.isolation import isolate_runtime

    mp = pytest.MonkeyPatch()
    isolate_runtime(mp, tmp_path_factory.mktemp("calibrate"))
    yaml_before = SETTINGS_PATH.read_bytes()
    data = Data("fixture")
    try:
        report = calibrate.run(data, RecommendationConfig.default(), log=lambda *_: None)
    finally:
        data.close()
        mp.undo()
    assert SETTINGS_PATH.read_bytes() == yaml_before  # analysis never writes the settings
    return report


def test_calibration_covers_every_p7_analysis_and_labels_its_data_basis(fixture_report):
    report = fixture_report
    for section in ("profile", "pools", "normalisation", "scores", "similarity", "stability", "convergence",
                    "exploration", "resolution", "trace", "cache", "browsing_curves"):
        assert section in report, section
    assert len(calibrate.HARD_SCENARIOS) >= 6 and set(report["pools"]) == set(calibrate.HARD_SCENARIOS)
    kinds = {"budget", "budget_bedrooms", "budget_bedrooms_district", "mrt_distance", "region", "amenity_distance", "none"}
    assert kinds <= set(report["pools"])
    for name, pool in report["pools"].items():  # partition invariant on every scenario pool
        assert pool["source_count"] == pool["size"] + pool["excluded_hard_fail"] + pool["excluded_hard_unknown"], name
    assert report["profile"]["basis"] == "real" and "design" in report["browsing_curves"]["basis"]
    assert "synthetic references" in report["similarity"]["basis"]
    # Every analysis section states its data basis (pools: per scenario).
    for section, content in report.items():
        if section == "pools":
            assert all(pool["basis"] == "real" for pool in content.values())
        elif isinstance(content, dict) and section != "settings":
            assert content.get("basis"), section
    assert "design" in report["cache"]["basis"] and "real" in report["cache"]["basis"]
    text = json.dumps(report).lower()
    assert "satisfaction" not in text  # never claimed from synthetic or real statistics
    # normalisation schemes compared side by side; S convergence per trial count; bins x targets incl. 30
    assert set(report["normalisation"]["none"]["spreads"]["price"]) >= {"known", "std", "iqr", "p05_p95"}
    conv = next(v for k, v in report["convergence"].items() if k != "basis" and not v.get("small_pool"))
    assert set(conv["per_trials"]) == {str(t) for t in calibrate.TRIALS}
    cells = next(v for k, v in report["exploration"].items() if k != "basis" and not v.get("small_pool"))["cells"]
    assert any(c.endswith("target=30") for c in cells) and any(c.startswith("bins=5,") for c in cells)
    stab = report["stability"]["none/mixed/refs"]
    assert set(stab["per_epsilon"]) == {f"{e:g}" for e in calibrate.EPSILONS}
    assert set(stab["S_by_fraction"]) == {f"{f:g}" for f in calibrate.FRACTIONS}
    assert report["resolution"]["configured"]["max_candidates"] == RecommendationConfig.default().entity_resolution.max_candidates
    assert all(run["status"] == "success" for run in report["trace"]["runs"].values())


def test_trace_sizes_grow_with_the_limits_and_strings_stay_below_max_string_chars(fixture_report):
    runs = fixture_report["trace"]["runs"]
    summary = sorted((json.loads(k.split("/", 3)[3])["trace.preview_rows"], v["trace_bytes"])
                     for k, v in runs.items() if k.startswith("none/mixed/summary"))
    assert [p for p, _ in summary] == [5, 10, 20] and summary[0][1] <= summary[-1][1]
    limit = RecommendationConfig.default().trace.max_string_chars
    assert all(v["longest_tool_string"] < limit for v in runs.values())


def test_simulated_evaluation_three_modes_and_correctness(tmp_path, monkeypatch):
    data = Data("fixture")
    try:
        report = simulate.evaluate(data, RecommendationConfig.default(), sessions=2, rounds=3,
                                   plan=(("none", "no_soft"),), log=lambda *_: None)
    finally:
        data.close()
    assert "SIMULATED" in report["basis"] and "not user satisfaction" in report["basis"]
    scenario = report["scenarios"]["none/no_soft"]
    assert set(scenario["modes"]) == set(simulate.MODES)
    assert scenario["no_cache_equals_pool_cache"] is True  # the cache never changes a selection
    for mode, result in scenario["modes"].items():
        assert result["requests"] == 6
        assert result["checks"] == {name: 6 for name in result["checks"]}, (mode, result["checks"])
        assert set(result["checks"]) == {"status_ok", "no_duplicates", "all_in_pool", "pool_version_matches",
                                         "hard_criteria_pass", "selection_replay_identical", "replay_identical"}
        assert 0 < result["coverage"] <= 1 and 0 <= result["exposure_gini_incl_zero"] < 1
        assert "scoring" in result["stages_ms"] and result["end_to_end_ms"]["service"]["p95"] is not None
    modes = scenario["modes"]
    assert modes["no_cache"]["pool_cache"] == {"bypassed": 6}
    assert modes["pool_cache"]["pool_cache"] == {"built": 2, "reused": 4}
    assert "cold_start_exploration" in modes["cache_cold_start"]["selection_modes"]
    assert "cold_start_exploration" not in modes["pool_cache"]["selection_modes"]
    # Deterministic ranking (no cold start, all tied without soft criteria and before feedback)
    # has no score loss; exploration's loss is reported, never hidden.
    assert modes["pool_cache"]["score_loss_vs_deterministic_top_k"]["percentiles"]["p50"] == pytest.approx(0.0)


def test_simulated_user_feedback_is_a_valid_orchestrator_snapshot():
    """The simulator emulates main's orchestrator: a session-cumulative behavior_context."""
    from recommendation.contracts.behavior import BehaviorContext

    data = Data("fixture")
    try:
        user = simulate.SimulatedUser(data.rows, 0)
        records = [user.browse(1, r) for r in data.rows]
        assert records == [user.browse(1, r) for r in data.rows]  # independent of call order / mode
        assert all(r["status"] == "missing" or r["duration_seconds"] >= 0 for r in records)
        session = simulate.Session(next(iter(simulate.HARD_SCENARIOS)), next(iter(simulate.SOFT_SETS)), 0, user, "pool_cache")
        assert session.body(1)["behavior_context"]["listing_view_durations"] == []
        context = BehaviorContext.model_validate(session.body(1)["behavior_context"])
        assert context.version == "1.0" and context.book_viewing_listings == []
    finally:
        data.close()


def test_runtime_modules_never_import_the_analysis_package():
    """Exposure / coverage live in the external evaluation only (no subagent history context)."""
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "analysis" in path.parts or "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            if any("analysis" in (n or "").split(".") for n in names):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


# --- weight-perturbation stability (analysis only since mpd-v1; the epsilon derivation uses it) ---
def _stability_pool(rows, criteria):
    from recommendation.ranking.scoring_v2 import score_pool
    from recommendation.validation.rules import evaluate
    from .test_selection_v1 import make_request, settings

    req = make_request(criteria)
    config = settings()
    results = {r.listing_id: evaluate(r, req.requirements.criteria, []) for r in rows}
    return score_pool(rows, req.requirements.criteria, results, {}, req.behavior_context, config)


def test_top_k_stability_reweights_components_and_is_reproducible(monkeypatch):
    import numpy as np
    from recommendation.ranking import scoring_v2
    from .test_selection_v1 import PLAN, plan_rows

    scored = _stability_pool(plan_rows(3, 30, 40), PLAN)
    first = stability.top_k_stability(scored, 10, 20, stability.FRACTION, stability.generator(7))
    # Trials only reweight the stored components: no PoolScores is built again.
    monkeypatch.setattr(scoring_v2.PoolScores, "__init__", lambda *a, **k: pytest.fail("rescored"))
    assert stability.top_k_stability(scored, 10, 20, stability.FRACTION, stability.generator(7)) == first
    # u = 0 keeps every layer; any u renormalises every layer.
    w = scored.weights
    width = len(w.soft_criteria) + 4 + len(w.features)
    same = stability.perturbed_weights(w, np.zeros(width))
    assert np.allclose(same.soft_criteria, w.soft_criteria / w.soft_criteria.sum()) and same.soft == w.soft
    moved = stability.perturbed_weights(w, np.full(width, 0.1) * np.arange(width))
    assert math.isclose(moved.soft + moved.behavior, 1) and math.isclose(moved.appointment + moved.browsing, 1)
    assert math.isclose(moved.soft_criteria.sum(), 1) and moved.soft != w.soft


def test_top_k_stability_random_tie_break_never_fakes_stability():
    # All tied: the official order is by listing ID, but each trial breaks ties at random, so
    # S is about K / |pool| instead of 1.
    from .test_selection_v1 import listing

    scored = _stability_pool([listing(k) for k in range(1, 41)], ())
    assert stability.top_k_stability(scored, 10, 20, stability.FRACTION, stability.generator(3)) < 0.5


def test_historical_exit_test_keeps_the_superseded_thresholds():
    assert stability.exit_conditions(stability.C_MIN, stability.B_MAX, stability.S_MIN)
    assert not stability.exit_conditions(math.nextafter(stability.C_MIN, 0), stability.B_MAX, stability.S_MIN)
    assert not stability.exit_conditions(stability.C_MIN, stability.B_MAX + 1, stability.S_MIN)
    assert not stability.exit_conditions(1.0, 1, None)


# --- trace.max_string_chars (former slim.TEXT_LIMIT) -------------------------------------------
def test_max_string_chars_is_a_required_trace_setting_and_cuts_tool_strings():
    config = RecommendationConfig.default()
    assert not hasattr(slim, "TEXT_LIMIT")
    small = config.with_overrides({"trace.max_string_chars": 8})
    compacted = slim.compact_arguments({"context_ref": "x", "query": "y" * 30}, small)
    assert compacted["query"] == "y" * 8 and compacted["_truncated"] == {"query": 30}
    assert slim.compact_arguments({"query": "y" * 30}, config)["query"] == "y" * 30
    data = config.to_data()
    data["trace"]["max_string_chars"] = None
    with pytest.raises(ConfigError, match="trace.max_string_chars"):
        RecommendationConfig.from_data(data, "fixture")
    data["trace"]["max_string_chars"] = 0
    with pytest.raises(ConfigError, match="trace.max_string_chars"):
        RecommendationConfig.from_data(data, "fixture")
    assert small.selection_hash == config.selection_hash and small.config_hash != config.config_hash


# --- TUNING.md provenance -----------------------------------------------------------------------
ROW = re.compile(r"^\|\s*`([a-z_.]+)`\s*\|\s*`([^`]*)`\s*\|\s*([a-z-]+)\s*\|(.*)\|\s*$")


def tuning_rows():
    rows = {}
    for line in (EVALUATION / "TUNING.md").read_text().splitlines():
        match = ROW.match(line)
        if match:
            rows[match.group(1)] = (json.loads(match.group(2)), match.group(3), match.group(4).strip())
    return rows


def leaves(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from leaves(child, f"{prefix}{key}.")
    else:
        yield prefix.rstrip("."), value


def test_tuning_provenance_table_matches_the_committed_yaml():
    config = RecommendationConfig.default()
    rows = tuning_rows()
    keys = {k for k, _ in leaves(config.to_data()) if k != "config_version"}
    assert set(rows) == keys, sorted(set(rows) ^ keys)
    for key, (value, source, evidence) in rows.items():
        current = lookup(config, key)
        current = list(current) if isinstance(current, tuple) else current
        if isinstance(current, list) and current and isinstance(current[0], str):
            value = sorted(value)  # a set in the settings (stored sorted)
        assert value == current, (key, value, current)
        assert source in SOURCES, (key, source)
        assert evidence, key
    # Every provisional value says it is not calibrated.
    assert all("not calibrated" in e.lower() for _, s, e in rows.values() if s == "provisional")


def section(path, heading):
    """Text of a markdown section (from its heading to the next heading of the same or higher level)."""
    lines = path.read_text().splitlines()
    level = heading.split(" ")[0]
    start = lines.index(heading)
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("#") and len(lines[i].split(" ")[0]) <= len(level)), len(lines))
    return "\n".join(lines[start + 1:end])


def test_provisional_settings_are_listed_as_pending_decisions():
    """P7 review: an uncalibrated value never passes as calibrated. Every provisional key is a pending
    user decision in OPEN_ITEMS.md, and the P7 calibration item is not reported as pass while any remains."""
    provisional = {k for k, (_, s, _) in tuning_rows().items() if s == "provisional"}
    pending = section(EVALUATION / "OPEN_ITEMS.md", "### Decisions pending for the user")
    row = next(line for line in pending.splitlines() if "Uncalibrated `provisional` settings" in line)
    listed = set(re.findall(r"`((?:ranking|cache|cold_start|selection|similarity|controller|trace|recommendation|"
                            r"geography|entity_resolution)\.[a-z_.]+)`", row))
    assert provisional and listed - {"selection.tie_max_score_span"} == provisional, sorted(provisional ^ listed)
    assert "decision 2" in row and "not met yet" in row
    validation = (EVALUATION / "VALIDATION.md").read_text()
    item = next(line for line in validation.splitlines() if line.startswith("| Provisional YAML values replaced"))
    assert item.rstrip(" |").split("|")[-1].strip().startswith("partial"), item


def test_yaml_source_tags_agree_with_tuning_md():
    rows = tuning_rows()
    tag = re.compile(r"#\s*(confirmed|existing-default|experiment-result|provisional)\b")
    stack = []
    for line in SETTINGS_PATH.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        key = line.partition("#")[0].partition(":")[0].strip()
        stack.append((indent, key))
        dotted = ".".join(k for _, k in stack)
        match = tag.search(line)
        if dotted in rows and match:
            assert match.group(1) == rows[dotted][1], (dotted, match.group(1), rows[dotted][1])
    assert "provisional-kept" not in SETTINGS_PATH.read_text()  # no self-introduced fourth label (P7 review)
