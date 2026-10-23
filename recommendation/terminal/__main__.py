import argparse, json, uuid, os
from pathlib import Path
from pydantic import ValidationError
from recommendation.contracts.requirements import Requirements
from recommendation.contracts.behavior import BehaviorContext, request_uuid
from recommendation.contracts.request import (
    ColdStartInput,
    RecommendationRequest,
)
from recommendation.contracts.errors import RecommendationError
from ..repositories.connections import (
    ROOT,
    load_environment,
    repositories,
    listing_connection,
    geographic_connection,
)
from ..agent.controller import ScriptedController, LiveController
from ..state.store import StateStore
from ..service import RecommendationService
from ..context.manager import check_feedback
from ..config import RecommendationConfig
from . import debug


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def feedback_example(response, previous_input=None, as_of=None):
    """Filled sample behavior_context (simulates the orchestrator, main contract): the previous
    INPUT's cumulative listing_view_durations and book_viewing_listings carried over (never native
    history), plus 90 s (rank 1) / 30 s (others) added for every listing shown in ``response``."""
    as_of = as_of or _now()
    previous = previous_input.model_dump(mode="json") if previous_input is not None else None
    durations = {d["listing_id"]: d for d in (previous or {}).get("listing_view_durations", [])}
    for i, item in enumerate(response.recommendations):
        listing_id = item.listing.listing_id
        seconds = 90 if i == 0 else 30
        entry = durations.get(listing_id)
        if entry:
            entry = {**entry, "duration_seconds": entry["duration_seconds"] + seconds,
                     "view_count": entry["view_count"] + 1, "last_viewed_at": as_of,
                     "source_request_ids": sorted(set(entry["source_request_ids"]) | {request_uuid(response.request_id)})}
        else:
            entry = {"listing_id": listing_id, "duration_seconds": seconds, "view_count": 1,
                     "last_viewed_at": as_of, "source_request_ids": [request_uuid(response.request_id)],
                     "interest_eligible": True}
        durations[listing_id] = entry
    return {
        "version": "1.0",
        "as_of": as_of,
        "listing_view_durations": [durations[k] for k in sorted(durations)],
        "book_viewing_listings": list((previous or {}).get("book_viewing_listings", [])),
    }


def feedback_template(response, previous_input=None, as_of=None):
    """behavior_context template: the previous INPUT's cumulative entries (never native history)
    plus one entry with a null duration placeholder per newly shown listing; fill in the cumulative
    seconds and interest_eligible. Loading it unfilled is rejected."""
    template = feedback_example(response, previous_input, as_of)
    previous = {d.listing_id: d for d in (previous_input.listing_view_durations if previous_input else [])}
    shown = {r.listing.listing_id for r in response.recommendations}
    for entry in template["listing_view_durations"]:
        if entry["listing_id"] in shown:
            old = previous.get(entry["listing_id"])
            entry["duration_seconds"] = None if old is None else old.duration_seconds
            entry["view_count"] = 1 if old is None else old.view_count
            entry["interest_eligible"] = False if old is None else old.interest_eligible
    return template


def read_payload(arg):
    if arg.startswith("@"):
        return json.loads(Path(arg[1:]).read_text())
    print("Paste JSON, then END on its own line:")
    lines = []
    while True:
        line = input()
        if line == "END":
            break
        lines.append(line)
    return json.loads("\n".join(lines))


def check_connections(data_mode, controller):
    report = {}
    for name, factory in [
        ("listing", listing_connection),
        ("neo4j", geographic_connection),
    ]:
        if data_mode == "fixture":
            report[name] = {"mode": "fixture", "live_contacted": False}
            continue
        obj = None
        try:
            obj = factory()
            report[name] = obj.health()
        except RecommendationError as e:
            report[name] = {"contacted": False, "error": e.detail.model_dump()}
        except ImportError:
            report[name] = {
                "contacted": False,
                "error": "Missing repository dependency; install recommendation requirements",
            }
        except Exception:
            report[name] = {"contacted": False, "error": "Connection failed"}
        finally:
            if obj:
                obj.close()
    model = os.getenv("CHAT_MODEL", "openai:gpt-4.1-mini")
    key = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google-genai": "GOOGLE_API_KEY",
    }.get(model.split(":")[0])
    report["llm"] = {
        "mode": controller,
        "model": model,
        "configured": bool(key and os.getenv(key)),
        "contacted": False,
        "note": "Model execution is tested by recommend, not by configuration inspection.",
    }
    return report


