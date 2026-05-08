from __future__ import annotations

from app.services.telegram_notifications import is_telegram_configured, send_telegram_message
from app.storage.public_alert_store import absolute_public_url


if __name__ == "__main__":
    print(f"telegram_configured: {is_telegram_configured()}")
    result = send_telegram_message(
        "Prueba de avisos Funkcionario\n\n"
        "Si ves este mensaje, el canal de Telegram ya recibe publicaciones automáticas.\n\n"
        f"{absolute_public_url('/avisos')}"
    )
    print(result)
