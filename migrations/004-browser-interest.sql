-- Browser observations do not change the displayed recommendation or queue agents.
ALTER TABLE listing_feedback ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE recommendation_calls ADD COLUMN IF NOT EXISTS feedback_cutoff TIMESTAMPTZ;
ALTER TABLE recommendation_calls ADD COLUMN IF NOT EXISTS interest_context JSONB;
CREATE TABLE IF NOT EXISTS listing_exposures(
 session_id TEXT NOT NULL REFERENCES session_domain_state, source_request_id UUID NOT NULL,
 listing_id TEXT NOT NULL, tab_id UUID NOT NULL, position INTEGER NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(session_id,source_request_id,listing_id,tab_id));
CREATE TABLE IF NOT EXISTS listing_view_segments(
 segment_id UUID PRIMARY KEY, view_session_id UUID NOT NULL, session_id TEXT NOT NULL REFERENCES session_domain_state,
 source_request_id UUID NOT NULL, requirements_version INTEGER NOT NULL, listing_id TEXT NOT NULL,
 tab_id UUID NOT NULL, verified BOOLEAN NOT NULL, started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_sequence INTEGER NOT NULL DEFAULT 0,
 active_duration_ms INTEGER NOT NULL DEFAULT 0, completed_at TIMESTAMPTZ, end_reason TEXT,
 last_hash TEXT NOT NULL, quality_note TEXT);
CREATE INDEX IF NOT EXISTS listing_view_session_idx ON listing_view_segments(session_id,completed_at);
CREATE TABLE IF NOT EXISTS browser_reading_leases(
 owner_hash TEXT PRIMARY KEY, tab_id UUID NOT NULL, segment_id UUID NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS browser_behavior_receipts(
 session_id TEXT NOT NULL REFERENCES session_domain_state, event_id UUID NOT NULL,
 payload_hash TEXT NOT NULL, response JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(session_id,event_id));
