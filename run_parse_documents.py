import sys

from app.services.document_parse_dispatcher import DocumentParseDispatcherService

service = DocumentParseDispatcherService()
result = service.run()

has_failures = False

for summary in result.parser_summaries:
    print("=" * 100)
    print(summary.parser_name)
    print(f"Documentos procesados: {summary.documents_processed}")
    print(f"Correctos: {summary.success_count}")
    print(f"Fallidos: {summary.failed_count}")
    print()

    for item in summary.raw_results:
        print(f"document_id={item['document_id']}")
        print(f"original_filename={item['original_filename']}")

        if 'rows_extracted' in item:
            print(f"rows_extracted={item['rows_extracted']}")
        if 'positions_extracted' in item:
            print(f"positions_extracted={item['positions_extracted']}")
        if 'candidates_extracted' in item:
            print(f"candidates_extracted={item['candidates_extracted']}")

        print(f"status={item['status']}")
        if 'error' in item:
            has_failures = True
            print(f"error={item['error']}")
        print("-" * 80)

print("=" * 100)
print("assignment_matcher")
print(f"Ejecutado: {'sí' if result.matcher_executed else 'no'}")
if result.matcher_executed:
    print(f"Asignaciones procesadas: {result.matcher_total_assignments}")
    print(f"Matched exactos: {result.matcher_matched_exact}")
    print(f"Matched refinados: {result.matcher_matched_refined}")
    print(f"Ambiguos: {result.matcher_ambiguous}")
    print(f"Sin match: {result.matcher_no_match}")

if has_failures:
    sys.exit(1)
