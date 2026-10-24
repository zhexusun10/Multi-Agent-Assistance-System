"""User-requested identity, meaningful-feature and pool normalization regressions."""
import pytest
from recommendation.contracts.evidence import Evidence
from .test_scoring_v2 import pool_scores, fixture_rows, criteria, settings, SOFT_CHEAP, HARD_BEDROOMS


def test_price_utilities_follow_current_pool_not_static_bounds():
    rows = [r.model_copy(update={"price": p}) for r, p in zip(fixture_rows(), [3000., 4000., 5000.])]
    scored = pool_scores(rows, criteria(SOFT_CHEAP))
    assert [scored.breakdown(r.listing_id).soft_score for r in rows] == [1., .5, 0.]
    smaller = pool_scores(rows[:2], criteria(SOFT_CHEAP))
    assert smaller.breakdown(rows[1].listing_id).soft_score == 0.
    # Positive affine changes preserve normalized utility.
    shifted = pool_scores([r.model_copy(update={"price": r.price * 2 + 100}) for r in rows], criteria(SOFT_CHEAP))
    assert shifted.util.tolist() == scored.util.tolist()


@pytest.mark.parametrize("criterion_id", ["near", "price"])
def test_distance_utilities_use_full_pool_minmax_and_preserve_unknown(criterion_id):
    rows = fixture_rows()[:4]
    cs = criteria(SOFT_CHEAP, {"criterion_id":criterion_id,"field":"distance","operator":"lte","value":500,
                   "unit":"m","strength":"soft","utility":"proximity","category":"MRTStation",
                   "distance_type":"straight_line"})
    evidence = {r.listing_id:[Evidence(evidence_id=str(i), listing_id=r.listing_id, criterion_id=criterion_id,
        source="fixture", observed_at="2026-10-23", value=d, unit="m", distance_type="straight_line",
        method="fixture", snapshot_hash="fixture")] for i,(r,d) in enumerate(zip(rows,[100.,300.,900.]))}
    scored = pool_scores(rows, cs, evidence=evidence)
    assert scored.util[:,1].tolist() == pytest.approx([1.,.75,0.,0.])
    assert scored.known[:,1].tolist() == [True,True,True,False]
    price_only = pool_scores(rows, criteria(SOFT_CHEAP))
    assert scored.util[:,0].tolist() == price_only.util[:,0].tolist()
    assert scored.summary(10)["normalization_bounds"]["distance"][criterion_id] == (100.,900.)


def test_constant_features_dropped_but_self_signal_is_still_full():
    base = fixture_rows()[0]
    rows = [base.model_copy(update={"listing_id":str(i)}) for i in range(3)]
    scored = pool_scores(rows, criteria(HARD_BEDROOMS), appointments=[("0",[])])
    assert scored.features == []
    own = scored.breakdown("0")
    assert own.appointment_score == 1.
    assert own.contributions[0].method == "self_identity"
    assert scored.breakdown("1").appointment_score is None


def test_constant_soft_values_are_neutral_and_missing_stays_unknown():
    rows = [r.model_copy(update={"price":3000. if i<2 else None}) for i,r in enumerate(fixture_rows()[:3])]
    scored = pool_scores(rows, criteria(SOFT_CHEAP))
    assert scored.util[:,0].tolist() == [.5,.5,0.]
    assert scored.known[:,0].tolist() == [True,True,False]


def test_hard_features_retained_with_pool_ranges_and_identity_contributions():
    rows = fixture_rows()[:9]
    scored = pool_scores(rows, criteria(HARD_BEDROOMS), browsing=[(rows[0].listing_id,60)])
    assert "bedrooms" in scored.features
    assert scored.suppressed == []
    assert scored.breakdown(rows[0].listing_id).browsing_score == 1.
    for r in rows:
        b = scored.breakdown(r.listing_id)
        assert sum(c.contribution for c in b.contributions if c.branch=="browsing") == pytest.approx(b.browsing_score or 0.)


def test_geographic_similarity_range_comes_from_all_candidates_for_reference():
    rows = [r.model_copy(update={"latitude":1., "longitude":103. + i * .01})
            for i,r in enumerate(fixture_rows()[:3])]
    config = settings(**{"ranking.similarity.feature_weights": {
        "price":0., "bedrooms":0., "floor_area_sqft":0., "property_type":0., "geography":1.}})
    scored = pool_scores(rows, browsing=[(rows[0].listing_id,60)], config=config)
    assert scored.breakdown(rows[0].listing_id).browsing_score == 1.
    assert scored.breakdown(rows[1].listing_id).browsing_score == pytest.approx(.5, abs=1e-7)
    assert scored.breakdown(rows[2].listing_id).browsing_score == pytest.approx(0.)
    smaller = pool_scores(rows[:2], browsing=[(rows[0].listing_id,60)], config=config)
    assert smaller.breakdown(rows[1].listing_id).browsing_score == pytest.approx(0.)
