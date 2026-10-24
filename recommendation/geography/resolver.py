"""Deterministic geographic entity resolution (spec section 6, R12). Code, never the model.

Order for one criterion target (destination_id / destination_name / category):

1. category: the 10 OSM categories (resolved when the catalog has places of that category),
   ``MRTStation`` (nearest LTA MRT station; LRT excluded, TS parity) and ``LRTStation``.
   Anything else -> unresolved ``unsupported_category``.
2. destination_id: an ID in a supported namespace (OSM:, LTA:, URA:, plus the place catalog's
   own namespace, e.g. ``fixture-`` for synthetic places) is verified against the catalog and
   reused. Unsupported namespace -> ``unsupported_namespace``; unknown ID -> ``id_not_found``;
   an LTA exit ID -> ``exit_not_destination`` (the station is the entity, R1c). Each of these
   continues with a name search when a destination_name is present (reason recorded first).
   ID and name resolving to different entities -> ambiguous ``id_name_conflict`` (the ID is
   consistent with an ambiguous name when it is one of ALL the name's exact matches, not only of
   the listed candidates). A generic MRT name never conflicts with a verified ID.
3. destination_name (port of TS LocationService.resolve, exact normalised matches only):
   a) unless the mention is an explicit station request, URA area/region names + area aliases;
   b) LTA stations, filtered to MRT or LRT when the mention says so, matching the station name
      + aliases.json[base] + area-aliases[base];
   c) the TS NUS/NTU name map, then OSM place name / name_local / full_name / alt_names.
   One hit -> resolved. Several hits at any step -> ambiguous with candidates (sorted by ID,
   at most ``entity_resolution.max_candidates``; ``matches`` keeps every hit), never a first
   pick. Nothing -> unresolved ``no_exact_match`` (a name that is not in the catalog is never
   invented or substring-matched). A generic MRT mention (TS isGenericMrt: 'MRT', '地铁站',
   'nearest MRT station', ...) without an ID is the ``MRTStation`` category (method
   ``name->category``, TS geo-intake parity).
4. A resolved OSM place without valid coordinates is unusable as a destination: unresolved
   ``destination_without_coordinates`` (a criterion-level problem, R8), never "resolved with
   every listing unknown".

Contract 2.0 criteria carry a category alone, or a destination_id and/or a destination_name, so
every path above is reachable from a request.
"""

from dataclasses import dataclass, field

from recommendation.contracts.summary import ResolutionCandidate, ResolutionOriginal, ResolutionResult
from dataclasses import replace

from .catalog import OSM_CATEGORIES, STATION_CATEGORIES, Place, valid_point
from .normalize import explicit_station, is_generic_mrt, normalize_place, osm_name, requested_mode

GEO_FIELDS = ("distance", "commute")


@dataclass(frozen=True)
class Resolution:
    status: str  # resolved | ambiguous | unresolved | not_applicable
    method: str
    reason: str | None = None
    entity_id: str | None = None
    entity_kind: str | None = None
    candidates: tuple = ()  # ((entity_id, name, kind), ...)
    entity: object = field(default=None, compare=False, repr=False)  # catalog object or category name
    matches: tuple = field(default=(), compare=False, repr=False)  # every exact name match (IDs, untruncated)

    @property
    def resolved(self):
        return self.status == "resolved"

    def key(self):
        """(entity_id, entity_kind) for pool_key / canonical criterion keys, None unless resolved."""
        return (self.entity_id, self.entity_kind) if self.resolved else None

    def summary(self):
        return {"status": self.status, "entity_id": self.entity_id, "entity_kind": self.entity_kind,
                "method": self.method, "reason": self.reason,
                "candidates": [list(c) for c in self.candidates]}


def _name(entity):
    return getattr(entity, "name", None)


def _resolved(entity, method, reason, matches=()):
    return Resolution("resolved", method, reason, entity.place_id, entity.kind, entity=entity,
                      matches=matches or (entity.place_id,))


def _choice(hits, method, single_reason, limit):
    hits = sorted(hits, key=lambda e: e.place_id)
    matches = tuple(e.place_id for e in hits)
    if len(hits) == 1:
        return _resolved(hits[0], method, single_reason, matches)
    candidates = tuple((e.place_id, _name(e), e.kind) for e in hits[:limit])
    return Resolution("ambiguous", method, f"multiple_exact_matches ({len(hits)})", candidates=candidates,
                      matches=matches)


def without_coordinates(entity):
    return isinstance(entity, Place) and not valid_point(entity.latitude, entity.longitude)


def usable(resolution):
    """A resolved OSM place without valid coordinates -> unresolved destination_without_coordinates."""
    if not (resolution.resolved and without_coordinates(resolution.entity)):
        return resolution
    reason = "; ".join(r for r in (resolution.reason, f"destination_without_coordinates ({resolution.entity_id})") if r)
    return Resolution("unresolved", resolution.method, reason, matches=resolution.matches)


def resolve_name(catalog, name, max_candidates):
    key = normalize_place(name)
    if not key:
        return Resolution("unresolved", "name", "empty_name")
    if not explicit_station(name):
        hits = [e for e in (*catalog.static.areas, *catalog.static.regions) if key in e.keys]
        if hits:
            return _choice(hits, "name", "unique_exact_area_match", max_candidates)
    mode = requested_mode(name)
    hits = [s for s in catalog.static.stations if (mode is None or s.mode == mode) and key in s.keys]
    if hits:
        return _choice(hits, "name", "unique_exact_station_match", max_candidates)
    ids = catalog.places.names.get(normalize_place(osm_name(name)), [])
    if ids:
        return _choice([catalog.places.by_id[i] for i in ids], "name", "unique_exact_place_match", max_candidates)
    return Resolution("unresolved", "name", "no_exact_match")


