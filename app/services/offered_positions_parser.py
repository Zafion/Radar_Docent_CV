from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Optional

from pypdf import PdfReader

from app.storage.offered_positions_store import OfferedPositionsStore


PARSER_KEY = "offered_positions_parser"
PARSER_VERSION = "0.3.0"

ROW_START_RE = re.compile(r"^(?:\d+\s+)?(.+?)\s+-\s+\d{8}\s+-\s+.+$")
LOCALITY_CENTER_RE = re.compile(
    r"^(?P<locality>.+?)\s+-\s+(?P<center_code>\d{8})\s+-\s+(?P<rest>.+)$"
)
SPECIALTY_RE_1 = re.compile(r"^(?P<code>[0-9A-Z]+)\s*-\s*(?P<name>.+)$")
SPECIALTY_RE_2 = re.compile(r"^(?P<code>[0-9A-Z]+)\s+(?P<name>.+)$")
POSITION_CODE_RE = re.compile(r"\b(?P<code>\d{6})\b")
HOURS_RE = re.compile(r"\b(?P<hours>\d{1,2}(?:,\d+)?)\b")


@dataclass(slots=True)
class ParsedOfferedPosition:
    source_type: str
    body_code: Optional[str]
    body_name: Optional[str]
    specialty_code: Optional[str]
    specialty_name: Optional[str]
    province: Optional[str]
    locality: Optional[str]
    center_code: Optional[str]
    center_name: Optional[str]
    position_code: Optional[str]
    hours_text: Optional[str]
    hours_value: Optional[float]
    is_itinerant: Optional[bool]
    valenciano_required_text: Optional[str]
    position_type: Optional[str]
    composition: Optional[str]
    observations: Optional[str]
    raw_row_text: str


