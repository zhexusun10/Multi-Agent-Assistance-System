"""Schema and loader for recommendation/settings/recommendation.yaml.

Every tunable recommendation parameter is defined in that one file, and a parameter that is
reachable in the enabled configuration but null fails at load time with a message naming
the key. The numeric limits in this module are validation ceilings (spec R2), not tunable
parameters.

Migration caveat: not every key is wired yet. recommendation/settings/pending.py lists the
keys whose values the runtime currently ignores (feature not built yet); the YAML marks each of
them "[not in effect until Pn ...]" and every trace records the list.

RECOMMENDATION_CONFIG / --config may name an alternative file of the same schema (YAML by
.yaml/.yml suffix, JSON by .json) for tests and experiments. RECOMMENDATION_TRACE_MODE only
toggles trace.mode. Settings objects are immutable (frozen), so one object is a stable
per-request snapshot; with_overrides() returns a new validated object.
"""

import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, PrivateAttr, ValidationError, field_validator, model_validator

from recommendation.contracts.requirements import Contract
from recommendation.contracts.limits import MAX_RESULT_LIMIT

SETTINGS_PATH = Path(__file__).with_name("recommendation.yaml")
ENV_CONFIG = "RECOMMENDATION_CONFIG"
ENV_TRACE_MODE = "RECOMMENDATION_TRACE_MODE"
WEIGHT_SUM_TOLERANCE = 1e-9

# Validation ceilings (spec R2 "schema hard ceilings"); not tunables.
RESULT_LIMIT_CEILING = MAX_RESULT_LIMIT  # derived from the native contract 2.0 maximum
MAX_MODEL_TURNS_CEILING = 16
MAX_TOOL_CALLS_CEILING = 40
TIMEOUT_SECONDS_CEILING = 600  # bound kept from the previous run limits
REPAIRS_CEILING = 2  # bound kept from the previous run limits
MODEL_RESULT_CANDIDATES_CEILING = 500  # bound kept from the previous run limits
DETECTOR_WINDOW_CEILING = 1000  # mpd-v1 D is O(W^2) pairs per evaluated round (about 26 ms at 1000)

# Result-affecting sections: their digest (selection_hash) seeds selection randomness.
SELECTION_SECTIONS = ("recommendation", "ranking", "geography", "cold_start", "selection")

# Secret-like keys (spec R2: password, token, key, secret, dsn, url). Fragments match anywhere
# in the lowercased key, so camelCase and run-together names (databaseUrl, accessKey,
# privatekey, neo4jPass) are caught; tokens match whole words only ("uri" would otherwise hit
# words such as "security"). No schema key contains any of them (test-checked), so no
# allow-list is needed.
SECRET_KEY_FRAGMENTS = (
    "password", "passwd", "pass", "pwd", "secret", "token", "credential", "key", "dsn", "url",
)
SECRET_KEY_TOKENS = frozenset({"uri"})
SECRET_KEY_ALLOWED = frozenset()
# user:password@host DSN without a scheme.
USERINFO_VALUE = re.compile(r"^[^\s@/:]+:[^\s@]*@\S+$")
# Statuses that may be counted as appointment signals (spec section 1 / R1b; the R7 TS valid
# set). The excluded orchestrator statuses (DRAFT, PENDING_REVIEW, APPROVED, CANCELLED, FAILED)
# and unknown strings are rejected.
# behavior_context.book_viewing_listings[].signal values (main contract, 2026-10-23).
COUNTABLE_APPOINTMENT_SIGNALS = ("viewing_draft_saved",)


class SettingsError(ValueError):
    """Invalid settings; ``errors`` lists one message per offending key."""

    def __init__(self, source, errors):
        self.source = source
        self.errors = list(errors)
        super().__init__(f"Invalid recommendation settings ({source}): " + "; ".join(self.errors))


class Section(Contract):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True, strict=True)


Unit = Annotated[float, Field(ge=0, le=1)]
NonNegative = Annotated[float, Field(ge=0)]
Positive = Annotated[float, Field(gt=0)]
Count = Annotated[int, Field(ge=1)]
Bounds = tuple[NonNegative, NonNegative]
Signal = Annotated[str, Field(pattern=r"^[a-z][a-z_]*$")]


class RecommendationLimits(Section):
    default_result_limit: Annotated[int, Field(ge=1, le=RESULT_LIMIT_CEILING)]
    max_result_limit: Annotated[int, Field(ge=1, le=RESULT_LIMIT_CEILING)]


