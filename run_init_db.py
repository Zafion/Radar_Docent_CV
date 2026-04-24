from app.storage.db import init_db

db_url = init_db()
print(f"Base de datos PostgreSQL inicializada en: {db_url}")