"""P6 part 2 terminal transcript: drives `python -m recommendation.terminal` commands and records
an echoed transcript (each command after the prompt, then its output).

Modes:
  fixture   fixture data, ScriptedController, temporary state (same flows as the automated test
            recommendation/tests/test_terminal_debug.py::test_terminal_transcript_covers_the_p6_flows)
  database  LIVE PostgreSQL + Neo4j, read-only (SQL READ ONLY / REPEATABLE READ transactions, Neo4j
            reads), ScriptedController (the LLM is never called), TEMPORARY state file (never
            data/.runtime). Pool-cache rows go to the temporary state only.

Flows: first recommendation (fixed seed), inspection commands, single-listing inspection (retained
and recomputed), feedback re-rank (cache hit), config and weight comparison with the fixed seed, cache miss on a
hard change, entity ambiguity, scripted step mode, original-request replay, export.

Usage (repository root):
  PYTHONPATH=. .venv/bin/python evaluation/recommendation-exploration-2026-10-23/p6/terminal_transcript.py \
      database evaluation/recommendation-exploration-2026-10-23/p6/live-terminal-transcript.txt
"""
import argparse, io, json, re, sys, tempfile
from contextlib import redirect_stdout
from pathlib import Path

from recommendation.repositories.connections import load_environment
from recommendation.terminal.__main__ import Terminal

SECRET = re.compile(r"(postgres(ql)?(\+\w+)?://|bolt://|neo4j(\+s)?://|password|api[_-]?key|sk-[A-Za-z0-9]{8})", re.I)


def criteria(budget=3000, district="D05"):
    return [{"criterion_id": "budget", "field": "price", "operator": "lte", "value": budget, "unit": "SGD/month",
             "strength": "hard"},
            {"criterion_id": "bedrooms", "field": "bedrooms", "operator": "gte", "value": 2, "strength": "hard"},
            {"criterion_id": "district", "field": "district_code", "operator": "in", "value": [district],
             "strength": "soft", "weight": 1}]


GEO_CONFLICT = {"criterion_id": "geo", "field": "distance", "operator": "lte", "value": 5000, "unit": "m",
                "strength": "hard", "distance_type": "straight_line", "destination_id": "URA:AREA:CLEMENTI",
                "destination_name": "Bedok"}


class Driver:
    def __init__(self, terminal, tmp):
        self.t, self.tmp, self.parts = terminal, tmp, []

    def __call__(self, line, note=None):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            if note:
                print(f"# {note}")
            print(f"recommendation> {line}")
            try:
                self.t.command(line)
            except Exception as e:  # printed like the interactive loop does
                detail = getattr(e, "detail", None)
                print(f"{detail.code}: {detail.message}" if detail else str(e))
        text = buffer.getvalue().replace(str(self.tmp.resolve()), "<tmp>").replace(str(self.tmp), "<tmp>")
        sys.stdout.write(text)
        self.parts.append(text)
        return text

    def file(self, name, content):
        path = self.tmp / name
        path.write_text(json.dumps(content, indent=2))
        return path


