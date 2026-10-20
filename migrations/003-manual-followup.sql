-- User-supplied correspondence is deliberately separate from provider webhooks.
-- It must never open a WhatsApp service window or imply provider delivery.
CREATE TABLE IF NOT EXISTS manual_followup_replies (
  id UUID PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES session_domain_state(session_id),
  action_id UUID NOT NULL REFERENCES workflow_actions(id),
  text TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'user_pasted' CHECK (source='user_pasted'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  analysis JSONB,
  analysis_state TEXT NOT NULL DEFAULT 'PENDING',
  analysis_started_at TIMESTAMPTZ,
  analysis_revision INTEGER NOT NULL DEFAULT 0,
  next_action_id UUID REFERENCES workflow_actions(id)
);
CREATE INDEX IF NOT EXISTS manual_followup_replies_session ON manual_followup_replies(session_id,created_at);
