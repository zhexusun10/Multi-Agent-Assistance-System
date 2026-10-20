"""Print a requirements example using a place ID actually read from Neo4j."""

import json
import argparse
from ..repositories.connections import load_environment, geographic_connection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--near-listings",
        action="store_true",
        help="Select an active School with recorded proximity edges to imported listings",
    )
    args = parser.parse_args()
    load_environment()
    repo = geographic_connection()
    try:
        query = (
            "MATCH (:Listing)-[:NEAR_PLACE]->(p:Place {active:true,category:'School'}) RETURN p.place_id AS id,p.name AS name,count(*) AS linked_listings ORDER BY linked_listings DESC,id LIMIT 1"
            if args.near_listings
            else "MATCH (p:Place {active:true,category:'School'}) RETURN p.place_id AS id,p.name AS name ORDER BY p.place_id LIMIT 1"
        )
        rows = repo.query(query)
        if not rows:
            raise SystemExit("No active School place found; no example generated.")
        destination = rows[0]["id"]
        print(
            json.dumps(
                {
                    "listing_type": "RENT",
                    "criteria": [
                        {
                            "criterion_id": "school-radius",
                            "field": "distance",
                            "operator": "lte",
                            "value": 1500,
                            "unit": "m",
                            "strength": "hard",
                            "destination_id": destination,
                            "distance_type": "straight_line",
                        },
                        {
                            "criterion_id": "nearer-school",
                            "field": "distance",
                            "operator": "lte",
                            "value": 1500,
                            "unit": "m",
                            "strength": "soft",
                            "weight": 1,
                            "utility": "proximity",
                            "destination_id": destination,
                            "distance_type": "straight_line",
                        },
                    ],
                },
                indent=2,
            )
        )
    finally:
        repo.close()


if __name__ == "__main__":
    main()
