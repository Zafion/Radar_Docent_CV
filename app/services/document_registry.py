from __future__ import annotations

from dataclasses import dataclass

from app.services.document_classifier import DocumentClassifierService
from app.storage.document_store import DocumentStore
from app.storage.notification_eligibility_store import register_detection


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

    def register_selected_live(self, entries: list[dict]) -> list[dict]:
        """Register exact pre-authorized versions, without producing alerts.

        This primitive does not authenticate provenance or establish novelty;
        callers must do so before invoking it. It is not a production runner.
        """
        import re
        from app.services.parse_selection import verified_sha

        fields = {'asset_id', 'document_version_id', 'sha256', 'canonical_url', 'source_key'}
        if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
            raise ValueError('invalid_selection')
        for e in entries:
            if (not isinstance(e, dict) or set(e) != fields
                or any(type(e[k]) is not int or e[k] < 1 for k in ('asset_id', 'document_version_id'))
                or not isinstance(e['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', e['sha256'])
                or not all(isinstance(e[k], str) and e[k] for k in ('canonical_url', 'source_key'))):
                raise ValueError('invalid_selection_entry')
        if len({e['asset_id'] for e in entries}) != len(entries) or len({e['document_version_id'] for e in entries}) != len(entries):
            raise ValueError('duplicate_selection')
        store = DocumentStore()
        try:
            rows = store.list_selected_unregistered_candidates([e['asset_id'] for e in entries])
            by_id = {r['asset_id']: r for r in rows}
            if len(rows) != len(entries) or len(by_id) != len(entries):
                raise ValueError('selection_stale_or_registered')
            result = []
            for e in entries:
                row = by_id[e['asset_id']]
                if any(row[k] != e[k] for k in ('document_version_id', 'sha256', 'canonical_url', 'source_key')):
                    raise ValueError('selection_identity_mismatch')
                if row['notification_ingest_intent'] != 'live_new':
                    raise ValueError('non_live_version')
                verified_sha(row['file_path'], e['sha256'])
                if row.get('existing_document_id') is not None:
                    if (row['existing_detection_intent'] != 'live_new'
                            or row['existing_detection_url'] != e['canonical_url']):
                        raise ValueError('registered_document_not_authorized_live')
                    result.append({'document_id': row['existing_document_id'],
                                   'document_version_id': e['document_version_id'],
                                   'sha256': e['sha256']})
                    continue
                classified = self.classifier.classify(
                    file_path=row['file_path'], original_filename=row['original_filename'],
                    asset_title=row['asset_title'], asset_role=row['asset_role'],
                    source_key=row['source_key'], source_label=row['source_label'],
                    section=row['section'], publication_label=row['publication_label'],
                    publication_date_text=row['publication_date_text'])
                verified_sha(row['file_path'], e['sha256'])
                document_id = store.create_document(
                    document_version_id=e['document_version_id'], source_id=int(row['source_id']),
                    doc_family=classified.doc_family, title=classified.title,
                    document_date_text=classified.document_date_text, document_date_iso=classified.document_date_iso,
                    list_scope=classified.list_scope, notes='classifier_version=' + classified.classifier_version)
                register_detection(store.connection, document_id, e['document_version_id'],
                                   e['canonical_url'], row['publication_date_text'], 'live_new')
                result.append({'document_id': document_id, 'document_version_id': e['document_version_id'], 'sha256': e['sha256']})
            store.connection.commit()
            return result
        except Exception:
            store.connection.rollback()
            raise
        finally:
            store.close()

    def register_unclassified_documents(self, *, notification_intent: str = "unknown") -> list[RegisteredDocument]:
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

                register_detection(store.connection, document_id, document_version_id,
                                   row.get("canonical_url"), row.get("publication_date_text"), notification_intent)

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

            store.connection.commit()
            return registered

        except Exception:
            store.connection.rollback()
            raise

        finally:
            store.close()
