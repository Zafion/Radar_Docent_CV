"""Auditoría offline. expectedFailure documenta contratos todavía incumplidos.

Ejecutar: .venv/bin/python -B -m unittest discover -s tests -v
No usa bases de datos ni servicios de red; todos los nombres son sintéticos.
"""
from tests.document_fixtures import pdf_bytes
from dataclasses import asdict
import contextlib
import io
import hashlib
import json
import runpy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx

from app.services.discovery.rrhh_main import RrhhMainDiscoveryAdapter
from app.services.discovery.non_docent import (
    NonDocentSubstitutionDiscoveryAdapter, SUBSTITUTION_SOURCE_PAGES,
)
from app.services.document_classifier import DocumentClassifierService
from app.services.document_sync import DocumentSyncService
from app.services.offered_positions_parser import OfferedPositionsParserService
from app.storage.db import PgCompatConnection
from scripts import fetch_ceice_remote as fetcher
from scripts import import_remote_fetch as importer

ROOT = "https://ceice.gva.es/es/web/rrhh-educacion"
NEWS = ROOT + "/-/vacantes-inicio-curso-actualizaci%C3%93n"
PDF = "https://ceice.gva.es/documents/162909733/412501121/lis_vac_act_adj_ini_26_27.pdf"


def response(url, text, status=200, content_type="text/html"):
    return httpx.Response(status, **({"content": text} if isinstance(text, bytes) else {"text": text}), headers={"content-type": content_type},
                          request=httpx.Request("GET", url))


def asset():
    adapter = RrhhMainDiscoveryAdapter()
    pages = {ROOT: response(ROOT, f'<a href="{NEWS}">Vacantes</a>'),
             NEWS: response(NEWS, f'<h1>Vacantes inicio curso</h1><a href="{PDF}">PDF</a>')}
    with patch.object(adapter, "_fetch_response", side_effect=pages.get):
        return adapter.discover_assets()[0]


