from __future__ import annotations

from app.services.document_registry import DocumentRegistryService, RegisteredDocument
from app.services.push_notifications import is_push_configured
from app.storage.db import get_connection
from app.storage.push_event_store import enqueue_push_notification_event
from run_send_notifications import main as send_pending_notifications


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


def is_non_docent_adc_call(item: RegisteredDocument) -> bool:
    return item.doc_family == "non_docent_adc_call"


def is_non_docent_adc_award(item: RegisteredDocument) -> bool:
    return item.doc_family == "non_docent_adc_award"


def is_non_docent_bag_document(item: RegisteredDocument) -> bool:
    return item.doc_family in {"non_docent_bag_update", "non_docent_funcion_publica_bag"}


def document_ids(items: list[RegisteredDocument]) -> list[int]:
    return sorted(int(item.document_id) for item in items)


def build_event_key(key: str, items: list[RegisteredDocument]) -> str:
    ids = "-".join(str(item) for item in document_ids(items))
    return f"registered_documents:{key}:{ids}"


def build_push_batches(items: list[RegisteredDocument]) -> list[dict[str, object]]:
    offered_positions = [item for item in items if is_offered_positions_document(item)]
    difficult_coverage = [item for item in items if is_difficult_coverage_document(item)]
    award_listings = [item for item in items if is_award_listing_document(item)]
    non_docent_adc_calls = [item for item in items if is_non_docent_adc_call(item)]
    non_docent_adc_awards = [item for item in items if is_non_docent_adc_award(item)]
    non_docent_bags = [item for item in items if is_non_docent_bag_document(item)]

    batches: list[dict[str, object]] = []

    def add_batch(
        *,
        key: str,
        event_type: str,
        selected: list[RegisteredDocument],
        title: str,
        singular_body: str,
        plural_body: str,
        url: str,
    ) -> None:
        if not selected:
            return
        count = len(selected)
        batches.append(
            {
                "key": key,
                "event_key": build_event_key(key, selected),
                "event_type": event_type,
                "count": count,
                "title": title,
                "body": singular_body if count == 1 else plural_body.format(count=count),
                "url": url,
                "document_ids": document_ids(selected),
            }
        )

    add_batch(
        key="offered_positions",
        event_type="docent_offered_positions_new",
        selected=offered_positions,
        title="Nuevas plazas ofertadas",
        singular_body="La Conselleria ha publicado una nueva actualización de plazas ofertadas. Pulsa para consultarla.",
        plural_body="La Conselleria ha publicado {count} nuevas actualizaciones de plazas ofertadas. Pulsa para consultarlas.",
        url="/plazas-ofertadas",
    )
    add_batch(
        key="difficult_coverage",
        event_type="docent_difficult_coverage_new",
        selected=difficult_coverage,
        title="Novedades de difícil cobertura",
        singular_body="La Conselleria ha publicado una nueva actualización de difícil cobertura. Pulsa para consultarla.",
        plural_body="La Conselleria ha publicado {count} nuevas actualizaciones de difícil cobertura. Pulsa para consultarlas.",
        url="/dificil-cobertura",
    )
    add_batch(
        key="final_award_listing",
        event_type="docent_awards_new",
        selected=award_listings,
        title="Nuevas adjudicaciones publicadas",
        singular_body="La Conselleria ha publicado un nuevo listado de adjudicaciones. Pulsa para consultarlo.",
        plural_body="La Conselleria ha publicado {count} nuevos listados de adjudicaciones. Pulsa para consultarlos.",
        url="/valencia-docentes",
    )
    add_batch(
        key="non_docent_adc_call",
        event_type="non_docent_adc_call_new",
        selected=non_docent_adc_calls,
        title="Nuevas plazas no docentes",
        singular_body="La Conselleria ha publicado una nueva convocatoria ADC no docente. Pulsa para consultar las plazas.",
        plural_body="La Conselleria ha publicado {count} nuevas convocatorias ADC no docentes. Pulsa para consultar las plazas.",
        url="/no-docente/plazas",
    )
    add_batch(
        key="non_docent_adc_award",
        event_type="non_docent_adc_award_new",
        selected=non_docent_adc_awards,
        title="Nuevas adjudicaciones no docentes",
        singular_body="La Conselleria ha publicado una nueva adjudicación ADC no docente. Pulsa para consultarla.",
        plural_body="La Conselleria ha publicado {count} nuevas adjudicaciones ADC no docentes. Pulsa para consultarlas.",
        url="/no-docente/adjudicaciones",
    )
    add_batch(
        key="non_docent_bags",
        event_type="non_docent_bags_new",
        selected=non_docent_bags,
        title="Novedades en bolsas no docentes",
        singular_body="La Conselleria ha publicado una nueva actualización de bolsas no docentes. Pulsa para consultarla.",
        plural_body="La Conselleria ha publicado {count} nuevas actualizaciones de bolsas no docentes. Pulsa para consultarlas.",
        url="/no-docente/consulta-persona",
    )

    return batches


actionable = [
    item
    for item in registered
    if is_offered_positions_document(item)
    or is_difficult_coverage_document(item)
    or is_award_listing_document(item)
    or is_non_docent_adc_call(item)
    or is_non_docent_adc_award(item)
    or is_non_docent_bag_document(item)
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
    print("Resumen de eventos push a registrar:")
    conn = get_connection()
    try:
        for batch in push_batches:
            print(f"- {batch['key']}: {batch['count']} documento(s)")
            enqueue_push_notification_event(
                conn,
                event_key=str(batch["event_key"]),
                event_type=str(batch["event_type"]),
                title=str(batch["title"]),
                body=str(batch["body"]),
                url=str(batch["url"]),
                payload={"document_ids": batch["document_ids"], "count": batch["count"]},
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn._conn.close()

    if is_push_configured():
        send_pending_notifications()
    else:
        print()
        print("Push no configurado: eventos registrados como pendientes, pero no enviados.")
else:
    print("Sin nuevos documentos accionables: no se registran eventos push.")
