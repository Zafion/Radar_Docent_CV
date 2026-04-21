from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.assignment_matcher import AssignmentMatcherService
from app.services.difficult_coverage_provisional_parser import (
    DifficultCoverageProvisionalParserService,
)
from app.services.final_award_listing_maestros_parser import (
    FinalAwardListingMaestrosParserService,
)
from app.services.final_award_listing_secundaria_parser import (
    FinalAwardListingSecundariaParserService,
)
from app.services.offered_positions_parser import OfferedPositionsParserService


@dataclass(slots=True)
class ParserDispatchSummary:
    parser_name: str
    documents_processed: int
    success_count: int
    failed_count: int
    raw_results: list[dict[str, Any]]


@dataclass(slots=True)
class ParseDispatchRunResult:
    parser_summaries: list[ParserDispatchSummary]
    matcher_executed: bool
    matcher_total_assignments: int
    matcher_matched_exact: int
    matcher_matched_refined: int
    matcher_ambiguous: int
    matcher_no_match: int


class DocumentParseDispatcherService:
    def run(self) -> ParseDispatchRunResult:
        parser_definitions = [
            ("offered_positions", OfferedPositionsParserService()),
            ("final_award_listing_maestros", FinalAwardListingMaestrosParserService()),
            ("final_award_listing_secundaria", FinalAwardListingSecundariaParserService()),
            ("difficult_coverage_provisional", DifficultCoverageProvisionalParserService()),
        ]

        parser_summaries: list[ParserDispatchSummary] = []
        should_run_matcher = False

        for parser_name, service in parser_definitions:
            raw_results = service.parse_all_documents()
            success_count = sum(1 for item in raw_results if item.get("status") == "success")
            failed_count = sum(1 for item in raw_results if item.get("status") == "failed")

            parser_summaries.append(
                ParserDispatchSummary(
                    parser_name=parser_name,
                    documents_processed=len(raw_results),
                    success_count=success_count,
                    failed_count=failed_count,
                    raw_results=raw_results,
                )
            )

            if success_count > 0 and parser_name in {
                "offered_positions",
                "final_award_listing_maestros",
                "final_award_listing_secundaria",
            }:
                should_run_matcher = True

        matcher_total_assignments = 0
        matcher_matched_exact = 0
        matcher_matched_refined = 0
        matcher_ambiguous = 0
        matcher_no_match = 0

        if should_run_matcher:
            matcher_results = AssignmentMatcherService().match_all()
            matcher_total_assignments = len(matcher_results)
            matcher_matched_exact = sum(
                1
                for item in matcher_results
                if item.match_status == "matched_exact_position_code"
            )
            matcher_matched_refined = sum(
                1
                for item in matcher_results
                if item.match_status == "matched_refined"
            )
            matcher_ambiguous = sum(
                1
                for item in matcher_results
                if item.match_status == "ambiguous_multiple_candidates"
            )
            matcher_no_match = sum(
                1
                for item in matcher_results
                if item.match_status == "no_match"
            )

        return ParseDispatchRunResult(
            parser_summaries=parser_summaries,
            matcher_executed=should_run_matcher,
            matcher_total_assignments=matcher_total_assignments,
            matcher_matched_exact=matcher_matched_exact,
            matcher_matched_refined=matcher_matched_refined,
            matcher_ambiguous=matcher_ambiguous,
            matcher_no_match=matcher_no_match,
        )
