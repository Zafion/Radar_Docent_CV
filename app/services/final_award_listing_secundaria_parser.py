from __future__ import annotations

from app.services.parse_selection import locked_documents

from dataclasses import dataclass
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import re
import unicodedata
from typing import Optional

from pypdf import PdfReader

from app.storage.award_results_store import AwardResultsStore
from app.services.career_practice_award_parser import (
    CareerAwardValidationError, is_career_layout, parse_career_awards,
)


PARSER_KEY = "final_award_listing_secundaria_parser"
PARSER_VERSION = "0.4.2"

ENTRY_START_WITH_NAME_RE = re.compile(
    r"^(?P<order>\d{1,5})(?:\s*/\s*\d{1,5})?\s*"
    r"(?P<name>[A-ZÁÉÍÓÚÀÈÌÒÙÄËÏÖÜÑÇ][^,]+,\s*.+)$"
)
ENTRY_START_ONLY_ORDER_RE = re.compile(r"^(?P<order>\d{1,5})(?:\s*/\s*\d{1,5})?$")
ENTRY_NAME_TRAILING_ORDER_RE = re.compile(r"^(?P<name>[A-ZÁÉÍÓÚÀÈÌÒÙÄËÏÖÜÑÇ][^,]+,\s*.+?)\s+(?P<order>\d{1,5})(?:\s*/\s*\d{1,5})?$")

CENTER_LINE_RE = re.compile(
    r"^(?P<locality>.+?)\((?P<center_code>\d{8})\)(?P<center_name>.+)$"
)
ASSIGNMENT_SPECIALTY_RE = re.compile(
    r"^(?P<code>[0-9A-Z]{2,5})\s*/\s*(?P<name>.+)$"
)
POSITION_CODE_RE = re.compile(r"^\d{6}$")
NUMERIC_HOURS_RE = re.compile(r"^(?P<hours>\d{1,2}(?:,\d+)?)\s+hor(?:a|e)s?$", re.IGNORECASE)

HEADING_SPECIALTY_RE = re.compile(
    r"^(?P<name>.+?)\s+(?P<code>[0-9A-Z]{2,5})$"
)

STATUS_VALUES = (
    "Adjudicat",
    "No adjudicat",
    "Ha participat",
    "No ha participat",
    "Desactivat",
)

ASSIGNMENT_KIND_VALUES = (
    "VACANT",
    "SUBSTITUCIÓ DETERMINADA",
    "SUBSTITUCIÓ INDETERMINADA",
)

