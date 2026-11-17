-- Additive only. Apply exclusively to temporary PostgreSQL until deployment approval.
CREATE TABLE IF NOT EXISTS notification_validation_attestations (
    parse_run_id BIGINT PRIMARY KEY REFERENCES document_parse_runs(id),
    document_id BIGINT NOT NULL REFERENCES documents(id),
    contract_revision TEXT NOT NULL,
    rows_validated INTEGER NOT NULL CHECK (rows_validated > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS notification_document_state (
    document_id BIGINT PRIMARY KEY REFERENCES documents(id),
    document_version_id BIGINT NOT NULL UNIQUE REFERENCES document_versions(id),
    intent TEXT NOT NULL CHECK (intent IN ('live_new','historical_import','reprocess','unknown')),
    canonical_url TEXT,
    first_seen TIMESTAMPTZ NOT NULL,
    official_date DATE,
    date_provenance TEXT,
    corrected_url BOOLEAN NOT NULL DEFAULT FALSE,
    phase TEXT NOT NULL DEFAULT 'detected' CHECK (phase IN ('detected','processing','validated','eligible','held','queued')),
    reason TEXT NOT NULL DEFAULT 'awaiting_validation',
    validation_run_id BIGINT REFERENCES notification_validation_attestations(parse_run_id),
    policy_revision TEXT,
    policy_cutover TIMESTAMPTZ,
    queued_event_key TEXT UNIQUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS notification_state_history (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES notification_document_state(document_id),
    phase TEXT NOT NULL,
    reason TEXT NOT NULL,
    validation_run_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE OR REPLACE FUNCTION trace_notification_state() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='INSERT' OR (NEW.phase,NEW.reason,NEW.validation_run_id)
       IS DISTINCT FROM (OLD.phase,OLD.reason,OLD.validation_run_id) THEN
        INSERT INTO notification_state_history(document_id,phase,reason,validation_run_id)
        VALUES(NEW.document_id,NEW.phase,NEW.reason,NEW.validation_run_id);
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS notification_state_trace ON notification_document_state;
CREATE TRIGGER notification_state_trace AFTER INSERT OR UPDATE ON notification_document_state
FOR EACH ROW EXECUTE FUNCTION trace_notification_state();
CREATE INDEX IF NOT EXISTS idx_notification_state_phase ON notification_document_state(phase,document_id);
CREATE TABLE IF NOT EXISTS notification_delivery_attempts (
    id BIGSERIAL PRIMARY KEY,
    channel TEXT NOT NULL CHECK(channel IN ('telegram','push')),
    queue_id BIGINT NOT NULL,
    event_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('sending','sent','failed','disabled','blocked','uncertain')),
    reason TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    UNIQUE(channel,queue_id)
);
CREATE TABLE IF NOT EXISTS notification_legacy_holds (
    channel TEXT NOT NULL CHECK(channel IN ('telegram','push')),
    queue_id BIGINT NOT NULL,
    reason TEXT NOT NULL DEFAULT 'legacy_without_eligibility',
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(channel,queue_id)
);

CREATE TABLE IF NOT EXISTS notification_delivery_history (
    id BIGSERIAL PRIMARY KEY,
    attempt_id BIGINT NOT NULL REFERENCES notification_delivery_attempts(id),
    state TEXT NOT NULL,
    reason TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION trace_notification_delivery() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO notification_delivery_history(attempt_id,state,reason) VALUES(NEW.id,NEW.state,NEW.reason);
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS notification_delivery_trace ON notification_delivery_attempts;
CREATE TRIGGER notification_delivery_trace AFTER INSERT OR UPDATE ON notification_delivery_attempts
FOR EACH ROW EXECUTE FUNCTION trace_notification_delivery();

ALTER TABLE notification_document_state ADD COLUMN IF NOT EXISTS delivery_hold_reason TEXT;

-- Superset of the prototype constraint; no business/event rows are rewritten.
ALTER TABLE notification_document_state DROP CONSTRAINT IF EXISTS notification_document_state_phase_check;
ALTER TABLE notification_document_state ADD CONSTRAINT notification_document_state_phase_check
    CHECK (phase IN ('detected','processing','validated','eligible','held','queued'));

-- Durable first-ingestion intent closes the import-commit/registration gap.
-- Existing versions remain unknown; no retroactive promotion to live.
ALTER TABLE document_versions ADD COLUMN IF NOT EXISTS notification_ingest_intent TEXT NOT NULL DEFAULT 'unknown'
    CHECK(notification_ingest_intent IN ('live_new','historical_import','reprocess','unknown'));
