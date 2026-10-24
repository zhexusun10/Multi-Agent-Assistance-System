"""Pure statistics for the P7 calibration and evaluation (no I/O, no service imports).

Conventions: numpy percentiles use method "linear" (as the scorer, R11); every function takes
plain Python / numpy values and returns JSON-safe Python values (floats rounded only by the
caller when printing).
"""

import math
from collections import Counter

import numpy as np

PERCENTILES = (0, 1, 5, 10, 25, 50, 75, 90, 95, 99, 100)


def _finite(values):
    return np.array([v for v in values if v is not None and isinstance(v, (int, float)) and math.isfinite(v)], float)


def summarize(values, percentiles=PERCENTILES):
    """n, missing, mean, std (ddof 0), distinct and the given percentiles of the finite values."""
    values = list(values)
    v = _finite(values)
    out = {"n": len(values), "missing": len(values) - len(v), "distinct": int(len(np.unique(v)))}
    if not len(v):
        return {**out, "mean": None, "std": None, "percentiles": {}}
    q = np.percentile(v, percentiles, method="linear")
    return {**out, "mean": float(v.mean()), "std": float(v.std()),
            "percentiles": {f"p{p:g}": float(x) for p, x in zip(percentiles, q)}}


def duplicates(values, top=10):
    """How concentrated a value column is: distinct count, the ``top`` most common values with
    their counts, the share of rows taken by them and the share of rows whose value is shared
    with at least one other row."""
    v = [x for x in values if x is not None]
    if not v:
        return {"n": 0, "distinct": 0, "top": [], "top_share": 0.0, "shared_value_share": 0.0}
    counts = Counter(v)
    common = counts.most_common(top)
    shared = sum(c for c in counts.values() if c > 1)
    return {"n": len(v), "distinct": len(counts), "top": [[k, c] for k, c in common],
            "top_share": sum(c for _, c in common) / len(v), "shared_value_share": shared / len(v)}


def outliers(values, thresholds=()):
    """Counts above p99, above the Tukey far fence (Q3 + 3 IQR) and above fixed thresholds."""
    v = _finite(values)
    if not len(v):
        return {"n": 0}
    q1, q3, p99 = np.percentile(v, [25, 75, 99], method="linear")
    fence = q3 + 3 * (q3 - q1)
    return {"n": int(len(v)), "max": float(v.max()), "p99": float(p99), "tukey_far_fence": float(fence),
            "above_p99": int((v > p99).sum()), "above_tukey_far": int((v > fence).sum()),
            "above": {f"{t:g}": int((v > t).sum()) for t in thresholds}}


def missingness(rows, fields):
    """{field: {missing, share}} over ListingSummary-like objects."""
    n = len(rows)
    out = {}
    for f in fields:
        missing = sum(getattr(r, f) is None for r in rows)
        out[f] = {"missing": missing, "share": missing / n if n else 0.0}
    return out


def gini(counts):
    """Gini coefficient of non-negative counts, zero entries INCLUDED (0 = equal exposure,
    (n-1)/n = everything on one item). An all-zero vector gives 0."""
    x = np.sort(np.asarray(list(counts), float))
    if (x < 0).any():
        raise ValueError("gini needs non-negative counts")
    n = len(x)
    total = x.sum()
    if n == 0 or total <= 0:
        return 0.0
    i = np.arange(1, n + 1)
    return float((2 * (i * x).sum()) / (n * total) - (n + 1) / n)


def coverage(exposure, pool_ids):
    """Fraction of ``pool_ids`` with at least one exposure (``exposure``: id -> count)."""
    pool = list(pool_ids)
    if not pool:
        return 0.0
    return sum(exposure.get(i, 0) > 0 for i in pool) / len(pool)


