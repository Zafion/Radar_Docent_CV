from app.services.document_registry import DocumentRegistryService

service = DocumentRegistryService()
registered = service.register_unclassified_documents()

print(f"Documentos registrados: {len(registered)}")
print()

for item in registered:
    print(f"document_id={item.document_id}")
    print(f"document_version_id={item.document_version_id}")
    print(f"source_key={item.source_key}")
    print(f"doc_family={item.doc_family}")
    print(f"title={item.title}")
    print(f"document_date_text={item.document_date_text}")
    print(f"document_date_iso={item.document_date_iso}")
    print(f"list_scope={item.list_scope}")
    print(f"original_filename={item.original_filename}")
    print("-" * 80)