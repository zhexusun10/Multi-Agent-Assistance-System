ALTER TABLE session_domain_state ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS listing_viewing_intents(
 sequence BIGSERIAL PRIMARY KEY, session_id TEXT NOT NULL REFERENCES session_domain_state,
 action_id UUID NOT NULL REFERENCES workflow_actions, listing_id TEXT NOT NULL,
 source_request_id UUID NOT NULL, requirements_version INTEGER NOT NULL, revision INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('active','withdrawn')), slots JSONB,
 recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(session_id,action_id,listing_id,revision,state));
CREATE INDEX IF NOT EXISTS viewing_intents_session_cutoff ON listing_viewing_intents(session_id,recorded_at,sequence);