def response_summary(result):
    """Compact lines for a response (status, pool cache hit/miss, selection, items)."""
    lines = [f"Summary: status={result.status} request_id={result.request_id} "
             f"config_hash={result.execution.config_hash[:12]}"]
    if result.error:
        lines.append(f"  error: {result.error.code}: {result.error.message} (retryable={result.error.retryable})")
    pool = result.pool
    if pool:
        hit = {"reused": "HIT", "built": "MISS"}.get(pool.cache, pool.cache)
        lines.append(f"  pool: cache={pool.cache} ({hit}) size={pool.size} source_count={pool.source_count} "
                     f"excluded_hard_fail={pool.excluded_hard_fail} excluded_hard_unknown={pool.excluded_hard_unknown} "
                     f"pool_version={pool.pool_version[:12]}")
    selection = result.selection
    if selection:
        cold = selection.cold_start
        lines.append(f"  selection: mode={selection.mode} seed={selection.seed[:12]} leaders={selection.leaders} "
                     f"tie_group={selection.tie_group.size if selection.tie_group else 0} cold_start="
                     f"{'active' if cold.active else 'inactive'} (exit_conditions_met={cold.exit_conditions_met})")
        if cold.evaluated and cold.exit_conditions_met:
            lines.append(f"  cold start exit conditions met: an orchestrator adopting this response sends cold_start "
                         f"{{exited: true, exited_pool_key: {cold.pool_key[:12]}...}} next (stage it with load-request)")
    for item in result.recommendations:
        lines.append(f"  #{item.rank} {item.listing.listing_id} score_rank={item.score_rank} "
                     f"combined={item.scores.combined_score:.4f} reason={item.selection_reason}")
    for r in result.resolution:
        if r.status in ("ambiguous", "unresolved"):
            lines.append(f"  resolution: {r.criterion_id} {r.status} ({r.reason}); candidates="
                         f"{[c.entity_id for c in r.candidates]}")
    lines += [f"  clarification: {c}" for c in result.clarifications]
    return "\n".join(lines)


