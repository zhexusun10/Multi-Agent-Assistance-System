from recommendation.contracts.evidence import CriterionResult

UNSUPPORTED = {"commute", "furnishing", "availability", "lease_months"}


def compare(value, c):
    if c.operator == "eq":
        return value == c.value
    if c.operator == "in":
        return value in c.value
    if c.operator == "lte":
        return value <= c.value
    if c.operator == "gte":
        return value >= c.value
    return c.value[0] <= value <= c.value[1]


def evaluate(listing, criteria, evidence):
    results = []
    for c in criteria:
        refs = []
        if c.field in UNSUPPORTED or (
            c.field == "distance" and c.distance_type != "straight_line"
        ):
            outcome, reason = "unsupported", "No trusted evidence for this requirement"
        else:
            value = getattr(listing, c.field, None)
            reason = "Missing listing attribute"
            if c.field == "distance":
                fact = next(
                    (
                        e
                        for e in evidence
                        if e.listing_id == listing.listing_id
                        and e.criterion_id == c.criterion_id
                    ),
                    None,
                )
                value = fact.value if fact else None
                refs = [fact.evidence_id] if fact else []
                reason = (
                    fact.unknown_reason if fact else "Geographic evidence not requested"
                ) or "Missing distance"
            if value is None:
                outcome = "unknown"
            else:
                outcome = "pass" if compare(value, c) else "fail"
                reason = "Exact criterion comparison"
        results.append(
            CriterionResult(
                criterion_id=c.criterion_id,
                outcome=outcome,
                reason=reason,
                evidence_refs=refs,
            )
        )
    return results


def eligible(results, criteria):
    hard = {c.criterion_id for c in criteria if c.strength == "hard"}
    return all(
        r.outcome == "pass" for r in results if r.criterion_id in hard
    ) and hard <= {r.criterion_id for r in results}
