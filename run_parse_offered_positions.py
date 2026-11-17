from app.services.parse_cli import automatic_entrypoint
if __name__ == '__main__':
    automatic_entrypoint()

import sys

from app.services.offered_positions_parser import OfferedPositionsParserService

service = OfferedPositionsParserService()
results = service.parse_all_documents()

print(f"Documentos procesados: {len(results)}")
print()

has_failures = False

for item in results:
    print(f"document_id={item['document_id']}")
    print(f"original_filename={item['original_filename']}")
    print(f"rows_extracted={item['rows_extracted']}")
    print(f"status={item['status']}")
    if "error" in item:
        print(f"error={item['error']}")
    if item["status"] == "failed":
        has_failures = True
    print("-" * 80)


from app.services.notification_eligibility import reconcile_if_configured
reconcile_if_configured()

if has_failures:
    sys.exit(1)