class NumericBounds(Section):
    price: Bounds
    bedrooms: Bounds
    floor_area_sqft: Bounds

    def as_dict(self):
        return {name: tuple(getattr(self, name)) for name in type(self).model_fields}


class SoftScoring(Section):
    numeric_bounds: NumericBounds


class BehaviorScoring(Section):
    appointment_weight: Unit
    browsing_weight: Unit
    missing_branch_contribution: Unit
    redistribute_missing_branch_weight: bool
    counted_appointment_signals: Annotated[tuple[Signal, ...], Field(min_length=1)]
    browsing_requires_interest_eligible: bool
    reference_scope: Literal["in_pool"]

    @field_validator("counted_appointment_signals")
    @classmethod
    def canonical_signals(cls, signals):
        # A set, stored sorted: the YAML order never changes config_hash / selection_hash.
        return tuple(sorted(signals))


class BrowsingScoring(Section):
    transform: Literal["log1p"]
    duration_cap_seconds: Positive | None  # null = explicitly uncapped
    time_scale_seconds: Positive | None


class FeatureWeights(Section):
    price: NonNegative | None
    property_type: NonNegative | None
    bedrooms: NonNegative | None
    floor_area_sqft: NonNegative | None
    geography: NonNegative | None

    def as_dict(self):
        return {name: getattr(self, name) for name in type(self).model_fields}


class SimilarityScoring(Section):
    suppression: Literal["hard_criteria", "all_criteria", "none"]
    feature_weights: FeatureWeights
    numeric_scaling: Literal["std", "iqr", "p05_p95", "minmax"] | None
    geographic_scale_m: Positive | None
    geography_district_fallback: bool | None
    min_comparison_coverage: Unit | None


class Ranking(Section):
    soft_weight: Unit
    behavior_weight: Unit
    soft: SoftScoring
    behavior: BehaviorScoring
    browsing: BrowsingScoring
    similarity: SimilarityScoring


class Geography(Section):
    station_distance_method: Literal["nearest_exit", "exit_centroid"]


class PriceSampling(Section):
    # select-v2: the overall top ``exploration_window`` of O (leaders included) is the exploration
    # window; ``intermediate_size`` listings are selected from it before the result_limit prefix.
    exploration_window: Count | None
    intermediate_size: Count | None
    quantile_bins: Count | None


class Detector(Section):
    # mpd-v1 (ranking/selection.py): D over the first candidate_window scores of O; active when
    # D < min_mean_pairwise_difference. A window below 2 would have no pair to compare.
    candidate_window: Annotated[int, Field(ge=2, le=DETECTOR_WINDOW_CEILING)] | None
    min_mean_pairwise_difference: NonNegative | None


class ColdStart(Section):
    enabled: bool
    price_sampling: PriceSampling
    detector: Detector


class Selection(Section):
    tie_max_score_span: NonNegative | None
    random_policy: Literal["uniform_without_replacement"]
    seed_namespace: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")]


class Cache(Section):
    enabled: bool
    ttl_seconds: Annotated[int, Field(ge=0)] | None  # 0 = explicitly no TTL
    max_entries_per_conversation: Count
    pool_retention_per_conversation: Count
    memory_max_entries: Count


class EntityResolution(Section):
    max_search_attempts: Count | None
    max_candidates: Count | None


class Controller(Section):
    max_model_turns: Annotated[int, Field(ge=1, le=MAX_MODEL_TURNS_CEILING)] | None
    max_tool_calls: Annotated[int, Field(ge=1, le=MAX_TOOL_CALLS_CEILING)] | None
    max_resolution_tool_calls: Count | None
    timeout_seconds: Annotated[float, Field(gt=0, le=TIMEOUT_SECONDS_CEILING)]
    repairs: Annotated[int, Field(ge=0, le=REPAIRS_CEILING)]
    model_result_candidates: Annotated[int, Field(ge=1, le=MODEL_RESULT_CANDIDATES_CEILING)]


class Trace(Section):
    mode: Literal["summary", "debug"]
    preview_rows: Count | None
    debug_max_rows: Count | None
    max_string_chars: Count | None  # characters kept of one string in compacted tool data / arguments


