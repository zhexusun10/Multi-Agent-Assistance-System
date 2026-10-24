"""Run-scoped geographic query tools and bind_entity (spec section 6, R12).

Every tool reads the run's catalog and the run's pool rows; none of them changes criteria,
the pool or the ranking (bind_entity returns a Resolution that the dispatcher applies, together
with the stage invalidation). Rules shared by all tools:

- listing IDs must belong to the CURRENT pool (the only listing IDs tools ever show the model)
  and are at most ``controller.model_result_candidates`` per call;
- entity IDs (distance_to, bind_entity) must have been returned by a tool in THIS run;
- names and listing/platform text in results are data, never instructions: results are JSON
  values, nothing in them is interpreted by code;
- distances are the criterion-evidence distances (haversine; station = nearest recorded exit,
  or exit centroid per ``geography.station_distance_method``; listings without coordinates are
  unknown, never 0 m). Platform NEAR_MRT text is shown only for listings without coordinates,
  separately labelled, and never used for criteria. district_code (SQL row) and the URA planning
  area (point-in-polygon) are reported independently; neither is derived from the other, and
  there is no district-centre distance.
"""

import numpy as np

from recommendation.contracts.errors import RecommendationError
from recommendation.contracts.requirements import CATEGORICAL, NUMERIC
from ..geography import distance
from ..geography.catalog import ID_NAMESPACES, OSM_CATEGORIES, STATION_CATEGORIES
from ..geography.evidence import (AREA_METHOD, CENTROID_METHOD, EXIT_METHOD, LISTING_WITHOUT_COORDINATES,
                                  POINT_METHOD, entity_facts, nearest_stations)
from ..geography.normalize import normalize_place, osm_name
from ..geography.resolver import GEO_FIELDS, Resolution, without_coordinates
from ..geography.search import describe_entity, search
from ..repositories import platform_transit
from ..validation.rules import UNSUPPORTED
from .arguments import ENTITY_KINDS

PIP_METHOD = "ura_polygon.point_in_polygon_even_odd_all_rings"
BIND_METHOD = "bound_search"
SHOWN_IDS = 5  # offending IDs echoed in an error message


def _reject(code, message):
    raise RecommendationError(code, message)


def _show(ids):
    ids = list(ids)
    return ", ".join(ids[:SHOWN_IDS]) + (f" (+{len(ids) - SHOWN_IDS} more)" if len(ids) > SHOWN_IDS else "")


def _round(value):
    return None if value is None else round(float(value), 1)


