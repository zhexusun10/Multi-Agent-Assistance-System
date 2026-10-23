-- Recommendation contract 2.0: the orchestrator sends a COMPLETE feedback snapshot with every
-- native call (previous adopted round's browsing + all counted VIEWING appointment signals +
-- cold-start exit state). It is built once inside the agent-A apply transaction and persisted
-- here and in agent B's queued body, so retries resend the identical body (never recomputed).
ALTER TABLE recommendation_calls ADD COLUMN IF NOT EXISTS feedback_snapshot JSONB;
-- Cold-start exit state managed by the orchestrator ({exited, exited_pool_key}); updated only when
-- an ADOPTED native response reports selection.cold_start.evaluated && exit_conditions_met.
ALTER TABLE session_domain_state ADD COLUMN IF NOT EXISTS recommendation_cold_start JSONB;
CREATE INDEX IF NOT EXISTS recommendation_calls_session ON recommendation_calls(session_id,created_at);
CREATE INDEX IF NOT EXISTS workflow_actions_session ON workflow_actions(session_id);
CREATE INDEX IF NOT EXISTS listing_feedback_round ON listing_feedback(session_id,(body->'payload'->>'recommendation_request_id'));