SIMILARITY_KEYS = tuple(
    [f"ranking.similarity.feature_weights.{name}" for name in FeatureWeights.model_fields]
    + [
        "ranking.similarity.numeric_scaling",
        "ranking.similarity.geographic_scale_m",
        "ranking.similarity.geography_district_fallback",
        "ranking.similarity.min_comparison_coverage",
    ]
)
# (condition, human-readable condition, keys that must be non-null when it holds) -- spec R2.
REQUIRED_WHEN_REACHABLE = (
    (lambda s: s.ranking.behavior_weight > 0, "ranking.behavior_weight > 0", SIMILARITY_KEYS),
    (
        lambda s: s.ranking.behavior_weight > 0 and s.ranking.behavior.browsing_weight > 0,
        "ranking.behavior_weight > 0 and ranking.behavior.browsing_weight > 0",
        ("ranking.browsing.time_scale_seconds",),
    ),
    (
        lambda s: s.cold_start.enabled,
        "cold_start.enabled is true",
        (
            "cold_start.price_sampling.exploration_window",
            "cold_start.price_sampling.intermediate_size",
            "cold_start.price_sampling.quantile_bins",
            "cold_start.detector.candidate_window",
            "cold_start.detector.min_mean_pairwise_difference",
        ),
    ),
    (lambda s: s.cache.enabled, "cache.enabled is true", ("cache.ttl_seconds",)),
    (
        lambda s: True,
        "always required",
        (
            "selection.tie_max_score_span",
            "entity_resolution.max_search_attempts",
            "entity_resolution.max_candidates",
            "controller.max_model_turns",
            "controller.max_tool_calls",
            "controller.max_resolution_tool_calls",
            "trace.preview_rows",
            "trace.debug_max_rows",
            "trace.max_string_chars",
        ),
    ),
)


