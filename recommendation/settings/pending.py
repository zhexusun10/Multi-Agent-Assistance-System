"""Settings keys that are validated but not yet in effect at runtime (migration bookkeeping).

recommendation.yaml holds the target v2 values. Until the phase named here wires a key, the
runtime ignores its value because the feature does not exist yet (the weighted-v1 legacy
constants of INVENTORY.md section L were removed with full-pool fused scoring in P5; the
cold_start and selection sections are in effect since select-v1, P5 part 2; the trace section
since the slim trace, P6). Each key
or section listed here carries a matching "[not in effect until Pn ...]" marker on its YAML
line (checked by recommendation/tests/test_settings.py), and the list is recorded in every
trace (context.scoring_policy.settings_not_in_effect). The phase that wires a key removes
its entry and its YAML marker. A section entry covers every key below it.
"""

NOT_IN_EFFECT = {
    # Empty since P6: trace.mode and trace.debug_max_rows are read by the slim trace
    # (recommendation/tracing/slim.py).
}


def not_in_effect():
    """Copy of the pending-key table (key or section -> phase and reason)."""
    return dict(NOT_IN_EFFECT)


def covering_entry(dotted):
    """The NOT_IN_EFFECT entry that covers ``dotted`` (itself or a parent section), or None."""
    parts = dotted.split(".")
    for end in range(len(parts), 0, -1):
        prefix = ".".join(parts[:end])
        if prefix in NOT_IN_EFFECT:
            return prefix
    return None
