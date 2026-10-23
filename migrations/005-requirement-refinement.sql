CREATE TABLE IF NOT EXISTS requirement_refinement_plans (
 id UUID PRIMARY KEY, session_id TEXT NOT NULL REFERENCES session_domain_state(session_id),
 source_request_id UUID NOT NULL, source_operation_id UUID NOT NULL, requirements_version INTEGER NOT NULL,
 body JSONB NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','SUBMITTED','DISMISSED','STALE')),
 answers JSONB NOT NULL DEFAULT '[]', answer_summary JSONB NOT NULL DEFAULT '[]',
 submission_id UUID, submitted_operation_id UUID, submission_hash TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(session_id,source_request_id)
);
CREATE INDEX IF NOT EXISTS refinement_session ON requirement_refinement_plans(session_id,created_at);
