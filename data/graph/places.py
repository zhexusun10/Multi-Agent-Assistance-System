"""Auditable Singapore POIs and their rebuildable Neo4j proximity projection.

OSM object IDs, not fuzzy names, define identity. Polygon centers are reference
points, not entrances. Never infer a walking route, school admission eligibility,
or a postal district from a bounding box / nearby listing.
"""
import hashlib
import json
import logging
import math
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from data.graph.semantics import location_for

logger = logging.getLogger(__name__)
CATEGORIES = ("School", "FoodCourt", "Mall", "Market", "Hospital", "Clinic", "Park", "Supermarket", "Library")
CAMPUS_CATEGORIES = ("CampusBuilding",)
ALL_CATEGORIES = CATEGORIES + CAMPUS_CATEGORIES
# Campus buildings must sit inside an OSM university grounds polygon; most
# "building=yes" school-footprint polygons duplicate the school itself.
CAMPUS_BUILDING_KEYS = ("university", "college", "faculty", "research", "dormitory",
                        "library", "canteen", "sports_hall", "student_union")
from data.core.paths import PLACE_SNAPSHOT, RUNTIME_DIR

SNAPSHOT = PLACE_SNAPSHOT
PBF_URL = "https://download.bbbike.org/osm/bbbike/Singapore/Singapore.osm.pbf"
BOUNDARY_URL = ("https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/main/"
                "releaseData/gbOpen/SGP/ADM0/geoBoundaries-SGP-ADM0_simplified.geojson")
OVERPASS_ENDPOINTS = ("https://overpass-api.de/api/interpreter",
                      "https://overpass.kumi.systems/api/interpreter",
                      "https://overpass.private.coffee/api/interpreter")
OVERPASS_QUERY = """[out:json][timeout:90];
area[\"ISO3166-1\"=\"SG\"][admin_level=2]->.sg;
(nwr[amenity~\"^(school|college|university|food_court|marketplace|hospital|clinic|library)$\"](area.sg);
 nwr[shop~\"^(mall|supermarket)$\"](area.sg);nwr[leisure=park](area.sg););
out center tags;"""
SCHEMA = (
    "CREATE CONSTRAINT place_id_unique IF NOT EXISTS FOR (p:Place) REQUIRE p.place_id IS UNIQUE",
    "CREATE CONSTRAINT dataset_id_unique IF NOT EXISTS FOR (d:Dataset) REQUIRE d.dataset_id IS UNIQUE",
    "CREATE POINT INDEX place_position IF NOT EXISTS FOR (p:Place) ON (p.position)",
    "CREATE INDEX place_category IF NOT EXISTS FOR (p:Place) ON (p.category)",
)
RELATE_LISTINGS = """
MATCH (l:Listing {source: 'propertyguru'}) WHERE l.listing_id > $after
WITH l ORDER BY l.listing_id LIMIT $batch_size
CALL (l) {
    OPTIONAL MATCH (l)-[old:NEAR_PLACE {source: 'osm_sg_proximity'}]->()
    DELETE old
}
CALL (l) {
    WITH l WHERE l.coordinate_status = 'valid' AND l.position IS NOT NULL
    MATCH (p:Place {source: 'openstreetmap', active: true})
    WHERE point.distance(p.position, l.position) <= $radius_m
    MERGE (l)-[r:NEAR_PLACE {source: 'osm_sg_proximity'}]->(p)
    SET r.source = 'osm_sg_proximity', r.distance_m = round(point.distance(p.position, l.position), 2),
        r.distance_type = 'geodesic', r.distance_unit = 'm', r.route_verified = false,
        r.measurement_method = 'neo4j.point.distance.WGS84', r.radius_m = $radius_m,
        r.computed_at = $computed_at, r.listing_source_updated_at = l.source_updated_at,
        r.place_observed_at = p.observed_at, r.distance_label = '直线距离（非步行路线）'
    RETURN count(r) AS relationships
}
RETURN max(l.listing_id) AS last_id, count(l) AS listings, sum(relationships) AS relationships
"""
DISTRICT_PLACES = """
MATCH (d:District)
CALL (d) {
    OPTIONAL MATCH (d)-[old:HAS_NEARBY_PLACE {source: 'osm_sg_proximity'}]->()
    DELETE old
}
CALL (d) {
    MATCH (d)<-[:IN_DISTRICT]-(l:Listing)-[r:NEAR_PLACE {source: 'osm_sg_proximity'}]->(p:Place)
    WITH d, p, count(DISTINCT l) AS evidence_count, min(r.distance_m) AS minimum_distance_m
    MERGE (d)-[r:HAS_NEARBY_PLACE {source: 'osm_sg_proximity'}]->(p)
    SET r.source = 'osm_sg_proximity', r.membership_method = 'near_listings_in_district',
        r.is_administrative_membership = false, r.evidence_count = evidence_count,
        r.minimum_distance_m = minimum_distance_m, r.computed_at = $computed_at
    RETURN count(r) AS relationships
}
RETURN sum(relationships) AS relationships
"""


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def category_for(tags, in_campus=False):
    # Some bus stops incorrectly carry shop=mall/amenity=college in OSM.
    if tags.get("highway") == "bus_stop" or tags.get("public_transport") == "platform":
        return None
    if tags.get("disused") == "yes" or tags.get("abandoned") == "yes":
        return None
    amenity = tags.get("amenity")
    building = tags.get("building")
    if in_campus:
        # Grounds polygons (amenity=university without a building) stay ordinary
        # School POIs; campus membership evidence comes from campus_places.
        if building and building != "yes" and (building in CAMPUS_BUILDING_KEYS or amenity in ("college", "university")):
            return "CampusBuilding"
        return None  # plain building=yes shapes duplicate the school grounds
    if amenity in ("school", "college", "university"):
        return "School"
    if amenity == "food_court":
        return "FoodCourt"
    if amenity == "marketplace":
        name = (tags.get("name:en") or tags.get("name") or "").casefold()
        return "FoodCourt" if any(x in name for x in ("hawker", "food centre", "food center")) else "Market"
    return ({"hospital": "Hospital", "clinic": "Clinic", "library": "Library"}.get(amenity)
            or {"mall": "Mall", "supermarket": "Supermarket"}.get(tags.get("shop"))
            or ("Park" if tags.get("leisure") == "park" else None))