REQUEST_KEYWORDS = (
    "Petición:",
    "Peticio:",
    "Voluntaria",
    "PREFERÈNCIA",
    "PREFERENCIA",
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
    body_name: Optional[str]
    specialty_code: Optional[str]
    specialty_name: Optional[str]
    raw_block_text: str
    assignment: ParsedAssignment | None


class SecundariaExtractionValidationError(ValueError):
    """Aggregate diagnostics only; no document text or personal data."""


class FinalAwardListingSecundariaParserService:
    def parse_all_documents(self) -> list[dict]:
        store = AwardResultsStore()
        summaries: list[dict] = []

        try:
            documents = store.list_final_listing_documents(
                list_scope="secundaria_otros",
                parser_key=PARSER_KEY,
                parser_version=PARSER_VERSION,
            )

            for document in locked_documents(store, documents, PARSER_KEY, PARSER_VERSION):
                started_at = self._utc_now_iso()
                parse_run_id = store.create_parse_run(
                    document_version_id=int(document["document_version_id"]),
                    parser_key=PARSER_KEY,
                    parser_version=PARSER_VERSION,
                    started_at=started_at,
                )

                store.connection.commit()

                try:
                    rows = self._parse_document(document)
                    if not rows:
                        raise ValueError("No results parsed from secundaria award document")
                    store.clear_award_results_for_document(int(document["document_id"]))

                    for row in rows:
                        award_result_id = store.insert_award_result(
                            document_id=int(document["document_id"]),
                            list_scope="secundaria_otros",
                            body_code=None,
                            body_name=row.body_name,
                            specialty_code=row.specialty_code,
                            specialty_name=row.specialty_name,
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
                    store.attest_validation(document_id=int(document["document_id"]), parse_run_id=parse_run_id)
                    store.mark_document_parsed(
                        document_id=int(document["document_id"]),
                        parsed_at=finished_at,
                    )
                    store.connection.commit()

                    summaries.append(
                        {
                            "document_id": int(document["document_id"]),
                            "original_filename": document["original_filename"],
                            "rows_extracted": len(rows),
                            "status": "success",
                        }
                    )

                except Exception as exc:
                    store.connection.rollback()
                    finished_at = self._utc_now_iso()

                    try:
                        store.finish_parse_run(
                            parse_run_id=parse_run_id,
                            finished_at=finished_at,
                            status="failed",
                            rows_extracted=0,
                            error_message=str(exc),
                        )
                        store.connection.commit()
                    except Exception:
                        store.connection.rollback()

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
        if is_career_layout(lines):
            return [ParsedAwardResult(
                order_number=row.order_number,
                person_display_name=row.person_display_name,
                person_name_normalized=self._normalize_person_name(row.person_display_name),
                status=row.status,
                specialty_code=row.specialty_code,
                specialty_name=row.specialty_name,
                raw_block_text=row.raw_block_text,
                assignment=ParsedAssignment(**row.assignment) if row.assignment else None,
                body_name=row.body_name,
            ) for row in parse_career_awards(lines)]

        current_body_name: Optional[str] = None
        current_specialty_code: Optional[str] = None
        current_specialty_name: Optional[str] = None

        blocks: list[tuple[list[str], Optional[str], Optional[str], Optional[str]]] = []
        current_block: list[str] = []

        for index, line in enumerate(lines):
            if self._is_body_heading(line):
                if current_block:
                    blocks.append(
                        (
                            current_block,
                            current_body_name,
                            current_specialty_code,
                            current_specialty_name,
                        )
                    )
                    current_block = []

                current_body_name = self._parse_body_heading(line)
                continue

            if self._is_specialty_heading(line):
                if current_block:
                    blocks.append(
                        (
                            current_block,
                            current_body_name,
                            current_specialty_code,
                            current_specialty_name,
                        )
                    )
                    current_block = []

                (
                    current_specialty_code,
                    current_specialty_name,
                ) = self._parse_specialty_heading(line)
                continue

            if self._is_entry_start(lines, index):
                if current_block:
                    blocks.append(
                        (
                            current_block,
                            current_body_name,
                            current_specialty_code,
                            current_specialty_name,
                        )
                    )
                current_block = [line]
                continue

            if current_block:
                current_block.append(line)

        if current_block:
            blocks.append(
                (
                    current_block,
                    current_body_name,
                    current_specialty_code,
                    current_specialty_name,
                )
            )

        parsed: list[ParsedAwardResult] = []

        rejected = 0
        ambiguous = 0
        for block_lines, body_name, specialty_code, specialty_name in blocks:
            ambiguous += sum(line in STATUS_VALUES for line in block_lines) != 1
            row = self._parse_block(
                block_lines=block_lines,
                body_name=body_name,
                specialty_code=specialty_code,
                specialty_name=specialty_name,
            )
            if row is not None:
                parsed.append(row)
            else:
                rejected += 1

        expected = Counter(line for line in lines if line in STATUS_VALUES)
        actual = Counter(row.status for row in parsed)
        missing = sum(
            not row.person_display_name.strip() or not row.person_name_normalized.strip()
            or row.order_number is None or row.order_number <= 0
            or (row.status == 'Adjudicat' and (
                row.assignment is None
                or not re.fullmatch(r'\d{8}', row.assignment.center_code or '')
                or not re.fullmatch(r'\d{6}', row.assignment.position_code or '')))
            for row in parsed
        )
        if not expected or expected != actual or rejected or ambiguous or missing:
            raise SecundariaExtractionValidationError(
                f'secundaria_extraction_incomplete: statuses_match={expected == actual}; '
                f'rejected_blocks={rejected}; ambiguous_blocks={ambiguous}; missing_fields={missing}'
            )

        return parsed

    def _parse_block(
        self,
        *,
        block_lines: list[str],
        body_name: Optional[str],
        specialty_code: Optional[str],
        specialty_name: Optional[str],
    ) -> ParsedAwardResult | None:
        cleaned_lines = [
            self._normalize_spaces(line)
            for line in block_lines
            if self._normalize_spaces(line)
        ]
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
            trailing = ENTRY_NAME_TRAILING_ORDER_RE.match(first_line)
            if (trailing and len(cleaned_lines) > 1
                    and cleaned_lines[1] in STATUS_VALUES
                    and cleaned_lines[1] != "Adjudicat"
                    and not self._looks_like_petition_line(first_line)):
                order_number = int(trailing.group("order"))
                person_display_name = trailing.group("name").strip()
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

        if not person_display_name and status != 'Adjudicat':
            petitions = [line for line in remaining_lines if self._looks_like_petition_line(line)]
            if len(petitions) == 1:
                person_display_name, _, _ = self._parse_petition_line(petitions[0])

        if not person_display_name:
            return None

        return ParsedAwardResult(
            order_number=order_number,
            person_display_name=person_display_name,
            person_name_normalized=self._normalize_person_name(person_display_name),
            status=status,
            body_name=body_name,
            specialty_code=specialty_code,
            specialty_name=specialty_name,
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
                assignment_kind = "VACANT" if line.upper().startswith("VACANT") else line
                continue

            if center_code is None:
                center_match = CENTER_LINE_RE.match(line)
                if center_match:
                    locality = center_match.group("locality").strip()
                    center_code = center_match.group("center_code").strip()
                    center_name = center_match.group("center_name").strip()
                    continue

            if specialty_code is None:
                specialty_match = ASSIGNMENT_SPECIALTY_RE.match(line)
                if specialty_match:
                    specialty_code = specialty_match.group("code").strip()
                    specialty_name = specialty_match.group("name").strip()
                    continue

            if position_code is None and POSITION_CODE_RE.match(line):
                position_code = line
                continue

            if hours_text is None and self._looks_like_hours_line(line):
                hours_text = line
                hours_value = self._extract_numeric_hours(line)
                continue

            if petition_text is None and self._looks_like_petition_line(line):
                petition_text, request_type, petition_number = self._parse_petition_line(line)
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
        for line in reversed(lines):
            for status in STATUS_VALUES:
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

        if upper.startswith("NN / MM") or upper.startswith("NN/MM"):
            return True

        ignored_prefixes = (
            "ADJUDICACIÓ DE PERSONAL DOCENT INTERÍ DIA",
            "ADJUDICACIÓN DE PERSONAL DOCENTE INTERINO DÍA",
            "ADJUDICACIÓ DE PERSONAL DOCENT INICI DE CURS",
            "ADJUDICACIÓN DE PERSONAL DOCENTE INICIO DE CURSO",
            "PÀG ",
            "PÁG ",
            "PAG ",
            "ALTRES COSSOS / OTROS CUERPOS",
            "COS / CUERPO",
            "ESPECIALITAT / ESPECIALIDAD",
            "ESPECIALIDAD Y NÚMERO DE ORDEN",
        )

        return upper.startswith(ignored_prefixes)

    def _is_body_heading(self, line: str) -> bool:
        upper = line.upper()

        if "," in line:
            return False
        if line in STATUS_VALUES:
            return False
        if self._is_assignment_kind_line(line):
            return False
        if self._is_specialty_heading(line):
            return False
        if ENTRY_START_WITH_NAME_RE.match(line) or ENTRY_START_ONLY_ORDER_RE.match(line):
            return False

        body_markers = (
            "PROFESSORS ",
            "PROFESORES ",
            "MESTRES",
            "MAESTROS",
            "CATEDRÀTICS",
            "CATEDRATICOS",
        )

        return upper.startswith(body_markers)

    def _parse_body_heading(self, line: str) -> str:
        return line.strip()

    def _is_specialty_heading(self, line: str) -> bool:
        if "," in line:
            return False
        if line in STATUS_VALUES:
            return False
        if self._is_assignment_kind_line(line):
            return False
        if ENTRY_START_WITH_NAME_RE.match(line) or ENTRY_START_ONLY_ORDER_RE.match(line):
            return False
        if "(" in line and ")" in line:
            return False
        if "/" in line:
            return False
        if POSITION_CODE_RE.match(line):
            return False

        match = HEADING_SPECIALTY_RE.match(line)
        if not match:
            return False

        code = match.group("code").strip()
        name = match.group("name").strip()

        if not code:
            return False
        if not name:
            return False
        if code.isdigit() and len(code) > 3:
            return False

        return True

    def _parse_specialty_heading(self, line: str) -> tuple[Optional[str], Optional[str]]:
        match = HEADING_SPECIALTY_RE.match(line.strip())
        if not match:
            return None, line.strip()

        return match.group("code").strip(), match.group("name").strip()

    def _is_entry_start(self, lines: list[str], index: int) -> bool:
        line = lines[index]

        specialty = ASSIGNMENT_SPECIALTY_RE.match(line)
        if specialty and re.search(r"[A-Z]", specialty.group("code")):
            # E.g. "2A5 / MÀQUINES, SERVEIS..." resembles a glued ordinal/name.
            # Numeric n/m ordinals remain valid person starts.
            return False

        if ENTRY_START_WITH_NAME_RE.match(line):
            return True

        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        if (ENTRY_NAME_TRAILING_ORDER_RE.match(line)
                and next_line in STATUS_VALUES and next_line != "Adjudicat"
                and not self._looks_like_petition_line(line)):
            return True

        only_order_match = ENTRY_START_ONLY_ORDER_RE.match(line)
        if not only_order_match:
            return False

        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        return self._is_assignment_kind_line(next_line)

    def _is_assignment_kind_line(self, line: str) -> bool:
        upper = line.upper()
        return (
            upper in ASSIGNMENT_KIND_VALUES
            or upper.startswith("VACANT")
            or bool(re.fullmatch(r"SUBSTITUCIÓ (?:DETERMINADA|INDETERMINADA) ITINERANTE", upper))
        )

    def _looks_like_hours_line(self, line: str) -> bool:
        lower = line.lower()
        return "jornada" in lower or "hora" in lower

    def _extract_numeric_hours(self, line: str) -> Optional[float]:
        match = NUMERIC_HOURS_RE.match(line)
        if not match:
            return None

        try:
            return float(match.group("hours").replace(",", "."))
        except ValueError:
            return None

    def _looks_like_petition_line(self, line: str) -> bool:
        if "," not in line:
            return False

        upper = line.upper()
        return any(keyword.upper() in upper for keyword in REQUEST_KEYWORDS)

    def _parse_petition_line(
        self,
        line: str,
    ) -> tuple[str, Optional[str], Optional[int]]:
        value = line.strip()

        if "Petición:" in value:
            name, tail = value.split("Petición:", 1)
            person_name = name.strip()
            request_type, petition_number = self._parse_request_tail(tail.strip())
            return person_name, request_type, petition_number

        if "Peticio:" in value:
            name, tail = value.split("Peticio:", 1)
            person_name = name.strip()
            request_type, petition_number = self._parse_request_tail(tail.strip())
            return person_name, request_type, petition_number

        name_part, tail = self._split_name_and_request_tail(value)
        request_type, petition_number = self._parse_request_tail(tail)
        return name_part, request_type, petition_number

    def _split_name_and_request_tail(self, value: str) -> tuple[str, str]:
        for keyword in ("Voluntaria", "PREFERÈNCIA", "PREFERENCIA"):
            idx = value.upper().find(keyword.upper())
            if idx != -1:
                return value[:idx].strip(), value[idx:].strip()

        return value.strip(), ""

    def _parse_request_tail(self, value: str) -> tuple[Optional[str], Optional[int]]:
        if not value:
            return None, None

        match = re.match(r"^(?P<text>.+?)\s*(?P<number>\d+)$", value)
        if match:
            return match.group("text").strip(), int(match.group("number"))

        return value.strip(), None

    def _normalize_person_name(self, value: str) -> str:
        value = value.strip().upper()
        value = unicodedata.normalize("NFKD", value)
        value = "".join(char for char in value if not unicodedata.combining(char))
        value = re.sub(r"\s+", " ", value)
        return value

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
