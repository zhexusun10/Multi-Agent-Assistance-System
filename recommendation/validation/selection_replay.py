"""Stored selection input and its deterministic replay (spec section 4, section 5, R6).

The compact run snapshot does not store the pool. It stores ``selection_input``, which is
enough to recompute the selection. replay_gate takes its parameters ONLY from that record and
dispatches on ``algorithm_version``:

- ``select-v1`` (P5 part 2, current): leaders + tie group / exploration head, bin edges and
  seeds; recomputed by ``ranking.selection.replay`` (the same ``select_v1`` code the run used).
- ``ranked-prefix-v1`` (P3 to P5 part 1 snapshots): head = the first K = min(result_limit,
  pool_size) entries of the full-pool order as [listing_id, combined_score, price|null]; the
  selection is that prefix. Kept so older stored runs still replay.

A replayer returns a ``Replayed`` (ids in display order, plus the per-item selection reasons
and the mode when the algorithm defines them).
"""

import platform
from dataclasses import dataclass

from recommendation.contracts.errors import RecommendationError

PREFIX_ALGORITHM = "ranked-prefix-v1"


@dataclass
class Replayed:
    ids: list
    reasons: dict | None = None
    mode: str | None = None


def prefix_selection_input(ranked, scores, rows_by_id, result_limit):
    k = min(result_limit, len(ranked))
    return {
        "algorithm_version": PREFIX_ALGORITHM,
        "python_version": platform.python_version(),
        "pool_size": len(ranked),
        "result_limit": result_limit,
        "K": k,
        "head": [[i, scores[i].combined_score, rows_by_id[i].price] for i in ranked[:k]],
    }


def _invalid(message):
    return RecommendationError("INVALID_SNAPSHOT", message)


def replay_prefix(selection_input):
    head = selection_input["head"]
    k = selection_input["K"]
    if k != min(selection_input["result_limit"], selection_input["pool_size"]) or len(head) != k:
        raise _invalid("Stored selection input has an inconsistent head length")
    order = [(-score, listing_id) for listing_id, score, _ in head]
    if order != sorted(order) or len({h[0] for h in head}) != len(head):
        raise _invalid("Stored selection head is not the full-pool score order")
    return Replayed([h[0] for h in head])


def replay_select_v1(selection_input):
    from ..ranking import selection

    try:
        outcome = selection.replay(selection_input)
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise _invalid(f"Stored select-v1 input cannot be replayed: {exc}") from None
    return Replayed(outcome.ids, outcome.reasons, outcome.mode)


REPLAYERS = {PREFIX_ALGORITHM: replay_prefix, "select-v1": replay_select_v1}


def replay_select(selection_input):
    """Replayed selection of a stored selection_input (dispatch on algorithm_version)."""
    replayer = REPLAYERS.get(selection_input.get("algorithm_version"))
    if replayer is None:
        raise _invalid(f"Unknown selection algorithm {selection_input.get('algorithm_version')!r}")
    return replayer(selection_input)
