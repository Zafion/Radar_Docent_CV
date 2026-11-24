-- Run only in an explicitly authorized environment. Unattested rows stay NULL.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS validation_status TEXT
    CHECK (validation_status IN ('validated', 'validated_with_limitations', 'needs_validation'));
ALTER TABLE documents ADD COLUMN IF NOT EXISTS validation_parse_run_id BIGINT REFERENCES document_parse_runs(id);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS historical_status TEXT
    CHECK (historical_status IN ('accredited', 'quarantine', 'unknown'));
ALTER TABLE documents ADD COLUMN IF NOT EXISTS publication_date_quality TEXT
    CHECK (publication_date_quality IN ('official', 'inferred', 'unknown'));
