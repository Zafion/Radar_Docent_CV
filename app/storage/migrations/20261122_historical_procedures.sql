-- Documentary procedure evidence; apply only in an explicitly authorized DB.
CREATE TABLE IF NOT EXISTS historical_procedures (
    procedure_key TEXT PRIMARY KEY,
    procedure_kind TEXT NOT NULL,
    procedure_date DATE NOT NULL,
    date_evidence TEXT NOT NULL,
    publication_date DATE,
    publication_date_quality TEXT NOT NULL DEFAULT 'unknown',
    CHECK (publication_date_quality IN ('unknown','official','inferred'))
);
CREATE TABLE IF NOT EXISTS historical_procedure_documents (
    procedure_key TEXT NOT NULL REFERENCES historical_procedures(procedure_key),
    document_id BIGINT NOT NULL REFERENCES documents(id),
    document_role TEXT NOT NULL,
    relationship_evidence TEXT NOT NULL,
    PRIMARY KEY (procedure_key, document_id)
);
CREATE TABLE IF NOT EXISTS historical_unawarded_expert_vacancies (
    document_id BIGINT NOT NULL REFERENCES documents(id),
    center_code TEXT NOT NULL,
    source_position_id TEXT NOT NULL,
    province TEXT NOT NULL,
    locality TEXT NOT NULL,
    center_name TEXT NOT NULL,
    cycle TEXT NOT NULL,
    modules TEXT NOT NULL,
    teaching_hours TEXT NOT NULL,
    total_hours TEXT NOT NULL,
    valencian_source TEXT NOT NULL,
    result_status TEXT NOT NULL CHECK (result_status='unawarded_no_requests'),
    PRIMARY KEY (document_id, center_code, source_position_id)
);
