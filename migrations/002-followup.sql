CREATE TABLE IF NOT EXISTS message_outbox(
 execution_key TEXT PRIMARY KEY, action_id UUID NOT NULL REFERENCES workflow_actions(id),session_id TEXT NOT NULL REFERENCES session_domain_state,
 channel TEXT NOT NULL,address TEXT NOT NULL,payload JSONB NOT NULL,state TEXT NOT NULL DEFAULT 'QUEUED',
 provider_message_id TEXT,error_code TEXT,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),started_at TIMESTAMPTZ,updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS outbox_pending ON message_outbox(state,created_at);
CREATE INDEX IF NOT EXISTS outbox_provider_id ON message_outbox(channel,provider_message_id);
CREATE TABLE IF NOT EXISTS followup_events(id BIGSERIAL PRIMARY KEY,session_id TEXT NOT NULL,action_id UUID,kind TEXT NOT NULL,body JSONB NOT NULL,event_key TEXT UNIQUE,created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS followup_events_session ON followup_events(session_id,id);
CREATE TABLE IF NOT EXISTS messaging_inbound(event_key TEXT PRIMARY KEY,channel TEXT NOT NULL,address TEXT NOT NULL,provider_message_id TEXT,reply_to TEXT,body TEXT NOT NULL,action_id UUID,session_id TEXT,received_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS followup_tasks(id UUID PRIMARY KEY,session_id TEXT NOT NULL REFERENCES session_domain_state,action_id UUID NOT NULL REFERENCES workflow_actions(id),kind TEXT NOT NULL,due_at TIMESTAMPTZ NOT NULL,status TEXT NOT NULL DEFAULT 'OPEN',note TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 1,reminded_revision INTEGER NOT NULL DEFAULT 0,created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS followup_due ON followup_tasks(status,due_at);
ALTER TABLE workflow_artifacts ALTER COLUMN source_request_id DROP NOT NULL;
ALTER TABLE workflow_artifacts ADD COLUMN IF NOT EXISTS title TEXT NOT NULL DEFAULT '租房需求与推荐总结';
ALTER TABLE workflow_artifacts ADD COLUMN IF NOT EXISTS artifact_type TEXT NOT NULL DEFAULT 'recommendation';
CREATE TABLE IF NOT EXISTS conversation_log(id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES session_domain_state,role TEXT NOT NULL,body TEXT NOT NULL,at TIMESTAMPTZ NOT NULL);
CREATE INDEX IF NOT EXISTS conversation_log_day ON conversation_log(session_id,at);
CREATE TABLE IF NOT EXISTS messaging_status_receipts(event_key TEXT PRIMARY KEY,channel TEXT NOT NULL,message_id TEXT NOT NULL,body JSONB NOT NULL,processed BOOLEAN NOT NULL DEFAULT false,received_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS integration_offsets(integration TEXT PRIMARY KEY,last_update BIGINT NOT NULL DEFAULT -1);
ALTER TABLE messaging_inbound ADD COLUMN IF NOT EXISTS provider_at TIMESTAMPTZ;
