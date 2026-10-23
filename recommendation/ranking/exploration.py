"""Cold-start price-stratified exploration (spec section 8, R6, R11; selection algorithm select-v1).

Used only while cold start is active (ranking/selection.py decides). Pure functions over plain
Python values so that the live selection and replay_gate run the SAME code on the SAME inputs:

- ``price_bin_edges``: quantile edges over the FULL pool's non-null prices with
  ``cold_start.price_sampling.quantile_bins`` bins (numpy method 'linear', R11); duplicate edges
  are merged, so heavily repeated prices give fewer, wider bins.
- ``bin_index``: bins are [e_i, e_{i+1}) with the LAST bin closed [e_{n-1}, e_n] (R11); a single
  distinct price gives the one bin [e_0, e_0]; a null price falls into its own "unknown" bin
  (index = number of numeric bins).
- ``allocate``: proportional largest-remainder quotas. Integer arithmetic (floor(size*count /
  total) plus the exact integer remainders), so equal remainders are exact ties; ties are broken
  by one RNG key per bin. A quota never exceeds its bin's availability; anything a capped bin
  cannot take is redistributed in the same remainder order (with proportional quotas the cap
  never binds, the loop is kept for safety).
- ``stratified_sample``: allocate, then draw each bin's quota uniformly without replacement
  (``random.Random.sample`` over the bin members in score order O).

Every random choice uses the ``random.Random`` stream passed in (stageA or stageB, R3): first
one ``random()`` key per allocated bin (in bin order), then one ``sample`` call per bin with a
positive quota (in bin order). The order of these calls is part of select-v1.
"""

import bisect
import math

import numpy as np

QUANTILE_METHOD = "linear"  # R11


def price_bin_edges(prices, bins):
    """Sorted distinct quantile edges of the finite, non-null ``prices`` (empty when none)."""
    values = np.array([p for p in prices if p is not None and math.isfinite(p)], float)
    if not len(values):
        return []
    edges = np.quantile(values, np.linspace(0.0, 1.0, bins + 1), method=QUANTILE_METHOD)
    return sorted({float(e) for e in edges})  # duplicate edges merged


def numeric_bins(edges):
    """Number of numeric bins described by ``edges``."""
    return max(len(edges) - 1, 1) if edges else 0


def bin_index(price, edges):
    """Bin of ``price``: [e_i, e_{i+1}), last bin closed; null -> the unknown bin."""
    n = numeric_bins(edges)
    if price is None:
        return n
    if not edges or not edges[0] <= price <= edges[-1]:
        raise ValueError(f"price {price!r} lies outside the pool's price bins")
    if len(edges) == 1:
        return 0
    return min(bisect.bisect_right(edges, price) - 1, n - 1)


def bin_bounds(index, edges):
    """(lower, upper) of a bin; (None, None) for the unknown bin."""
    n = numeric_bins(edges)
    if index >= n:
        return None, None
    if len(edges) == 1:
        return edges[0], edges[0]
    return edges[index], edges[index + 1]


def allocate(counts, size, rng):
    """Largest-remainder quotas of ``size`` proportional to ``counts`` (list of ints)."""
    total = sum(counts)
    if size > total or size < 0:
        raise ValueError(f"cannot allocate {size} from {total} items")
    keys = [rng.random() for _ in counts]  # remainder tie-break, one key per bin
    if not total:
        return [0] * len(counts)
    quotas = [size * c // total for c in counts]
    remainders = [size * c % total for c in counts]
    order = sorted(range(len(counts)), key=lambda b: (-remainders[b], keys[b]))
    left = size - sum(quotas)
    while left:
        for b in order:
            if left and quotas[b] < counts[b]:
                quotas[b] += 1
                left -= 1
    return quotas


def stratified_sample(members, size, rng):
    """Proportional stratified sample of ``size`` items.

    ``members``: list of (bin index, [item, ...] in score order O) for every bin that has items,
    in bin order. Returns (sampled items as a set, quotas per bin in the same order)."""
    quotas = allocate([len(items) for _, items in members], size, rng)
    sampled = set()
    for (_, items), quota in zip(members, quotas):
        if quota:
            sampled.update(rng.sample(items, quota))
    return sampled, quotas


def group_by_bin(items, price_of, edges):
    """[(bin index, [items in the given order])] for the non-empty bins, in bin order."""
    groups = {}
    for item in items:
        groups.setdefault(bin_index(price_of[item], edges), []).append(item)
    return sorted(groups.items())
