from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from app.services.centers_import_service import import_centers_catalog


def apply_schema(db_path: Path, schema_path: Path) -> None:
    sql = schema_path.read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(sql)
        conn.commit()


def main() -> None:
    repo_root = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(description="Importa el catálogo de centros desde un Excel a SQLite")
    parser.add_argument(
        "--db-path",
        default=str(repo_root / "radar_docent_cv.db"),
        help="Ruta a la base de datos SQLite",
    )
    parser.add_argument(
        "--xlsx-path",
        default=str(repo_root / "data" / "manual" / "centers" / "Listado_Centros_Provincias.xlsx"),
        help="Ruta al Excel de centros",
    )
    parser.add_argument(
        "--schema-path",
        default=str(repo_root / "app" / "storage" / "schema.sql"),
        help="Ruta al schema.sql",
    )
    parser.add_argument(
        "--skip-schema",
        action="store_true",
        help="No reejecutar schema.sql antes de importar",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    xlsx_path = Path(args.xlsx_path)
    schema_path = Path(args.schema_path)

    if not args.skip_schema:
        apply_schema(db_path=db_path, schema_path=schema_path)

    summary = import_centers_catalog(db_path=db_path, xlsx_path=xlsx_path)

    print("Importación de centros completada")
    for key, value in summary.items():
        print(f"- {key}: {value}")


if __name__ == "__main__":
    main()
