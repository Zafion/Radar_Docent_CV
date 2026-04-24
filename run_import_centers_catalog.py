from __future__ import annotations

import argparse
from pathlib import Path

from app.services.centers_import_service import import_centers_catalog
from app.storage.db import init_db


def resolve_default_xlsx_path(repo_root: Path) -> Path:
    automated = repo_root / "data" / "raw" / "centers_catalog" / "all" / "Listado_Centros_Provincias.xlsx"
    legacy_automated = repo_root / "data" / "raw" / "centers_catalog" / "Listado_Centros_Provincias.xlsx"
    manual = repo_root / "data" / "manual" / "centers" / "Listado_Centros_Provincias.xlsx"

    if automated.exists():
        return automated
    if legacy_automated.exists():
        return legacy_automated
    return manual


def main() -> None:
    repo_root = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(
        description="Importa el catálogo de centros desde un Excel a PostgreSQL"
    )
    parser.add_argument(
        "--xlsx-path",
        default=None,
        help="Ruta al Excel de centros. Si no se indica, prioriza data/raw/centers_catalog/all, luego la ruta legacy y después data/manual/centers",
    )
    parser.add_argument(
        "--schema-path",
        default=str(repo_root / "app" / "storage" / "schema.sql"),
        help="Ruta al schema.sql de PostgreSQL",
    )
    parser.add_argument(
        "--skip-schema",
        action="store_true",
        help="No reejecutar schema.sql antes de importar",
    )
    args = parser.parse_args()

    xlsx_path = Path(args.xlsx_path) if args.xlsx_path else resolve_default_xlsx_path(repo_root)
    schema_path = Path(args.schema_path)

    if not args.skip_schema:
        db_url = init_db(schema_path=str(schema_path))
        print(f"Esquema PostgreSQL inicializado en: {db_url}")

    summary = import_centers_catalog(xlsx_path=xlsx_path)

    print("Importación de centros completada")
    for key, value in summary.items():
        print(f"- {key}: {value}")


if __name__ == "__main__":
    main()