class Terminal:
    def __init__(self, args):
        self.args = args
        self.base_config = RecommendationConfig.from_file(getattr(args, "config", None))
        self.config = self.base_config  # + session overrides (set-config); never written to a file
        self.overrides = {}
        self.seed = None  # set-seed test override of the selection base seed
        self.store = StateStore(args.state_path)
        self.service = None
        self.controller = None  # debug.ObservedController of the service
        self.events = debug.Events()
        self.step = None  # debug.StepSession (scripted step mode)
        self.view_source = "run"
        self.run_dispatcher = None  # dispatcher retained from this terminal's latest run
        self.conversation = args.conversation_id or str(uuid.uuid4())
        self.load()

    def load(self):
        from ..models.candidate import Response

        current = self.store.current(self.conversation)
        self.current = current
        self.behavior = None  # staged behavior_context (full snapshot replace)
        self.last_input = (
            BehaviorContext.model_validate(current["request"]["behavior_context"])
            if current and current["request"].get("behavior_context")
            else None
        )
        self.pending_request = None
        self.response = None
        self.requirements = (
            Requirements.model_validate(current["request"]["requirements"])
            if current
            else None
        )
        self.version = current["request"]["requirements_version"] if current else 0
        # Orchestrator-managed request fields stay as last committed until load-request changes them.
        self.cold_start = (
            ColdStartInput.model_validate(current["request"]["cold_start"]) if current else ColdStartInput()
        )
        self.result_limit = current["request"].get("result_limit") if current else None
        self.step = None
        self.view_source = "run"
        self.run_dispatcher = None
        if current:
            self.response = Response.model_validate(
                self.store.cached(self.conversation, current["request"]["request_id"])[
                    1
                ]
            )

    def request(self):
        if not self.requirements:
            raise ValueError("Submit requirements first")
        return RecommendationRequest(
            request_id=str(uuid.uuid4()),
            conversation_id=self.conversation,
            requirements_version=self.version,
            requirements=self.requirements,
            behavior_context=self.behavior,
            cold_start=self.cold_start,
            result_limit=self.result_limit,
            previous_state_id=self.current["state_id"] if self.current else None,
        )

    def ensure_service(self):
        if self.service is None:
            listing, geo = repositories(self.args.data_mode)
            inner = LiveController() if self.args.controller == "live" else ScriptedController()
            self.controller = debug.ObservedController(inner, self.events)
            # Progress comes from the observing controller (structured events), not the service hook.
            self.service = RecommendationService(listing, geo, self.store, self.controller, config=self.config)
        return self.service

    def apply_config(self, config):
        """Session settings for NEW requests (the service freezes settings per request)."""
        self.config = config
        if self.service is not None:
            self.service.config = config
        self.input_changed()

    def input_changed(self):
        """Staged input changed: the next recommend mints a new request_id; step stages that
        depend on the change are invalidated."""
        self.pending_request = None
        if self.step is not None:
            self.pending_request = self.request()
            try:
                level, invalidated = self.step.update(self.pending_request, self.config, self.seed)
            except (ValueError, ValidationError, RecommendationError) as e:
                self.step, self.view_source = None, "run"
                detail = f"{e.detail.code}: {e.detail.message}" if isinstance(e, RecommendationError) else str(e)
                print(f"Step session discarded: the new input does not pass the run checks ({detail}).")
                return
            if level is not None:
                print(f"Step session: input changed -> invalidated: {', '.join(invalidated) or 'no completed stage'}; "
                      f"still current: {', '.join(self.step.completed()) or 'none'}")

    def step_session(self):
        if self.pending_request is None:
            self.pending_request = self.request()
        if self.step is None:
            self.step = debug.StepSession(self, self.pending_request, self.config, self.seed)
            print(f"Step session for request {self.pending_request.request_id} (the request recommend will submit).")
        else:
            self.step.update(self.pending_request, self.config, self.seed)
        return self.step

    def print_response(self, result):
        if not getattr(self.args, "brief", False):
            print(result.model_dump_json(indent=2))
        print(response_summary(result))

    def command(self, line):
        command, _, arg = line.partition(" ")
        if command == "check-connections":
            print(
                json.dumps(
                    check_connections(self.args.data_mode, self.args.controller),
                    indent=2,
                )
            )
            return
        if command == "requirements":
            parsed = Requirements.model_validate(read_payload(arg))
            if parsed != self.requirements:
                self.version += 1
            self.requirements = parsed
            print(f"Accepted requirements v{self.version}; committed result unchanged.")
            self.input_changed()
            return
        if command == "feedback":
            # Full behavior_context replace (orchestrator semantics): nothing is merged natively.
            payload = read_payload(arg)
            if not isinstance(payload, dict):
                raise ValueError(
                    "Expected a behavior_context object {version, as_of, listing_view_durations, book_viewing_listings}"
                )
            snapshot = BehaviorContext.model_validate(payload)
            staged = self.request().model_copy(update={"behavior_context": snapshot})
            check_feedback(staged, self.store, self.config)  # Validate before replacing staged input.
            self.behavior = snapshot
            print(
                f"Staged behavior_context as of {snapshot.as_of.isoformat()}: "
                f"{len(snapshot.listing_view_durations)} cumulative browsing summaries, "
                f"{len(snapshot.book_viewing_listings)} viewing-draft listings; replaces any staged snapshot."
            )
            self.input_changed()
            return
        if command in ("feedback-example", "feedback-template"):
            if not self.response or not self.response.recommendations:
                raise ValueError("No verified response available")
            payload = (
                feedback_example(self.response, self.last_input)
                if command == "feedback-example"
                else feedback_template(self.response, self.last_input)
            )
            print(json.dumps(payload, indent=2))
            if arg:
                Path(arg).write_text(json.dumps(payload, indent=2) + "\n")
            return
        if command == "recommend":
            self.ensure_service()
            # Always a NEW request_id, except the retry of a failed pending request (R14).
            if self.pending_request is None:
                self.pending_request = self.request()
            if self.step is not None:
                print("Step session closed: recommend submits the full request.")
                self.step = None
            self.view_source = "run"
            self.controller.begin()
            self.controller.seed_override = self.seed
            result = self.service.recommend(self.pending_request)
            from ..schemas import validate_response

            validate_response(result)
            try:
                self.controller.report_gates(self.store.trace(self.conversation, result.request_id).get("stages"))
            except RecommendationError:
                pass
            self.print_response(result)
            directory = Path(self.args.state_path).resolve().parent / "exports"
            directory.mkdir(exist_ok=True)
            # Generated IDs, never caller-controlled path components.
            artifact = directory / f"{uuid.uuid4()}.json"
            artifact.write_text(result.model_dump_json(indent=2))
            print(f"Response artifact: {artifact}")
            if result.status != "error":
                if self.response:
                    old = {
                        r.listing.listing_id: r for r in self.response.recommendations
                    }
                    print(
                        "Rank/score changes:",
                        json.dumps(
                            {
                                r.listing.listing_id: {
                                    "old_rank": (
                                        old[r.listing.listing_id].rank
                                        if r.listing.listing_id in old
                                        else None
                                    ),
                                    "new_rank": r.rank,
                                    "old_combined": (
                                        old[r.listing.listing_id].scores.combined_score
                                        if r.listing.listing_id in old
                                        else None
                                    ),
                                    "combined": r.scores.combined_score,
                                    "score_change": (
                                        r.scores.combined_score
                                        - old[
                                            r.listing.listing_id
                                        ].scores.combined_score
                                        if r.listing.listing_id in old
                                        else None
                                    ),
                                }
                                for r in result.recommendations
                            }
                        ),
                    )
                self.response = result
                self.run_dispatcher = self.controller.dispatcher
                self.current = self.store.current(self.conversation)
                self.last_input = self.pending_request.behavior_context
                self.behavior = None
                self.pending_request = None
            return
        if command == "show-state":
            print(
                json.dumps(
                    {
                        "conversation_id": self.conversation,
                        "requirements_version": self.version,
                        "accepted_requirements": (
                            self.requirements.model_dump()
                            if self.requirements
                            else None
                        ),
                        "staged_behavior_context": (
                            self.behavior.model_dump(mode="json") if self.behavior else None
                        ),
                        "committed_state_id": (
                            self.current["state_id"] if self.current else None
                        ),
                        "coverage": (
                            self.response.state.model_dump() if self.response else None
                        ),
                        "profile": self.current["profile"] if self.current else None,
                    },
                    indent=2,
                )
            )
            return
        if command == "reset":
            self.conversation = str(uuid.uuid4())
            self.load()
            print("Conversation:", self.conversation)
            return
        if debug.handle(self, command, arg):
            return
        if command not in ("help", ""):
            raise ValueError("Unknown command (help lists the commands)")
        print(debug.HELP)

    def lines(self):
        """Interactive prompt, or the --script file (each command echoed after the prompt)."""
        script = getattr(self.args, "script", None)
        if not script:
            while True:
                yield input("recommendation> ").strip()
        for raw in Path(script).read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            print(f"recommendation> {line}", flush=True)
            yield line

    def run(self):
        print(
            f"data-mode={self.args.data_mode} controller={self.args.controller} conversation={self.conversation}\nState: {self.args.state_path}\nSettings: {self.config.source} (config_hash {self.config.config_hash[:12]})\nUse help for commands. Committed state resumes; staged input is not saved on exit."
        )
        if not self.current and self.store.legacy_head(self.conversation):
            print("Note: this conversation only has legacy contract-1.0 state; the next request starts a fresh v2 chain.")
        try:
            for line in self.lines():
                try:
                    if line == "exit":
                        break
                    self.command(line)
                except RecommendationError as e:
                    print(f"{e.detail.code}: {e.detail.message}")
                except (ValueError, ValidationError, OSError) as e:
                    print(str(e))
                except ImportError:
                    print(
                        "Missing dependency; install recommendation/requirements.txt"
                    )
        except (EOFError, KeyboardInterrupt):
            pass
        finally:
            if self.service:
                self.service.close()
            else:
                self.store.close()


def main():
    load_environment()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-mode", choices=["database", "fixture"], default="database"
    )
    parser.add_argument("--controller", choices=["live", "scripted"], default="live")
    parser.add_argument("--conversation-id")
    parser.add_argument(
        "--state-path",
        default=os.getenv(
            "RECOMMENDATION_STATE_PATH",
            str(ROOT / "recommendation/.runtime/state.sqlite"),
        ),
    )
    parser.add_argument("--check-connections", action="store_true")
    parser.add_argument("--script", help="Run the commands of this file (one per line, # comments) instead of the prompt")
    parser.add_argument("--brief", action="store_true", help="Print response summaries instead of full JSON (artifacts keep the full JSON)")
    parser.add_argument(
        "--config",
        default=os.getenv("RECOMMENDATION_CONFIG"),
        help="Alternative settings file (YAML or JSON, same schema as recommendation/settings/recommendation.yaml); default: the committed YAML",
    )
    args = parser.parse_args()
    if args.check_connections:
        print(json.dumps(check_connections(args.data_mode, args.controller), indent=2))
        return
    Terminal(args).run()


if __name__ == "__main__":
    main()
