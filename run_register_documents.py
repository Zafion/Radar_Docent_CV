from __future__ import annotations

from app.services.document_registry import DocumentRegistryService, RegisteredDocument
from app.services.push_notifications import (
    is_push_configured,
    send_push_notification_to_all,
)
from app.storage.db import get_connection


service = DocumentRegistryService()
registered = service.register_unclassified_documents()


def is_difficult_coverage_document(item: RegisteredDocument) -> bool:
    return item.doc_family == "difficult_coverage_provisional" or (
        item.doc_family == "offered_positions" and item.list_scope == "dificil_cobertura"
    )


def is_offered_positions_document(item: RegisteredDocument) -> bool:
    return item.doc_family == "offered_positions" and item.list_scope != "dificil_cobertura"


def is_award_listing_document(item: RegisteredDocument) -> bool:
    return item.doc_family == "final_award_listing"


def build_push_batches(items: list[RegisteredDocument]) -> list[dict[str, str | int]]:
    offered_positions = [item for item in items if is_offered_positions_document(item)]
    difficult_coverage = [item for item in items if is_difficult_coverage_document(item)]
    award_listings = [item for item in items if is_award_listing_document(item)]

    batches: list[dict[str, str | int]] = []

    if offered_positions:
        count = len(offered_positions)
        batches.append(
            {
                "key": "offered_positions",
                "count": count,
                "title": "Nuevas plazas ofertadas",
                "body": (
                    "La Conselleria ha publicado una nueva actualización de plazas ofertadas. "
                    "Pulsa para consultarla."
                    if count == 1
                    else f"La Conselleria ha publicado {count} nuevas actualizaciones de plazas ofertadas. Pulsa para consultarlas."
                ),
                "url": "/plazas-ofertadas",
            }
        )

    if difficult_coverage:
        count = len(difficult_coverage)
        batches.append(
            {
                "key": "difficult_coverage",
                "count": count,
                "title": "Novedades de difícil cobertura",
                "body": (
                    "La Conselleria ha publicado una nueva actualización de difícil cobertura. "
                    "Pulsa para consultarla."
                    if count == 1
                    else f"La Conselleria ha publicado {count} nuevas actualizaciones de difícil cobertura. Pulsa para consultarlas."
                ),
                "url": "/dificil-cobertura",
            }
        )

    if award_listings:
        count = len(award_listings)
        batches.append(
            {
                "key": "final_award_listing",
                "count": count,
                "title": "Nuevas adjudicaciones publicadas",
                "body": (
                    "La Conselleria ha publicado un nuevo listado de adjudicaciones. "
                    "Pulsa para consultarlo."
                    if count == 1
                    else f"La Conselleria ha publicado {count} nuevos listados de adjudicaciones. Pulsa para consultarlos."
                ),
                "url": "/valencia-docentes",
            }
        )

    return batches


actionable = [
    item
    for item in registered
    if is_offered_positions_document(item)
    or is_difficult_coverage_document(item)
    or is_award_listing_document(item)
]
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

push_batches = build_push_batches(registered)

if push_batches:
    print()
    print("Resumen de notificaciones a enviar:")
    for batch in push_batches:
        print(f"- {batch['key']}: {batch['count']} documento(s)")
else:
    print("Sin nuevos documentos accionables: no se envían alertas.")

if push_batches and is_push_configured():
    conn = get_connection()
    results: list[dict[str, str | int | dict[str, int | str]]] = []

    try:
        for batch in push_batches:
            send_result = send_push_notification_to_all(
                conn,
                title=str(batch["title"]),
                body=str(batch["body"]),
                url=str(batch["url"]),
            )
            results.append(
                {
                    "key": str(batch["key"]),
                    "count": int(batch["count"]),
                    "result": send_result,
                }
            )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn._conn.close()

    print()
    print("Notificaciones push enviadas por tipo:")
    for item in results:
        print(f"- {item['key']}: {item['result']}")

elif push_batches:
    print()
    print("Push no configurado: no se han enviado alertas.")