def lookup(settings, dotted):
    value = settings
    for part in dotted.split("."):
        value = getattr(value, part)
    return value


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_json(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _sums_to_one(total):
    return abs(total - 1.0) <= WEIGHT_SUM_TOLERANCE


class RecommendationSettings(Section):
    config_version: Literal[1]
    recommendation: RecommendationLimits
    ranking: Ranking
    geography: Geography
    cold_start: ColdStart
    selection: Selection
    cache: Cache
    entity_resolution: EntityResolution
    controller: Controller
    trace: Trace

    _source: str | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def consistent(self):
        errors = []
        for condition, reason, keys in REQUIRED_WHEN_REACHABLE:
            if condition(self):
                errors += [f"{key} is null but required ({reason})" for key in keys if lookup(self, key) is None]
        ranking = self.ranking
        if not _sums_to_one(ranking.soft_weight + ranking.behavior_weight):
            errors.append(
                "ranking.soft_weight + ranking.behavior_weight must equal 1 "
                f"(got {ranking.soft_weight + ranking.behavior_weight!r})"
            )
        behavior = ranking.behavior
        if not _sums_to_one(behavior.appointment_weight + behavior.browsing_weight):
            errors.append(
                "ranking.behavior.appointment_weight + ranking.behavior.browsing_weight must equal 1 "
                f"(got {behavior.appointment_weight + behavior.browsing_weight!r})"
            )
        if behavior.missing_branch_contribution != 0:
            errors.append("ranking.behavior.missing_branch_contribution must be 0.0 in this version")
        if behavior.redistribute_missing_branch_weight:
            errors.append("ranking.behavior.redistribute_missing_branch_weight must be false in this version")
        signals = behavior.counted_appointment_signals
        if len(set(signals)) != len(signals):
            errors.append("ranking.behavior.counted_appointment_signals must not contain duplicates")
        uncountable = sorted(set(signals) - set(COUNTABLE_APPOINTMENT_SIGNALS))
        if uncountable:
            errors.append(
                f"ranking.behavior.counted_appointment_signals contains uncountable signal(s) {uncountable} "
                f"(allowed: {list(COUNTABLE_APPOINTMENT_SIGNALS)})"
            )
        weights = ranking.similarity.feature_weights.as_dict()
        if all(v is not None for v in weights.values()) and not _sums_to_one(sum(weights.values())):
            errors.append(
                f"ranking.similarity.feature_weights must sum to 1 (got {sum(weights.values())!r})"
            )
        for name, (low, high) in ranking.soft.numeric_bounds.as_dict().items():
            if high <= low:
                errors.append(f"ranking.soft.numeric_bounds.{name} must be increasing [low, high] (got [{low}, {high}])")
        limits = self.recommendation
        if limits.default_result_limit > limits.max_result_limit:
            errors.append("recommendation.default_result_limit must not exceed recommendation.max_result_limit")
        sampling = self.cold_start.price_sampling
        if sampling.intermediate_size is not None and sampling.intermediate_size < limits.max_result_limit:
            errors.append(
                "cold_start.price_sampling.intermediate_size must be >= recommendation.max_result_limit"
            )
        if (sampling.exploration_window is not None and sampling.intermediate_size is not None
                and sampling.exploration_window < sampling.intermediate_size):
            errors.append(
                "cold_start.price_sampling.exploration_window must be >= cold_start.price_sampling.intermediate_size"
            )
        controller = self.controller
        if controller.model_result_candidates < limits.max_result_limit:
            errors.append("controller.model_result_candidates must be >= recommendation.max_result_limit")
        if (
            controller.max_resolution_tool_calls is not None
            and controller.max_tool_calls is not None
            and controller.max_resolution_tool_calls > controller.max_tool_calls
        ):
            errors.append(
                "controller.max_resolution_tool_calls must not exceed controller.max_tool_calls "
                "(resolution calls are counted inside the total)"
            )
        trace = self.trace
        if trace.preview_rows is not None and trace.debug_max_rows is not None and trace.debug_max_rows < trace.preview_rows:
            errors.append("trace.debug_max_rows must be >= trace.preview_rows")
        if self.cache.pool_retention_per_conversation < self.cache.max_entries_per_conversation:
            errors.append("cache.pool_retention_per_conversation must be >= cache.max_entries_per_conversation")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    # Identity ---------------------------------------------------------------------------
    @property
    def source(self):
        return self._source

    def to_data(self):
        return self.model_dump(mode="json")

    @property
    def config_hash(self):
        """sha256 of the canonical JSON of the whole validated model."""
        return sha256_json(self.to_data())

    @property
    def selection_hash(self):
        """sha256 of only the result-affecting sections (seeds use this, never config_hash)."""
        data = self.to_data()
        return sha256_json({name: data[name] for name in SELECTION_SECTIONS})

    def identity(self):
        return {
            "config_version": self.config_version,
            "config_hash": self.config_hash,
            "selection_hash": self.selection_hash,
            "config_source": self.source,
        }

    # Construction -----------------------------------------------------------------------
    @classmethod
    def from_data(cls, data, source="<data>"):
        if not isinstance(data, dict):
            raise SettingsError(source, ["top level must be a mapping"])
        errors = secret_like_entries(data)
        if errors:
            raise SettingsError(source, errors)
        try:
            text = json.dumps(data, allow_nan=True)
        except (TypeError, ValueError) as exc:
            raise SettingsError(source, [f"unsupported value type ({exc})"]) from None
        try:
            settings = cls.model_validate_json(text)
        except ValidationError as exc:
            raise SettingsError(source, format_errors(exc)) from None
        settings._source = source
        return settings

    @classmethod
    def from_file(cls, path=None):
        """Load ``path`` (YAML or JSON by suffix); None or '' loads the committed YAML."""
        if not path:
            return cls.default()
        return cls._read(Path(path))

    @classmethod
    def _read(cls, path):
        source = str(path)
        data = read_settings_file(path)
        if isinstance(data, dict) and set(data) == {"extends", "overrides"}:
            # Overlay file: only the named keys differ from the committed YAML, which stays the
            # single parameter source (e.g. config/recommendation-full.json: wall-clock budget).
            if data["extends"] != "committed" or not isinstance(data["overrides"], dict):
                raise SettingsError(source, ['overlay files need {"extends": "committed", "overrides": {...}}'])
            base = read_settings_file(SETTINGS_PATH)
            merge_overrides(base, data["overrides"])
            data = base
        if isinstance(data, dict) and os.getenv(ENV_TRACE_MODE):
            data = {**data, "trace": {**(data.get("trace") or {}), "mode": os.getenv(ENV_TRACE_MODE)}}
        return cls.from_data(data, source)

    @classmethod
    def default(cls):
        """The committed production settings (cached while the file is unchanged)."""
        stat = SETTINGS_PATH.stat()
        return _cached_default(str(SETTINGS_PATH), stat.st_mtime_ns, stat.st_size, os.getenv(ENV_TRACE_MODE))

    def with_overrides(self, overrides, source=None):
        """New validated settings with nested and/or dotted-key overrides applied."""
        data = self.to_data()
        merge_overrides(data, overrides)
        return type(self).from_data(data, source or f"{self.source or '<data>'} + overrides")


@lru_cache(maxsize=4)
def _cached_default(path, mtime_ns, size, trace_mode):
    return RecommendationSettings._read(Path(path))


def merge_overrides(data, overrides, prefix=""):
    for key, value in overrides.items():
        parts = str(key).split(".")
        target = data
        walked = prefix
        for part in parts[:-1]:
            walked = f"{walked}{part}."
            if not isinstance(target.get(part), dict):
                raise SettingsError("overrides", [f"{walked.rstrip('.')}: unknown settings section"])
            target = target[part]
        leaf = parts[-1]
        if isinstance(value, dict) and isinstance(target.get(leaf), dict):
            merge_overrides(target[leaf], value, f"{walked}{leaf}.")
        else:
            target[leaf] = value


# Keys removed on 2026-10-24: the superseded C/B/S cold-start detector and remembered exit, and the
# select-v1 stage-A sample size (select-v2 uses exploration_window + intermediate_size).
DETECTOR_KEYS = ("cold_start.reenter_on_pool_rebuild", "cold_start.min_evidence_coverage",
                 "cold_start.max_boundary_tie_group_size", "cold_start.min_top_k_stability", "cold_start.stability")
REMOVED_KEYS = {
    **{key: "removed with the C/B/S cold-start detector; see cold_start.detector" for key in DETECTOR_KEYS},
    "cold_start.price_sampling.target_sample_size": "replaced by cold_start.price_sampling.exploration_window "
                                                    "and intermediate_size (select-v2)",
}


def format_errors(error):
    messages = []
    for item in error.errors():
        key = ".".join(str(part) for part in item["loc"])
        message = item["msg"]
        if item["type"] == "value_error":
            message = message.removeprefix("Value error, ")
        elif item["type"] == "extra_forbidden" and key in REMOVED_KEYS:
            message = f"unknown key ({REMOVED_KEYS[key]})"
        elif item["type"] == "extra_forbidden":
            message = "unknown key (not part of the settings schema)"
        elif item["type"] == "missing":
            message = "missing required key"
        messages.append(f"{key}: {message}" if key else message)
    return messages


def secret_like_entries(value, prefix=""):
    """Keys that look like credentials, and string values that look like URLs/DSNs."""
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            name = str(key)
            dotted = f"{prefix}{name}"
            if is_secret_like_key(name):
                found.append(f"{dotted}: secret-like key is not allowed in recommendation settings")
            found += secret_like_entries(child, f"{dotted}.")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found += secret_like_entries(child, f"{prefix}{index}.")
    elif isinstance(value, str) and ("://" in value or USERINFO_VALUE.match(value.strip())):
        found.append(f"{prefix.rstrip('.')}: URL/DSN-like value is not allowed in recommendation settings")
    return found


def is_secret_like_key(name):
    lowered = str(name).lower()
    if lowered in SECRET_KEY_ALLOWED:
        return False
    # Split camelCase before lowercasing for whole-word tokens.
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(name)).lower()
    tokens = set(re.split(r"[^a-z0-9]+", words))
    return bool(tokens & SECRET_KEY_TOKENS) or any(fragment in lowered for fragment in SECRET_KEY_FRAGMENTS)


