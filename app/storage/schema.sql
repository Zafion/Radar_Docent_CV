PRAGMA foreign_keys = ON;

BEGIN;

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT NOT NULL UNIQUE,
    source_url TEXT NOT NULL,
    label TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL, -- success, partial, failed
    discovered_assets_count INTEGER NOT NULL DEFAULT 0,
    downloadable_assets_count INTEGER NOT NULL DEFAULT 0,
    new_versions_count INTEGER NOT NULL DEFAULT 0,
    known_versions_count INTEGER NOT NULL DEFAULT 0,
    duplicate_assets_count INTEGER NOT NULL DEFAULT 0,
    non_downloadable_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    sync_run_id INTEGER NOT NULL,
    asset_role TEXT NOT NULL,
    title TEXT NOT NULL,
    section TEXT,
    publication_label TEXT,
    publication_date_text TEXT,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    is_downloadable INTEGER NOT NULL DEFAULT 1,
    document_version_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES sources(id),
    FOREIGN KEY (sync_run_id) REFERENCES sync_runs(id),
    FOREIGN KEY (document_version_id) REFERENCES document_versions(id)
);

CREATE TABLE IF NOT EXISTS document_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL UNIQUE,
    original_filename TEXT NOT NULL,
    stored_filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
    content_type TEXT,
    size_bytes INTEGER NOT NULL,
    downloaded_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS document_parse_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_version_id INTEGER NOT NULL,
    parser_key TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    status TEXT NOT NULL, -- success, failed
    started_at TEXT NOT NULL,
    finished_at TEXT,
    rows_extracted INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_version_id) REFERENCES document_versions(id)
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_version_id INTEGER NOT NULL UNIQUE,
    source_id INTEGER NOT NULL,
    doc_family TEXT NOT NULL,              -- final_award_listing, offered_positions, difficult_coverage_provisional, resolution_text
    title TEXT,
    document_date_text TEXT,
    document_date_iso TEXT,                -- nullable hasta que normalicemos fecha
    list_scope TEXT,                       -- maestros, secundaria_otros, dificil_cobertura...
    notes TEXT,
    parsed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_version_id) REFERENCES document_versions(id),
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS offered_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    source_type TEXT NOT NULL,             -- inicio_curso, continua, dificil_cobertura
    body_code TEXT,
    body_name TEXT,
    specialty_code TEXT,
    specialty_name TEXT,
    province TEXT,
    locality TEXT,
    center_code TEXT,
    center_name TEXT,
    position_code TEXT,
    hours_text TEXT,
    hours_value REAL,
    is_itinerant INTEGER,                  -- 0/1/null
    valenciano_required_text TEXT,         -- SI / NO / null
    position_type TEXT,                    -- VACANTE, Sust. Ind., Sust. Det., etc.
    composition TEXT,
    observations TEXT,
    raw_row_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id)
);

CREATE TABLE IF NOT EXISTS award_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    list_scope TEXT NOT NULL,              -- maestros / secundaria_otros
    body_code TEXT,
    body_name TEXT,
    specialty_code TEXT,
    specialty_name TEXT,
    order_number INTEGER,
    person_display_name TEXT NOT NULL,
    person_name_normalized TEXT NOT NULL,
    status TEXT NOT NULL,                  -- Desactivat, Ha participat, No ha participat, No adjudicat, Adjudicat
    raw_block_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id)
);

CREATE TABLE IF NOT EXISTS award_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    award_result_id INTEGER NOT NULL,
    assignment_kind TEXT,                  -- VACANT, SUBSTITUCIÓ DETERMINADA, SUBSTITUCIÓ INDETERMINADA
    locality TEXT,
    center_code TEXT,
    center_name TEXT,
    position_specialty_code TEXT,
    position_specialty_name TEXT,
    position_code TEXT,
    hours_text TEXT,
    hours_value REAL,
    petition_text TEXT,
    petition_number INTEGER,
    request_type TEXT,
    matched_offered_position_id INTEGER,
    raw_assignment_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (award_result_id) REFERENCES award_results(id),
    FOREIGN KEY (matched_offered_position_id) REFERENCES offered_positions(id)
);

