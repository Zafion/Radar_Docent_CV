from __future__ import annotations

import argparse
from pathlib import Path

from app.services.centers_catalog_sync_service import CentersCatalogSyncService
from app.storage.db import init_db


def main() -> None:
    repo_root = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(
        description="Descarga automáticamente el catálogo de centros y lo importa en PostgreSQL si cambia"
    )
    parser.add_argument(
        "--schema-path",
        default=str(repo_root / "app" / "storage" / "schema.sql"),
        help="Ruta al schema.sql de PostgreSQL",
    )
    parser.add_argument(
        "--raw-dir",
        default=str(repo_root / "data" / "raw" / "centers_catalog"),
        help="Directorio donde guardar la respuesta JSON, el XLSX y el sha256",
    )
    parser.add_argument(
        "--cod-provincia",
        default="",
        help="Código de provincia. Vacío = todas las provincias",
    )
    parser.add_argument(
        "--skip-schema",
        action="store_true",
        help="No reejecutar schema.sql antes de sincronizar",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Abrir el navegador de Playwright en modo visible",
    )
    args = parser.parse_args()

    schema_path = Path(args.schema_path)
    raw_dir = Path(args.raw_dir)

    if not args.skip_schema:
        db_url = init_db(schema_path=str(schema_path))
        print(f"Esquema PostgreSQL inicializado en: {db_url}")

    service = CentersCatalogSyncService()
    summary = service.sync(
        raw_dir=raw_dir,
        cod_provincia=args.cod_provincia,
        headless=not args.headful,
    )

    print("Sincronización del catálogo de centros finalizada")
    for key, value in summary.items():
        print(f"- {key}: {value}")

    if summary["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()