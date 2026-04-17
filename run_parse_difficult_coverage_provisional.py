from app.services.difficult_coverage_provisional_parser import (
    DifficultCoverageProvisionalParserService,
)

service = DifficultCoverageProvisionalParserService()
results = service.parse_all_documents()

print(f"Documentos procesados: {len(results)}")
print()

for item in results:
    print(f"document_id={item['document_id']}")
    print(f"original_filename={item['original_filename']}")
    print(f"positions_extracted={item['positions_extracted']}")
    print(f"candidates_extracted={item['candidates_extracted']}")
    print(f"status={item['status']}")
    if 'error' in item:
        print(f"error={item['error']}")
    print("-" * 80)