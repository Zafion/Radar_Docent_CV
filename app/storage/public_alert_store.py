from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any
from app.storage.notification_eligibility_store import eligible_event_sql, deliverable_event_sql


def _to_iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def serialize_alert(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    for key in ("detected_at", "created_at", "updated_at"):
        if key in item:
            item[key] = _to_iso(item[key])
    payload = item.get("payload_json")
    if isinstance(payload, str):
        try:
            item["payload_json"] = json.loads(payload)
        except json.JSONDecodeError:
            item["payload_json"] = {}
    return item


def public_base_url() -> str:
    return os.getenv("RADAR_PUBLIC_BASE_URL", "https://funkcionario.com").strip().rstrip("/")


def absolute_public_url(path_or_url: str) -> str:
    value = (path_or_url or "/").strip()
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if not value.startswith("/"):
        value = f"/{value}"
    return f"{public_base_url()}{value}"


def render_public_alert_text(*, title: str, summary: str, url: str) -> str:
    full_url = absolute_public_url(url)
    return "\n".join(
        [
            f"{title}",
            "",
            summary,
            "",
            f"Consultar en Funkcionario.com: {full_url}",
            "",
            "Aviso de detección de publicaciones oficiales. Comprueba siempre la fuente oficial para trámites o plazos.",
        ]
    )


def enqueue_public_alert(
    conn: Any,
    *,
    event_key: str,
    event_type: str,
    title: str,
    summary: str,
    public_url: str,
    source_url: str | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    row = conn.execute(
        """
        INSERT INTO public_alerts (
            event_key,
            event_type,
            title,
            summary,
            public_url,
            source_url,
            payload_json,
            detected_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
        ON CONFLICT (event_key) DO UPDATE
        SET updated_at = public_alerts.updated_at
        RETURNING id
        """,
        (event_key, event_type, title, summary, public_url, source_url, payload_json),
    ).fetchone()
    alert_id = int(row[0])

    rendered_text = render_public_alert_text(title=title, summary=summary, url=public_url)
    for channel, status in (
        ("web", "published"),
        ("rss", "published"),
        ("json", "published"),
        ("telegram", "pending"),
    ):
        conn.execute(
            """
            INSERT INTO notification_posts (
                alert_id,
                channel,
                rendered_text,
                status,
                published_at
            )
            VALUES (%s, %s, %s, %s, CASE WHEN %s = 'published' THEN NOW() ELSE NULL END)
            ON CONFLICT (alert_id, channel) DO NOTHING
            """,
            (alert_id, channel, rendered_text, status, status),
        )

    return alert_id


def list_public_alerts(conn: Any, *, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            id,
            event_key,
            event_type,
            title,
            summary,
            public_url,
            source_url,
            payload_json,
            detected_at,
            created_at,
            updated_at
        FROM public_alerts
        WHERE __ELIGIBLE_PUBLIC__
        ORDER BY detected_at DESC, id DESC
        LIMIT %s OFFSET %s
        """.replace("__ELIGIBLE_PUBLIC__",eligible_event_sql("public_alerts.event_key")),
        (limit, offset),
    ).fetchall()
    return [serialize_alert(dict(row)) for row in rows]


def count_public_alerts(conn: Any) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM public_alerts WHERE "+eligible_event_sql("public_alerts.event_key")).fetchone()[0])


def list_pending_notification_posts(
    conn: Any,
    *,
    channel: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    gate,gate_params=deliverable_event_sql("pa.event_key")
    rows = conn.execute(
        """
        SELECT
            np.id,
            np.alert_id,
            np.channel,
            np.rendered_text,
            np.status,
            np.created_at,
            pa.event_key,
            pa.event_type,
            pa.title,
            pa.public_url
        FROM notification_posts np
        JOIN public_alerts pa ON pa.id = np.alert_id
        WHERE np.channel = %s
          AND np.status = 'pending'
          AND __ELIGIBLE_PENDING__
          AND NOT EXISTS(SELECT 1 FROM notification_legacy_holds h WHERE h.channel='telegram' AND h.queue_id=np.id)
          AND NOT EXISTS(SELECT 1 FROM notification_delivery_attempts da WHERE da.channel='telegram' AND da.queue_id=np.id AND da.state NOT IN ('disabled','blocked'))
        ORDER BY np.created_at ASC, np.id ASC
        LIMIT %s
        """.replace("__ELIGIBLE_PENDING__",gate),
        (channel, *gate_params, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def mark_notification_post_published(
    conn: Any,
    *,
    post_id: int,
    external_message_id: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE notification_posts
        SET
            status = 'published',
            published_at = NOW(),
            error_message = NULL,
            external_message_id = %s
        WHERE id = %s
        """,
        (external_message_id, post_id),
    )


def mark_notification_post_failed(
    conn: Any,
    *,
    post_id: int,
    error_message: str,
) -> None:
    conn.execute(
        """
        UPDATE notification_posts
        SET
            status = 'failed',
            error_message = %s
        WHERE id = %s
        """,
        (error_message[:2000], post_id),
    )


def list_public_detections(conn, *, limit=50):
    """A separate, neutral history. Never reuse an unvalidated legacy promise."""
    rows=conn.execute("""SELECT 'legacy:'||p.id AS id,p.detected_at,p.source_url
        FROM public_alerts p WHERE NOT EXISTS(
            SELECT 1 FROM notification_document_state ns WHERE ns.queued_event_key=p.event_key)
        UNION ALL SELECT 'document:'||ns.document_id,ns.first_seen,ns.canonical_url
        FROM notification_document_state ns WHERE ns.phase<>'queued'
        ORDER BY detected_at DESC LIMIT %s""",(limit,)).fetchall()
    return [dict(id=r['id'],detected_at=_to_iso(r['detected_at']),source_url=r['source_url'],
                 information_kind='detection',title='Publicación oficial detectada',
                 summary='Detección registrada; el procesamiento de los datos no está acreditado para este aviso.') for r in rows]
