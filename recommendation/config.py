"""Validated application-owned policy; model tool arguments cannot change it.

Thin adapter that keeps the public name ``RecommendationConfig``. The schema, the
validation rules and the loader live in recommendation/settings/loader.py; every tunable
value is defined only in recommendation/settings/recommendation.yaml (no code defaults).
During the migration some keys are not in effect yet because their feature is not built
(recommendation/settings/pending.py).

    RecommendationConfig.default()              committed YAML (production)
    RecommendationConfig.from_file(path)        alternative YAML/JSON (tests, experiments)
    config.with_overrides({"a.b": value})       new validated copy
"""

from .settings.loader import (
    ENV_CONFIG,
    SETTINGS_PATH,
    RecommendationSettings,
    SettingsError,
    load_settings,
)

RecommendationConfig = RecommendationSettings
ConfigError = SettingsError
load_config = load_settings

__all__ = [
    "ENV_CONFIG",
    "SETTINGS_PATH",
    "ConfigError",
    "RecommendationConfig",
    "load_config",
]
