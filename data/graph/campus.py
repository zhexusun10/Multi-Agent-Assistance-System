"""Named campus buildings/facilities, with explicit spatial membership evidence.

A place reference point inside an OSM university grounds polygon is NOT proof of
legal ownership, faculty affiliation or an entrance/walking route. Bus stops and
buildings with the same code stay separate OSM objects. Reviewed cross-source
corroborations keep OSM identity and add a clearly separated official reference
field; they never relocate or rename an OSM object.
"""
import json
from pathlib import Path

from data.core.paths import DATA_DIR
from data.graph.semantics import location_for

CORROBORATIONS = DATA_DIR / "datasets" / "campus_corroborations.json"
MEMBERSHIP_METHOD = "reference_point_in_osm_campus_polygon"


def campus_ground(tags):
    """Grounds polygons: amenity=university WITHOUT a building key.

    Grounds themselves are ordinary School POIs; plain ``building=yes`` shapes
    duplicate the grounds footprint and are not campus buildings either.
    """
    return (tags.get("amenity") == "university" and tags.get("building") in (None, "no")
            and bool(tags.get("name:en") or tags.get("name"))
            and tags.get("disused") != "yes" and tags.get("abandoned") != "yes")


def campus_feature(kind, oid, tags, geometry, timestamp):
    return {"type": "Feature", "geometry": geometry, "properties": {
        "place_id": f"OSM:{kind}:{oid}", "name": tags.get("name:en") or tags.get("name"),
        "source_url": f"https://www.openstreetmap.org/{kind}/{oid}", "source_updated_at": timestamp}}


def geometry_center(geometry):
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    coords = [p for polygon in polygons for ring in polygon for p in ring]
    if not coords:
        return None
    return {"lon": (min(p[0] for p in coords) + max(p[0] for p in coords)) / 2,
            "lat": (min(p[1] for p in coords) + max(p[1] for p in coords)) / 2}


def attach_membership(row, features):
    """Point-in-polygon evidence only; keep OSM identity and coordinates."""
    from data.graph.places import inside_boundary
    parents = [f for f in features if f["properties"]["place_id"] != row["place_id"]
               and inside_boundary(row["longitude"], row["latitude"], {"features": [f]})]
    if parents:
        row["campus_ids"] = sorted({f["properties"]["place_id"] for f in parents})
        row["campus_membership_method"] = MEMBERSHIP_METHOD
        row["campus_membership_source_urls"] = sorted({f["properties"]["source_url"] for f in parents})
    return row


def apply_corroborations(rows, path=CORROBORATIONS):
    """Reviewed cross-source checks, never fuzzy automatic name/nearby merging.

    A check is applied only when the OSM identity fields it was reviewed against
    still match; stale checks are dropped instead of silently reused.
    """
    if not Path(path).is_file():
        return rows
    checks = json.loads(Path(path).read_text(encoding="utf-8"))["checks"]
    for row in rows:
        check = next((c for c in checks if c["place_id"] == row["place_id"]), None)
        if not check:
            continue
        if (row["name"] != check["osm_name"] or row.get("postal_code") != check["postal_code"]
                or check["campus_id"] not in row.get("campus_ids", [])):
            continue
        for key in ("official_source_url", "official_api_url", "official_record_id", "official_name",
                    "official_campus_name", "official_reference_latitude", "official_reference_longitude",
                    "official_verified_at", "official_verification_method", "official_source_license"):
            row[key] = check[key]
    return rows


def _stitch_rings(segments):
    """Assemble OSM relation member ways; incomplete geometry yields None."""
    segments = [list(s) for s in segments if len(s) >= 2]
    rings = []
    while segments:
        chain = segments.pop()
        while chain[0] != chain[-1]:
            found = False
            for i, part in enumerate(segments):
                if chain[-1] == part[0]:
                    chain += part[1:]
                elif chain[-1] == part[-1]:
                    chain += list(reversed(part))[1:]
                elif chain[0] == part[-1]:
                    chain = part[:-1] + chain
                elif chain[0] == part[0]:
                    chain = list(reversed(part))[:-1] + chain
                else:
                    continue
                segments.pop(i)
                found = True
                break
            if not found:
                return None
        if len(chain) < 4:
            return None
        rings.append(chain)
    return rings


def overpass_geometry(element):
    """Closed ways and fully assembled relations only; never a partial polygon."""
    if element.get("type") == "way":
        points = [[p["lon"], p["lat"]] for p in element.get("geometry", []) if "lon" in p and "lat" in p]
        if len(points) >= 4 and points[0] == points[-1]:
            return {"type": "Polygon", "coordinates": [points]}
    if element.get("type") == "relation":
        from data.graph.places import _inside_ring
        segments = {"outer": [], "inner": []}
        for member in element.get("members", []):
            role = member.get("role") or "outer"
            if role in segments and member.get("type") == "way":
                segments[role].append([[p["lon"], p["lat"]] for p in member.get("geometry", []) if "lon" in p and "lat" in p])
        outers, inners = _stitch_rings(segments["outer"]), _stitch_rings(segments["inner"])
        if outers and inners is not None:
            polygons = [[outer] + [h for h in inners if _inside_ring(*h[0], outer)] for outer in outers]
            return {"type": "MultiPolygon", "coordinates": polygons}
    return None


def overpass_grounds(elements):
    from data.graph.places import inside_boundary
    features = []
    for element in elements:
        if campus_ground(element.get("tags", {})):
            geometry = overpass_geometry(element)
            if geometry:
                center = geometry_center(geometry)
                if center and inside_boundary(center["lon"], center["lat"], {"features": [
                        {"type": "Feature", "geometry": geometry, "properties": {}}]}):
                    features.append(campus_feature(element["type"], element["id"],
                                                   element["tags"], geometry, element.get("timestamp")))
    return features


def map_overpass_campus(elements, grounds, observed_at):
    """Campus features from an Overpass response; None values never invent centers."""
    from data.graph.places import category_for, map_osm_element
    rows = []
    for original in elements:
        tags = original.get("tags", {})
        if not category_for(tags, in_campus=True):
            continue
        element = dict(original)
        if element["type"] != "node":
            geometry = overpass_geometry(element)
            if geometry:
                center = geometry_center(geometry)
            elif element.get("geometry"):
                coords = [p for p in element["geometry"] if "lon" in p and "lat" in p]
                center = ({"lon": (min(p["lon"] for p in coords) + max(p["lon"] for p in coords)) / 2,
                           "lat": (min(p["lat"] for p in coords) + max(p["lat"] for p in coords)) / 2}
                          if coords else None)
            else:
                center = None
            if center:
                element["center"] = center
        if element["type"] == "node":
            if "lat" not in element or "lon" not in element:
                continue
        elif "center" not in element:
            continue
        row = map_osm_element(element, observed_at, in_campus=True)
        if row:
            row = attach_membership(row, grounds)
            if row.get("campus_ids"):
                rows.append(row)
    if grounds and not rows:
        raise ValueError("Overpass returned grounds but no campus buildings; refusing a truncated campus refresh")
    return apply_corroborations(rows)
