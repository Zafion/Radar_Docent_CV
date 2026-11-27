\set ON_ERROR_STOP on
BEGIN;
DO $check$
BEGIN
  IF current_database() <> 'radar_docent_cv_recovered_20261126' THEN
    RAISE EXCEPTION 'unexpected_database';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='funkcionario_reader'
                 AND rolcanlogin AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) THEN
    RAISE EXCEPTION 'reader_role_not_verified';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
     WHERE p.oid=to_regprocedure('public.normalize_text(text)')
       AND l.lanname='sql' AND p.provolatile='i' AND NOT p.prosecdef
       AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
       AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='6499239416c077c6199df160cfdae5f456602460bacfe15b12c4213d8ab5605e'
  ) THEN RAISE EXCEPTION 'normalize_text_identity_mismatch'; END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
      JOIN pg_depend d ON d.classid='pg_proc'::regclass AND d.objid=p.oid
        AND d.refclassid='pg_extension'::regclass AND d.deptype='e'
      JOIN pg_extension e ON e.oid=d.refobjid
     WHERE p.oid=to_regprocedure('public.unaccent(regdictionary,text)')
       AND l.lanname='c' AND p.provolatile='s' AND NOT p.prosecdef
       AND p.proisstrict AND p.proparallel='s'
       AND p.prosrc='unaccent_dict' AND p.probin='$libdir/unaccent'
       AND e.extname='unaccent' AND e.extversion='1.1'
  ) THEN RAISE EXCEPTION 'unaccent_identity_mismatch'; END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_ts_dict d JOIN pg_namespace dn ON dn.oid=d.dictnamespace
      JOIN pg_ts_template t ON t.oid=d.dicttemplate
      JOIN pg_namespace tn ON tn.oid=t.tmplnamespace
      JOIN pg_depend dep ON dep.classid='pg_ts_dict'::regclass AND dep.objid=d.oid
        AND dep.refclassid='pg_extension'::regclass AND dep.deptype='e'
      JOIN pg_extension e ON e.oid=dep.refobjid
     WHERE dn.nspname='public' AND d.dictname='unaccent'
       AND d.dictinitoption=$rules$rules = 'unaccent'$rules$
       AND tn.nspname='public' AND t.tmplname='unaccent' AND e.extname='unaccent'
  ) THEN RAISE EXCEPTION 'unaccent_dictionary_mismatch'; END IF;
END
$check$;
GRANT EXECUTE ON FUNCTION public.normalize_text(text) TO funkcionario_reader;
GRANT EXECUTE ON FUNCTION public.unaccent(regdictionary, text) TO funkcionario_reader;
COMMIT;
