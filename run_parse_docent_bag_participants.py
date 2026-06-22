from app.services.docent_bag_participants_parser import DocentBagParticipantsParserService

service = DocentBagParticipantsParserService()
results = service.parse_all_documents()

for item in results:
    print(f"document_id={item['document_id']}")
    print(f"doc_family={item.get('doc_family')}")
    print(f"original_filename={item['original_filename']}")
    print(f"rows_extracted={item.get('rows_extracted', 0)}")
    print(f"snapshots_extracted={item.get('snapshots_extracted', 0)}")
    print(f"status={item['status']}")
    if "error" in item:
        print(f"error={item['error']}")
    print("-" * 80)
