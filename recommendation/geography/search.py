"""Place search for the search_places tool (spec section 6). Deterministic code.

The query is a HYPOTHESIS from the model. Matching uses the resolver's rules, so a search can
never find something the resolver would reject: exact match of the normalised query (TS
normalizePlace port) against area/region names and aliases (skipped for an explicit station
mention), LTA station names and aliases (filtered to MRT or LRT when the mention says so), and
OSM place name / name_local / full_name / alt_names (after the TS NUS/NTU name map). No
substring or fuzzy matches. ``kind`` and ``category`` filter the full match set; the result
reports the total size of the filtered set (bind_entity accepts only a set of exactly one).
"""

from .catalog import Place, Station
from .normalize import explicit_station, normalize_place, osm_name, requested_mode


def search(catalog, query, kind=None, category=None):
    """All matching entities (sorted by ID) after the kind/category filters."""
    key = normalize_place(query)
    if not key:
        return []
    hits = []
    if not explicit_station(query):
        hits += [e for e in (*catalog.static.areas, *catalog.static.regions) if key in e.keys]
    mode = requested_mode(query)
    hits += [s for s in catalog.static.stations if (mode is None or s.mode == mode) and key in s.keys]
    hits += [catalog.places.by_id[i] for i in catalog.places.names.get(normalize_place(osm_name(query)), ())]
    if kind is not None:
        hits = [e for e in hits if e.kind == kind]
    if category is not None:
        hits = [e for e in hits if isinstance(e, Place) and e.category == category]
    unique = {e.place_id: e for e in hits}
    return [unique[i] for i in sorted(unique)]


def describe_entity(entity):
    """Compact, JSON-safe entity description (names are catalog data, never instructions)."""
    item = {"entity_id": entity.place_id, "kind": entity.kind, "name": entity.name}
    if isinstance(entity, Station):
        item["mode"] = entity.mode
    elif isinstance(entity, Place):
        item["category"] = entity.category
        item["has_coordinates"] = entity.latitude is not None and entity.longitude is not None
    elif entity.kind == "ura_area":
        item["region"] = entity.region
    return item