class DiscoveryTests(unittest.TestCase):
    def test_historical_url_through_intermediate_and_multiple_pdfs(self):
        adapter = RrhhMainDiscoveryAdapter()
        pages = {ROOT: response(ROOT, f'<a href="{NEWS}">Vacantes</a>'),
                 NEWS: response(NEWS, f'<h1>Vacantes inicio curso</h1><a href="{PDF}">PDF</a>'
                                f'<a href="{PDF}?download=true">Duplicado</a>'
                                '<a href="/documents/1/2/vacantes-anexo.pdf">Anexo</a>')}
        with patch.object(adapter, "_fetch_response", side_effect=pages.get):
            assets = adapter.discover_assets()
        self.assertEqual({a.canonical_url for a in assets}, {PDF, "https://ceice.gva.es/documents/1/2/vacantes-anexo.pdf"})

    def test_network_timeout_is_silently_empty(self):
        adapter = RrhhMainDiscoveryAdapter()
        with patch("app.services.discovery.base.httpx.get", side_effect=httpx.ConnectTimeout("simulado")):
            self.assertEqual(adapter.discover_assets(), [])

    def test_http_failures_are_silently_empty(self):
        for code in (403, 404, 429, 500):
            with self.subTest(code=code), patch("app.services.discovery.base.httpx.get", return_value=response(ROOT, "fallo", code)) as get:
                self.assertEqual(RrhhMainDiscoveryAdapter().discover_assets(), [])
                self.assertEqual(get.call_count, 1)

    def test_query_parameter_pages_must_remain_distinct(self):
        adapter = RrhhMainDiscoveryAdapter()
        self.assertNotEqual(adapter._canonicalize_url(ROOT + "?page=1"),
                            adapter._canonicalize_url(ROOT + "?page=2"))

    def test_valencian_intermediate_should_be_followed(self):
        self.assertTrue(RrhhMainDiscoveryAdapter()._should_follow_html_url(
            "https://ceice.gva.es/va/web/rrhh-educacion/-/vacants"))

    def test_non_docent_intermediate_should_discover_pdf(self):
        adapter = NonDocentSubstitutionDiscoveryAdapter(SUBSTITUTION_SOURCE_PAGES[0])
        html = '<a href="/es/web/inclusioeducativa/-/convocatoria-adc">Convocatoria ADC EDU</a>'
        detail = response("https://ceice.gva.es/es/web/inclusioeducativa/-/convocatoria-adc",
            '<a href="/documents/1/2/ADC_EDU_1_26.pdf">Convocatoria ADC EDU</a>')
        with patch.object(adapter, "_fetch_html", return_value=html), patch.object(adapter, "_fetch_response", return_value=detail), patch("app.services.discovery.base.httpx.get", side_effect=AssertionError("Red no simulada")):
            self.assertGreater(len(adapter.discover_assets()), 0)


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        # Parser transactions are isolated; claims have real PostgreSQL coverage.
        claim = patch("app.services.offered_positions_parser.locked_documents",
                      side_effect=lambda store, rows, key, version: rows)
        claim.start()
        self.addCleanup(claim.stop)

    def test_direct_download_detects_changed_same_url_and_known_hash(self):
        with tempfile.TemporaryDirectory(dir="docs/auditoria_evidencias") as tmp:
            service = DocumentSyncService(base_dir=tmp)
            store = Mock()
            store.get_document_version_by_sha256.return_value = None
            store.create_document_version.return_value = 1
            item = asset()
            with patch("app.services.document_sync.httpx.stream", side_effect=[contextlib.nullcontext(response(PDF, pdf_bytes("version1"))), contextlib.nullcontext(response(PDF, pdf_bytes("version2")))]):
                one = service._download_and_store_asset(item, Path(tmp), store)
                two = service._download_and_store_asset(item, Path(tmp), store)
            self.assertNotEqual(one.sha256, two.sha256)
            self.assertNotEqual(one.stored_filename, two.stored_filename)
            store.get_document_version_by_sha256.return_value = {"id": 1, "stored_filename": one.stored_filename,
                "file_path": one.file_path, "content_type": "application/pdf", "size_bytes": one.size_bytes}
            with patch("app.services.document_sync.httpx.stream", return_value=contextlib.nullcontext(response(PDF, pdf_bytes("version1")))):
                known = service._download_and_store_asset(item, Path(tmp), store)
            self.assertEqual(known.status, "already_known_hash")
            self.assertEqual(store.create_document_version.call_count, 2)

    def test_html_200_must_not_be_saved_as_pdf(self):
        with tempfile.TemporaryDirectory(dir="docs/auditoria_evidencias") as tmp:
            store = Mock()
            store.get_document_version_by_sha256.return_value = None
            store.create_document_version.return_value = 1
            with patch("app.services.document_sync.httpx.stream", return_value=contextlib.nullcontext(response(PDF, "<html>Servicio no disponible</html>"))):
                with self.assertRaises(ValueError):
                    DocumentSyncService(base_dir=tmp)._download_and_store_asset(asset(), Path(tmp), store)

    def test_fresh_remote_cache_is_reused_within_ttl(self):
        with tempfile.TemporaryDirectory(dir="docs/auditoria_evidencias") as tmp:
            output = Path(tmp)
            cache = output / "cache/files"
            cache.mkdir(parents=True)
            (cache / "old.pdf").write_bytes(pdf_bytes("old"))
            item = asset()
            (output / "state.json").write_text(json.dumps({"assets_by_canonical_url": {PDF: {
                "stored_filename": "old.pdf", "original_filename": "original.pdf", "sha256": hashlib.sha256(pdf_bytes("old")).hexdigest(), "last_checked_at": fetcher.utc_now_iso()}}}))
            adapter = Mock(source_key="audit", source_url=ROOT, source_label="audit")
            adapter.discover_assets.return_value = [item]
            with patch.object(fetcher, "select_adapters", return_value=[adapter]), patch.object(fetcher, "download_asset") as download, patch("sys.argv", ["fetcher", "--output-dir", tmp]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(fetcher.main(), 0)
            download.assert_not_called()
            manifest = json.loads((output / "current/manifest.json").read_text())
            self.assertEqual(manifest["sources"][0]["results"][0]["status"], "cached_url")

    def test_import_failed_source_must_not_become_success(self):
        with tempfile.TemporaryDirectory(dir="docs/auditoria_evidencias") as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps({"schema_version": 1, "sources": [{
                "source_key": "audit", "source_url": ROOT, "status": "failed", "error_message": "HTTP 403", "assets": [], "results": []}]}))
            store = Mock()
            with patch.object(importer, "SyncStore", return_value=store), contextlib.redirect_stdout(io.StringIO()):
                importer.import_manifest(manifest_path=path, incoming_dir=Path(tmp), data_dir=Path(tmp))
            self.assertEqual(store.finish_sync_run.call_args.kwargs["status"], "failed")
            self.assertEqual(store.finish_sync_run.call_args.kwargs["error_message"], "HTTP 403")

    def test_replayed_manifest_reuses_known_hash(self):
        with tempfile.TemporaryDirectory(dir="docs/auditoria_evidencias") as tmp:
            path = Path(tmp) / "manifest.json"
            received = Path(tmp) / 'received.pdf'
            received.write_bytes(pdf_bytes('known'))
            digest = hashlib.sha256(received.read_bytes()).hexdigest()
            path.write_text(json.dumps({"schema_version": 1, "sources": [{"source_key": "audit", "source_url": ROOT,
                "status": "success", "assets": [asdict(asset())], "results": [{"asset": {**asdict(asset()), "source_key": "audit"},
                    "status": "cached_url", "sha256": digest, "size_bytes": received.stat().st_size,
                    "relative_file_path": "received.pdf", "stored_filename": "received.pdf", "original_filename": "received.pdf"}]}]}))
            store = Mock()
            store.get_document_version_by_sha256.return_value = {"id": 7, "sha256": digest, "size_bytes": received.stat().st_size, "file_path": str(received)}
            with patch.object(importer, "SyncStore", return_value=store), contextlib.redirect_stdout(io.StringIO()):
                for _ in range(2):
                    importer.import_manifest(manifest_path=path, incoming_dir=Path(tmp), data_dir=Path(tmp))
            store.create_document_version.assert_not_called()
            self.assertEqual(store.set_asset_document_version.call_count, 2)

    def test_registration_must_respect_disabled_notifications(self):
        from app.services.document_registry import RegisteredDocument
        item = RegisteredDocument(1, 1, "audit", "offered_positions", "Sintético", None, None, "continua", "synthetic.pdf")
        with patch("app.services.document_registry.DocumentRegistryService.register_unclassified_documents", return_value=[item]), patch("app.storage.db.get_connection", return_value=Mock()), patch("app.storage.public_alert_store.enqueue_public_alert"), patch("app.storage.push_event_store.enqueue_push_notification_event"), patch("app.services.push_notifications.is_push_configured", return_value=True), patch("run_send_notifications.main") as send, patch.dict("os.environ", {"ENABLE_NOTIFICATIONS": "0"}), contextlib.redirect_stdout(io.StringIO()):
            runpy.run_path("run_register_documents.py")
        send.assert_not_called()

    def test_raw_connection_close_must_close(self):
        connection = Mock()
        PgCompatConnection(connection).close()
        connection.close.assert_called_once()

    def test_zero_rows_must_not_be_success(self):
        store = Mock()
        store.list_offered_position_documents.return_value = [{"document_id": 1, "document_version_id": 1, "original_filename": "synthetic.pdf"}]
        with patch("app.services.offered_positions_parser.OfferedPositionsStore", return_value=store), patch.object(OfferedPositionsParserService, "_parse_document", return_value=[]):
            result = OfferedPositionsParserService().parse_all_documents()
        self.assertEqual(result[0]["status"], "failed")

    def test_historical_filename_should_classify_without_descriptive_link(self):
        classifier = DocumentClassifierService()
        with patch.object(classifier, "_read_pdf_preview", return_value=""):
            classified = classifier.classify(file_path="synthetic.pdf", original_filename="lis_vac_act_adj_ini_26_27.pdf",
                asset_title="PDF", asset_role="pdf_candidate", source_key="rrhh_main", source_label="RRHH",
                section=None, publication_label="VACANTES INICIO CURSO ACTUALIZACIÓN", publication_date_text=None)
        self.assertEqual(classified.doc_family, "offered_positions")