def tie_group_bounds(scores, k, epsilon):
    """[start, end) of the cutoff group G = {i: |s_i - s_K| <= epsilon/2} (same rule as
    ranking/selection.tie_group, kept separate so the analysis never changes runtime code)."""
    s_k = scores[k - 1]
    half = epsilon / 2
    start = k - 1
    while start > 0 and abs(scores[start - 1] - s_k) <= half:
        start -= 1
    end = k
    while end < len(scores) and abs(scores[end] - s_k) <= half:
        end += 1
    return start, end


def tie_profile(scores, k, epsilons):
    """Cutoff structure of a non-increasing score list for result size ``k``.

    exact_ties = items whose score equals s_K exactly; per epsilon: B = |G|, leaders = |L| and
    m = k - |L|. Also the top-1 to top-K span, the gap between s_K and s_{K+1} and the number of
    distinct scores in the top 3K."""
    scores = [float(s) for s in scores]
    n = len(scores)
    if n <= k:
        return {"pool": n, "k": k, "small_pool": True}
    s_k = scores[k - 1]
    out = {
        "pool": n, "k": k, "small_pool": False, "top": scores[0], "s_k": s_k,
        "top_to_k_span": scores[0] - s_k, "gap_k_next": s_k - scores[k],
        "exact_ties": sum(s == s_k for s in scores),
        "distinct_top_3k": len(set(scores[: 3 * k])),
        "per_epsilon": {},
    }
    for e in epsilons:
        start, end = tie_group_bounds(scores, k, e)
        out["per_epsilon"][f"{e:g}"] = {"B": end - start, "leaders": start, "m": k - start}
    return out


def bin_balance(counts):
    """Balance of a histogram: Shannon entropy normalised by log(number of bins given) (1 =
    uniform over all bins), min/max ratio and the number of empty bins."""
    c = np.asarray(list(counts), float)
    total = c.sum()
    if not len(c) or total <= 0:
        return {"bins": len(c), "entropy": 0.0, "min_max_ratio": 0.0, "empty": len(c)}
    p = c[c > 0] / total
    entropy = float(-(p * np.log(p)).sum() / math.log(len(c))) if len(c) > 1 else 1.0
    return {"bins": len(c), "entropy": entropy, "min_max_ratio": float(c.min() / c.max()),
            "empty": int((c == 0).sum())}


def browsing_weight(seconds, scale, cap=None):
    t = seconds if cap is None else min(seconds, cap)
    return math.log1p(t / scale)


def browsing_curves(scales, durations, glance=5.0, read=60.0, long=600.0):
    """Weight table log1p(t / scale) and the ratios that characterise each scale: how much a
    glance counts relative to reading (w(glance)/w(read)) and how much long dwell still adds
    (w(long)/w(read)); also the marginal gain from 120 s to 240 s (> 0 for every scale: the log
    curve keeps increasing beyond 120 s, spec P5)."""
    out = {}
    for s in scales:
        w = {f"{t:g}": browsing_weight(t, s) for t in durations}
        out[f"{s:g}"] = {
            "weights": w,
            "glance_over_read": browsing_weight(glance, s) / browsing_weight(read, s),
            "long_over_read": browsing_weight(long, s) / browsing_weight(read, s),
            "gain_120_to_240": browsing_weight(240, s) - browsing_weight(120, s),
        }
    return out


def overlap(a, b, k):
    """|a[:k] & b[:k]| / k."""
    if k <= 0:
        return 1.0
    return len(set(list(a)[:k]) & set(list(b)[:k])) / k


def percentile(values, q):
    v = _finite(values)
    return float(np.percentile(v, q, method="linear")) if len(v) else None


def p50_p95(values):
    return {"n": len([v for v in values if v is not None]), "p50": percentile(values, 50),
            "p95": percentile(values, 95)}


def estimate_spread(estimates):
    """Mean, std (ddof 1) and range of repeated estimates of one quantity (convergence)."""
    v = _finite(estimates)
    if not len(v):
        return {"n": 0}
    return {"n": int(len(v)), "mean": float(v.mean()), "std": float(v.std(ddof=1)) if len(v) > 1 else 0.0,
            "min": float(v.min()), "max": float(v.max())}
