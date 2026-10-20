import argparse, json, uuid, os
from pathlib import Path
from pydantic import ValidationError
from dev.sharedContract.recommendationSubagentContract.requirements import Requirements
from dev.sharedContract.recommendationSubagentContract.feedback import ViewingEvent
from dev.sharedContract.recommendationSubagentContract.request import (
    RecommendationRequest,
)
from dev.sharedContract.recommendationSubagentContract.errors import RecommendationError
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
from ..tracing.trace import now
from ..context.manager import prepare
from ..config import RecommendationConfig


def feedback_example(response):
    return {
        "events": [
            {
                "event_id": str(uuid.uuid4()),
                "recommendation_request_id": response.request_id,
                "listing_id": r.listing.listing_id,
                "event_type": "listing_view_duration",
                "source": "sample",
                "occurred_at": now(),
                "payload": {
                    "view_session_id": str(uuid.uuid4()),
                    "duration_seconds": 90 if i == 0 else 30,
                },
            }
            for i, r in enumerate(response.recommendations)
        ]
    }


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


class Terminal:
    def __init__(self, args):
        self.args = args
        self.config = RecommendationConfig.from_file(getattr(args, "config", None))
        self.store = StateStore(args.state_path)
        self.service = None
        self.conversation = args.conversation_id or str(uuid.uuid4())
        self.load()

    def load(self):
        from ..models.candidate import Response

        current = self.store.current(self.conversation)
        self.current = current
        self.events = []
        self.pending_request = None
        self.response = None
        self.requirements = (
            Requirements.model_validate(current["request"]["requirements"])
            if current
            else None
        )
        self.version = current["request"]["requirements_version"] if current else 0
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
            feedback=self.events,
            previous_state_id=self.current["state_id"] if self.current else None,
        )

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
            self.pending_request = None
            print(f"Accepted requirements v{self.version}; committed result unchanged.")
            return
        if command == "feedback":
            payload = read_payload(arg)
            events = []
            if (
                not isinstance(payload, dict)
                or set(payload) != {"events"}
                or not isinstance(payload["events"], list)
            ):
                raise ValueError("Expected an events array")
            for item in payload["events"]:
                if not isinstance(item, dict):
                    raise ValueError("Each event must be an object")
                if item.get("conversation_id", self.conversation) != self.conversation:
                    raise ValueError("Conflicting conversation_id")
                events.append(
                    ViewingEvent.model_validate(
                        {**item, "conversation_id": self.conversation}
                    )
                )
            staged = self.request().model_copy(
                update={"feedback": self.events + events}
            )
            prepare(
                staged, self.current, self.store
            )  # Validate references before modifying staged input.
            self.events += events
            self.pending_request = None
            print(f"Accepted {len(events)} events; staged until recommend commits.")
            return
        if command == "feedback-example":
            if not self.response or not self.response.recommendations:
                raise ValueError("No verified response available")
            payload = feedback_example(self.response)
            print(json.dumps(payload, indent=2))
            if arg:
                Path(arg).write_text(json.dumps(payload, indent=2) + "\n")
            return
        if command == "recommend":
            if self.service is None:
                listing, geo = repositories(self.args.data_mode)
                controller = (
                    LiveController()
                    if self.args.controller == "live"
                    else ScriptedController()
                )
                self.service = RecommendationService(
                    listing,
                    geo,
                    self.store,
                    controller,
                    progress=lambda name: print(f"tool: {name}", flush=True),
                    config=self.config,
                )
            if self.pending_request is None:
                self.pending_request = self.request()
            result = self.service.recommend(self.pending_request)
            from ..schemas import validate_response

            validate_response(result)
            print(result.model_dump_json(indent=2))
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
                self.current = self.store.current(self.conversation)
                self.events = []
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
                        "pending_feedback": [
                            e.model_dump(mode="json") for e in self.events
                        ],
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
        if command == "show-trace":
            print(
                json.dumps(self.store.trace(self.conversation, arg or None), indent=2)
            )
            return
        if command == "reset":
            self.conversation = str(uuid.uuid4())
            self.load()
            print("Conversation:", self.conversation)
            return
        if command not in ("help", ""):
            raise ValueError("Unknown command")
        print(
            "check-connections | requirements [@file] | feedback [@file] | feedback-example [file] | recommend | show-state | show-trace [request-id] | reset | exit"
        )

    def run(self):
        print(
            f"data-mode={self.args.data_mode} controller={self.args.controller} conversation={self.conversation}\nState: {self.args.state_path}\nUse help for commands. Committed state resumes; staged input is not saved on exit."
        )
        try:
            while True:
                try:
                    line = input("recommendation> ").strip()
                    if line == "exit":
                        break
                    self.command(line)
                except (ValueError, ValidationError, RecommendationError, OSError) as e:
                    print(str(e))
                except ImportError:
                    print(
                        "Missing dependency; install dev/recommendationSubagent/requirements.txt"
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
            str(ROOT / "dev/recommendationSubagent/.runtime/state.sqlite"),
        ),
    )
    parser.add_argument("--check-connections", action="store_true")
    parser.add_argument(
        "--config",
        default=os.getenv("RECOMMENDATION_CONFIG"),
        help="Application-owned scoring and execution policy JSON",
    )
    args = parser.parse_args()
    if args.check_connections:
        print(json.dumps(check_connections(args.data_mode, args.controller), indent=2))
        return
    Terminal(args).run()


if __name__ == "__main__":
    main()
