---
id: system-evidence
title: Understanding recommendations, distances and unverified listing facts
scope: system_help
revision: 2026-10-20.1
checked_at: '2026-10-20'
review_after: '2027-01-20'
sources:
- title: Project guide knowledge/rental/system-evidence.md
  url: project://knowledge/rental/system-evidence.md
---
# Understanding recommendation evidence

## Hard requirements and preferences
Explicit must, minimum and maximum statements form hard requirements; ideally, closer is better and nearby can form preferences. Recommendations check hard requirements before ranking soft preferences. Near Clementi can resolve to the area or place and use closer-first distance without asking the user to invent a radius. Within three kilometres is an explicit cap and must not silently become only a preference. Places that cannot be identified uniquely still need a targeted clarification.

## Unverified is not passed, and does not always mean an empty field
A condition may be unverified because historical data is missing, a description does not prove that particular condition, the data capability is unsupported or an external service is not connected. One bathroom, 1B1B or a master-room label alone does not prove exclusive, unshared bathroom use. An agent’s explicit statement that a private bathroom is provided can be extracted as an affirmative statement, but remains user-supplied reply content rather than an independently platform-verified property fact.

## OSM, geodesic distance and campus relationships
OSM supplies open-map data, and Neo4j stores the imported places and relationships. Here geodesic means a spherical straight-line distance, not a public-transport API journey time or actual walking route. Inside-campus relationships are geometric evidence between a map point and campus polygon, not proof of administrative affiliation, entrance location or actual access. Near NUS cannot automatically become within thirty minutes by bus.

## What zero distance to an area means
With a URA area polygon as reference, a home inside the area can have zero area distance. This means inside the area, not zero metres from a station or precise landmark. For a particular MRT, campus reference point or other location, show the actual reference used. Do not mix an area centre, boundary and station coordinates.

## Historical snapshots and current status
Cards show historical prices, photos and fields, prioritising requirement matches; other recorded details can be expanded. A historical listing does not prove current availability. Search evidence, user requirements and agents’ spoken/written replies are stored separately; knowledge answers do not rewrite the property database. Homes with unverified hard requirements may still receive enquiry drafts to clarify those questions, without treating unknown conditions as satisfied.
