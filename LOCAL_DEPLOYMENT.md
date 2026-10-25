# Running the English edition

This project is the independent sibling `Multi-Agent-Assistance-System-English`. The Chinese source and application remain separate. English startup reads the existing local property snapshot; it does not rebuild the shared Neo4j listing graph or recreate the Chinese project's data containers.

## This workstation

1. Open Docker Desktop and wait for its engine.
2. In PowerShell, change to `D:\Real Estate Agent\Multi-Agent-Assistance-System-English`.
3. Run `.\scripts\start-app.ps1 -SkipDataImport`.
4. Open http://127.0.0.1:8020/ (Chinese remains on 8010).

The English `.env` points to conversation database `multi_agent_assistance_en`, graph3011, data8099, retrieval8101 and native8102. It contains local provider/database credentials and must remain ignored by Git. Native SQLite state and PID records are local to this project. `RETRIEVAL_NAMESPACE=english` prevents the English 42-template index from replacing the Chinese index. Curated English knowledge uses a separately hashed generation.

## Fresh installation

Install Node.js and a supported Python, run `npm ci`, create `.venv` and install `requirements-dev.txt`. Copy `.env.example` to the ignored `.env` and configure actual credentials. The bundled `data/snapshots/propertyguru-2026-10-09.zip` supplies the same listing snapshot as the Chinese version; preserve its hashes and use the existing data import tools to initialise the shared PostgreSQL/Neo4j snapshot before English startup.

The current local deployment reuses `real-estate-agent-postgres-1`, `real-estate-agent-neo4j-1` and `real-estate-agent-chroma-1`. English startup checks/starts these existing containers rather than recreating them. Create the separate English conversation database with the configured conversation role as owner; optionally create `multi_agent_assistance_en_test` for isolated persistence tests. PropertyGuru and Neo4j credentials refer to the shared listing data, not the English conversation database. Do not point conversation migrations at the PropertyGuru database.

## Updates and shutdown

Run `npm run build` after backend source changes. Use `.\scripts\restart-component.ps1 -Name graph` to load them, or stop/start this edition. `scripts/stop-app.ps1` only stops this directory's recorded PID trees after checking creation times. It leaves data containers and the Chinese application running. The frontend uses no-store cache headers; reload the English tab after UI changes.

## Verification

Check `/health` and `/api/config` on8020, plus health on3011/8099/8101/8102. The native health should remain controller=live for normal product use. Local regression tests and recorded browser tests cost no model calls. Run paid tests only under AGENTS.md’s explicit cost constraints. Draft sending defaults to the manual/simulation flow unless live integrations are separately configured and approved.
