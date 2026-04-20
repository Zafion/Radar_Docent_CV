import os
import sqlite3

from app.services.document_registry import DocumentRegistryService
from app.services.push_notifications import send_push_notification_to_all, is_push_configured

service = DocumentRegistryService()
registered = service.register_unclassified_documents()

print(f"Documentos registrados: {len(registered)}")
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

if len(registered) > 0:
    db_path = os.getenv("RADAR_DOCENT_DB_PATH", "/mnt/data/radar_docent_cv.db")
    if is_push_configured():
        with sqlite3.connect(db_path) as conn:
            result = send_push_notification_to_all(
                conn,
                title="Nuevo documento publicado por Conselleria",
                body="La Conselleria ha publicado un nuevo documento. Pulsa para consultarlo.",
                url="/valencia-docentes",
            )
            conn.commit()
        print()
        print(f"Notificaciones push enviadas: {result}")
    else:
        print()
        print("Push no configurado: no se han enviado alertas.")
else:
    print("Sin nuevos documentos: no se envían alertas.")
