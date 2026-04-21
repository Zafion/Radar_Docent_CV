from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import unicodedata
from typing import Optional

from pypdf import PdfReader

from app.storage.difficult_coverage_store import DifficultCoverageStore


PARSER_KEY = "difficult_coverage_provisional_parser"
PARSER_VERSION = "0.3.0"

DATE_TIME_RE = re.compile(
    r"(?P<dt>\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})"
)

SELECTED_PREFIX_RE = re.compile(r"^\s*-->\s*")

BODY_RE = re.compile(r"^CUERPO/COS:\s*(?P<body>.+)$", re.IGNORECASE)
SPECIALTY_RE = re.compile(
    r"^(?P<code>[0-9A-Z]{2,5})\s+(?P<name>.+)$"
)

POSITION_LINE_RE = re.compile(
    r"^(?P<prefix>.+?)\s+(?P<position_code>\d{6})\s+(?P<center_code>\d{8})\s+PUESTO\s*:\s*$",
    re.IGNORECASE,
)

PURE_INT_RE = re.compile(r"^\d+$")


@dataclass(slots=True)
class ParsedPosition:
    body_name: Optional[str]
    specialty_code: Optional[str]
    specialty_name: Optional[str]
    position_code: str
    locality: Optional[str]
    center_code: Optional[str]
    center_name: Optional[str]
    num_participants: Optional[int]
    sorteo_number: Optional[str]
    registro_superior: Optional[str]
    registro_inferior: Optional[str]
    raw_header_text: str


@dataclass(slots=True)
class ParsedCandidate:
    row_number: Optional[int]
    is_selected: int
    last_name_1: Optional[str]
    last_name_2: Optional[str]
    first_name: Optional[str]
    full_name: str
    full_name_normalized: str
    registration_datetime_text: Optional[str]
    registration_code_or_bag_order: Optional[str]
    petition_text: Optional[str]
    petition_number: Optional[int]
    has_master_text: Optional[str]
    valenciano_requirement_text: Optional[str]
    adjudication_group_text: Optional[str]
    assigned_position_code: Optional[str]
    raw_row_text: str