def map_osm_element(element, observed_at, in_campus=False):
    tags = element.get("tags", {})
    category = category_for(tags, in_campus=in_campus)
    allowed = ALL_CATEGORIES if in_campus else CATEGORIES
    name = (tags.get("name:en") or tags.get("name") or tags.get("full_name") or tags.get("alt_name"))
    kind, oid = element.get("type"), element.get("id")
    center = element if kind == "node" else element.get("center", {})
    location, status = location_for(center.get("lat"), center.get("lon"))
    if (not category or category not in allowed or not isinstance(name, str) or not name.strip() or status != "valid"
            or kind not in ("node", "way", "relation") or not isinstance(oid, int) or oid <= 0):
        return None
    row = {
        "place_id": f"OSM:{kind}:{oid}", "name": name.strip(), "category": category,
        "latitude": location["latitude"], "longitude": location["longitude"],
        "source": "openstreetmap", "dataset_id": "osm_sg_places", "source_url": f"https://www.openstreetmap.org/{kind}/{oid}",
        "source_object_type": kind, "source_object_id": str(oid), "observed_at": observed_at,
        "source_updated_at": element.get("timestamp"),
        "coordinate_method": "osm_node" if kind == "node" else "osm_geometry_bbox_center",
        "coordinate_status": "valid", "license": "ODbL-1.0", "active": True,
        "attribution": "© OpenStreetMap contributors", "identity_method": "osm_object_id",
    }
    for key, tag in (("name_local", "name:zh"), ("postal_code", "addr:postcode"),
                     ("street_name", "addr:street"), ("house_number", "addr:housenumber"),
                     ("full_name", "full_name"),
                     ("alt_names", "alt_name"),
                     ("opening_hours", "opening_hours"), ("website", "website"), ("wikidata", "wikidata"),
                     ("education_level", "isced:level"), ("operator", "operator")):
        if tags.get(tag):
            row[key] = ([x.strip() for x in tags[tag].split(";") if x.strip()]
                        if tag == "alt_name" else tags[tag])
    row["subcategory"] = tags.get("amenity") or tags.get("building") or tags.get("shop") or tags.get("leisure")
    return {k: v for k, v in row.items() if v is not None}


