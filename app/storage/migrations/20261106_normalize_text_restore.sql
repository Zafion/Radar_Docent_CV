-- DR-01: apply in one client transaction. No data/index/extension is removed.
-- Resolve the actual extension namespace; public is the application's existing
-- normalize_text namespace, not an assumption about where unaccent is installed.
DO $migration$
DECLARE
    extension_schema text;
BEGIN
    SELECT n.nspname INTO extension_schema
    FROM pg_catalog.pg_extension e
    JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace
    WHERE e.extname = 'unaccent';
    IF extension_schema IS NULL THEN
        RAISE EXCEPTION 'normalize_text requires the unaccent extension'
            USING ERRCODE = '55000';
    END IF;

    EXECUTE pg_catalog.format($definition$
        CREATE OR REPLACE FUNCTION public.normalize_text(value text)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        SET search_path = pg_catalog
        AS $body$
            SELECT pg_catalog.btrim(
                pg_catalog.regexp_replace(
                    pg_catalog.lower(%I.unaccent(%L::pg_catalog.regdictionary, coalesce(value, ''))),
                    '\s+',
                    ' ',
                    'g'
                )
            );
        $body$;
    $definition$, extension_schema,
        pg_catalog.format('%I.unaccent', extension_schema));
END;
$migration$;
