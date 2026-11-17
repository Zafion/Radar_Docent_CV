from app.services.parse_cli import automatic_entrypoint
if __name__ == '__main__':
    automatic_entrypoint()

from app.services.final_award_listing_maestros_parser import (
    FinalAwardListingMaestrosParserService,
)

service = FinalAwardListingMaestrosParserService()
results = service.parse_all_documents()

print(f"Documentos procesados: {len(results)}")
print()

for item in results:
    print(f"document_id={item['document_id']}")
    print(f"original_filename={item['original_filename']}")
    print(f"rows_extracted={item['rows_extracted']}")
    print(f"status={item['status']}")
    if "error" in item:
        print(f"error={item['error']}")
    print("-" * 80)
from app.services.notification_eligibility import reconcile_if_configured
reconcile_if_configured()
