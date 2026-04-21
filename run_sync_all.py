import sys

from app.services.discovery.registry import get_discovery_adapters
from app.services.document_sync import DocumentSyncService

sync_service = DocumentSyncService()
has_failures = False

for adapter in get_discovery_adapters():
    try:
        summary = sync_service.sync_adapter(adapter)

        print("=" * 100)
        print(summary["source_key"])
        print(summary["source_url"])
        print(f"Inicio: {summary['started_at']}")
        print(f"Fin: {summary['finished_at']}")
        print(f"Assets descubiertos: {summary['discovered_assets_count']}")
        print(f"Assets descargables: {summary['downloadable_assets_count']}")
        print(f"Nuevas versiones: {summary['new_versions_count']}")
        print(f"Versiones conocidas: {summary['known_versions_count']}")
        print(f"Duplicados en la misma ejecución: {summary['same_run_duplicate_count']}")
        print(f"No descargables: {summary['non_downloadable_count']}")
        print(f"Errores de descarga: {summary['download_error_count']}")
        print(f"Directorio: {summary['source_dir']}")
    except Exception as exc:
        has_failures = True
        print("=" * 100)
        print(adapter.source_key)
        print(adapter.source_url)
        print(f"ERROR: {exc}")

if has_failures:
    sys.exit(1)