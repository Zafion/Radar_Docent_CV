"""Bounded first-live parsing; deliberately does not run matcher or reconciliation."""
from app.services.parse_selection import selective_live_selection, selection_scope
from app.services.offered_positions_parser import OfferedPositionsParserService
from app.services.final_award_listing_maestros_parser import FinalAwardListingMaestrosParserService
from app.services.final_award_listing_secundaria_parser import FinalAwardListingSecundariaParserService
from app.services.difficult_coverage_provisional_parser import DifficultCoverageProvisionalParserService
from app.services.docent_bag_participants_parser import DocentBagParticipantsParserService
from app.services.non_docent_parser import NonDocentParserService


def parse_selected(entries):
    selection = selective_live_selection(entries)
    counts = {'processed': 0, 'failed': 0}
    services = (OfferedPositionsParserService, FinalAwardListingMaestrosParserService,
                FinalAwardListingSecundariaParserService, DifficultCoverageProvisionalParserService,
                DocentBagParticipantsParserService, NonDocentParserService)
    with selection_scope(selection):
        for service in services:
            for result in service().parse_all_documents():
                counts['processed'] += 1
                counts['failed'] += result.get('status') == 'failed'
    return counts
