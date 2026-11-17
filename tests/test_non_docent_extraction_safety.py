"""F09: validate every family before any destructive store operation."""
import unittest
from unittest.mock import Mock, patch
from app.services import non_docent_parser as module
from app.services.non_docent_validation import NonDocentExtractionError

AWARD = '4-E. TERAPIA OCUPACIONAL 51.95 AE APELLIDO SINTETICO NOMBRE No 123 - CENTRO SINTETICO'
BAG = '100-E BOLSA SINTETICA\n1 ***1234** PERSONA SINTETICA 5.5 Disponible'
CALL = 'CONVOCATORIA ADC EDU 1/26\nANNEX: PUESTOS OFERTADOS\n12345 A2 20 E CENTRO SINTETICO 100 Adscripció funcional: CENTRO - LOCALIDAD Província: ALICANTE Motiu: VACANT'


class NonDocentExtractionSafetyTests(unittest.TestCase):
    def setUp(self):
        # Parser transactions are isolated; claims have real PostgreSQL coverage.
        claim = patch("app.services.non_docent_parser.locked_documents",
                      side_effect=lambda store, rows, key, version: rows)
        claim.start()
        self.addCleanup(claim.stop)

    def parse(self, family, text, store=None):
        store = store or Mock()
        store.get_staff_group_id_by_code.return_value=1
        store.upsert_publication.return_value=9
        store.insert_bag_snapshot.side_effect=range(20,100)
        p=module.NonDocentParserService()
        document=dict(document_id=1,doc_family=family,title='Sintético',source_key='non_docent_adc_eee',
                      file_path='synthetic.pdf',original_filename='listadobolsa_100.pdf')
        with patch.object(p,'_extract_pages_text',return_value=[text]):
            result=p._parse_document(store,document)
        return store,result

    def assert_preserved(self,family,text,expected=NonDocentExtractionError):
        store=Mock()
        with self.assertRaises(expected) as raised:self.parse(family,text,store)
        if expected is NonDocentExtractionError:
            self.assertNotIsInstance(raised.exception,module.EmptyNonDocentDocument)
        store.upsert_publication.assert_not_called()
        store.clear_publication_rows.assert_not_called()
        return store

    def test_unreadable_and_unknown_documents_preserve_every_family(self):
        for family in module.NON_DOCENT_DOC_FAMILIES:
            for text in ('','DOCUMENTO DE FORMATO NUEVO NO RECONOCIDO'):
                with self.subTest(family=family,text=text):self.assert_preserved(family,text)

    def test_partial_awards_are_not_committed(self):
        self.assert_preserved('non_docent_adc_award',AWARD+' 5-E. OTRA BOLSA SIN PUNTUACION AE PERSONA SINTETICA No 456 - OTRO CENTRO')

    def test_partial_bags_are_not_committed(self):
        for family in ('non_docent_bag_update','non_docent_funcion_publica_bag'):
            with self.subTest(family=family):self.assert_preserved(family,BAG+'\n2 ***5678** OTRA PERSONA SIN METRICAS')

    def test_partial_call_with_unsupported_short_code_is_not_committed(self):
        self.assert_preserved('non_docent_adc_call',CALL+'\n456 A2 20 E OTRO CENTRO 100')

    def test_valid_family_rows_still_work_including_f19(self):
        for family,text in [('non_docent_adc_award',AWARD),('non_docent_adc_call',CALL),
                            ('non_docent_bag_update',BAG),('non_docent_funcion_publica_bag',BAG)]:
            with self.subTest(family=family):
                store,result=self.parse(family,text)
                self.assertEqual(result.rows_extracted,1)
                store.clear_publication_rows.assert_called_once_with(publication_id=9)

    def test_empty_deserted_award_is_skipped_without_replacing_data(self):
        self.assert_preserved('non_docent_adc_award','ADJUDICACION EDU-1/26\nQuedan desiertos por no haber sido solicitados:\n12345 - CENTRO SINTETICO',module.EmptyNonDocentDocument)

    def test_explicit_empty_bag_and_call_preserve_data(self):
        for family,text in [('non_docent_bag_update','BOLSA DE EMPLEO: No hay personas integrantes.'),
                            ('non_docent_funcion_publica_bag','BORSA: No hi ha persones integrants.'),
                            ('non_docent_adc_call','CONVOCATORIA ADC: No hay puestos ofertados.')]:
            with self.subTest(family=family):self.assert_preserved(family,text,module.EmptyNonDocentDocument)

    def test_footer_about_no_deserted_positions_is_not_an_empty_award(self):
        self.assert_preserved('non_docent_adc_award','ADJUDICACION EDU-1/26 No han quedado puestos desiertos.')

    def test_desertion_template_does_not_hide_an_unreadable_result_table(self):
        self.assert_preserved('non_docent_adc_award',
            'ADJUDICACION EDU-1/26 Puntuación Apellidos NOMBRAMIENTO NO RECONOCIDO '
            'Quedan desiertos: 12345 - CENTRO')

    def test_bag_snapshot_ids_are_remapped_only_after_validation(self):
        store,result=self.parse('non_docent_bag_update',BAG)
        self.assertEqual(result.rows_extracted,1)
        self.assertEqual(store.insert_bag_member.call_args.kwargs['snapshot_id'],20)
        self.assertEqual(store.insert_bag_snapshot.call_args.kwargs['publication_id'],9)

    def test_desertion_footer_does_not_hide_valid_award(self):
        _,result=self.parse('non_docent_adc_award',AWARD+' Quedan desiertos: 34567 - CENTRO SINTETICO')
        self.assertEqual(result.rows_extracted,1)

    def test_empty_notice_is_not_recorded_as_success_and_next_document_runs(self):
        store=Mock()
        store.list_documents_for_parser.return_value=[
            dict(document_id=i,document_version_id=i,doc_family='non_docent_adc_award',
                 original_filename=f'synthetic-{i}.pdf') for i in (1,2)]
        store.create_parse_run.side_effect=[1,2]
        p=module.NonDocentParserService()
        skipped=module.EmptyNonDocentDocument('explicitly_empty',{'extracted':0,'markers':0})
        with patch.object(module,'NonDocentStore',return_value=store),patch.object(
                p,'_parse_document',side_effect=[skipped,module.ParseResult(1,9)]):
            result=p.parse_all_documents()
        self.assertEqual([r['status'] for r in result],['skipped','success'])
        self.assertEqual(result[0]['reason'],'explicitly_empty')
        self.assertEqual([c.kwargs['status'] for c in store.finish_parse_run.call_args_list],['skipped','success'])
        self.assertEqual(store.mark_document_parsed.call_args.kwargs['document_id'],2)
        self.assertEqual(store.mark_document_parsed.call_count,1)

    def test_failed_validation_has_aggregate_diagnostics_only(self):
        store=Mock()
        try:self.parse('non_docent_adc_award',AWARD+' No 1234567 - CENTRO',store)
        except ValueError as exc:
            self.assertIn('extracted',str(exc))
            self.assertNotIn('APELLIDO',str(exc))
        else:self.fail('incomplete extraction accepted')
