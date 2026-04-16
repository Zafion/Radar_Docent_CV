from app.services.gva_adjudicacion import GvaAdjudicacionService

service = GvaAdjudicacionService()

candidates = service.discover_pdf_links()
snapshot_path = service.save_discovery_snapshot(candidates)
downloads = service.download_unique_pdfs(candidates)
report_path = service.save_download_report(downloads)

new_count = sum(1 for item in downloads if item.status == "new_version_saved")
known_count = sum(1 for item in downloads if item.status == "already_known_hash")

print(f"Enlaces descubiertos: {len(candidates)}")
print(f"PDFs únicos procesados: {len(downloads)}")
print(f"Nuevas versiones guardadas: {new_count}")
print(f"Versiones ya conocidas por hash: {known_count}")
print(f"Snapshot guardado en: {snapshot_path}")
print(f"Reporte guardado en: {report_path}")
print()

for item in downloads:
    print(f"{item.status} -> {item.original_filename}")
    print(f"stored_filename: {item.stored_filename}")
    print(f"sha256: {item.sha256}")
    print(item.file_path)
    print(f"{item.size_bytes} bytes")
    print("-" * 80)