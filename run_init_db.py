from app.storage.sqlite import init_db

db_path = init_db()
print(f"Base de datos inicializada en: {db_path}")