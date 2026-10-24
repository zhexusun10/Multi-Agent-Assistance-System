"""Tool registry (spec section 6, R12): names, argument models, kinds, stage dependencies and the
descriptions the live controller sends to the model.

Kinds:
- ``pipeline``: the four stages, in this fixed order. A stage requires the previous one for the
  CURRENT inputs (``requires``); calling it out of order is a STALE_REFERENCE tool error. Running
  a stage discards every later stage's results.
- ``query``: read-only geographic lookups (no effect on criteria, pool or ranking).
- ``bind``: bind_entity, which changes a criterion's resolved entity and therefore pool_key: it
  discards build_pool / get_geographic_evidence / rank_candidates results.

Budgets (settings ``controller``): every call counts against ``max_tool_calls`` (exhaustion ends
the run, LOOP_EXHAUSTED); ``query`` and ``bind`` calls that actually run (not rejected for their
arguments, stage order or a stale reference) also count against ``max_resolution_tool_calls``
(exhaustion is a tool error; the pipeline can still finish);
search_places calls made for a criterion count against ``entity_resolution.max_search_attempts``
of that criterion.
"""

from dataclasses import dataclass

from ..models.tool_results import ToolArguments
from . import arguments as a


@dataclass(frozen=True)
class ToolSpec:
    name: str
    arguments: type
    kind: str  # pipeline | query | bind
    description: str
    requires: str | None = None  # stage that must be current before this tool runs


TOOLS = (
    ToolSpec("resolve_entities", ToolArguments, "pipeline",
             "Resolve every geographic criterion target with deterministic code (exact names/aliases, verified IDs, "
             "categories). Returns status, entity, reason and candidates per criterion. Discards later stages."),
    ToolSpec("build_pool", ToolArguments, "pipeline",
             "Build or reuse the exploration pool: every listing that passes ALL hard criteria (geographic ones "
             "included). Requires resolve_entities.", requires="resolve_entities"),
    ToolSpec("get_geographic_evidence", ToolArguments, "pipeline",
             "Measure soft geographic criteria over the current pool (straight-line). Requires build_pool.",
             requires="build_pool"),
    ToolSpec("rank_candidates", ToolArguments, "pipeline",
             "Validate, score and select from the current pool with code. Returns selected_ids, the final "
             "selection. Requires get_geographic_evidence.", requires="get_geographic_evidence"),
    ToolSpec("search_places", a.SearchPlacesArguments, "query",
             "Look up catalog entities (LTA stations, URA areas/regions, OSM places) whose name or alias matches "
             "the query exactly after normalisation. Results are candidates, not facts."),
    ToolSpec("describe_fields", a.DescribeFieldsArguments, "query",
             "Describe the supported criterion fields, units, operators, categories and entity kinds."),
    ToolSpec("nearest_mrt", a.NearestMrtArguments, "query",
             "Nearest LTA station (default MRT) of pool listings: station, straight-line distance to its nearest "
             "recorded exit (exit = provenance). Listings without coordinates get labelled platform-reported text "
             "only, never used for criteria. Requires build_pool.", requires="build_pool"),
    ToolSpec("distance_to", a.DistanceToArguments, "query",
             "Straight-line distance from pool listings to one entity ID returned by a tool in this run. "
             "Requires build_pool.", requires="build_pool"),
    ToolSpec("nearby_amenities", a.NearbyAmenitiesArguments, "query",
             "OSM places of the given categories within radius_m of pool listings, nearest first. "
             "Requires build_pool.", requires="build_pool"),
    ToolSpec("district_membership", a.DistrictMembershipArguments, "query",
             "Postal district code from the listing row and URA planning area by point-in-polygon, reported "
             "independently. Requires build_pool.", requires="build_pool"),
    ToolSpec("bind_entity", a.BindEntityArguments, "bind",
             "Bind an ambiguous name-based geographic criterion to the ONLY candidate of a search_places result "
             "made for that criterion in this run with the criterion's own destination_name as the query "
             "(kind/category narrowing). Discards build_pool and later stages.",
             requires="resolve_entities"),
)

BY_NAME = {t.name: t for t in TOOLS}
NAMES = tuple(t.name for t in TOOLS)
PIPELINE = tuple(t.name for t in TOOLS if t.kind == "pipeline")
RESOLUTION_TOOLS = frozenset(t.name for t in TOOLS if t.kind in ("query", "bind"))


def model_tools():
    """[{name, description, parameters}] with each tool's own JSON schema."""
    return [{"name": t.name, "description": t.description, "parameters": t.arguments.model_json_schema()}
            for t in TOOLS]