def _reject_duplicate_json_keys(pairs):
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"duplicate key(s): {', '.join(duplicates)}")
    return dict(pairs)


def read_settings_file(path):
    path = Path(path)
    source = str(path)
    suffix = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SettingsError(source, [f"cannot read file ({exc.strerror or type(exc).__name__})"]) from None
    if suffix in (".yaml", ".yml"):
        import yaml

        class UniqueKeyLoader(yaml.SafeLoader):
            pass

        def construct_mapping(loader, node, deep=False):
            keys = [loader.construct_object(k, deep=deep) for k, _ in node.value]
            duplicates = sorted({str(k) for k in keys if keys.count(k) > 1})
            if duplicates:
                raise SettingsError(source, [f"duplicate key(s): {', '.join(duplicates)}"])
            return loader.construct_mapping(node, deep=deep)

        UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping)
        try:
            return yaml.load(text, Loader=UniqueKeyLoader)
        except yaml.YAMLError as exc:
            raise SettingsError(source, [f"invalid YAML ({type(exc).__name__})"]) from None
    if suffix == ".json":
        try:
            return json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
        except ValueError as exc:
            raise SettingsError(source, [f"invalid JSON ({exc})"]) from None
    raise SettingsError(source, ["unsupported settings file suffix (use .yaml, .yml or .json)"])


def load_settings(path=None):
    return RecommendationSettings.from_file(path)