def _category(catalog, category):
    if category in STATION_CATEGORIES:
        mode = STATION_CATEGORIES[category]
        stations = catalog.static.mode_exits[mode][0]
        if stations:
            return Resolution("resolved", "category", f"nearest of {len(stations)} LTA {mode} stations",
                              category, "category", entity=category)
        return Resolution("unresolved", "category", "category_has_no_entities")
    if category in OSM_CATEGORIES:
        located = catalog.places.categories.get(category, ((),))[0]
        if located:
            return Resolution("resolved", "category", f"nearest of {len(located)} known {category} places",
                              category, "category", entity=category)
        return Resolution("unresolved", "category", "category_has_no_entities")
    return Resolution("unresolved", "category", "unsupported_category")


def resolve(catalog, destination_id=None, destination_name=None, category=None, max_candidates=None):
    if max_candidates is None or max_candidates < 1:
        raise ValueError("entity_resolution.max_candidates must be a positive integer")
    if category:
        return _category(catalog, category)
    return usable(_target(catalog, destination_id, destination_name, max_candidates))


def _target(catalog, destination_id, destination_name, max_candidates):
    notes, by_id = [], None
    if destination_id:
        if not catalog.supported_namespace(destination_id):
            notes.append("unsupported_namespace")
        elif destination_id.startswith("LTA:EXIT:"):
            notes.append("exit_not_destination")
        else:
            by_id = catalog.entity(destination_id)
            if by_id is None:
                notes.append("id_not_found")
    generic = bool(destination_name) and is_generic_mrt(destination_name)
    if generic and by_id is not None:  # a generic MRT mention names no particular entity
        return _resolved(by_id, "id+name", "verified_id; generic MRT name")
    if generic:
        named = replace(_category(catalog, "MRTStation"), method="name->category")
    else:
        named = resolve_name(catalog, destination_name, max_candidates) if destination_name else None
    if by_id is not None and named is not None:
        if named.status == "resolved" and named.entity_id != by_id.place_id or (
            named.status == "ambiguous" and by_id.place_id not in named.matches
        ):
            others = named.candidates or ((named.entity_id, _name(named.entity), named.entity_kind),)
            candidates = sorted({(by_id.place_id, _name(by_id), by_id.kind), *others})[:max_candidates]
            return Resolution("ambiguous", "id+name", "id_name_conflict", candidates=tuple(candidates))
        return _resolved(by_id, "id+name", f"verified_id; name {named.status}")
    if by_id is not None:
        return _resolved(by_id, "id", "verified_id")
    if named is not None:
        if notes:
            return Resolution(named.status, "id_then_name", "; ".join([*notes, named.reason or ""]).rstrip("; "),
                              named.entity_id, named.entity_kind, named.candidates, named.entity, named.matches)
        return named
    return Resolution("unresolved", "id", "; ".join(notes) or "no_target")


def resolve_criteria(catalog, criteria, max_candidates):
    """{criterion_id: Resolution} for every geographic criterion."""
    return {
        c.criterion_id: resolve(catalog, c.destination_id, c.destination_name, c.category, max_candidates)
        for c in criteria if c.field in GEO_FIELDS
    }


def key_map(resolutions):
    """criterion_id -> (entity_id, entity_kind) for resolved criteria (pool_builder.criterion_key)."""
    return {cid: r.key() for cid, r in (resolutions or {}).items() if r.resolved}


def to_contract(criterion, resolution):
    return ResolutionResult(
        criterion_id=criterion.criterion_id,
        field=criterion.field,
        original=ResolutionOriginal(destination_id=criterion.destination_id,
                                    destination_name=criterion.destination_name, category=criterion.category),
        status=resolution.status,
        entity_id=resolution.entity_id if resolution.resolved else None,
        entity_kind=resolution.entity_kind if resolution.resolved else None,
        method=resolution.method,
        reason=resolution.reason,
        candidates=[ResolutionCandidate(entity_id=i, name=n, kind=k) for i, n, k in resolution.candidates],
    )


REASON_TEXT = {
    "unsupported_namespace": "the destination ID namespace is not supported (use OSM:, LTA: or URA: IDs)",
    "id_not_found": "the destination ID is not in the geographic catalog",
    "exit_not_destination": "an LTA exit is not a destination; use its station ID",
    "no_exact_match": "no catalog entity matches the name exactly",
    "multiple_exact_matches": "several catalog entities match the name exactly",
    "empty_name": "the destination name is empty after normalisation",
    "unsupported_category": "the category is not supported (10 OSM categories, MRTStation, LRTStation)",
    "category_has_no_entities": "the catalog has no entities of this category",
    "id_name_conflict": "the destination ID and name point to different entities",
    "no_target": "no destination given",
    "destination_without_coordinates": "the destination has no valid coordinates in the catalog, so no distance "
    "can be measured",
}


def describe(resolution):
    """Human-readable reason for clarifications and unknown reasons (codes stay in ``reason``).
    Accepts a Resolution or a contract ResolutionResult."""
    reason = resolution.reason or ""
    parts = [REASON_TEXT.get(code.split(" ")[0], code) for code in reason.split("; ") if code]
    if resolution.status == "ambiguous":
        pairs = [(c[0], c[1]) if isinstance(c, tuple) else (c.entity_id, c.name) for c in resolution.candidates]
        names = ", ".join(f"{name or entity_id} ({entity_id})" for entity_id, name in pairs)
        parts.append(f"candidates: {names}" if names else "several candidates")
    return "; ".join(p for p in parts if p) or resolution.status
