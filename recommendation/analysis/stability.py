"""Top-K stability under weight perturbation (analysis only; moved out of ranking/selection.py).

Until 2026-10-24 the runtime cold-start detector used S as its third exit condition (C/B/S).
The mpd-v1 detector replaced it, so nothing at runtime runs these trials any more. They are kept
here because ``selection.tie_max_score_span`` (epsilon) was derived with them (calibrate.py,
CALIBRATION.md section 8), and that derivation must stay reproducible.

- S = mean over ``trials`` of |T0 & Tt| / K, T0 = O[:K], Tt = top K after multiplying every weight
  by (1 + u), u ~ U[-f, f]: each soft criterion weight, soft/behaviour, appointment/browsing and
  each active similarity feature weight, each layer renormalised. The components are reweighted
  (PoolScores.combine), never rescored. Ties in a trial are broken by a per-trial random key, so
  the listing-ID order can never fake stability.
- FRACTION and the C/B/S thresholds are the historical values the P7 calibration ran under; they
  are no longer settings.
"""

from dataclasses import replace

import numpy as np

FRACTION = 0.2  # weight_perturbation_fraction epsilon was derived under (P7)
C_MIN, B_MAX, S_MIN = 0.8, 5, 0.9  # superseded C/B/S exit thresholds (historical reports only)


def generator(seed):
    return np.random.Generator(np.random.PCG64(seed))


def exit_conditions(c, b, s):
    """Superseded C/B/S exit test (historical calibration reports only)."""
    if c is None or b is None or s is None:
        return False
    return c >= C_MIN and b <= B_MAX and s >= S_MIN


def _renormalised(values):
    total = float(values.sum())
    return values / total if total > 0 else values


def perturbed_weights(weights, u):
    """Weights with every entry multiplied by (1 + u) and each layer renormalised.

    ``u`` layout: soft criteria, (soft, behaviour), (appointment, browsing), active features."""
    ns, nf = len(weights.soft_criteria), len(weights.features)
    soft_criteria = _renormalised(weights.soft_criteria * (1 + u[:ns]))
    fusion = _renormalised(np.array([weights.soft, weights.behavior]) * (1 + u[ns:ns + 2]))
    branch = _renormalised(np.array([weights.appointment, weights.browsing]) * (1 + u[ns + 2:ns + 4]))
    features = _renormalised(weights.features * (1 + u[ns + 4:ns + 4 + nf]))
    return replace(weights, soft_criteria=soft_criteria, soft=float(fusion[0]), behavior=float(fusion[1]),
                   appointment=float(branch[0]), browsing=float(branch[1]), features=features)


def top_k_stability(scored, k, trials, fraction, generator):
    """S: mean overlap of the perturbed top K with T0 = O[:K] (random tie-break per trial)."""
    n = len(scored.ids)
    in_t0 = np.zeros(n, bool)
    in_t0[scored.order[:k]] = True
    width = len(scored.weights.soft_criteria) + 4 + len(scored.weights.features)
    overlap = 0.0
    for _ in range(trials):
        u = generator.uniform(-fraction, fraction, size=width)
        combined = scored.combine(perturbed_weights(scored.weights, u)).combined
        keys = generator.random(n)
        top = np.lexsort((keys, -combined))[:k]
        overlap += int(in_t0[top].sum()) / k
    return overlap / trials
