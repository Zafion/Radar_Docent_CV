from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Optional

from app.storage.document_store import DocumentStore


@dataclass(slots=True)
class RegisteredDocument:
    document_id: int
    document_version_id: int
    source_key: str
    doc_family: str
    title: str | None
    document_date_text: str | None
    document_date_iso: str | None
    list_scope: str | None
    original_filename: str


class DocumentRegistryService:
    def register_unclassified_documents(self) -> list[RegisteredDocument]:
        store = DocumentStore()
        registered: list[RegisteredDocument] = []

        try:
            rows = store.list_unregistered_document_candidates()
            seen_versions: set[int] = set()

            for row in rows:
                document_version_id = int(row["document_version_id"])

                if document_version_id in seen_versions:
                    continue
                seen_versions.add(document_version_id)

                doc_family, list_scope = self._classify_document(
                    asset_role=row["asset_role"],
                    source_key=row["source_key"],
                )

                document_date_text, document_date_iso = self._infer_document_date(
                    publication_date_text=row["publication_date_text"],
                    original_filename=row["original_filename"],
                )

                title = row["asset_title"]
                notes = (
                    f"classified_from_asset_role={row['asset_role']};"
                    f" source_key={row['source_key']}"
                )

                document_id = store.create_document(
                    document_version_id=document_version_id,
                    source_id=int(row["source_id"]),
                    doc_family=doc_family,
                    title=title,
                    document_date_text=document_date_text,
                    document_date_iso=document_date_iso,
                    list_scope=list_scope,
                    notes=notes,
                )

                registered.append(
                    RegisteredDocument(
                        document_id=document_id,
                        document_version_id=document_version_id,
                        source_key=row["source_key"],
                        doc_family=doc_family,
                        title=title,
                        document_date_text=document_date_text,
                        document_date_iso=document_date_iso,
                        list_scope=list_scope,
                        original_filename=row["original_filename"],
                    )
                )

            return registered

        finally:
            store.close()

    def _classify_document(
        self,
        asset_role: str,
        source_key: str,
    ) -> tuple[str, Optional[str]]:
        if asset_role == "resolucion_pdf":
            return "resolution_text", None

        if asset_role == "listado_maestros_pdf":
            return "final_award_listing", "maestros"

        if asset_role == "listado_secundaria_pdf":
            return "final_award_listing", "secundaria_otros"

        if asset_role in {"puestos_pdf", "puestos_definitivos_pdf"}:
            if source_key == "adjudicacion3":
                return "offered_positions", "inicio_curso"
            if source_key == "resolucion":
                return "offered_positions", "continua"
            if source_key == "resolucion1":
                return "offered_positions", "dificil_cobertura"
            return "offered_positions", None

        if asset_role == "provisional_listado_pdf":
            return "difficult_coverage_provisional", "dificil_cobertura"

        return "unknown", None

    def _infer_document_date(
        self,
        publication_date_text: str | None,
        original_filename: str,
    ) -> tuple[Optional[str], Optional[str]]:
        if publication_date_text:
            iso_date = self._parse_ddmmyyyy_to_iso(publication_date_text)
            return publication_date_text, iso_date

        filename_match = re.match(r"^(?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{2})_", original_filename)
        if filename_match:
            dd = filename_match.group("dd")
            mm = filename_match.group("mm")
            yy = filename_match.group("yy")
            date_text = f"{dd}/{mm}/20{yy}"
            iso_date = self._parse_ddmmyyyy_to_iso(date_text)
            return date_text, iso_date

        return None, None

    def _parse_ddmmyyyy_to_iso(self, value: str) -> Optional[str]:
        try:
            return datetime.strptime(value, "%d/%m/%Y").date().isoformat()
        except ValueError:
            return None