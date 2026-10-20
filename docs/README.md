# Project guide

For the current local database, start with [SQLite import and manual testing](testing/sqlite-import.md). The earlier [CSV setup guide](testing/csv-sample.md) remains available for a separate sample-only installation.

## Where things belong

```text
src/                         TypeScript multi-agent runtime
api/                         Python HTTP entry point
tests/                       Runtime and HTTP API tests
dataPreprocessing/           Data import, cleaning, SQL and Neo4j code
  tests/                     Data pipeline tests
  compose.yaml               Local PostgreSQL and Neo4j services
data/
  exports/                   Checked-in CSV samples
  datasets/                  Geographic reference data
dev/
  recommendationSubagent/    Standalone recommendation implementation
    examples/                Requirement inputs and runnable examples
    tests/                   Recommendation tests
  sharedContract/            Shared recommendation contracts and schemas
docs/
  testing/                   Manual testing instructions
  preprocessing/             Database guides and migration history
  recommendation/            Recommendation acceptance records
```

The recommendation module is still separate from the A–D agent runtime. Its `dev/` package location remains intentional until integration; run it using the existing `python -m dev.recommendationSubagent...` commands.

## Choose a guide

| Task | Guide |
| --- | --- |
| Run the small CSV test | [CSV test](testing/csv-sample.md) |
| Understand or configure recommendations | [Recommendation module](../dev/recommendationSubagent/README.md) |
| Set up PostgreSQL or query listings | [PostgreSQL](preprocessing/postgresql.md) |
| Set up Neo4j or query geographic evidence | [Neo4j](preprocessing/neo4j.md) |
| Find preprocessing entry points | [Preprocessing module](../dataPreprocessing/README.md) |
| Understand the multi-agent architecture | [Root README](../README.md) |
| Review test results and remaining limitations | [Acceptance notes](recommendation/acceptance-notes.md) |
| Understand the earlier `data/` package move | [Migration history](preprocessing/migration.md) |

## Generated files and local settings

`.venv/`, `node_modules/`, `dist/`, `__pycache__/`, `.pytest_cache/`, and `.runtime/` directories hold installed dependencies, build output, caches or local test results. VS Code hides them through [workspace settings](../.vscode/settings.json); they remain on disk and can be accessed from the terminal. Change `files.exclude` in those settings to show them again.

Local credentials remain in the ignored root `.env`. CSV data stays in `data/exports/`; recommendation conversation state and acceptance output stay in `dev/recommendationSubagent/.runtime/`. Docker database storage is managed by the existing Compose volumes.

## Documentation relocation

Only guide locations changed in this cleanup. Source packages, database configuration, sample inputs and terminal commands retain their existing locations.

| Previous guide | Current guide |
| --- | --- |
| `dataPreprocessing/CSV_SAMPLE.md` | `docs/testing/csv-sample.md` |
| `dataPreprocessing/NEO4J.md` | `docs/preprocessing/neo4j.md` |
| PostgreSQL content in `dataPreprocessing/README.md` | `docs/preprocessing/postgresql.md` |
| `dataPreprocessing/MIGRATION.md` | `docs/preprocessing/migration.md` |
| `dev/recommendationSubagent/ASSUMPTIONS.md` | `docs/recommendation/acceptance-notes.md` |

Each code module keeps its own README for navigation and usage. Put cross-module setup guides, manual test walkthroughs and historical acceptance notes under `docs/`.

Data-folder cleanup: empty `data/data`, `data/api` and `data/src` trees were removed. The loose `sample_export.csv` now lives in `data/exports/`. Preprocessing reports and local runtime files now live in `dataPreprocessing/.runtime/`; Docker volumes are unchanged.
