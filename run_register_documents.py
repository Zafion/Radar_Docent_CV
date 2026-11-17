"""Register detections only. Validated notices are reconciled separately."""
import argparse
from app.services.document_registry import DocumentRegistryService

intent="unknown"
if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin',choices=('live_new','historical_import','reprocess','unknown'),default='unknown')
    intent=parser.parse_known_args()[0].origin
registered=DocumentRegistryService().register_unclassified_documents(notification_intent=intent)
print(f"Documentos detectados/registrados: {len(registered)}; no se crean avisos antes de validación.")
