"""Multi-Agent Assistance System - Data Layer.

Structured subpackages:
- dataPreprocessing.core: Paths, configuration, and API schema contracts.
- dataPreprocessing.scraper: PropertyGuru crawler, parser, cleaner, and ingestion pipeline.
- dataPreprocessing.storage: Relational PostgreSQL/SQLite models, database engine, repository, migration, and snapshot assertions.
- dataPreprocessing.graph: Neo4j knowledge graph projection, spatial semantics, OSM places, and Neo4j browser helpers.
- dataPreprocessing.service: Read-only HTTP REST API and cross-system data validation.
"""
from dataPreprocessing import core, graph, scraper, service, storage

__all__ = ["core", "scraper", "storage", "graph", "service"]
