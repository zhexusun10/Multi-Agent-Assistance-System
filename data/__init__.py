"""Multi-Agent Assistance System - Data Layer.

Structured subpackages:
- data.core: Paths, configuration, and API schema contracts.
- data.scraper: PropertyGuru crawler, parser, cleaner, and ingestion pipeline.
- data.storage: Relational PostgreSQL/SQLite models, database engine, repository, migration, and snapshot assertions.
- data.graph: Neo4j knowledge graph projection, spatial semantics, OSM places, and Neo4j browser helpers.
- data.service: Read-only HTTP REST API and cross-system data validation.
"""
from data import core, graph, scraper, service, storage

__all__ = ["core", "scraper", "storage", "graph", "service"]