class ParserRegressionTests(unittest.TestCase):
    def test_split_position_code_from_official_pdf(self):
        row = OfferedPositionsParserService()._parse_continua_row(
            row_text="11 SUSTITUCIÓN INDETERMINADA DOLORES - 03011197 - CEIP VIRGEN DE LOS DOLORES 7178 46 NO",
            source_type="continua", body_name="MAESTROS", specialty_code="120",
            specialty_name="EDUCACIÓN INFANTIL", province="ALICANTE")
        self.assertIsNotNone(row)
        self.assertEqual(row.position_code, "717846")

    def test_specialty_code_before_label_from_official_pdf(self):
        self.assertEqual(OfferedPositionsParserService()._parse_specialty(
            "2A2 ESPECIALIDAD/ESPECIALITAT: INSTALACIONES ELECTROTÉCNICAS"),
            ("2A2", "INSTALACIONES ELECTROTÉCNICAS"))

    def test_hours_before_locality_from_official_pdf(self):
        row = OfferedPositionsParserService()._parse_dificil_cobertura_row(
            row_text="18 ELDA - 03005768 - IES LA TORRETA NO 211258 Sust. Ind.",
            source_type="dificil_cobertura", body_name=None, specialty_code="2A2",
            specialty_name=None, province="ALICANTE")
        self.assertEqual(row.hours_value, 18)
        self.assertEqual(row.locality, "ELDA")

    def test_itinerant_flag_must_not_be_language_requirement(self):
        row = OfferedPositionsParserService()._parse_continua_row(
            row_text="1 SUSTITUCIÓN DETERMINADA BURJASSOT - 46017501 - IES FEDERICA MONTSENY 915287 SI",
            source_type="continua", body_name=None, specialty_code="3A5", specialty_name=None, province=None)
        self.assertTrue(row.is_itinerant)
        self.assertIsNone(row.valenciano_required_text)


