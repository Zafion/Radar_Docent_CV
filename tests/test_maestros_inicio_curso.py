"""Regresiones de maquetaciones de maestros; nombres y centros sintéticos."""
import unittest
from unittest.mock import Mock, patch

from app.services.document_classifier import DocumentClassifierService
from app.services.final_award_listing_maestros_parser import FinalAwardListingMaestrosParserService


class MaestrosInicioCursoTests(unittest.TestCase):
    def setUp(self):
        # Parser transactions are isolated; claims have real PostgreSQL coverage.
        claim = patch("app.services.final_award_listing_maestros_parser.locked_documents",
                      side_effect=lambda store, rows, key, version: rows)
        claim.start()
        self.addCleanup(claim.stop)

    def parse_lines(self, lines):
        parser = FinalAwardListingMaestrosParserService()
        with patch.object(parser, "_extract_clean_lines", return_value=lines):
            return parser._parse_document({"file_path": "synthetic.pdf"})

    def test_interino_layout_keeps_person_and_assignment(self):
        rows = self.parse_lines([
            "1", "VACANT", "LOCALIDAD(00000001)CENTRO SINTÉTICO", "128 / EDUCACIÓN PRIMARIA",
            "123456", "23 horas", "APELLIDO SINTÉTICO, NOMBRE Petición: Voluntaria 2", "Adjudicat",
            "2 OTRO APELLIDO, OTRA PERSONA", "No adjudicat",
        ])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].person_display_name, "APELLIDO SINTÉTICO, NOMBRE")
        self.assertEqual(rows[0].assignment.position_code, "123456")
        self.assertEqual(rows[0].assignment.center_code, "00000001")
        self.assertIsNone(rows[1].assignment)

    def test_both_filenames_are_routed_to_maestros(self):
        classifier = DocumentClassifierService()
        for filename in ("ini_2025_adj_rpp_lis_mae.pdf", "ini_2025_adj_int_lis_mae.pdf"):
            with self.subTest(filename=filename):
                result = classifier._classify_by_filename(filename=filename, title="", combined_text="")
                self.assertEqual(result[:2], ("final_award_listing", "maestros"))

    def test_rpp_layout_must_keep_awarded_and_not_awarded(self):
        # Orden observado en pypdf: centro, resultado, nombre, especialidad,
        # orden/colectivo. No corresponde al layout de interinos.
        rows = self.parse_lines([
            "EDUCACIÓ FÍSICA (114)", "Ordre Cognoms i Nom Col·lectiu",
            "00000001 CENTRO SINTÉTICO - LOCALIDAD",
            "Adjudicat per la petició número 2. Lloc: 123456",
            "APELLIDO SINTÉTICO, NOMBRE", "128 EDUCACIÓN PRIMARIA", "1 PROVISIONALS",
            "--", "No adjudicat.", "OTRO APELLIDO, OTRA PERSONA", "--", "2 REINGRESSATS",
        ])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].status, "Adjudicat")
        self.assertEqual(rows[0].assignment.position_code, "123456")
        self.assertEqual(rows[1].status, "No adjudicat")

    def test_rpp_officio_layout_must_keep_award(self):
        rows = self.parse_lines([
            "00000001 CENTRO SINTÉTICO - LOCALIDAD",
            "Adjudicat d'ofici província preferent Lloc: 123456",
            "APELLIDO SINTÉTICO, NOMBRE", "I 120 EDUCACIÓN INFANTIL", "1 PROVISIONALS",
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].assignment.position_code, "123456")

    def test_empty_maestros_parse_must_not_be_success(self):
        store = Mock()
        store.list_final_listing_documents.return_value = [{
            "document_id": 1, "document_version_id": 1, "original_filename": "synthetic.pdf"}]
        with patch("app.services.final_award_listing_maestros_parser.AwardResultsStore", return_value=store), patch.object(FinalAwardListingMaestrosParserService, "_parse_document", return_value=[]):
            result = FinalAwardListingMaestrosParserService().parse_all_documents()
        self.assertEqual(result[0]["status"], "failed")
