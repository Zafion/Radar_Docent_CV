import sys

from app.services.document_registry import DocumentRegistryService
from app.services.non_docent_parser import NonDocentParserService


registered = DocumentRegistryService().register_unclassified_documents()
print("=" * 100)
print("document_registry")
print(f"Documentos registrados: {len(registered)}")

for item in registered:
    if item.doc_family.startswith("non_docent"):
        print(
            f"document_id={item.document_id} "
            f"doc_family={item.doc_family} "
            f"original_filename={item.original_filename}"
        )

print("=" * 100)
print("non_docent_parser")

results = NonDocentParserService().parse_all_documents()
has_failures = False

print(f"Documentos procesados: {len(results)}")
for item in results:
    print("-" * 80)
    print(f"document_id={item['document_id']}")
    print(f"doc_family={item.get('doc_family')}")
    print(f"original_filename={item['original_filename']}")
    print(f"rows_extracted={item['rows_extracted']}")
    print(f"status={item['status']}")
    if item["status"] == "failed":
        has_failures = True
        print(f"error={item.get('error')}")

if has_failures:
    sys.exit(1)
