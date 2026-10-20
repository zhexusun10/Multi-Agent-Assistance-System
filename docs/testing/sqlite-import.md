# Local SQLite import — 20 October 2026

`data/propertyguru.db` was imported into the existing local PostgreSQL `propertyguru` database, then projected into the configured local Neo4j database. The SQLite source was opened read-only.

The import updates matching IDs and preserves existing IDs absent from the source. This installation therefore contains the archived snapshot **plus 269 extra CSV sample listings**, not the exact fixed snapshot.

| Records | SQLite source | PostgreSQL after import |
| --- | ---: | ---: |
| Listings | 21,431 | 21,700 |
| Agents | 4,657 | 4,657 |
| Images | 418,700 | 418,700 |
| Price history | 0 | 0 |

Every source listing ID is present in PostgreSQL. Neo4j contains the same 21,700 listing IDs; projected listing fields and dimension relationships passed comparison with PostgreSQL. Geographic checks found no incorrect distances, missing proximity edges or duplicate proximity edges when allowing both source timestamps to be unknown.

The strict fixed-snapshot verifier does **not** pass for this merged dataset: its expected counts differ, and 31,821 proximity edges from retained CSV records have unknown source update dates. Those dates were not fabricated. These are archived records, not a verified current availability feed.

Local verification reports:

- `dataPreprocessing/.runtime/sqlite-postgres-verification.json`
- `dataPreprocessing/.runtime/sqlite-graph-verification.json`

All eight recommendation acceptance steps passed with the real databases and scripted controller: connections, initial search, geographic search, feedback, duplicate feedback, tighter requirements, trace inspection and state restart. Initial search assessed 3,063 candidates and returned four verified listings; geographic search assessed 21,700 and returned four. Report: `dev/recommendationSubagent/.runtime/sqlite-database-acceptance/acceptance.json`. No model calls were made by this test.

Use the existing recommendation terminal with a new conversation ID:

```bash
.venv/bin/python -m dev.recommendationSubagent.terminal \
  --data-mode database --controller scripted \
  --conversation-id sqlite-manual-001
```

Then enter each command separately:

```text
check-connections
requirements @dev/recommendationSubagent/examples/requirements-csv.json
recommend
```

The requirements file still describes a budget of S$4,000/month, at least two bedrooms and a soft D05 preference; its name does not restrict searches to CSV records. Results now come from the expanded database.

Do not rerun the small CSV importer against this populated database; its sample-only guard will refuse it. Do not use `prepare-postgres` to overwrite it; that command requires an empty database or the exact fixed snapshot.
