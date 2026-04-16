from app.services.gva_adjudicacion import GvaAdjudicacionService

service = GvaAdjudicacionService()
items = service.discover_pdf_links()

for item in items:
    print(f"[{item.section}] {item.title}")
    print(item.url)
    print("-" * 80)