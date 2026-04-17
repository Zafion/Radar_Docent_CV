from __future__ import annotations

from dataclasses import dataclass
import unicodedata
import re

from app.storage.assignment_matching_store import AssignmentMatchingStore


@dataclass(slots=True)
class MatchResult:
    assignment_id: int
    position_code: str
    matched_offered_position_id: int | None
    match_status: str


class AssignmentMatcherService:
    def match_all(self) -> list[MatchResult]:
        store = AssignmentMatchingStore()
        results: list[MatchResult] = []

        try:
            assignments = store.list_unmatched_award_assignments()

            for row in assignments:
                candidates = store.find_candidate_offered_positions(
                    source_id=int(row["source_id"]),
                    document_date_iso=row["document_date_iso"],
                    position_code=row["position_code"],
                )

                matched_id = None
                status = "no_match"

                if len(candidates) == 1:
                    matched_id = int(candidates[0]["id"])
                    status = "matched_exact_position_code"
                elif len(candidates) > 1:
                    refined = self._refine_candidates(row, candidates)
                    if len(refined) == 1:
                        matched_id = int(refined[0]["id"])
                        status = "matched_refined"
                    else:
                        status = "ambiguous_multiple_candidates"

                if matched_id is not None:
                    store.set_assignment_match(
                        assignment_id=int(row["assignment_id"]),
                        offered_position_id=matched_id,
                    )

                results.append(
                    MatchResult(
                        assignment_id=int(row["assignment_id"]),
                        position_code=row["position_code"],
                        matched_offered_position_id=matched_id,
                        match_status=status,
                    )
                )

            return results

        finally:
            store.close()

    def _refine_candidates(self, assignment_row, candidates):
        center_code = assignment_row["center_code"]
        locality = self._norm(assignment_row["locality"])
        specialty_code = assignment_row["position_specialty_code"]

        refined = candidates

        if center_code:
            same_center = [c for c in refined if c["center_code"] == center_code]
            if same_center:
                refined = same_center

        if specialty_code:
            same_specialty = [c for c in refined if c["specialty_code"] == specialty_code]
            if same_specialty:
                refined = same_specialty

        if locality:
            same_locality = [
                c for c in refined
                if self._norm(c["locality"]) == locality
            ]
            if same_locality:
                refined = same_locality

        return refined

    def _norm(self, value: str | None) -> str | None:
        if value is None:
            return None

        value = value.strip().upper()
        value = unicodedata.normalize("NFKD", value)
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        value = re.sub(r"\s+", " ", value)
        return value