class OfferedPositionsParserService:
    def parse_all_documents(self) -> list[dict]:
        store = OfferedPositionsStore()
        summaries: list[dict] = []

        try:
            documents = store.list_offered_position_documents(
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
                    store.clear_offered_positions_for_document(
                        int(document["document_id"])
                    )

                    for row in rows:
                        store.insert_offered_position(
                            document_id=int(document["document_id"]),
                            source_type=row.source_type,
                            body_code=row.body_code,
                            body_name=row.body_name,
                            specialty_code=row.specialty_code,
                            specialty_name=row.specialty_name,
                            province=row.province,
                            locality=row.locality,
                            center_code=row.center_code,
                            center_name=row.center_name,
                            position_code=row.position_code,
                            hours_text=row.hours_text,
                            hours_value=row.hours_value,
                            is_itinerant=row.is_itinerant,
                            valenciano_required_text=row.valenciano_required_text,
                            position_type=row.position_type,
                            composition=row.composition,
                            observations=row.observations,
                            raw_row_text=row.raw_row_text,
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

    def _parse_document(self, document) -> list[ParsedOfferedPosition]:
        file_path = self._resolve_file_path(document["file_path"])
        source_type = document["list_scope"] or "unknown"

        current_body_name: Optional[str] = None
        current_specialty_code: Optional[str] = None
        current_specialty_name: Optional[str] = None
        current_province: Optional[str] = None

        row_buffer: Optional[str] = None
        parsed_rows: list[ParsedOfferedPosition] = []

        for line in self._extract_clean_lines(file_path):
            if self._is_body_line(line):
                if row_buffer:
                    parsed = self._parse_row(
                        row_text=row_buffer,
                        source_type=source_type,
                        body_name=current_body_name,
                        specialty_code=current_specialty_code,
                        specialty_name=current_specialty_name,
                        province=current_province,
                    )
                    if parsed:
                        parsed_rows.append(parsed)
                    row_buffer = None

                current_body_name = self._parse_body_name(line)
                continue

            if self._is_specialty_line(line):
                if row_buffer:
                    parsed = self._parse_row(
                        row_text=row_buffer,
                        source_type=source_type,
                        body_name=current_body_name,
                        specialty_code=current_specialty_code,
                        specialty_name=current_specialty_name,
                        province=current_province,
                    )
                    if parsed:
                        parsed_rows.append(parsed)
                    row_buffer = None

                (
                    current_specialty_code,
                    current_specialty_name,
                ) = self._parse_specialty(line)
                continue

            if self._is_province_line(line):
                if row_buffer:
                    parsed = self._parse_row(
                        row_text=row_buffer,
                        source_type=source_type,
                        body_name=current_body_name,
                        specialty_code=current_specialty_code,
                        specialty_name=current_specialty_name,
                        province=current_province,
                    )
                    if parsed:
                        parsed_rows.append(parsed)
                    row_buffer = None

                current_province = self._parse_province(line)
                continue

            if self._should_ignore_non_row_line(line):
                continue

            if self._looks_like_row_start(line):
                if row_buffer:
                    parsed = self._parse_row(
                        row_text=row_buffer,
                        source_type=source_type,
                        body_name=current_body_name,
                        specialty_code=current_specialty_code,
                        specialty_name=current_specialty_name,
                        province=current_province,
                    )
                    if parsed:
                        parsed_rows.append(parsed)

                row_buffer = line
                continue

            if row_buffer:
                row_buffer = f"{row_buffer} {line}"

        if row_buffer:
            parsed = self._parse_row(
                row_text=row_buffer,
                source_type=source_type,
                body_name=current_body_name,
                specialty_code=current_specialty_code,
                specialty_name=current_specialty_name,
                province=current_province,
            )
            if parsed:
                parsed_rows.append(parsed)

        return parsed_rows

    def _parse_row(
        self,
        *,
        row_text: str,
        source_type: str,
        body_name: Optional[str],
        specialty_code: Optional[str],
        specialty_name: Optional[str],
        province: Optional[str],
    ) -> Optional[ParsedOfferedPosition]:
        if source_type == "dificil_cobertura":
            return self._parse_dificil_cobertura_row(
                row_text=row_text,
                source_type=source_type,
                body_name=body_name,
                specialty_code=specialty_code,
                specialty_name=specialty_name,
                province=province,
            )

        return self._parse_continua_row(
            row_text=row_text,
            source_type=source_type,
            body_name=body_name,
            specialty_code=specialty_code,
            specialty_name=specialty_name,
            province=province,
        )

    def _parse_continua_row(
        self,
        *,
        row_text: str,
        source_type: str,
        body_name: Optional[str],
        specialty_code: Optional[str],
        specialty_name: Optional[str],
        province: Optional[str],
    ) -> Optional[ParsedOfferedPosition]:
        cleaned = self._remove_header_noise(self._normalize_spaces(row_text))
        cleaned = re.sub(r"^\d+\s+", "", cleaned).strip()

        position_type, remainder = self._extract_continua_position_type_from_start(
            cleaned
        )
        locality_match = LOCALITY_CENTER_RE.match(remainder)
        if not locality_match:
            return None

        locality = locality_match.group("locality").strip()
        center_code = locality_match.group("center_code").strip()
        rest = locality_match.group("rest").strip()

        position_match = POSITION_CODE_RE.search(rest)
        if not position_match:
            return None

        center_name = rest[: position_match.start()].strip()
        position_code = position_match.group("code")
        tail = rest[position_match.end() :].strip()

        tail = self._remove_header_noise(tail)

        valenciano_required_text = self._extract_last_yes_no(tail)
        if valenciano_required_text:
            tail = re.sub(
                rf"\b{re.escape(valenciano_required_text)}\b\s*$",
                "",
                tail,
                flags=re.IGNORECASE,
            ).strip()

        hours_text = self._extract_last_hours(tail)
        hours_value = self._coerce_hours(hours_text)
        if hours_text:
            tail = re.sub(rf"\b{re.escape(hours_text)}\b\s*$", "", tail).strip()

        observations = self._normalize_spaces(tail) or None
        observations = self._clean_trailing_yes_no_noise(observations)

        return ParsedOfferedPosition(
            source_type=source_type,
            body_code=None,
            body_name=body_name,
            specialty_code=specialty_code,
            specialty_name=specialty_name,
            province=province,
            locality=locality,
            center_code=center_code,
            center_name=center_name or None,
            position_code=position_code,
            hours_text=hours_text,
            hours_value=hours_value,
            is_itinerant=None,
            valenciano_required_text=valenciano_required_text,
            position_type=position_type,
            composition=None,
            observations=observations,
            raw_row_text=cleaned,
        )

    def _parse_dificil_cobertura_row(
        self,
        *,
        row_text: str,
        source_type: str,
        body_name: Optional[str],
        specialty_code: Optional[str],
        specialty_name: Optional[str],
        province: Optional[str],
    ) -> Optional[ParsedOfferedPosition]:
        cleaned = self._remove_header_noise(self._normalize_spaces(row_text))
        cleaned = re.sub(r"^\d+\s+", "", cleaned).strip()

        locality_match = LOCALITY_CENTER_RE.match(cleaned)
        if not locality_match:
            return None

        locality = self._clean_locality(locality_match.group("locality").strip())
        center_code = locality_match.group("center_code").strip()
        rest = locality_match.group("rest").strip()

        position_match = POSITION_CODE_RE.search(rest)
        if not position_match:
            return None

        before_code = rest[: position_match.start()].strip()
        position_code = position_match.group("code")
        after_code = rest[position_match.end() :].strip()

        pre_hours = self._extract_last_hours(before_code)
        pre_yes_no = self._extract_last_yes_no(before_code)

        center_name = before_code
        if pre_yes_no:
            center_name = re.sub(
                rf"\b{re.escape(pre_yes_no)}\b\s*$",
                "",
                center_name,
                flags=re.IGNORECASE,
            ).strip()
        if pre_hours:
            center_name = re.sub(
                rf"\b{re.escape(pre_hours)}\b\s*$",
                "",
                center_name,
            ).strip()

        after_code = self._remove_header_noise(after_code)

        position_type = self._extract_dificil_position_type(after_code)
        if position_type:
            after_code = re.sub(
                re.escape(position_type),
                "",
                after_code,
                count=1,
                flags=re.IGNORECASE,
            ).strip()

        post_hours = self._extract_first_hours(after_code)
        post_yes_no = self._extract_first_yes_no(after_code)

        hours_text = post_hours or pre_hours
        hours_value = self._coerce_hours(hours_text)
        valenciano_required_text = post_yes_no or pre_yes_no

        remainder = after_code
        if post_hours:
            remainder = re.sub(
                rf"\b{re.escape(post_hours)}\b", "", remainder, count=1
            ).strip()
        if post_yes_no:
            remainder = re.sub(
                rf"\b{re.escape(post_yes_no)}\b",
                "",
                remainder,
                count=1,
                flags=re.IGNORECASE,
            ).strip()

        composition = self._normalize_spaces(remainder) or None
        composition = self._clean_trailing_yes_no_noise(composition)

        return ParsedOfferedPosition(
            source_type=source_type,
            body_code=None,
            body_name=body_name,
            specialty_code=specialty_code,
            specialty_name=specialty_name,
            province=province,
            locality=locality,
            center_code=center_code,
            center_name=center_name or None,
            position_code=position_code,
            hours_text=hours_text,
            hours_value=hours_value,
            is_itinerant=None,
            valenciano_required_text=valenciano_required_text,
            position_type=position_type,
            composition=composition,
            observations=None,
            raw_row_text=cleaned,
        )

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

    def _resolve_file_path(self, file_path: str) -> Path:
        path = Path(file_path)
        if path.is_absolute():
            return path

        base_dir = Path(__file__).resolve().parents[2]
        return base_dir / path

    def _is_body_line(self, line: str) -> bool:
        return "CUERPO/COS:" in line.upper()

    def _parse_body_name(self, line: str) -> str:
        return line.split(":", 1)[1].strip()

    def _is_specialty_line(self, line: str) -> bool:
        return "ESPECIALIDAD/ESPECIALITAT:" in line.upper()

    def _parse_specialty(self, line: str) -> tuple[Optional[str], Optional[str]]:
        value = line.split(":", 1)[1].strip()

        match = SPECIALTY_RE_1.match(value)
        if match:
            return match.group("code").strip(), match.group("name").strip()

        match = SPECIALTY_RE_2.match(value)
        if match:
            return match.group("code").strip(), match.group("name").strip()

        return None, value

    def _is_province_line(self, line: str) -> bool:
        upper = line.upper()

        if "PROVÍNCIA/PROVINCIA:" in upper or "PROVINCIA/PROVINCIA:" in upper:
            return True

        bare = self._normalize_spaces(upper)
        return bare in {
            "ALACANT",
            "ALICANTE",
            "CASTELLÓ",
            "CASTELLON",
            "VALÈNCIA",
            "VALENCIA",
        }

    def _parse_province(self, line: str) -> str:
        if ":" in line:
            value = line.split(":", 1)[1].strip()
        else:
            value = line.strip()

        normalized = value.upper()
        mapping = {
            "ALACANT": "ALICANTE",
            "ALICANTE": "ALICANTE",
            "CASTELLÓ": "CASTELLON",
            "CASTELLON": "CASTELLON",
            "VALÈNCIA": "VALENCIA",
            "VALENCIA": "VALENCIA",
        }
        return mapping.get(normalized, value)

    def _looks_like_row_start(self, line: str) -> bool:
        return ROW_START_RE.match(line) is not None

    def _should_ignore_line(self, line: str) -> bool:
        upper = line.upper()

        if re.fullmatch(r"\d{2}/\d{2}/\d{4}", line):
            return True

        if upper.startswith("PÁGINA ") or upper.startswith("PÀG "):
            return True

        ignored_prefixes = (
            "AVGDA.CAMPANAR",
            "LOCALITAT/LOCALIDAD",
            "PUESTOS DE DIFÍCIL COBERTURA CONVOCADOS EN EL",
            "PUESTOS DE DIFÍCIL COBERTURA CONVOCADOS",
            "LLOCS DE DIFÍCIL COBERTURA CONVOCATS EN EL",
            "LLOCS DE DIFÍCIL COBERTURA CONVOCATS",
            "ADJUDICACIÓN DE PERSONAL DOCENTE INTERINO DÍA",
            "ADJUDICACIÓ DE PERSONAL DOCENT INTERÍ DIA",
            "TIPUS/TIPO",
            "OBSERV./OBSERV.",
            "LLOC ITINERANTE",
            "LLOC HORES",
            "HORES.REQ. LING.",
            "LLOCS OFERTATS/ PUESTOS OFERTADOS",
            "LLOCS OFERTATS / PUESTOS OFERTADOS",
        )

        return upper.startswith(ignored_prefixes)

    def _should_ignore_non_row_line(self, line: str) -> bool:
        upper = line.upper()

        ignored_prefixes = (
            "LOCALIDAD / LOCALITAT - CENTRO / CENTRE",
            "LOCALITAT/LOCALIDAD - CENTRE/CENTRO",
            "TIPUS/TIPO LLOC",
            "OBSERV./OBSERV.",
            "HORES.REQ. LING.",
            "LLOC ITINERANTE HORES",
            "LLOCS OFERTATS/ PUESTOS OFERTADOS",
            "LLOCS OFERTATS / PUESTOS OFERTADOS",
        )

        if upper.startswith(ignored_prefixes):
            return True

        return False

    def _remove_header_noise(self, text: str) -> str:
        value = text

        patterns = [
            r"ADJUDICACIÓN DE PERSONAL DOCENTE INTERINO DÍA\s+\d{0,2}/\d{2}/\d{4}",
            r"ADJUDICACIÓ DE PERSONAL DOCENT INTERÍ DIA\s+\d{0,2}/\d{2}/\d{4}",
            r"LOCALIDAD\s*/\s*LOCALITAT\s*-\s*CENTRO\s*/\s*CENTRE",
            r"LOCALITAT/LOCALIDAD\s*-\s*CENTRE/CENTRO",
            r"TIPUS/TIPO\s+LLOC",
            r"OBSERV\./OBSERV\.",
            r"HORES\.REQ\.\s*LING\.",
            r"LLOCS\s+OFERTATS\s*/\s*PUESTOS\s+OFERTADOS",
            r"LLOC\s+ITINERANTE\s+HORES\s+TIPUS/TIPO\s+COMPOSICIÓN\s+REQUISITO\s+OBSERVACIONES",
            r"LLOC\s+ITINERANTE\s+HORAS\s+TIPUS/TIPO\s+COMPOSICIÓN\s+REQUISITO\s+OBSERVACIONES",
        ]

        for pattern in patterns:
            value = re.sub(pattern, " ", value, flags=re.IGNORECASE)

        return self._normalize_spaces(value)

    def _extract_continua_position_type_from_start(
        self,
        text: str,
    ) -> tuple[Optional[str], str]:
        candidates = (
            "SUSTITUCIÓN INDETERMINADA",
            "SUSTITUCIÓN DETERMINADA",
            "VACANTE",
        )

        value = text.strip()
        upper = value.upper()

        for candidate in candidates:
            candidate_upper = candidate.upper()
            if upper.startswith(candidate_upper):
                remainder = value[len(candidate):].strip()
                return candidate, remainder

        return None, value

    def _extract_first_hours(self, text: str) -> Optional[str]:
        if not text:
            return None

        match = HOURS_RE.search(text)
        if match:
            return match.group("hours")
        return None

    def _extract_last_hours(self, text: str) -> Optional[str]:
        matches = re.findall(r"\b(\d{1,2}(?:,\d+)?)\b", text)
        if not matches:
            return None
        return matches[-1]

    def _extract_first_yes_no(self, text: str) -> Optional[str]:
        match = re.search(r"\b(SI|NO)\b", text, flags=re.IGNORECASE)
        if not match:
            return None
        return match.group(1).upper()

    def _extract_last_yes_no(self, text: str) -> Optional[str]:
        matches = re.findall(r"\b(SI|NO)\b", text, flags=re.IGNORECASE)
        if not matches:
            return None
        return matches[-1].upper()

    def _extract_dificil_position_type(self, text: str) -> Optional[str]:
        candidates = (
            "Sust. Ind.",
            "Sust. Det.",
            "Vacante",
        )

        for candidate in candidates:
            if re.search(re.escape(candidate), text, flags=re.IGNORECASE):
                return candidate

        return None

    def _clean_locality(self, value: str) -> str:
        cleaned = re.sub(r"^\d{1,2}(?=[A-ZÁÉÍÓÚÜÑ'])", "", value).strip()
        return cleaned or value

    def _clean_trailing_yes_no_noise(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None

        cleaned = re.sub(r"\b(SI|NO)\b\s*$", "", value, flags=re.IGNORECASE).strip()
        return cleaned or None

    def _coerce_hours(self, value: Optional[str]) -> Optional[float]:
        if not value:
            return None

        try:
            return float(value.replace(",", "."))
        except ValueError:
            return None

    def _normalize_spaces(self, value: str) -> str:
        return " ".join(value.split())

    def _utc_now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()
