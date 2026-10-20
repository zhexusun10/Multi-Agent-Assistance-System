# Preprocessing path migration

| Previous path/import | Current path/import |
| --- | --- |
| `data/core`, `data.core` | `dataPreprocessing/core`, `dataPreprocessing.core` |
| `data/scraper`, `data.scraper` | `dataPreprocessing/scraper`, `dataPreprocessing.scraper` |
| `data/storage`, `data.storage` | `dataPreprocessing/storage`, `dataPreprocessing.storage` |
| `data/graph`, `data.graph` | `dataPreprocessing/graph`, `dataPreprocessing.graph` |
| `data/service`, `data.service` | `dataPreprocessing/service`, `dataPreprocessing.service` |
| `data/main.py`, `data/knowledge_graph.py` | Same filenames under `dataPreprocessing/` |
| `data/tests` | `dataPreprocessing/tests` |
| `data/*.ps1`, `data/open_neo4j_browser.cmd` | Same filenames under `dataPreprocessing/` |
| `data/compose.yaml`, `data/docker/` | `dataPreprocessing/compose.yaml`, `dataPreprocessing/docker/` |
| Data requirements and setup guides | Same filenames under `dataPreprocessing/` |
| `data/datasets`, `data/exports`, `data/propertyguru.db`, `dataPreprocessing/.runtime` | Unchanged asset/runtime locations |
| Root `.env`, build configuration, `src/`, `api/`, reference `sources/` | Unchanged ownership and locations |

`core/paths.py` resolves asset defaults from the repository root. Windows startup scripts retain the original asset/runtime paths. Compose explicitly retains project name `data` so its default named volumes are not changed by moving the YAML file. If your previous deployment supplied a custom Compose project name, keep supplying that same name with `-p`.

No data exports/datasets were rewritten, no ingestion was executed and no live store was populated. Tests use fixtures and isolated stores. Recommendation runtime modules do not import this processing package. Existing external consumers of `data.*` must update their imports; no duplicate compatibility pipeline is retained.