class WebTests(unittest.TestCase):
    def test_feed_connection_closes_even_on_query_failure(self):
        from app.web import routes
        for error in (None, RuntimeError("simulado")):
            connection = Mock()
            with patch.object(routes, "get_connection", return_value=connection), patch.object(routes, "list_public_alerts", return_value=[], side_effect=error):
                if error:
                    with self.assertRaises(RuntimeError):
                        routes._feed_alerts()
                else:
                    self.assertEqual(routes._feed_alerts(), [])
            connection.close.assert_called_once()

    def test_old_award_must_not_override_later_result(self):
        from app.api.main import build_user_view
        result = build_user_view({"display_name": "PERSONA SINTÉTICA"}, [
            {"status": "Desactivat", "document_date_iso": "2026-11-01"},
            {"status": "Adjudicat", "document_date_iso": "2025-09-01", "assignments_count": 1,
             "assignments": [{"position_code": "123456"}]}], [])
        self.assertEqual(result["current_result"], "deactivated")

    def test_pages_render_without_database_and_without_literal_templates(self):
        from app.api.main import app
        from app.web import routes
        from app.web.i18n import translate_html, get_language_from_path
        from bs4 import BeautifulSoup
        from starlette.requests import Request
        # Render directo: sin lifespan, servidor, hilos ni pool de conexiones.
        with patch("app.storage.db.get_raw_connection", side_effect=AssertionError("DB prohibida")), patch("app.api.main.get_pool", side_effect=AssertionError("DB prohibida")):
            for path, endpoint in [("/", routes.home), ("/va", routes.home),
                         ("/consulta-persona", routes.person_search), ("/va/consulta-persona", routes.person_search),
                         ("/centros", routes.center_search), ("/plazas-ofertadas", routes.offered_positions),
                         ("/va/plazas-ofertadas", routes.offered_positions),
                         ("/valencia-no-docentes", routes.valencia_no_docentes),
                         ("/no-docente/consulta-persona", routes.non_docent_person_search),
                         ("/dificil-cobertura", routes.difficult_coverage), ("/politica-privacidad", routes.politica_privacidad)]:
                with self.subTest(path=path):
                    request = Request({"type": "http", "method": "GET", "path": path, "root_path": "",
                        "scheme": "https", "server": ("audit.invalid", 443), "query_string": b"",
                        "headers": [], "router": app.router, "app": app})
                    result = endpoint(request)
                    self.assertEqual(result.status_code, 200)
                    visible = BeautifulSoup(translate_html(result.body.decode(), get_language_from_path(path)), "html.parser")
                    for node in visible(["script", "style"]):
                        node.decompose()
                    text = visible.get_text()
                    for token in ("{{", "{%", "${"):
                        self.assertNotIn(token, text)
            self.assertEqual(routes.sitemap_xml(request).status_code, 200)
            self.assertEqual(routes.robots_txt(request).status_code, 200)


if __name__ == "__main__":
    unittest.main()
