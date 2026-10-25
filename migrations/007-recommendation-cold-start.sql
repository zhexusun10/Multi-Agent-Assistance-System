-- Native recommendation cold start (recommendation-owned extension of the main request contract):
-- the orchestrator keeps the exit state ({exited, exited_pool_key}) and sends it as `cold_start`
-- with every native call. Updated only when an ADOPTED native response reports
-- selection.cold_start.evaluated && exit_conditions_met (B-apply transaction).
-- Since the mpd-v1 cold-start detector (2026-10-24) no new exits are written; native decides each round
-- from the score spread D and ignores the sent value. The column and existing rows are kept.
ALTER TABLE session_domain_state ADD COLUMN IF NOT EXISTS recommendation_cold_start JSONB;