def _download(url, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    try:
        request = Request(url, headers={"User-Agent": "PropertyKnowledgeGraph/1.0 (OpenStreetMap POI import)"})
        with urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _inside_ring(lon, lat, ring):
    inside = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        if (a[1] > lat) != (b[1] > lat) and lon < (b[0] - a[0]) * (lat - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return inside


def inside_boundary(lon, lat, boundary):
    for feature in boundary["features"]:
        geometry = feature["geometry"]
        polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        for polygon in polygons:
            if _inside_ring(lon, lat, polygon[0]) and not any(_inside_ring(lon, lat, h) for h in polygon[1:]):
                return True
    return False


def read_pbf(path, boundary, observed_at):
    """BBBike is a rectangular extract; a country polygon is REQUIRED to exclude Johor.

    Pass 1 collects university grounds polygons; pass 2 maps ordinary POIs plus
    campus buildings that sit inside a mapped grounds polygon (point-in-polygon
    evidence only, never ownership or an entrance route).
    """
    import osmium
    from campus_places import (apply_corroborations, attach_membership, campus_feature,
                                campus_ground, geometry_center)
    elements, grounds = {}, []
    factory = osmium.geom.GeoJSONFactory()

    class Grounds(osmium.SimpleHandler):
        def area(self, area):
            if not campus_ground(area.tags):
                return
            try:
                geometry = json.loads(factory.create_multipolygon(area))
            except (RuntimeError, ValueError):
                return  # incomplete extracts must not invent a grounds polygon
            center = geometry_center(geometry)
            if not center or not inside_boundary(center["lon"], center["lat"], boundary):
                return
            kind = "way" if area.from_way() else "relation"
            grounds.append(campus_feature(kind, area.orig_id(), area.tags, geometry,
                                          area.timestamp.isoformat()))

    Grounds().apply_file(str(path), locations=True)

    def put(kind, oid, tags, coords, timestamp):
        if not coords:
            return
        lon = (min(c[0] for c in coords) + max(c[0] for c in coords)) / 2
        lat = (min(c[1] for c in coords) + max(c[1] for c in coords)) / 2
        if not inside_boundary(lon, lat, boundary):
            return
        element = {"type": kind, "id": oid, "tags": tags, "timestamp": timestamp,
                   "lat": lat, "lon": lon, "center": {"lat": lat, "lon": lon}}
        campus_row = map_osm_element(element, observed_at, in_campus=True)
        if campus_row is not None:
            campus_row = attach_membership(campus_row, grounds)
            # A building outside every mapped grounds falls back to a normal POI.
            if campus_row.get("campus_ids"):
                elements[campus_row["place_id"]] = campus_row
                return
        row = map_osm_element(element, observed_at)
        if row:
            elements[row["place_id"]] = row

    class Handler(osmium.SimpleHandler):
        def node(self, node):
            if (category_for(node.tags) or category_for(node.tags, in_campus=True)) and node.location.valid():
                put("node", node.id, dict(node.tags), [(node.location.lon, node.location.lat)],
                    node.timestamp.isoformat())

        def way(self, way):
            if category_for(way.tags) or category_for(way.tags, in_campus=True):
                put("way", way.id, dict(way.tags),
                    [(n.lon, n.lat) for n in way.nodes if n.location.valid()], way.timestamp.isoformat())

        def area(self, area):
            if (category_for(area.tags) or category_for(area.tags, in_campus=True)) and not area.from_way():
                coords = [(n.lon, n.lat) for ring in area.outer_rings() for n in ring if n.location.valid()]
                put("relation", area.orig_id(), dict(area.tags), coords, area.timestamp.isoformat())

    Handler().apply_file(str(path), locations=True)
    return sorted(apply_corroborations(elements.values()), key=lambda row: row["place_id"])


def validate_places(rows):
    result = {}
    for row in rows:
        location, status = location_for(row.get("latitude"), row.get("longitude"))
        if (status != "valid" or row.get("category") not in ALL_CATEGORIES
                or not row.get("place_id", "").startswith("OSM:") or not row.get("name")
                or row.get("source") != "openstreetmap"):
            raise ValueError("Invalid place snapshot record; refusing a partial import")
        if row.get("category") in CAMPUS_CATEGORIES and not row.get("campus_ids"):
            raise ValueError("Campus place without a mapped campus grounds; refusing a partial import")
        result[row["place_id"]] = dict(row, dataset_id="osm_sg_places", latitude=location["latitude"], longitude=location["longitude"])
    if not result:
        raise ValueError("Empty POI data; existing enrichment is unchanged")
    return sorted(result.values(), key=lambda row: row["place_id"])


def load_snapshot(path=SNAPSHOT):
    snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_places(snapshot["places"]), snapshot["metadata"]


def acquire_places(*, output=SNAPSHOT, pbf=None, cache_dir=RUNTIME_DIR / "places", refresh=False):
    """Use a reviewed cached snapshot, or fetch public data with an extract fallback.

    Failures never substitute hand-written demo locations. Write the snapshot
    atomically only after validating a nonempty result.
    """
    output, cache = Path(output), Path(cache_dir)
    if output.is_file() and not refresh and pbf is None:
        return load_snapshot(output)
    observed_at = utc_now()
    rows, retrieval_url, boundary_url = None, None, None
    if pbf is None:
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                url = endpoint + "?" + urlencode({"data": OVERPASS_QUERY})
                with urlopen(Request(url, headers={"User-Agent": "PropertyKnowledgeGraph/1.0"}), timeout=110) as response:
                    data = json.load(response)
                if data.get("remark"):
                    raise ValueError(data["remark"])
                from campus_places import map_overpass_campus, overpass_grounds
                grounds = overpass_grounds(data["elements"])
                campus_rows = map_overpass_campus(data["elements"], grounds, observed_at)
                if not campus_rows:
                    raise ValueError("Overpass returned no campus buildings; refusing a truncated refresh")
                merged = {r["place_id"]: r for r in
                          (map_osm_element(e, observed_at) for e in data["elements"]) if r}
                merged.update({r["place_id"]: r for r in campus_rows})
                rows = validate_places(list(merged.values()))
                retrieval_url = endpoint
                break
            except Exception as exc:
                logger.warning("Public Overpass source failed (%s): %s", endpoint, type(exc).__name__)
    if rows is None:
        path = Path(pbf) if pbf else cache / "Singapore.osm.pbf"
        if pbf is None:
            _download(PBF_URL, path)
        boundary_path = cache / "singapore-boundary.geojson"
        if not boundary_path.is_file() or refresh:
            _download(BOUNDARY_URL, boundary_path)
        boundary = json.loads(boundary_path.read_text(encoding="utf-8"))
        rows = validate_places(read_pbf(path, boundary, observed_at))
        retrieval_url, boundary_url = PBF_URL, BOUNDARY_URL
    categories = dict(Counter(r["category"] for r in rows))
    campus_count = sum(categories.get(category, 0) for category in CAMPUS_CATEGORIES)
    metadata = {
        "dataset_id": "osm_sg_places", "observed_at": observed_at, "retrieval_url": retrieval_url,
        "boundary_url": boundary_url, "boundary_method": "country_polygon" if boundary_url else "osm_country_area",
        "license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors",
        "license_url": "https://www.openstreetmap.org/copyright", "categories": categories,
        "place_count": len(rows), "campus_building_count": campus_count,
        "coverage": "named mapped POIs and campus buildings; not an exhaustive official register",
        "identity": "OSM object; different objects with the same name are not automatically merged",
        "campus_membership": "point-in-grounds-polygon evidence only; not ownership or an entrance",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".partial")
    temporary.write_text(json.dumps({"metadata": metadata, "places": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    return rows, metadata


def _upsert_places(tx, rows, category):
    # category comes ONLY from the fixed enum, never caller-provided Cypher.
    if category not in ALL_CATEGORIES:
        raise ValueError("Unknown place category")
    remove = ":".join(ALL_CATEGORIES)
    tx.run(f"""UNWIND $rows AS row MERGE (p:Place {{place_id: row.place_id}})
        REMOVE p:{remove} SET p:{category} SET p = row,
        p.position = point({{latitude: row.latitude, longitude: row.longitude, srid: 4326}})
        """, rows=rows).consume()


def _link_campus(tx, rows, computed_at):
    tx.run("""
UNWIND $rows AS row
MATCH (b:Place {place_id: row.place_id})
MATCH (c:Place) WHERE c.place_id IN row.campus_ids
MERGE (b)-[r:PART_OF_CAMPUS]->(c)
SET r.source = 'osm_sg_proximity', r.membership_method = 'reference_point_in_osm_campus_polygon',
    r.is_administrative_membership = false, r.evidence = 'osm_geometry_bbox_center_in_campus_polygon',
    r.route_verified = false, r.computed_at = $computed_at,
    r.building_source_updated_at = row.source_updated_at
""", rows=rows, computed_at=computed_at).consume()


def sync_places(driver, rows, metadata, *, database=None, radius_m=1500, batch_size=500):
    if not math.isfinite(radius_m) or not 1 <= radius_m <= 10000 or batch_size <= 0:
        raise ValueError("radius_m must be 1..10000 and batch_size must be positive")
    rows = validate_places(rows)
    computed_at, after, listing_count, edge_count = utc_now(), 0, 0, 0
    db = database or os.getenv("NEO4J_DATABASE", "neo4j")
    with driver.session(database=db) as session:
        for statement in SCHEMA:
            session.run(statement).consume()
        for category in ALL_CATEGORIES:
            group = [row for row in rows if row["category"] == category]
            for start in range(0, len(group), batch_size):
                session.execute_write(_upsert_places, group[start:start + batch_size], category)
        # Mark disappeared OSM objects inactive only AFTER the entire validated
        # snapshot was written. Never delete someone else's POI nodes/edges.
        session.run("MATCH (p:Place {source: 'openstreetmap', dataset_id: 'osm_sg_places'}) WHERE NOT p.place_id IN $ids SET p.active = false",
                    ids=[r["place_id"] for r in rows]).consume()
        while True:
            def relate(tx):
                return tx.run(RELATE_LISTINGS, after=after, batch_size=batch_size,
                              radius_m=radius_m, computed_at=computed_at).single()
            result = session.execute_write(relate)
            if not result or result["last_id"] is None:
                break
            after = result["last_id"]
            listing_count += result["listings"]
            edge_count += result["relationships"]
        district_count = session.execute_write(
            lambda tx: tx.run(DISTRICT_PLACES, computed_at=computed_at).single())["relationships"] or 0
        campus_rows = [r for r in rows if r["category"] in CAMPUS_CATEGORIES]
        if campus_rows:
            # Rebuild only this projection's membership edges, then MERGE fresh ones.
            session.run("MATCH ()-[old:PART_OF_CAMPUS {source: 'osm_sg_proximity'}]->() DELETE old").consume()
            for start in range(0, len(campus_rows), batch_size):
                session.execute_write(_link_campus, campus_rows[start:start + batch_size], computed_at)
        props = {k: metadata[k] for k in ("dataset_id", "observed_at", "retrieval_url", "license", "attribution", "coverage")}
        props.update(place_count=len(rows), radius_m=radius_m, computed_at=computed_at,
                     snapshot_sha256=hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest())
        session.run("MERGE (d:Dataset {dataset_id: $id}) SET d = $props", id="osm_sg_places", props=props).consume()
    return {"places": len(rows), "categories": dict(Counter(r["category"] for r in rows)),
            "campus_building_count": len(campus_rows),
            "campus_relationships": sum(len(r["campus_ids"]) for r in campus_rows),
            "listings_checked": listing_count, "near_place_relationships": edge_count,
            "district_place_relationships": district_count, "radius_m": radius_m,
            "distance_type": "geodesic", "route_verified": False, "computed_at": computed_at}
