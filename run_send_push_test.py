from __future__ import annotations

from app.services.push_notifications import is_push_configured, send_push_notification_to_all
from app.storage.db import get_connection


conn = get_connection()
try:
    print("push_configured:", is_push_configured())
    result = send_push_notification_to_all(
        conn,
        title="Prueba de alertas Funkcionario",
        body="Si ves esto, las alertas push funcionan.",
        url="/valencia-docentes",
    )
    conn.commit()
    print(result)
finally:
    conn._conn.close()
