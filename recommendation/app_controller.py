"""Application model-output protocol, outside the unchanged upstream algorithm.

Keeps the native four tools, limits, proposal IDs and final gate. Only frames the
output contract and unwraps one unambiguous, schema-valid proposal from prose.
"""
import json
from copy import deepcopy
from hashlib import sha256

from .agent.controller import LiveController, Proposal, visible_text

PROTOCOL_VERSION = "app-recommendation-output-v1"
OUTPUT_CONTRACT = """Application response contract:
Use the same four tools and the exact current context_ref. Data gaps are not
tool errors: an unresolved place or unknown hard condition must remain unknown.
After rank_candidates succeeds, stop gathering data and submit its ranked prefix
as the final proposal. An empty ranked_ids array is a completed ranking: return
{"listing_ids":[]} even when there are unverified candidate previews. The service
assembles unverified options separately. Never promote preview/candidate IDs into
the verified shortlist. Never repeat retrieval to repair unavailable source data.
Final output is ONE JSON object with listing_ids only, without prose or fences.
Example: ranking ready, ranked_ids=[] -> {"listing_ids":[]}.
Example: ranked prefix ["A","B"] -> {"listing_ids":["A","B"]}.
The final gate, not your narrative, determines whether a proposal is accepted.
"""


def canonical_proposal(content):
    text = visible_text(content).strip()
    if not text or len(text) > 65536:
        return None
    decoder = json.JSONDecoder()
    objects = []
    cursor = 0
    while (start := text.find("{", cursor)) >= 0:
        try:
            obj, length = decoder.raw_decode(text[start:])
        except ValueError:
            return None
        objects.append(obj)
        cursor = start + length  # Do not extract a nested object from an invalid envelope.
        if len(objects) > 1:
            return None
    if len(objects) != 1:
        return None
    try:
        return Proposal.model_validate(objects[0]).model_dump_json()
    except ValueError:
        return None


class ApplicationLiveController(LiveController):
    def propose(self, dispatcher, context, trace):
        self._dispatcher = dispatcher
        try:
            return super().propose(dispatcher, context, trace)
        finally:
            self._dispatcher = None

    def invoke(self, messages, tools, remaining):
        dispatcher = getattr(self, "_dispatcher", None)
        state = {
            "rank_computed": dispatcher is not None and dispatcher.ranked is not None,
            "current_ranked_prefix": (
                dispatcher.ranked[: dispatcher.request.result_limit]
                if dispatcher is not None and dispatcher.ranked is not None else None
            ),
        }
        suffix = OUTPUT_CONTRACT + "\nRead-only current run state: " + json.dumps(state)
        adapted = deepcopy(messages)
        adapted[0]["content"] += "\n\n" + suffix
        result = super().invoke(adapted, tools, remaining)
        if not isinstance(result, dict):
            return result  # The native controller owns protocol error handling.
        result = deepcopy(result)
        normalized = None if result.get("tool_calls") else canonical_proposal(result.get("content"))
        audit = {
            "version": PROTOCOL_VERSION,
            "system_suffix": suffix,
            "system_suffix_sha256": sha256(suffix.encode()).hexdigest(),
            "final_output_normalized": normalized is not None,
        }
        if normalized is not None:
            audit["raw_final_content"] = result.get("content")
            result["content"] = normalized
        result["app_output_protocol"] = audit
        return result