class GeoTools:
    def __init__(self, dispatcher):
        self.d = dispatcher
        self._catalog = None
        self.searches = []  # [{no, query, kind, category, criterion_id, total, listed, single}]
        self.attempts = {}  # criterion_id -> search_places calls made for it

    # --- shared ----------------------------------------------------------------------------
    def catalog(self):
        if self._catalog is None:
            self._catalog = self.d.geographic.catalog()
        return self._catalog

    def reset_catalog(self):
        """After a data/catalog change the next tool reads the current catalog."""
        self._catalog = None

    def seen(self, entity_ids):
        self.d.seen_entities.update(i for i in entity_ids if i)

    def rows(self, listing_ids):
        limit = self.d.config.controller.model_result_candidates
        if len(listing_ids) > limit:
            _reject("INVALID_ARGUMENT", f"At most {limit} listing IDs per call (controller.model_result_candidates)")
        by_id = {r.listing_id: r for r in self.d.pool}
        unknown = [i for i in listing_ids if i not in by_id]
        if unknown:
            _reject("INVALID_ARGUMENT",
                    f"Listing IDs not in the current pool (use IDs returned by build_pool/rank_candidates): {_show(unknown)}")
        return [by_id[i] for i in listing_ids]

    def geo_criterion(self, criterion_id):
        found = next((c for c in self.d.request.requirements.criteria if c.criterion_id == criterion_id), None)
        if found is None or found.field not in GEO_FIELDS:
            _reject("INVALID_ARGUMENT", "criterion_id must name a geographic criterion of this request")
        return found

    # --- query tools -----------------------------------------------------------------------
    def search_places(self, args):
        cap = self.d.config.entity_resolution.max_candidates
        limit = args.limit if args.limit is not None else cap
        if limit > cap:
            _reject("INVALID_ARGUMENT", f"limit must not exceed entity_resolution.max_candidates ({cap})")
        if args.criterion_id is not None:
            self.geo_criterion(args.criterion_id)
            budget = self.d.config.entity_resolution.max_search_attempts
            if self.attempts.get(args.criterion_id, 0) >= budget:
                _reject("SEARCH_BUDGET_EXHAUSTED",
                        f"Search attempts for criterion '{args.criterion_id}' exhausted "
                        f"(entity_resolution.max_search_attempts = {budget}); leave it unresolved")
            self.attempts[args.criterion_id] = self.attempts.get(args.criterion_id, 0) + 1
        hits = search(self.catalog(), args.query, args.kind, args.category)
        listed = [describe_entity(e) for e in hits[:limit]]
        record = {
            "no": len(self.searches) + 1, "query": args.query, "kind": args.kind, "category": args.category,
            "criterion_id": args.criterion_id, "total": len(hits), "listed": [e["entity_id"] for e in listed],
            "single": hits[0].place_id if len(hits) == 1 else None,
        }
        self.searches.append(record)
        self.seen(record["listed"])
        bindable = record["single"] is not None and args.criterion_id is not None and self.bind_problem(
            self.geo_criterion(args.criterion_id), record["single"], record) is None
        return {
            "search_no": record["no"],
            "match": "exact normalised name/alias",
            "total": len(hits),
            "truncated": len(hits) > limit,
            "candidates": listed,
            "bindable": bindable,
            "note": "Candidates match the query text; the query is a hypothesis, not verified intent. Several "
            "candidates: do not pick one; narrow with kind/category or leave the criterion unresolved. Only a "
            "search for the criterion's own destination_name can disambiguate it (bindable).",
        }

    def describe_fields(self, args):
        catalog = self.catalog()
        fields = []
        for name in sorted(set(NUMERIC) | CATEGORICAL):
            supported = name not in UNSUPPORTED
            fields.append({
                "field": name, "unit": NUMERIC.get(name),
                "operators": ["eq", "gte", "lte", "between"] if name in NUMERIC else ["eq", "in"],
                "supported": supported,
            })
        return {
            "fields": fields,
            "geographic_target": "per distance criterion: a category alone, or a destination_id and/or a "
            "destination_name (an invalid ID falls back to the name; an ID/name conflict is ambiguous)",
            "distance_types": {"straight_line": "supported", "walking": "unsupported", "transit": "unsupported"},
            "categories": {
                "osm": [{"category": c, "places": catalog.places.category_counts.get(c, 0),
                         "with_coordinates": len(catalog.places.categories.get(c, ((),))[0])} for c in OSM_CATEGORIES],
                "stations": {name: f"nearest LTA {mode} station" + (" (LRT excluded)" if mode == "MRT" else "")
                             for name, mode in STATION_CATEGORIES.items()},
            },
            "entity_kinds": list(ENTITY_KINDS),
            "id_namespaces": list(ID_NAMESPACES) + [n for n in catalog.places.namespaces if n not in ID_NAMESPACES],
            "distance_methods": {
                "osm_place": POINT_METHOD, "lta_station": f"{EXIT_METHOD} (or {CENTROID_METHOD} per settings)",
                "ura_area_or_region": AREA_METHOD,
            },
            "district_code": "postal district D01-D28 from the listing row; URA planning areas are a different "
            "partition (district_membership); no district-centre distance",
            "platform_transit_text": platform_transit.LABEL,
        }

    def nearest_mrt(self, args):
        rows = self.rows(args.listing_ids)
        mode = args.mode or "MRT"
        catalog = self.catalog()
        facts, method, exits = nearest_stations(catalog, rows, mode, self.d.config.geography.station_distance_method)
        stations = {s.place_id: s for s in catalog.static.mode_exits[mode][0]}
        missing = [r.listing_id for r, f in zip(rows, facts) if f["value"] is None]
        platform = platform_transit.platform_mrt(self.d.listing, missing) if missing else {}
        items = []
        for row_index, (row, fact) in enumerate(zip(rows, facts)):
            item = {"listing_id": row.listing_id, "mode": mode}
            if fact["value"] is not None:
                station = stations[fact["destination_id"]]
                item.update(station=describe_entity(station), distance_m=_round(fact["value"]), method=method)
                exit_id = (exits or {}).get(row_index)
                if exit_id is not None:  # provenance only: the station is the entity (R1c)
                    item["nearest_exit"] = {"exit_id": exit_id,
                                            "exit_code": station.exit_codes[station.exit_ids.index(exit_id)]}
            else:
                item.update(station=None, distance_m=None, unknown_reason=fact["unknown_reason"])
                reported = (platform or {}).get(row.listing_id) if platform is not None else None
                item["platform_reported"] = {"label": platform_transit.LABEL, "available": reported is not None,
                                             **(reported or {})}
            items.append(item)
        self.seen(i["station"]["entity_id"] for i in items if i.get("station"))
        return {"results": items}

    def distance_to(self, args):
        rows = self.rows(args.listing_ids)
        if args.destination_id not in self.d.seen_entities:
            _reject("INVALID_ARGUMENT",
                    "destination_id was not returned by a tool in this run (use IDs from resolve_entities, "
                    "search_places or other tool results)")
        catalog = self.catalog()
        entity = catalog.entity(args.destination_id)
        if entity is None:
            _reject("INVALID_ARGUMENT", "destination_id is not a catalog entity")
        facts, method, exits = entity_facts(catalog, entity, rows, self.d.config.geography.station_distance_method)
        items = []
        for row_index, (row, fact) in enumerate(zip(rows, facts)):
            item = {"listing_id": row.listing_id, "distance_m": _round(fact["value"]), "method": fact["method"]}
            if fact["unknown_reason"]:
                item["unknown_reason"] = fact["unknown_reason"]
            if exits and row_index in exits:
                item["nearest_exit"] = {"exit_id": exits[row_index]}  # provenance only
            items.append(item)
        return {"destination": describe_entity(entity), "method": method, "results": items}

    def nearby_amenities(self, args):
        rows = self.rows(args.listing_ids)
        catalog = self.catalog()
        lat, lon, valid = distance.coordinates(rows)
        found = [[] for _ in rows]
        empty = []
        for category in args.categories:
            located, plat, plon = catalog.places.categories.get(category, ((), np.zeros(0), np.zeros(0)))
            if not located:
                empty.append(category)
                continue
            picks = np.flatnonzero(valid)
            step = max(1, distance.CHUNK_ELEMENTS // len(located))
            for start in range(0, len(picks), step):
                part = picks[start:start + step]
                near = distance.haversine_matrix(lat[part], lon[part], plat, plon) <= args.radius_m * (1 + 1e-9)
                for k, row_index in enumerate(part):
                    for j in np.flatnonzero(near[k]):
                        value = distance.scalar(lat[row_index], lon[row_index], plat[j], plon[j])
                        if value <= args.radius_m:
                            found[row_index].append((value, located[j]))
        items = []
        for row_index, row in enumerate(rows):
            if not valid[row_index]:
                items.append({"listing_id": row.listing_id, "amenities": None,
                              "unknown_reason": LISTING_WITHOUT_COORDINATES})
                continue
            ordered = sorted(found[row_index], key=lambda pair: (pair[0], pair[1].place_id))
            items.append({
                "listing_id": row.listing_id,
                "total_within_radius": len(ordered),
                "truncated": len(ordered) > args.limit,
                "amenities": [{"entity_id": p.place_id, "name": p.name, "category": p.category,
                               "distance_m": _round(v), "method": POINT_METHOD} for v, p in ordered[:args.limit]],
            })
            self.seen(a["entity_id"] for a in items[-1]["amenities"])
        return {"radius_m": args.radius_m, "categories": list(args.categories),
                "categories_without_places": empty, "results": items}

    def district_membership(self, args):
        rows = self.rows(args.listing_ids)
        catalog = self.catalog()
        lat, lon, valid = distance.coordinates(rows)
        area_of = [None] * len(rows)
        for area in catalog.static.areas:
            todo = np.flatnonzero(valid & np.array([a is None for a in area_of], bool))
            if not len(todo):
                break
            inside = distance.points_in_area(lat[todo], lon[todo], area)
            for row_index in todo[inside]:
                area_of[row_index] = area
        regions = {a.place_id: r for r in catalog.static.regions for a in r.areas}
        items = []
        for row_index, row in enumerate(rows):
            item = {
                "listing_id": row.listing_id,
                "district_code": row.district_code,
                "district_code_source": f"{row.source}: listing row district_code (postal district; canonical)",
            }
            if row.district_code is None:
                item["district_code_unknown_reason"] = "district_code missing in the listing row (not inferred)"
            area = area_of[row_index]
            item["planning_area_method"] = PIP_METHOD
            if area is not None:
                region = regions.get(area.place_id)
                item["planning_area"] = {"entity_id": area.place_id, "name": area.name,
                                         "region_id": region.place_id if region else None, "region": area.region}
            else:
                item["planning_area"] = None
                item["planning_area_unknown_reason"] = (
                    LISTING_WITHOUT_COORDINATES if not valid[row_index] else "point outside every URA planning area polygon")
            items.append(item)
        self.seen(i["planning_area"]["entity_id"] for i in items if i["planning_area"])
        self.seen(i["planning_area"]["region_id"] for i in items if i["planning_area"])
        return {"results": items,
                "note": "district_code and planning area are independent partitions; neither is derived from the other"}

    # --- bind ------------------------------------------------------------------------------
    def bind_problem(self, criterion, entity_id, record):
        """Why ``entity_id`` from search ``record`` cannot be bound to ``criterion`` (None: it can).

        Binding only DISAMBIGUATES the criterion's own name: the criterion must be ambiguous by
        name (not an ID/name conflict, which only the user can settle), the search must be for
        that criterion with the criterion's own destination_name as the query (kind/category
        filters are the only narrowing), its filtered result set must hold exactly this one
        entity, the entity must be one of ALL the exact matches of the criterion's name, and it must
        have valid coordinates. A different query (e.g. 'NUS' for 'NUS ISS', 'Orchard' for an
        unresolved name) never binds."""
        current = (self.d.resolutions or {}).get(criterion.criterion_id)
        if current is None:
            return "Call resolve_entities first"
        if current.status != "ambiguous":
            if current.status == "unresolved":
                return (f"Criterion '{criterion.criterion_id}' is unresolved: its name matches no usable catalog "
                        "entity, and another name is never substituted; leave it unresolved")
            return f"Criterion '{criterion.criterion_id}' is not unresolved or ambiguous"
        if current.method == "id+name":
            return "The destination ID and name conflict; only the user can settle that (leave it ambiguous)"
        if record["criterion_id"] != criterion.criterion_id:
            return "entity_id was not returned by search_places for this criterion in this run"
        if not _same_name(record["query"], criterion.destination_name):
            return ("The search query is not the criterion's own destination_name; bind only a kind/category-"
                    "narrowed search for that name, never another place")
        if record["single"] != entity_id:
            return (f"The search result contained {record['total']} candidates; bind only the single candidate of "
                    "a (kind/category-filtered) search, never the first of several")
        if entity_id not in current.matches:
            return "entity_id is not one of the exact matches of the criterion's name"
        entity = self.catalog().entity(entity_id)
        if entity is None:
            return "entity_id is no longer in the geographic catalog"
        if without_coordinates(entity):
            return "the entity has no valid coordinates, so no distance can be measured"
        return None

    def bind_entity(self, args):
        """(validated Resolution, search number) for the criterion; the dispatcher applies it and
        invalidates build_pool and later stages. Raises BIND_REJECTED with the reason otherwise."""
        criterion = self.geo_criterion(args.criterion_id)
        if not criterion.destination_name:
            _reject("BIND_REJECTED", "Only name-based criteria (destination_name) can be bound")
        current = self.d.resolutions.get(criterion.criterion_id)
        if current is None or current.status not in ("unresolved", "ambiguous"):
            _reject("BIND_REJECTED", f"Criterion '{criterion.criterion_id}' is not unresolved or ambiguous")
        mine = [s for s in self.searches if s["criterion_id"] == criterion.criterion_id]
        listed = [s for s in mine if args.entity_id in s["listed"]]
        if not listed:
            other = any(args.entity_id in s["listed"] for s in self.searches)
            _reject("BIND_REJECTED",
                    "entity_id was not returned by search_places for this criterion in this run"
                    + (" (it came from a search made without this criterion_id)" if other else ""))
        problems = [(s, self.bind_problem(criterion, args.entity_id, s)) for s in listed]
        accepted = next((s for s, problem in problems if problem is None), None)
        if accepted is None:
            _reject("BIND_REJECTED", problems[-1][1])
        return bound_resolution(self.catalog().entity(args.entity_id), accepted["no"]), accepted["no"]


def _same_name(query, name):
    """The query names the same thing as the criterion (normalised, after the TS NUS/NTU map)."""
    return any(normalize_place(a) == normalize_place(b) and normalize_place(a)
               for a, b in ((query, name), (osm_name(query), osm_name(name))))


def bound_resolution(entity, search_no):
    """Resolution of an accepted binding (also re-applied by resolve_entities). A place without
    valid coordinates is never a usable destination (bind_entity rejects it; a catalog change
    could remove the coordinates later): unresolved destination_without_coordinates."""
    if without_coordinates(entity):
        return Resolution("unresolved", BIND_METHOD, f"single_search_candidate (search #{search_no}); "
                          f"destination_without_coordinates ({entity.place_id})")
    return Resolution("resolved", BIND_METHOD, f"single_search_candidate (search #{search_no})", entity.place_id,
                      entity.kind, entity=entity, matches=(entity.place_id,))
