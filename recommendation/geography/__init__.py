"""Native geography (spec section 6): catalog, place-name normalisation, resolver, distances.

Independent of the TS orchestrator: the subagent resolves LTA stations, URA planning areas and
regions and OSM places itself, from the same knowledge/locations files and datasets the TS
LocationService reads, and measures every straight-line distance with one haversine.
"""
