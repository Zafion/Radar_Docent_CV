from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import unicodedata
from typing import Optional

from pypdf import PdfReader

from app.storage.award_results_store import AwardResultsStore


PARSER_KEY = "final_award_listing_maestros_parser"
PARSER_VERSION = "0.1.0"

ENTRY_START_WITH_NAME_RE = re.compile(r"^(?P<order>\d{1,5})\s+(?P<name>.+,\s*.+)$")
ENTRY_START_ONLY_ORDER_RE = re.compile(r"^(?P<order>\d{1,5})$")
CENTER_LINE_RE = re.compile(
    r"^(?P<locality>.+?)\((?P<center_code>\d{8})\)(?P<center_name>.+)$"
)
SPECIALTY_LINE_RE = re.compile(
    r"^(?P<code>[0-9A-Z]{2,5})\s*/\s*(?P<name>.+)$"
)
POSITION_CODE_RE = re.compile(r"^\d{6}$")
HOURS_LINE_RE = re.compile(r"^(?P<hours>\d{1,2}(?:,\d+)?)\s+horas?$", re.IGNORECASE)
PETITION_LINE_RE = re.compile(
    r"^(?P<name>.+?)\s+Petici[oó]n:\s*(?P<request_type>.+?)\s+(?P<petition_number>\d+)$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ParsedAssignment:
    assignment_kind: Optional[str]
    locality: Optional[str]
    center_code: Optional[str]
    center_name: Optional[str]
    position_specialty_code: Optional[str]
    position_specialty_name: Optional[str]
    position_code: Optional[str]
    hours_text: Optional[str]
    hours_value: Optional[float]
    petition_text: Optional[str]
    petition_number: Optional[int]
    request_type: Optional[str]
    raw_assignment_text: str


@dataclass(slots=True)
class ParsedAwardResult:
    order_number: Optional[int]
    person_display_name: str
    person_name_normalized: str
    status: str
    raw_block_text: str
    assignment: ParsedAssignment | None


class FinalAwardListingMaestrosParserService:
    def parse_all_documents(self) -> list[dict]:
        store = AwardResultsStore()
        summaries: list[dict] = []

        try:
            documents = store.list_final_listing_documents(
                list_scope="maestros",
                parser_key=PARSER_KEY,
                parser_version=PARSER_VERSION,
            )

            for document in documents:
                started_at = self._utc_now_iso()
                parse_run_id = store.create_parse_run(
                    document_version_id=int(document["document_version_id"]),
                    parser_key=PARSER_KEY,
                    parser_version=PARSER_VERSION,
                    started_at=started_at,
                )

                try:
                    rows = self._parse_document(document)
                    store.clear_award_results_for_document(int(document["document_id"]))

                    for row in rows:
                        award_result_id = store.insert_award_result(
                            document_id=int(document["document_id"]),
                            list_scope="maestros",
                            body_code=None,
                            body_name="MESTRES / MAESTROS",
                            specialty_code=None,
                            specialty_name=None,
                            order_number=row.order_number,
                            person_display_name=row.person_display_name,
                            person_name_normalized=row.person_name_normalized,
                            status=row.status,
                            raw_block_text=row.raw_block_text,
                        )

                        if row.assignment is not None:
                            store.insert_award_assignment(
                                award_result_id=award_result_id,
                                assignment_kind=row.assignment.assignment_kind,
                                locality=row.assignment.locality,
                                center_code=row.assignment.center_code,
                                center_name=row.assignment.center_name,
                                position_specialty_code=row.assignment.position_specialty_code,
                                position_specialty_name=row.assignment.position_specialty_name,
                                position_code=row.assignment.position_code,
                                hours_text=row.assignment.hours_text,
                                hours_value=row.assignment.hours_value,
                                petition_text=row.assignment.petition_text,
                                petition_number=row.assignment.petition_number,
                                request_type=row.assignment.request_type,
                                matched_offered_position_id=None,
                                raw_assignment_text=row.assignment.raw_assignment_text,
                            )

                    finished_at = self._utc_now_iso()
                    store.finish_parse_run(
                        parse_run_id=parse_run_id,
                        finished_at=finished_at,
                        status="success",
                        rows_extracted=len(rows),
                        error_message=None,
                    )
                    store.mark_document_parsed(
                        document_id=int(document["document_id"]),
                        parsed_at=finished_at,
                    )

                    summaries.append(
                        {
                            "document_id": int(document["document_id"]),
                            "original_filename": document["original_filename"],
                            "rows_extracted": len(rows),
                            "status": "success",
                        }
                    )

                except Exception as exc:
                    finished_at = self._utc_now_iso()
                    store.finish_parse_run(
                        parse_run_id=parse_run_id,
                        finished_at=finished_at,
                        status="failed",
                        rows_extracted=0,
                        error_message=str(exc),
                    )
                    summaries.append(
                        {
                            "document_id": int(document["document_id"]),
                            "original_filename": document["original_filename"],
                            "rows_extracted": 0,
                            "status": "failed",
                            "error": str(exc),
                        }
                    )

            return summaries

        finally:
            store.close()

    def _parse_document(self, document) -> list[ParsedAwardResult]:
        file_path = self._resolve_file_path(document["file_path"])
        lines = self._extract_clean_lines(file_path)
        blocks = self._split_into_blocks(lines)

        parsed: list[ParsedAwardResult] = []

        for block in blocks:
            row = self._parse_block(block)
            if row is not None:
                parsed.append(row)

        return parsed

    def _split_into_blocks(self, lines: list[str]) -> list[list[str]]:
        blocks: list[list[str]] = []
        current: list[str] = []

        for index, line in enumerate(lines):
            if self._is_entry_start(lines, index):
                if current:
                    blocks.append(current)
                current = [line]
            else:
                if current:
                    current.append(line)

        if current:
            blocks.append(current)

        return blocks

    def _is_entry_start(self, lines: list[str], index: int) -> bool:
        line = lines[index]

        if ENTRY_START_WITH_NAME_RE.match(line):
            return True

        only_order_match = ENTRY_START_ONLY_ORDER_RE.match(line)
        if not only_order_match:
            return False

        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        return self._is_assignment_kind_line(next_line)

    def _parse_block(self, block_lines: list[str]) -> ParsedAwardResult | None:
        cleaned_lines = [self._normalize_spaces(line) for line in block_lines if self._normalize_spaces(line)]
        if not cleaned_lines:
            return None

        first_line = cleaned_lines[0]
        order_number: Optional[int] = None
        person_display_name: Optional[str] = None

        match = ENTRY_START_WITH_NAME_RE.match(first_line)
        if match:
            order_number = int(match.group("order"))
            person_display_name = match.group("name").strip()
            remaining_lines = cleaned_lines[1:]
        else:
            match = ENTRY_START_ONLY_ORDER_RE.match(first_line)
            if not match:
                return None
            order_number = int(match.group("order"))
            remaining_lines = cleaned_lines[1:]

        status = self._extract_status(cleaned_lines)
        if status is None:
            return None

        assignment = None

        if status == "Adjudicat":
            assignment = self._parse_assignment(remaining_lines)

            if assignment is not None and assignment.petition_text:
                person_display_name = assignment.petition_text

        if not person_display_name:
            return None

        return ParsedAwardResult(
            order_number=order_number,
            person_display_name=person_display_name,
            person_name_normalized=self._normalize_person_name(person_display_name),
            status=status,
            raw_block_text="\n".join(cleaned_lines),
            assignment=assignment,
        )

    def _parse_assignment(self, lines: list[str]) -> ParsedAssignment | None:
        if not lines:
            return None

        assignment_kind: Optional[str] = None
        locality: Optional[str] = None
        center_code: Optional[str] = None
        center_name: Optional[str] = None
        specialty_code: Optional[str] = None
        specialty_name: Optional[str] = None
        position_code: Optional[str] = None
        hours_text: Optional[str] = None
        hours_value: Optional[float] = None
        petition_text: Optional[str] = None
        petition_number: Optional[int] = None
        request_type: Optional[str] = None

        assignment_lines: list[str] = []

        for line in lines:
            if line == "Adjudicat":
                continue

            assignment_lines.append(line)

            if assignment_kind is None and self._is_assignment_kind_line(line):
                assignment_kind = line
                continue

            if center_code is None:
                center_match = CENTER_LINE_RE.match(line)
                if center_match:
                    locality = center_match.group("locality").strip()
                    center_code = center_match.group("center_code").strip()
                    center_name = center_match.group("center_name").strip()
                    continue

            if specialty_code is None:
                specialty_match = SPECIALTY_LINE_RE.match(line)
                if specialty_match:
                    specialty_code = specialty_match.group("code").strip()
                    specialty_name = specialty_match.group("name").strip()
                    continue

            if position_code is None and POSITION_CODE_RE.match(line):
                position_code = line
                continue

            if hours_text is None:
                hours_match = HOURS_LINE_RE.match(line)
                if hours_match:
                    hours_text = hours_match.group("hours")
                    hours_value = self._coerce_hours(hours_text)
                    continue

            petition_match = PETITION_LINE_RE.match(line)
            if petition_match:
                petition_text = petition_match.group("name").strip()
                request_type = petition_match.group("request_type").strip()
                petition_number = int(petition_match.group("petition_number"))
                continue

        if assignment_kind is None and not assignment_lines:
            return None

        return ParsedAssignment(
            assignment_kind=assignment_kind,
            locality=locality,
            center_code=center_code,
            center_name=center_name,
            position_specialty_code=specialty_code,
            position_specialty_name=specialty_name,
            position_code=position_code,
            hours_text=hours_text,
            hours_value=hours_value,
            petition_text=petition_text,
            petition_number=petition_number,
            request_type=request_type,
            raw_assignment_text="\n".join(assignment_lines),
        )

    def _extract_status(self, lines: list[str]) -> Optional[str]:
        statuses = (
            "Adjudicat",
            "No adjudicat",
            "Ha participat",
            "No ha participat",
            "Desactivat",
        )

        for line in reversed(lines):
            for status in statuses:
                if line == status:
                    return status

        return None

    def _extract_clean_lines(self, pdf_path: Path) -> list[str]:
        reader = PdfReader(str(pdf_path))
        lines: list[str] = []

        for page in reader.pages:
            text = page.extract_text() or ""
            for raw_line in text.splitlines():
                line = self._normalize_spaces(raw_line)
                if not line:
                    continue
                if self._should_ignore_line(line):
                    continue
                lines.append(line)

        return lines

    def _should_ignore_line(self, line: str) -> bool:
        upper = line.upper()

        if re.fullmatch(r"\d{2}/\d{2}/\d{4}", line):
            return True

        ignored_prefixes = (
            "ADJUDICACIÓ DE PERSONAL DOCENT INTERÍ DIA",
            "ADJUDICACIÓN DE PERSONAL DOCENTE INTERINO DÍA",
            "MESTRES / MAESTROS",
            "MESTRES",
            "PÀG ",
            "PÁG ",
            "PAG ",
        )

        return upper.startswith(ignored_prefixes)

    def _is_assignment_kind_line(self, line: str) -> bool:
        upper = line.upper()
        return upper in {
            "VACANT",
            "SUBSTITUCIÓ DETERMINADA",
            "SUBSTITUCIÓ INDETERMINADA",
        }

    def _normalize_person_name(self, value: str) -> str:
        value = value.strip().upper()
        value = unicodedata.normalize("NFKD", value)
        value = "".join(char for char in value if not unicodedata.combining(char))
        value = re.sub(r"\s+", " ", value)
        return value

    def _coerce_hours(self, value: Optional[str]) -> Optional[float]:
        if not value:
            return None

        try:
            return float(value.replace(",", "."))
        except ValueError:
            return None

    def _normalize_spaces(self, value: str) -> str:
        return " ".join(value.split())

    def _resolve_file_path(self, file_path: str) -> Path:
        path = Path(file_path)
        if path.is_absolute():
            return path

        base_dir = Path(__file__).resolve().parents[2]
        return base_dir / path

    def _utc_now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()