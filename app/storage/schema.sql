CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE OR REPLACE FUNCTION normalize_text(value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT btrim(
        regexp_replace(
            lower(unaccent(coalesce(value, ''))),
            '\s+',
            ' ',
            'g'
        )
    );
$$;

CREATE TABLE IF NOT EXISTS sources (
    id BIGSERIAL PRIMARY KEY,
    source_key TEXT NOT NULL UNIQUE,
    source_url TEXT NOT NULL,
    label TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES sources(id),
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    discovered_assets_count INTEGER NOT NULL DEFAULT 0,
    downloadable_assets_count INTEGER NOT NULL DEFAULT 0,
    new_versions_count INTEGER NOT NULL DEFAULT 0,
    known_versions_count INTEGER NOT NULL DEFAULT 0,
    duplicate_assets_count INTEGER NOT NULL DEFAULT 0,
    non_downloadable_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS document_versions (
    id BIGSERIAL PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    original_filename TEXT NOT NULL,
    stored_filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
    content_type TEXT,
    size_bytes BIGINT NOT NULL,
    downloaded_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS assets (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES sources(id),
    sync_run_id BIGINT NOT NULL REFERENCES sync_runs(id),
    asset_role TEXT NOT NULL,
    title TEXT NOT NULL,
    section TEXT,
    publication_label TEXT,
    publication_date_text TEXT,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    is_downloadable BOOLEAN NOT NULL DEFAULT TRUE,
    document_version_id BIGINT REFERENCES document_versions(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS document_parse_runs (
    id BIGSERIAL PRIMARY KEY,
    document_version_id BIGINT NOT NULL REFERENCES document_versions(id),
    parser_key TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    rows_extracted INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS documents (
    id BIGSERIAL PRIMARY KEY,
    document_version_id BIGINT NOT NULL UNIQUE REFERENCES document_versions(id),
    source_id BIGINT NOT NULL REFERENCES sources(id),
    doc_family TEXT NOT NULL,
    title TEXT,
    document_date_text TEXT,
    document_date_iso TEXT,
    list_scope TEXT,
    notes TEXT,
    parsed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS offered_positions (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id),
    source_type TEXT NOT NULL,
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
    hours_value DOUBLE PRECISION,
    is_itinerant BOOLEAN,
    valenciano_required_text TEXT,
    position_type TEXT,
    composition TEXT,
    observations TEXT,
    raw_row_text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS award_results (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id),
    list_scope TEXT NOT NULL,
    body_code TEXT,
    body_name TEXT,
    specialty_code TEXT,
    specialty_name TEXT,
    order_number INTEGER,
    person_display_name TEXT NOT NULL,
    person_name_normalized TEXT NOT NULL,
    status TEXT NOT NULL,
    raw_block_text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS award_assignments (
    id BIGSERIAL PRIMARY KEY,
    award_result_id BIGINT NOT NULL REFERENCES award_results(id),
    assignment_kind TEXT,
    locality TEXT,
    center_code TEXT,
    center_name TEXT,
    position_specialty_code TEXT,
    position_specialty_name TEXT,
    position_code TEXT,
    hours_text TEXT,
    hours_value DOUBLE PRECISION,
    petition_text TEXT,
    petition_number INTEGER,
    request_type TEXT,
    matched_offered_position_id BIGINT REFERENCES offered_positions(id),
    raw_assignment_text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS difficult_coverage_positions (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id),
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
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS difficult_coverage_candidates (
    id BIGSERIAL PRIMARY KEY,
    position_id BIGINT NOT NULL REFERENCES difficult_coverage_positions(id),
    row_number INTEGER,
    is_selected BOOLEAN NOT NULL DEFAULT FALSE,
    last_name_1 TEXT,
    last_name_2 TEXT,
    first_name TEXT,
    full_name TEXT NOT NULL,
    full_name_normalized TEXT NOT NULL,
    registration_datetime_text TEXT,
    registration_code_or_bag_order TEXT,
    petition_text TEXT,
    petition_number INTEGER,
    has_master_text TEXT,
    valenciano_requirement_text TEXT,
    adjudication_group_text TEXT,
    assigned_position_code TEXT,
    raw_row_text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    full_address TEXT,
    source_filename TEXT,
    generic_name_es TEXT,
    generic_name_val TEXT,
    specific_name TEXT,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS centers_catalog_sync_runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    cod_provincia TEXT,
    source_url TEXT NOT NULL,
    endpoint_url TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    json_path TEXT,
    xlsx_path TEXT,
    sha256_path TEXT,
    sha256_value TEXT,
    token_refresh_attempted BOOLEAN NOT NULL DEFAULT FALSE,
    downloaded_file_name TEXT,
    downloaded_mime_type TEXT,
    downloaded_size_bytes BIGINT,
    imported_rows INTEGER,
    centers_before INTEGER,
    centers_after INTEGER,
    changed BOOLEAN NOT NULL DEFAULT FALSE,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id BIGSERIAL PRIMARY KEY,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh_key TEXT NOT NULL,
    auth_key TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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

CREATE INDEX IF NOT EXISTS idx_centers_catalog_sync_runs_created_at
    ON centers_catalog_sync_runs(created_at);

CREATE INDEX IF NOT EXISTS idx_centers_catalog_sync_runs_status
    ON centers_catalog_sync_runs(status);

CREATE INDEX IF NOT EXISTS idx_push_subscriptions_active
    ON push_subscriptions(is_active);

CREATE INDEX IF NOT EXISTS idx_award_results_person_display_name_norm_trgm
    ON award_results
    USING gin (normalize_text(person_display_name) gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_difficult_candidates_full_name_norm_trgm
    ON difficult_coverage_candidates
    USING gin (normalize_text(full_name) gin_trgm_ops);