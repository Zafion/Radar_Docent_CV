from app.services.discovery.non_docent import get_non_docent_discovery_adapters
from app.services.document_sync import DocumentSyncService


service = DocumentSyncService()

for adapter in get_non_docent_discovery_adapters():
    print("=" * 100)
    print(adapter.source_key)
    print(adapter.source_url)

    result = service.sync_adapter(adapter)

    for key, value in result.items():
        print(f"{key}: {value}")
