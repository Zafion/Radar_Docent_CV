import os
import sqlite3

from app.services.document_registry import DocumentRegistryService
from app.services.push_notifications import (
    is_push_configured,
    send_push_notification_to_all,
)

service = DocumentRegistryService()
registered = service.register_unclassified_documents()

actionable_families = {
    "offered_positions",
    "final_award_listing",
    "difficult_coverage_provisional",
}

actionable = [item for item in registered if item.doc_family in actionable_families]
ignored = [item for item in registered if item.doc_family == "ignored"]
unknown = [item for item in registered if item.doc_family == "unknown"]

print(f"Documentos registrados: {len(registered)}")
print(f"Accionables: {len(actionable)}")
print(f"Ignorados: {len(ignored)}")
print(f"Unknown: {len(unknown)}")
print()

for item in registered:
    print(f"document_id={item.document_id}")
    print(f"document_version_id={item.document_version_id}")
    print(f"source_key={item.source_key}")
    print(f"doc_family={item.doc_family}")
    print(f"title={item.title}")
    print(f"document_date_text={item.document_date_text}")
    print(f"document_date_iso={item.document_date_iso}")
    print(f"list_scope={item.list_scope}")
    print(f"original_filename={item.original_filename}")
    print("-" * 80)

if len(actionable) > 0:
    db_path = os.getenv("RADAR_DOCENT_DB_PATH", "/mnt/data/radar_docent_cv.db")
    if is_push_configured():
        with sqlite3.connect(db_path) as conn:
            result = send_push_notification_to_all(
                conn,
                title="Nuevo documento publicado por Conselleria",
                body="La Conselleria ha publicado nueva información operativa. Pulsa para consultarla.",
                url="/valencia-docentes",
            )
            conn.commit()
        print()
        print(f"Notificaciones push enviadas: {result}")
    else:
        print()
        print("Push no configurado: no se han enviado alertas.")
else:
    print("Sin nuevos documentos accionables: no se envían alertas.")