def scenario(run, data_mode, limit):
    t = run.t
    run(f"requirements @{run.file('requirements.json', {'listing_type': 'RENT', 'criteria': criteria()})}",
        "1. First recommendation (fixed seed; session result limit)")
    run("show-config selection")
    if limit:
        run(f"set-config recommendation.default_result_limit {limit}")
    run("set-seed p6-transcript")
    run("validate-input")
    run("recommend")
    first = t.response
    run("show-pool --page 1", "2. Inspection of this run's retained data")
    run("show-resolution")
    run("show-scores --top 5")
    run("show-cold-start")
    run("show-selection")
    run(f"inspect-listing {first.recommendations[0].listing.listing_id}", "single-listing inspection (retained)")
    excluded = "fixture-11"
    if data_mode == "database":  # one live listing that fails the budget (a read-only full read, once)
        pool = {r.listing_id for r in t.run_dispatcher.pool}
        excluded = next((r.listing_id for r in sorted(t.service.listing.retrieve(), key=lambda r: r.listing_id)
                         if r.listing_id not in pool and r.price is not None and r.price > 3000), None)
    if excluded:
        run(f"inspect-listing {excluded}", "listing outside the pool (recomputed, labelled)")
    feedback = run.tmp / "feedback.json"
    run(f"feedback-template {run.tmp / 'template.json'}", "3. Feedback re-rank (orchestrator-style full snapshot)")
    template = json.loads((run.tmp / "template.json").read_text())
    for n, record in enumerate(template["previous_round"]["browsing"]):
        record["duration_seconds"] = 150 if n == 0 else 0
    feedback.write_text(json.dumps(template, indent=2))
    run(f"feedback @{feedback}")
    run("show-input")
    run("recommend")
    run("recommend", "4. Config comparison with the fixed seed: same settings + seed, new request_id")
    run("set-config cold_start.enabled false", "same seed, cold start disabled (only the settings differ)")
    run("recommend")
    # Weight comparison under the same fixed seed (plan: 可固定测试种子比较不同权重; P6 review): the
    # same previous-round feedback is staged for both runs and only the fusion weights differ.
    weights = run.tmp / "weights-feedback.json"
    run(f"feedback-template {weights}", "same seed, same feedback, different fusion weights")
    template = json.loads(weights.read_text())
    for n, record in enumerate(template["previous_round"]["browsing"]):
        record["duration_seconds"] = 150 if n == 0 else 0
    weights.write_text(json.dumps(template, indent=2))
    for value in ('{"soft_weight":0.6,"behavior_weight":0.4}', '{"soft_weight":0.0,"behavior_weight":1.0}'):
        run(f"set-config ranking {value}")
        run(f"feedback @{weights}")
        run("recommend")
        run("show-scores --top 5")
    run("reset-config")
    run(f"requirements @{run.file('hard.json', {'listing_type': 'RENT', 'criteria': criteria(budget=2500)})}",
        "5. Cache: hard-criteria change rebuilds the pool (miss)")
    if limit:
        run(f"set-config recommendation.default_result_limit {limit}")
    run("recommend")
    body = {"listing_type": "RENT", "criteria": criteria()[:1] + [GEO_CONFLICT]}
    run(f"requirements @{run.file('ambiguous.json', body)}", "6. Entity ambiguity (ID and name name different entities)")
    run("recommend")
    run("show-resolution")
    run(f"requirements @{run.file('requirements.json', {'listing_type': 'RENT', 'criteria': criteria()})}",
        "7. Scripted step mode (intermediate results only)")
    run("step rank_candidates")
    run("run-until build_pool")
    run("step")
    run("set-seed other-seed")
    run("run-until final_gate")
    run("show-selection")
    run("step reset")
    run(f"replay {first.request_id}", "8. Original-request replay (stored response)")
    run("show-trace", "the newest trace is the replay's (replay gate only)")
    run(f"export {run.tmp / 'export'}", "9. Export of the latest committed run")
    run("show-trace --debug")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("data_mode", choices=["fixture", "database"])
    parser.add_argument("out", nargs="?")
    parser.add_argument("--limit", type=int, default=None,
                        help="session recommendation.default_result_limit (fixture default 3)")
    args = parser.parse_args()
    load_environment()
    limit = args.limit if args.limit is not None else (3 if args.data_mode == "fixture" else None)
    with tempfile.TemporaryDirectory(prefix="p6-terminal-") as directory:
        tmp = Path(directory)
        terminal = Terminal(argparse.Namespace(
            state_path=str(tmp / "state.sqlite"), conversation_id=f"p6-{args.data_mode}-transcript",
            data_mode=args.data_mode, controller="scripted", brief=True))
        run = Driver(terminal, tmp)
        header = (f"# P6 terminal transcript: data-mode={args.data_mode} controller=scripted (no LLM) "
                  f"state=<tmp>/state.sqlite (temporary) settings={terminal.config.source} "
                  f"(config_hash {terminal.config.config_hash[:12]})\n")
        run.parts.append(header)
        try:
            scenario(run, args.data_mode, limit)
        finally:
            if terminal.service:
                terminal.service.close()
            else:
                terminal.store.close()
    text = "".join(run.parts)
    leaks = SECRET.findall(text)
    if leaks:
        raise SystemExit(f"refusing to save: secret-like text found ({len(leaks)} match(es))")
    if args.out:
        Path(args.out).write_text(text)
        print(f"\n[saved {len(text)} bytes to {args.out}]", file=sys.stderr)


if __name__ == "__main__":
    main()
