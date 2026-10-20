# Data preprocessing

Code for importing, cleaning, storing and projecting property data. Run commands from the repository root. Data files live in [`data/`](../data/).

| Folder / entry point | Purpose |
| --- | --- |
| `core/` | Configuration, paths and data contracts |
| `scraper/` | Scraping and cleaning |
| `storage/` | SQL models, repositories and snapshot validation |
| `graph/` | Neo4j projection and geographic evidence |
| `service/` | Read-only data API |
| `tests/` | Preprocessing tests |
| `csv_sample.py` | Import the small checked-in CSV sample |
| `main.py` | SQL import, export and scraping CLI |
| `knowledge_graph.py` | Graph lifecycle CLI |
| `compose.yaml`, `docker/`, `*.ps1`, `*.cmd` | Local database setup and launchers |

## Guides

- [Small CSV setup and manual test](../docs/testing/csv-sample.md) — start here for the current test dataset.
- [PostgreSQL setup and queries](../docs/preprocessing/postgresql.md)
- [Neo4j setup, queries and data API](../docs/preprocessing/neo4j.md)
- [Previous package migration](../docs/preprocessing/migration.md)
