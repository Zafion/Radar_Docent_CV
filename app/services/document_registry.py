from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.services.document_classifier import DocumentClassifierService
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
    def __init__(self) -> None:
        self.classifier = DocumentClassifierService()

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

                classified = self.classifier.classify(
                    file_path=row["file_path"],
                    original_filename=row["original_filename"],
                    asset_title=row["asset_title"],
                    asset_role=row["asset_role"],
                    source_key=row["source_key"],
                    source_label=row["source_label"],
                    section=row["section"],
                    publication_label=row["publication_label"],
                    publication_date_text=row["publication_date_text"],
                )

                notes = (
                    f"classifier_version={classified.classifier_version};"
                    f" source_key={row['source_key']};"
                    f" asset_role={row['asset_role']};"
                    f" signals={classified.signals}"
                )

                document_id = store.create_document(
                    document_version_id=document_version_id,
                    source_id=int(row["source_id"]),
                    doc_family=classified.doc_family,
                    title=classified.title,
                    document_date_text=classified.document_date_text,
                    document_date_iso=classified.document_date_iso,
                    list_scope=classified.list_scope,
                    notes=notes,
                )

                registered.append(
                    RegisteredDocument(
                        document_id=document_id,
                        document_version_id=document_version_id,
                        source_key=row["source_key"],
                        doc_family=classified.doc_family,
                        title=classified.title,
                        document_date_text=classified.document_date_text,
                        document_date_iso=classified.document_date_iso,
                        list_scope=classified.list_scope,
                        original_filename=row["original_filename"],
                    )
                )

            return registered

        finally:
            store.close()