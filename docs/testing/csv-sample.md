# Small CSV database test

**Current local database:** on 20 October 2026, the SQLite archive was added to the sample database. See [SQLite import and current test instructions](sqlite-import.md). The sample-only import steps below describe the earlier setup; do not rerun them against the expanded database.

This path uses the checked-in rental CSV and places JSON. It does **not** require `data/propertyguru.db`, does not fetch new data, and does not claim the full 21,431-listing snapshot.

For this checkout the inputs contain 475 rentals and 3,716 places. Imports are limited to local `propertyguru`/`propertyguru_sample` PostgreSQL and local Neo4j. Existing unmarked data is refused. An identical import can be rerun; source files are never changed. Do not run two import processes at once.

From the repository root, with Docker Desktop running and the local `.env` configured:

```bash
# Start the existing database services if necessary.
docker compose --env-file .env -f dataPreprocessing/compose.yaml up -d --wait

# Optional preview: validate the files without touching either database.
.venv/bin/python -m dataPreprocessing.csv_sample

# Import into empty local databases or resume this exact marked sample.
.venv/bin/python -m dataPreprocessing.csv_sample --apply
```

PostgreSQL gets rental property records; agent/image/history tables are empty in this sample workflow. Neo4j gets their projection, reviewed local places and proximity relationships. Ten negative bedroom sentinels in this CSV are retained as unknown. Reports contain source hashes and explicitly set `full_snapshot: false`; the report is `dataPreprocessing/.runtime/csv-sample-import.json`. The SQL import is transactional; interrupted graph imports can resume using identical source files. No unrelated database is cleared.

**Do not use `prepare-postgres` or the full-snapshot `verify` command on this partial dataset.** Those commands intentionally require the complete archived dataset. Use the recommendation acceptance command below for sample testing.

## Manual test now

```bash
.venv/bin/python -m dev.recommendationSubagent.terminal --data-mode database --controller scripted --conversation-id csv-manual-001
```

At the prompt:

```text
check-connections
requirements @dev/recommendationSubagent/examples/requirements-csv.json
recommend
feedback-example /tmp/csv-feedback.json
feedback @/tmp/csv-feedback.json
recommend
show-state
show-trace
requirements @dev/recommendationSubagent/examples/geography-csv.json
recommend
exit
```

The scripted controller exercises real PostgreSQL/Neo4j repositories, validation, ranking, feedback and state without a model API key. Edit the generated feedback durations before submitting if desired. Restart with the same conversation ID to resume committed state.

`requirements-csv.json` is a separate explicit sample: budget <= SGD 4,000/month, bedrooms >= 2, soft D05 preference. The original SGD 3,000 example remains unchanged and produces no verified matches in this dataset. `geography-csv.json` uses an actual School ID queried from this graph and a 1,500 m straight-line radius. Regenerate for another graph with:

```bash
.venv/bin/python -m dev.recommendationSubagent.examples.live_geography --near-listings > /tmp/geography-sample.json
```

## Repeatable database test

```bash
.venv/bin/python -m dev.recommendationSubagent.terminal.acceptance \
  --data-mode database --controller scripted \
  --requirements dev/recommendationSubagent/examples/requirements-csv.json \
  --geography dev/recommendationSubagent/examples/geography-csv.json \
  --output dev/recommendationSubagent/.runtime/csv-database-acceptance
```

The database test passed on 19 October 2026: 475 SQL and graph listing IDs matched, 3,716 places and 53,050 NEAR_PLACE relationships were imported. Initial search and geographic search returned four verified listings each. The geographic run read 46 criterion-evidence records from NEAR_PLACE (two criteria share a destination); remaining distances were explicitly labelled coordinate calculations. Feedback, duplicate events, a stricter cap and state restart passed. Report: `dev/recommendationSubagent/.runtime/csv-database-acceptance/acceptance.json`.

Model calls were **not** tested: no API key was configured. Once configured, use `--controller live` for the separate model acceptance step.