class DifficultCoverageProvisionalParserService:
    def parse_all_documents(self) -> list[dict]:
        store = DifficultCoverageStore()
        summaries: list[dict] = []

        try:
            documents = store.list_provisional_documents(
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
                    parsed_positions = self._parse_document(document)
                    if not parsed_positions:
                        raise ValueError("No positions parsed from difficult coverage document")

                    store.clear_for_document(int(document["document_id"]))

                    inserted_candidates = 0

                    for parsed_position, candidates in parsed_positions:
                        position_id = store.insert_position(
                            document_id=int(document["document_id"]),
                            body_code=None,
                            body_name=parsed_position.body_name,
                            specialty_code=parsed_position.specialty_code,
                            specialty_name=parsed_position.specialty_name,
                            position_code=parsed_position.position_code,
                            center_code=parsed_position.center_code,
                            center_name=parsed_position.center_name,
                            locality=parsed_position.locality,
                            num_participants=parsed_position.num_participants,
                            sorteo_number=parsed_position.sorteo_number,
                            registro_superior=parsed_position.registro_superior,
                            registro_inferior=parsed_position.registro_inferior,
                            raw_header_text=parsed_position.raw_header_text,
                        )

                        for candidate in candidates:
                            store.insert_candidate(
                                position_id=position_id,
                                row_number=candidate.row_number,
                                is_selected=candidate.is_selected,
                                last_name_1=candidate.last_name_1,
                                last_name_2=candidate.last_name_2,
                                first_name=candidate.first_name,
                                full_name=candidate.full_name,
                                full_name_normalized=candidate.full_name_normalized,
                                registration_datetime_text=candidate.registration_datetime_text,
                                registration_code_or_bag_order=candidate.registration_code_or_bag_order,
                                petition_text=candidate.petition_text,
                                petition_number=candidate.petition_number,
                                has_master_text=candidate.has_master_text,
                                valenciano_requirement_text=candidate.valenciano_requirement_text,
                                adjudication_group_text=candidate.adjudication_group_text,
                                assigned_position_code=candidate.assigned_position_code,
                                raw_row_text=candidate.raw_row_text,
                            )
                            inserted_candidates += 1

                    finished_at = self._utc_now_iso()
                    store.finish_parse_run(
                        parse_run_id=parse_run_id,
                        finished_at=finished_at,
                        status="success",
                        rows_extracted=inserted_candidates,
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
                            "positions_extracted": len(parsed_positions),
                            "candidates_extracted": inserted_candidates,
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
                            "positions_extracted": 0,
                            "candidates_extracted": 0,
                            "status": "failed",
                            "error": str(exc),
                        }
                    )

            return summaries

        finally:
            store.close()

    def _parse_document(self, document) -> list[tuple[ParsedPosition, list[ParsedCandidate]]]:
        file_path = self._resolve_file_path(document["file_path"])
        lines = self._extract_clean_lines(file_path)

        current_body_name: Optional[str] = None
        current_specialty_code: Optional[str] = None
        current_specialty_name: Optional[str] = None

        pending_meta_lines: list[str] = []
        current_position: Optional[ParsedPosition] = None
        current_position_key: Optional[tuple[str, Optional[str]]] = None
        current_candidate_lines: list[str] = []

        parsed_blocks: list[tuple[ParsedPosition, list[ParsedCandidate]]] = []

        for line in lines:
            body_match = BODY_RE.match(line)
            if body_match:
                current_body_name = body_match.group("body").strip()
                pending_meta_lines.append(line)
                continue

            specialty_match = self._match_specialty_line(line)
            if specialty_match is not None:
                current_specialty_code, current_specialty_name = specialty_match
                pending_meta_lines.append(line)
                continue

            position_match = POSITION_LINE_RE.match(line)
            if position_match:
                new_position = self._build_position(
                    position_line=line,
                    meta_lines=pending_meta_lines,
                    body_name=current_body_name,
                    specialty_code=current_specialty_code,
                    specialty_name=current_specialty_name,
                )
                new_key = (new_position.position_code, new_position.center_code)

                if current_position is not None:
                    if new_key == current_position_key:
                        pending_meta_lines = []
                        continue

                    candidates = self._parse_candidates(current_candidate_lines)
                    parsed_blocks.append((current_position, candidates))

                current_position = new_position
                current_position_key = new_key
                current_candidate_lines = []
                pending_meta_lines = []
                continue

            if current_position is not None:
                if DATE_TIME_RE.search(line):
                    current_candidate_lines.append(line)
                else:
                    pending_meta_lines.append(line)
            else:
                pending_meta_lines.append(line)

        if current_position is not None:
            candidates = self._parse_candidates(current_candidate_lines)
            parsed_blocks.append((current_position, candidates))

        return parsed_blocks

    def _build_position(
        self,
        *,
        position_line: str,
        meta_lines: list[str],
        body_name: Optional[str],
        specialty_code: Optional[str],
        specialty_name: Optional[str],
    ) -> ParsedPosition:
        match = POSITION_LINE_RE.match(position_line)
        if not match:
            raise ValueError(f"Invalid position line: {position_line}")

        prefix = match.group("prefix").strip()
        position_code = match.group("position_code").strip()
        center_code = match.group("center_code").strip()

        locality, center_name = self._split_locality_and_center(prefix)

        numeric_lines = [
            line.strip()
            for line in meta_lines
            if PURE_INT_RE.match(line.strip())
        ]

        short_numbers = [x for x in numeric_lines if len(x) <= 6]
        long_numbers = [x for x in numeric_lines if len(x) >= 7]

        num_participants = None
        if short_numbers:
            try:
                num_participants = int(short_numbers[-1])
            except ValueError:
                num_participants = None

        sorteo_number = long_numbers[0] if len(long_numbers) >= 1 else None
        registro_superior = long_numbers[1] if len(long_numbers) >= 2 else None
        registro_inferior = long_numbers[2] if len(long_numbers) >= 3 else None

        raw_header_text = self._normalize_spaces(" ".join(meta_lines + [position_line]))

        return ParsedPosition(
            body_name=body_name,
            specialty_code=specialty_code,
            specialty_name=specialty_name,
            position_code=position_code,
            locality=locality,
            center_code=center_code,
            center_name=center_name,
            num_participants=num_participants,
            sorteo_number=sorteo_number,
            registro_superior=registro_superior,
            registro_inferior=registro_inferior,
            raw_header_text=raw_header_text,
        )

    def _parse_candidates(self, lines: list[str]) -> list[ParsedCandidate]:
        parsed: list[ParsedCandidate] = []

        for raw_row in lines:
            candidate = self._parse_candidate_row(raw_row)
            if candidate is not None:
                parsed.append(candidate)

        return parsed

    def _parse_candidate_row(self, raw_row: str) -> Optional[ParsedCandidate]:
        cleaned = self._normalize_spaces(raw_row)
        if not cleaned:
            return None

        is_selected = 1 if SELECTED_PREFIX_RE.match(cleaned) else 0
        cleaned = SELECTED_PREFIX_RE.sub("", cleaned).strip()

        dt_match = DATE_TIME_RE.search(cleaned)
        if not dt_match:
            return None

        pre = cleaned[: dt_match.start()].strip()
        registration_datetime_text = dt_match.group("dt").strip()
        post = cleaned[dt_match.end() :].strip()

        pre_tokens = pre.split()
        row_number_idx = None

        for idx in range(len(pre_tokens) - 1, -1, -1):
            if pre_tokens[idx].isdigit():
                row_number_idx = idx
                break

        if row_number_idx is None:
            return None

        try:
            row_number = int(pre_tokens[row_number_idx])
        except ValueError:
            row_number = None

        full_name = " ".join(pre_tokens[:row_number_idx] + pre_tokens[row_number_idx + 1 :]).strip()
        if not full_name:
            return None

        post_tokens = post.split()

        assigned_position_code = None
        if post_tokens and re.fullmatch(r"\d{6}", post_tokens[-1]):
            assigned_position_code = post_tokens.pop()

        adjudication_group_text = None
        if post_tokens and re.fullmatch(r"\d+", post_tokens[-1]):
            adjudication_group_text = post_tokens.pop()

        valenciano_requirement_text = None
        if post_tokens and post_tokens[-1].upper() in {"S", "N", "SI", "NO"}:
            valenciano_requirement_text = post_tokens.pop().upper()

        registration_code_or_bag_order = post_tokens[0] if len(post_tokens) >= 1 else None
        remaining = post_tokens[1:] if len(post_tokens) >= 2 else []

        petition_text = None
        petition_number = None
        has_master_text = None

        if remaining:
            # Caso especial real del PDF:
            # reg, X, petición, S, grupo, p.adj
            if len(remaining) >= 2 and remaining[0].upper() == "X" and remaining[1].isdigit():
                has_master_text = "X"
                petition_text = remaining[1]
            else:
                petition_text = remaining[0]
                if len(remaining) >= 2 and remaining[1].upper() == "X":
                    has_master_text = "X"
                elif remaining[0].upper() == "X" and len(remaining) == 1:
                    has_master_text = "X"
                    petition_text = None

        if petition_text and petition_text.isdigit():
            petition_number = int(petition_text)

        last_name_1, last_name_2, first_name = self._split_person_name(full_name)

        return ParsedCandidate(
            row_number=row_number,
            is_selected=is_selected,
            last_name_1=last_name_1,
            last_name_2=last_name_2,
            first_name=first_name,
            full_name=full_name,
            full_name_normalized=self._normalize_person_name(full_name),
            registration_datetime_text=registration_datetime_text,
            registration_code_or_bag_order=registration_code_or_bag_order,
            petition_text=petition_text,
            petition_number=petition_number,
            has_master_text=has_master_text,
            valenciano_requirement_text=valenciano_requirement_text,
            adjudication_group_text=adjudication_group_text,
            assigned_position_code=assigned_position_code,
            raw_row_text=cleaned,
        )

    def _match_specialty_line(
        self,
        line: str,
    ) -> tuple[Optional[str], Optional[str]] | None:
        if "," in line:
            return None
        if DATE_TIME_RE.search(line):
            return None
        if POSITION_LINE_RE.match(line):
            return None
        if PURE_INT_RE.match(line):
            return None

        match = SPECIALTY_RE.match(line)
        if not match:
            return None

        code = match.group("code").strip()
        name = match.group("name").strip()

        if not code or not name:
            return None
        if len(code) > 5:
            return None

        return code, name

    def _split_locality_and_center(
        self,
        text: str,
    ) -> tuple[Optional[str], Optional[str]]:
        markers = (
            "CEIP",
            "IES",
            "CIPFP",
            "CEE",
            "CRA",
            "EOI",
            "CFPA",
            "FPA",
            "CIPF",
            "CEE PÚB.",
            "SECCIÓN DEL IES",
            "SECCIO DEL IES",
            "SECCIÓ DEL IES",
        )

        upper = text.upper()
        best_index: Optional[int] = None

        for marker in markers:
            idx = upper.find(marker.upper())
            if idx != -1 and (best_index is None or idx < best_index):
                best_index = idx

        if best_index is None:
            return text.strip(), None

        locality = text[:best_index].strip()
        center_name = text[best_index:].strip()

        return locality or None, center_name or None

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
            "PÁGINA ",
            "PAGINA ",
            "PAGE ",
            "CONVOCATORIA",
            "PARTICIPANTS I LLOC ASSIGNAT",
            "PARTICIPANTES Y PUESTO ASIGNADO",
            "COBERTURA A LA ESPERA",
            "COBERTURA A L'ESPERA",
            "APELLIDO 1",
            "APELLIDO 2",
            "NOMBRE FECHA DE REGISTRO",
            "NUMERO DE",
            "REGISTRO / ORDEN",
            "BOLSA",
            "PETICIÓN MASTER",
            "REQ.",
            "VAL G. ADJ. P. ADJ.",
        )

        return upper.startswith(ignored_prefixes)

    def _split_person_name(self, full_name: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
        parts = full_name.split()
        if not parts:
            return None, None, None
        if len(parts) == 1:
            return parts[0], None, None
        if len(parts) == 2:
            return parts[0], None, parts[1]

        return parts[0], parts[1], " ".join(parts[2:])

    def _normalize_person_name(self, value: str) -> str:
        value = value.strip().upper()
        value = unicodedata.normalize("NFKD", value)
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
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