CREATE TABLE IF NOT EXISTS difficult_coverage_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    body_code TEXT,
    body_name TEXT,
    specialty_code TEXT,
    specialty_name TEXT,
    position_code TEXT NOT NULL,
    center_code TEXT,
    center_name TEXT,
    locality TEXT,
    num_participants INTEGER,
    sorteo_number TEXT,
    registro_superior TEXT,
    registro_inferior TEXT,
    raw_header_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id)
);

CREATE TABLE IF NOT EXISTS difficult_coverage_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    row_number INTEGER,
    is_selected INTEGER NOT NULL DEFAULT 0,  -- 1 cuando aparece con -->
    last_name_1 TEXT,
    last_name_2 TEXT,
    first_name TEXT,
    full_name TEXT NOT NULL,
    full_name_normalized TEXT NOT NULL,
    registration_datetime_text TEXT,
    registration_code_or_bag_order TEXT,
    petition_text TEXT,
    petition_number INTEGER,
    has_master_text TEXT,                   -- S / N
    valenciano_requirement_text TEXT,       -- según columna REQ. VAL
    adjudication_group_text TEXT,           -- G. ADJ.
    assigned_position_code TEXT,            -- P. Adj.
    raw_row_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (position_id) REFERENCES difficult_coverage_positions(id)
);

CREATE TABLE IF NOT EXISTS centers (
    center_code TEXT PRIMARY KEY,
    denomination TEXT NOT NULL,
    regime TEXT,
    street_type TEXT,
    street_name TEXT,
    street_number TEXT,
    postal_code TEXT,
    locality TEXT,
    province TEXT,
    comarca TEXT,
    phone TEXT,
    fax TEXT,
    latitude REAL,
    longitude REAL,
    full_address TEXT,
    source_filename TEXT,
    generic_name_es TEXT,
    generic_name_val TEXT,
    specific_name TEXT,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);


CREATE INDEX IF NOT EXISTS idx_sources_key
    ON sources(source_key);

CREATE INDEX IF NOT EXISTS idx_sync_runs_source_id
    ON sync_runs(source_id);

CREATE INDEX IF NOT EXISTS idx_assets_source_run
    ON assets(source_id, sync_run_id);

CREATE INDEX IF NOT EXISTS idx_assets_canonical_url
    ON assets(canonical_url);

CREATE INDEX IF NOT EXISTS idx_assets_role
    ON assets(asset_role);

CREATE INDEX IF NOT EXISTS idx_document_versions_sha256
    ON document_versions(sha256);

CREATE INDEX IF NOT EXISTS idx_documents_doc_family
    ON documents(doc_family);

CREATE INDEX IF NOT EXISTS idx_documents_source_id
    ON documents(source_id);

CREATE INDEX IF NOT EXISTS idx_offered_positions_document_id
    ON offered_positions(document_id);

CREATE INDEX IF NOT EXISTS idx_offered_positions_position_code
    ON offered_positions(position_code);

CREATE INDEX IF NOT EXISTS idx_offered_positions_center_code
    ON offered_positions(center_code);

CREATE INDEX IF NOT EXISTS idx_offered_positions_specialty_code
    ON offered_positions(specialty_code);

CREATE INDEX IF NOT EXISTS idx_award_results_document_id
    ON award_results(document_id);

CREATE INDEX IF NOT EXISTS idx_award_results_person_name_norm
    ON award_results(person_name_normalized);

CREATE INDEX IF NOT EXISTS idx_award_results_status
    ON award_results(status);

CREATE INDEX IF NOT EXISTS idx_award_assignments_award_result_id
    ON award_assignments(award_result_id);

CREATE INDEX IF NOT EXISTS idx_award_assignments_position_code
    ON award_assignments(position_code);

CREATE INDEX IF NOT EXISTS idx_difficult_positions_document_id
    ON difficult_coverage_positions(document_id);

CREATE INDEX IF NOT EXISTS idx_difficult_positions_position_code
    ON difficult_coverage_positions(position_code);

CREATE INDEX IF NOT EXISTS idx_difficult_candidates_position_id
    ON difficult_coverage_candidates(position_id);

CREATE INDEX IF NOT EXISTS idx_difficult_candidates_full_name_norm
    ON difficult_coverage_candidates(full_name_normalized);

CREATE INDEX IF NOT EXISTS idx_centers_locality
    ON centers(locality);

CREATE INDEX IF NOT EXISTS idx_centers_province
    ON centers(province);

COMMIT;