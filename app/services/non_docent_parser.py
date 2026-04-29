from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import unicodedata
from typing import Any, Optional

from pypdf import PdfReader

from app.storage.non_docent_store import NonDocentStore


PARSER_KEY = "non_docent_parser"
PARSER_VERSION = "0.2.0"

NON_DOCENT_DOC_FAMILIES = (
    "non_docent_adc_call",
    "non_docent_adc_award",
    "non_docent_bag_update",
    "non_docent_funcion_publica_bag",
)

DATE_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
ADC_CODE_RE = re.compile(r"\bADC[-_ ]EDU[-_ ](?P<num>\d+)[/_-](?P<yy>\d{2})\b", re.IGNORECASE)
BAG_CODE_RE = re.compile(r"\b(?P<num>\d{3})[- ]?(?P<letter>[A-Z])\b")
MASKED_DNI_RE = re.compile(r"^\*{3}\d{4}\*{2}$")


@dataclass(slots=True)
class ParseResult:
    rows_extracted: int
    publication_id: int


class NonDocentParserService:
    def parse_all_documents(self) -> list[dict[str, Any]]:
        store = NonDocentStore()
        summaries: list[dict[str, Any]] = []

        try:
            documents = store.list_documents_for_parser(
                doc_families=NON_DOCENT_DOC_FAMILIES,
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
                    result = self._parse_document(store, document)
                    finished_at = self._utc_now_iso()

                    store.finish_parse_run(
                        parse_run_id=parse_run_id,
                        finished_at=finished_at,
                        status="success",
                        rows_extracted=result.rows_extracted,
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
                            "doc_family": document["doc_family"],
                            "original_filename": document["original_filename"],
                            "rows_extracted": result.rows_extracted,
                            "publication_id": result.publication_id,
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
                            "doc_family": document["doc_family"],
                            "original_filename": document["original_filename"],
                            "rows_extracted": 0,
                            "status": "failed",
                            "error": str(exc),
                        }
                    )

            return summaries

        finally:
            store.close()

    def _parse_document(
        self,
        store: NonDocentStore,
        document: dict[str, Any],
    ) -> ParseResult:
        pdf_path = self._resolve_file_path(document["file_path"])
        pages_text = self._extract_pages_text(pdf_path)
        lines = self._extract_clean_lines_from_pages(pages_text)
        full_text = "\n".join(pages_text)

        staff_group_code = self._infer_staff_group_code(document, full_text)
        staff_group_id = store.get_staff_group_id_by_code(staff_group_code)
        doc_family = document["doc_family"]

        publication_kind = {
            "non_docent_adc_call": "adc_call",
            "non_docent_adc_award": "adc_award",
            "non_docent_bag_update": "bag_update",
            "non_docent_funcion_publica_bag": "funcion_publica_bag",
        }[doc_family]

        publication_code = self._infer_publication_code(
            document=document,
            full_text=full_text,
            doc_family=doc_family,
        )

        title = document["title"] or document["original_filename"]
        publication_id = store.upsert_publication(
            staff_group_id=staff_group_id,
            document_id=int(document["document_id"]),
            publication_kind=publication_kind,
            publication_code=publication_code,
            title=title,
            source_page_url=document.get("source_url"),
            document_url=document.get("asset_url") or document.get("asset_canonical_url"),
            publication_date_text=document.get("document_date_text"),
            publication_date_iso=document.get("document_date_iso"),
            status_text=None,
            notes=f"parser={PARSER_KEY}; parser_version={PARSER_VERSION}; staff_group_code={staff_group_code}",
        )
        store.clear_publication_rows(publication_id=publication_id)

        if doc_family == "non_docent_adc_call":
            rows = self._parse_adc_call_positions(
                store=store,
                publication_id=publication_id,
                staff_group_id=staff_group_id,
                staff_group_code=staff_group_code,
                lines=lines,
            )
        elif doc_family == "non_docent_adc_award":
            rows = self._parse_adc_awards(
                store=store,
                publication_id=publication_id,
                staff_group_id=staff_group_id,
                full_text=full_text,
            )
        else:
            rows = self._parse_bag_document(
                store=store,
                publication_id=publication_id,
                staff_group_id=staff_group_id,
                document=document,
                lines=lines,
                source_kind="ceice_specific" if doc_family == "non_docent_bag_update" else "funcion_publica",
            )

        return ParseResult(rows_extracted=rows, publication_id=publication_id)

    def _parse_adc_call_positions(
        self,
        *,
        store: NonDocentStore,
        publication_id: int,
        staff_group_id: int | None,
        staff_group_code: str | None,
        lines: list[str],
    ) -> int:
        rows = self._split_adc_position_rows(lines)
        count = 0

        for row in rows:
            parsed = self._parse_adc_position_row(row, staff_group_code=staff_group_code)
            store.insert_offered_position(
                publication_id=publication_id,
                staff_group_id=staff_group_id,
                position_code=parsed["position_code"],
                classification=parsed["classification"],
                denomination=parsed["denomination"],
                center_name=parsed["center_name"],
                center_code=parsed["center_code"],
                locality=parsed["locality"],
                province=parsed["province"],
                occupancy_percent=parsed["occupancy_percent"],
                functional_assignment=parsed["functional_assignment"],
                reason=parsed["reason"],
                raw_row_text=row,
            )
            count += 1

        return count

    def _split_adc_position_rows(self, lines: list[str]) -> list[str]:
        in_annex = False
        buffer: list[str] = []
        rows: list[str] = []

        for line in lines:
            upper = line.upper()
            if "ANNEX" in upper and ("LLOCS OFERITS" in upper or "PUESTOS OFERTADOS" in upper):
                in_annex = True
                continue

            if not in_annex:
                continue

            if self._is_noise_line(line):
                continue

            if re.match(r"^\d{5}\s+", line):
                if buffer:
                    rows.append(self._normalize_spaces(" ".join(buffer)))
                buffer = [line]
                continue

            if buffer:
                buffer.append(line)

        if buffer:
            rows.append(self._normalize_spaces(" ".join(buffer)))

        return rows

    def _parse_adc_position_row(
        self,
        row_text: str,
        *,
        staff_group_code: str | None,
    ) -> dict[str, Any]:
        value = self._normalize_spaces(row_text)

        position_code = None
        classification = None
        denomination = self._default_denomination_for_staff_group(staff_group_code)
        center_name = None
        center_code = None
        locality = None
        province = None
        occupancy_percent = None
        functional_assignment = None
        reason = None

        match = re.match(r"^(?P<position_code>\d{5})\s+(?P<rest>.+)$", value)
        if match:
            position_code = match.group("position_code")
            rest = match.group("rest")
        else:
            rest = value

        class_match = re.match(r"^(?P<classification>[A-Z]\d?\s+\d+\s+[A-Z0-9]+)\s+(?P<tail>.+)$", rest)
        if class_match:
            classification = class_match.group("classification")
            tail = class_match.group("tail")
        else:
            tail = rest

        functional_match = re.search(
            r"Adscripci[oó] funcional:\s*(?P<value>.+?)\s+Prov[ií]ncia:",
            tail,
            flags=re.IGNORECASE,
        )
        if functional_match:
            functional_assignment = functional_match.group("value").strip()

        province_match = re.search(
            r"Prov[ií]ncia:\s*(?P<province>.+?)\s+Motiu:",
            tail,
            flags=re.IGNORECASE,
        )
        if province_match:
            province = province_match.group("province").strip()

        reason_match = re.search(r"Motiu:\s*(?P<reason>.+)$", tail, flags=re.IGNORECASE)
        if reason_match:
            reason = reason_match.group("reason").strip()

        before_functional = re.split(r"Adscripci[oó] funcional:", tail, flags=re.IGNORECASE)[0].strip()
        percent_match = re.search(r"\b(?P<percent>\d{1,3})\s*$", before_functional)
        if percent_match:
            occupancy_percent = self._parse_float(percent_match.group("percent"))
            before_functional = before_functional[: percent_match.start()].strip()

        if functional_assignment:
            center_name, locality = self._split_center_locality(functional_assignment)

        if not center_name and before_functional:
            center_name = before_functional

        return {
            "position_code": position_code,
            "classification": classification,
            "denomination": denomination,
            "center_name": center_name,
            "center_code": center_code,
            "locality": locality,
            "province": province,
            "occupancy_percent": occupancy_percent,
            "functional_assignment": functional_assignment,
            "reason": reason,
        }

    def _parse_adc_awards(
        self,
        *,
        store: NonDocentStore,
        publication_id: int,
        staff_group_id: int | None,
        full_text: str,
    ) -> int:
        text = self._normalize_spaces(full_text)
        rows = self._extract_adc_award_rows(text)
        count = 0

        for row in rows:
            store.insert_award(
                publication_id=publication_id,
                staff_group_id=staff_group_id,
                bag_code=row["bag_code"],
                bag_name=row["bag_name"],
                score=row["score"],
                scope_text=row["scope_text"],
                person_display_name=row["person_display_name"],
                person_name_normalized=self._normalize_person_name(row["person_display_name"]),
                career_official_text=row["career_official_text"],
                position_code=row["position_code"],
                position_text=row["position_text"],
                locality=row["locality"],
                center_name=row["center_name"],
                is_deserted=False,
                raw_row_text=row["raw_row_text"],
            )
            count += 1

        return count

    def _extract_adc_award_rows(self, text: str) -> list[dict[str, Any]]:
        pattern = re.compile(
            r"(?P<bag_code>\d{1,3}-E)\.\s*(?P<bag_name>.+?)\s+"
            r"(?P<score>\d+(?:[.,]\d+)?)\s+"
            r"(?P<scope>(?:[A-Z]{2,3}\d*(?:,\s*)?)+)\s+"
            r"(?P<name>[A-ZÁÉÍÓÚÜÑÇÀÈÒÏ' -]{4,}?)\s+"
            r"(?P<fc>S[ií]|No)\s+"
            r"(?P<position_code>\d{4,6})\s*-\s*"
            r"(?P<position_text>.+?)(?=(?:\d{1,3}-E\.|\*F\.C\.|Queden deserts|Quedan desiertos|S'han retirat|Se han retirado|No han quedat|No han quedado|$))",
            flags=re.IGNORECASE,
        )

        rows: list[dict[str, Any]] = []
        for match in pattern.finditer(text):
            position_text = self._clean_adc_award_position_text(
                self._normalize_spaces(match.group("position_text"))
            )
            locality = self._extract_parenthetical_locality(position_text or "")
            rows.append(
                {
                    "bag_code": match.group("bag_code"),
                    "bag_name": self._clean_adc_award_bag_name(match.group("bag_name")),
                    "score": self._parse_float(match.group("score")),
                    "scope_text": self._normalize_spaces(match.group("scope")),
                    "person_display_name": self._normalize_spaces(match.group("name")),
                    "career_official_text": match.group("fc"),
                    "position_code": match.group("position_code"),
                    "position_text": position_text,
                    "locality": locality,
                    "center_name": None,
                    "raw_row_text": self._normalize_spaces(match.group(0)),
                }
            )

        return rows

    def _parse_bag_document(
        self,
        *,
        store: NonDocentStore,
        publication_id: int,
        staff_group_id: int | None,
        document: dict[str, Any],
        lines: list[str],
        source_kind: str,
    ) -> int:
        snapshot_ids: dict[tuple[str, str | None], int] = {}
        current_bag_code = self._infer_bag_code_from_filename(document["original_filename"])
        current_bag_name: str | None = None
        current_zone: str | None = None
        snapshot_date_text = document.get("document_date_text")
        snapshot_date_iso = document.get("document_date_iso")

        count = 0
        row_buffer: str | None = None

        def flush_buffer() -> None:
            nonlocal count, row_buffer, current_bag_code, current_bag_name, current_zone
            if not row_buffer:
                return

            parsed_row = self._parse_bag_member_row(row_buffer)
            if parsed_row is None:
                row_buffer = None
                return

            bag_code = current_bag_code or self._infer_bag_code_from_text(row_buffer) or "UNKNOWN"
            snapshot_key = (bag_code, current_zone)
            snapshot_id = snapshot_ids.get(snapshot_key)
            if snapshot_id is None:
                staff_for_snapshot = staff_group_id
                if staff_for_snapshot is None:
                    staff_for_snapshot = store.get_staff_group_id_by_code(
                        self._staff_group_from_bag_code(bag_code)
                    )
                snapshot_id = store.insert_bag_snapshot(
                    publication_id=publication_id,
                    staff_group_id=staff_for_snapshot,
                    bag_code=bag_code,
                    bag_name=current_bag_name,
                    source_kind=source_kind,
                    snapshot_date_text=snapshot_date_text,
                    snapshot_date_iso=snapshot_date_iso,
                    zone_text=current_zone,
                )
                snapshot_ids[snapshot_key] = snapshot_id

            store.insert_bag_member(
                snapshot_id=snapshot_id,
                order_number=parsed_row["order_number"],
                masked_dni=parsed_row["masked_dni"],
                person_display_name=parsed_row["person_display_name"],
                person_name_normalized=self._normalize_person_name(parsed_row["person_display_name"]),
                total_score=parsed_row["total_score"],
                status_text=parsed_row["status_text"],
                annotation_text=parsed_row["annotation_text"],
                start_date_text=parsed_row["start_date_text"],
                end_date_text=parsed_row["end_date_text"],
                merit_json=json.dumps({"metrics": parsed_row["metrics"]}, ensure_ascii=False),
                raw_row_text=parsed_row["raw_row_text"],
            )
            count += 1
            row_buffer = None

        for line in lines:
            if self._is_noise_line(line):
                continue

            date_match = DATE_RE.search(line)
            if date_match and (snapshot_date_text is None or line.startswith(date_match.group(1))):
                snapshot_date_text = date_match.group(1)
                snapshot_date_iso = self._parse_ddmmyyyy_to_iso(snapshot_date_text)

            header_bag = self._extract_bag_header(line)
            if header_bag is not None and not self._looks_like_bag_row_start(line):
                flush_buffer()
                current_bag_code = header_bag["bag_code"]
                current_bag_name = header_bag["bag_name"]
                current_zone = header_bag["zone_text"] or current_zone
                continue

            if self._looks_like_bag_row_start(line):
                flush_buffer()
                row_buffer = line
                continue

            if row_buffer:
                if self._is_bag_continuation_noise(line):
                    continue
                row_buffer = f"{row_buffer} {line}"

        flush_buffer()
        return count

    def _extract_bag_header(self, line: str) -> dict[str, str | None] | None:
        if MASKED_DNI_RE.search(line):
            return None

        match = re.search(r"\b(?P<num>\d{3})-(?P<letter>[A-Z])\b", line)
        if match is None:
            return None

        bag_code = f"{match.group('num')}-{match.group('letter')}"
        before = self._normalize_spaces(line[: match.start()])
        after = self._normalize_spaces(line[match.start() :])
        zone_text = before or None
        bag_name = after or None

        return {
            "bag_code": bag_code,
            "bag_name": bag_name,
            "zone_text": zone_text,
        }

    def _looks_like_bag_row_start(self, line: str) -> bool:
        return re.match(r"^\s*\d+\s+\*{3}\d{4}\*{2}\s+", line) is not None

    def _parse_bag_member_row(self, row_text: str) -> dict[str, Any] | None:
        value = self._normalize_spaces(row_text)
        tokens = value.split()
        if len(tokens) < 4:
            return None

        try:
            order_number = int(tokens[0])
        except ValueError:
            return None

        masked_dni = tokens[1]
        if MASKED_DNI_RE.match(masked_dni) is None:
            return None

        metric_start = None
        for index in range(2, len(tokens)):
            if self._is_number_token(tokens[index]):
                metric_start = index
                break

        if metric_start is None or metric_start <= 2:
            return None

        name = " ".join(tokens[2:metric_start]).strip()
        metric_tokens: list[str] = []
        tail_start = len(tokens)

        for index in range(metric_start, len(tokens)):
            token = tokens[index]
            if self._is_number_token(token):
                metric_tokens.append(token)
                continue

            tail_start = index
            break

        tail_tokens = tokens[tail_start:]
        total_score = self._parse_float(metric_tokens[-1]) if metric_tokens else None
        start_date_text = None
        end_date_text = None

        if tail_tokens and DATE_RE.fullmatch(tail_tokens[-1]):
            start_date_text = tail_tokens[-1]
            tail_tokens = tail_tokens[:-1]

        status_text = " ".join(tail_tokens).strip() or None
        annotation_text = status_text

        return {
            "order_number": order_number,
            "masked_dni": masked_dni,
            "person_display_name": name,
            "total_score": total_score,
            "status_text": status_text,
            "annotation_text": annotation_text,
            "start_date_text": start_date_text,
            "end_date_text": end_date_text,
            "metrics": [self._parse_float(item) for item in metric_tokens],
            "raw_row_text": value,
        }

    def _infer_staff_group_code(
        self,
        document: dict[str, Any],
        full_text: str,
    ) -> str | None:
        source_key = document.get("source_key") or ""
        filename = document.get("original_filename") or ""
        title = document.get("title") or ""
        combined = self._normalize_match_text(f"{source_key} {filename} {title} {full_text[:2000]}")

        source_map = {
            "non_docent_adc_eee": "EEE",
            "non_docent_adc_eei": "EEI",
            "non_docent_adc_tgei": "TGEI",
            "non_docent_adc_fis": "FIS",
            "non_docent_adc_ils": "ILS",
            "non_docent_adc_es": "ES",
            "non_docent_adc_toc": "TOC",
        }
        if source_key in source_map:
            return source_map[source_key]

        monthly_map = {
            "100": "EEE",
            "101": "FIS",
            "102": "ES",
            "103": "TOC",
            "104": "TGEI",
            "105": "ILS",
        }

        match = re.search(r"listadobolsa_(\d{3})", filename, flags=re.IGNORECASE)
        if match:
            return monthly_map.get(match.group(1))

        bag_code = self._infer_bag_code_from_filename(filename) or self._infer_bag_code_from_text(full_text[:1000])
        if bag_code:
            return self._staff_group_from_bag_code(bag_code)

        text_markers = (
            ("terapeuta ocupacional", "TOC"),
            ("educador social", "ES"),
            ("educacion especial", "EEE"),
            ("educacio especial", "EEE"),
            ("educacion infantil", "EEI"),
            ("educacio infantil", "EEI"),
            ("fisioterapia", "FIS"),
            ("fisioterapeuta", "FIS"),
            ("interpretacion de la lengua de signos", "ILS"),
            ("interpretacio de la llengua de signes", "ILS"),
            ("trabajador social", "TSOC"),
            ("treballador social", "TSOC"),
        )
        for marker, code in text_markers:
            if marker in combined:
                return code

        return None

    def _staff_group_from_bag_code(self, bag_code: str | None) -> str | None:
        if not bag_code:
            return None

        normalized = bag_code.replace(" ", "").upper()
        number = normalized[:3]

        mapping = {
            "100": "EEE",
            "700": "EEE",
            "620": "EEE",
            "256": "EEE",
            "731": "EEE",
            "730": "EEE",
            "621": "EEI",
            "369": "EEI",
            "370": "EEI",
            "101": "FIS",
            "605": "FIS",
            "417": "FIS",
            "732": "FIS",
            "734": "FIS",
            "104": "TGEI",
            "105": "ILS",
            "495": "ILS",
            "347": "ILS",
            "757": "ILS",
            "102": "ES",
            "103": "TOC",
            "701": "TSOC",
            "672": "TSOC",
            "431": "TSOC",
            "505": "TSOC",
            "707": "TSOC",
            "702": "TSOC",
        }
        return mapping.get(number)

    def _infer_publication_code(
        self,
        *,
        document: dict[str, Any],
        full_text: str,
        doc_family: str,
    ) -> str | None:
        filename = document.get("original_filename") or ""
        title = document.get("title") or ""
        combined = f"{filename} {title} {full_text[:2000]}"

        adc_match = ADC_CODE_RE.search(combined)
        if adc_match:
            return f"ADC-EDU-{adc_match.group('num')}/{adc_match.group('yy')}"

        bag_code = self._infer_bag_code_from_filename(filename) or self._infer_bag_code_from_text(combined)
        if bag_code:
            return bag_code

        return None

    def _infer_bag_code_from_filename(self, filename: str) -> str | None:
        value = filename.upper()

        match = re.search(r"LISTADOBOLSA_(\d{3})", value)
        if match:
            return f"{match.group(1)}-E"

        match = re.search(r"(?<!\d)(\d{3})[-_ ]?([BL])(?=[^A-Z0-9]|$)", value)
        if match:
            return f"{match.group(1)}-{match.group(2)}"

        return None

    def _infer_bag_code_from_text(self, text: str) -> str | None:
        match = BAG_CODE_RE.search(text.upper())
        if match:
            return f"{match.group('num')}-{match.group('letter')}"
        return None

    def _default_denomination_for_staff_group(self, code: str | None) -> str | None:
        mapping = {
            "EEE": "Educador/a de educación especial",
            "EEI": "Educador/a de educación infantil",
            "TGEI": "Técnico/a de gestión en Educación Infantil",
            "FIS": "Fisioterapeuta",
            "ILS": "Técnico/a de gestión en interpretación de la lengua de signos",
            "ES": "Educador/a social",
            "TOC": "Terapeuta ocupacional",
            "TSOC": "Trabajador/a social",
        }
        return mapping.get(code or "")

    def _split_center_locality(self, value: str) -> tuple[str | None, str | None]:
        if " - " in value:
            center, locality = value.rsplit(" - ", 1)
            return center.strip() or None, locality.strip() or None
        return value.strip() or None, None

    def _extract_parenthetical_locality(self, value: str) -> str | None:
        matches = re.findall(r"\(([^()]+)\)", value)
        if matches:
            return matches[-1].strip()
        return None

    def _extract_pages_text(self, pdf_path: Path) -> list[str]:
        reader = PdfReader(str(pdf_path))
        return [page.extract_text() or "" for page in reader.pages]

    def _extract_clean_lines_from_pages(self, pages_text: list[str]) -> list[str]:
        lines: list[str] = []
        for text in pages_text:
            for raw_line in text.splitlines():
                line = self._normalize_spaces(raw_line)
                if not line:
                    continue
                lines.append(line)
        return lines

    def _resolve_file_path(self, file_path: str) -> Path:
        path = Path(file_path)
        if path.is_absolute():
            return path

        base_dir = Path(__file__).resolve().parents[2]
        return base_dir / path

    def _is_noise_line(self, line: str) -> bool:
        upper = line.upper()
        return (
            upper.startswith("CSV:")
            or upper.startswith("URL DE VALIDACI")
            or upper.startswith("FIRMAT PER")
            or upper.startswith("CÀRREC:")
            or upper.startswith("CARGO:")
            or upper.startswith("NRE. DNI")
            or upper.startswith("NRE DNI")
            or upper.startswith("Nº DNI")
            or upper.startswith("DNI COGNOMS")
            or upper.startswith("DNI APELLIDOS")
            or upper.startswith("COGNOMS I NOM")
            or upper.startswith("APELLIDOS Y NOMBRE")
            or upper.startswith("LLISTA D'ACTUALIZACI")
            or upper.startswith("LLISTA D'ACTUALITZACI")
            or upper.startswith("LLISTA DEFINITIVA")
            or upper.startswith("LISTA DEFINITIVA")
            or upper.startswith("PÁGINA ")
            or upper.startswith("PÀGINA ")
            or upper.startswith("PÁG ")
            or upper.startswith("PÀG ")
            or re.match(r"^\d{2}/\d{2}/\d{4}\s+\d+\s*/\s*\d+\b", line) is not None
            or re.match(r"^\d{2}/\d{2}/\d{4}\s+P[ÁAÀ]G", upper) is not None
        )

    def _is_number_token(self, token: str) -> bool:
        token = token.replace(",", ".")
        return re.fullmatch(r"-?\d+(?:\.\d+)?", token) is not None


    def _clean_adc_award_bag_name(self, value: str) -> str | None:
        cleaned = self._normalize_spaces(value)
        cleaned = re.sub(
            r"\b(Llocs|Puestos)\b.*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()
        return cleaned or None

    def _clean_adc_award_position_text(self, value: str) -> str | None:
        cleaned = self._normalize_spaces(value)
        split_patterns = (
            r"\*?[A-Z]{2,4}\d*:\s+",
            r"\b\d{2}/\d{2}/\d{4}\s+\d+\s*/\s*\d+\b",
            r"\bCSV:",
            r"\bURL de validaci",
            r"\bLlocs\s+",
            r"\bPuestos\s+",
            r"\bBorsa\s+Bolsa\b",
        )
        cut_at = len(cleaned)
        for pattern in split_patterns:
            match = re.search(pattern, cleaned, flags=re.IGNORECASE)
            if match:
                cut_at = min(cut_at, match.start())
        cleaned = cleaned[:cut_at].strip()
        cleaned = re.sub(r"\s+\*+$", "", cleaned).strip()
        return cleaned or None

    def _is_bag_continuation_noise(self, line: str) -> bool:
        upper = line.upper()
        if re.match(r"^\d{2}/\d{2}/\d{4}\s+\d+\s*/\s*\d+\b", line):
            return True
        if "EXCLOS" in upper or "EXCLUID" in upper:
            return True
        if "DNI COGNOMS" in upper or "DNI APELLIDOS" in upper:
            return True
        if upper in {"ALACANT/ALICANTE", "CASTELLÓ/CASTELLÓN", "CASTELLÓ/CASTELLON", "VALÈNCIA/VALENCIA", "VALENCIA/VALENCIA"}:
            return True
        return False

    def _parse_float(self, value: str | None) -> float | None:
        if value is None:
            return None
        try:
            return float(value.replace(",", "."))
        except ValueError:
            return None

    def _parse_ddmmyyyy_to_iso(self, value: str) -> str | None:
        try:
            return datetime.strptime(value, "%d/%m/%Y").date().isoformat()
        except ValueError:
            return None

    def _normalize_spaces(self, value: str) -> str:
        return " ".join(value.split())

    def _normalize_person_name(self, value: str) -> str:
        value = value.strip().lower()
        value = unicodedata.normalize("NFKD", value)
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        value = re.sub(r"[^a-z0-9ñç\s-]", " ", value)
        value = re.sub(r"\s+", " ", value)
        return value.strip()

    def _normalize_match_text(self, value: str) -> str:
        value = value.strip().lower()
        value = unicodedata.normalize("NFKD", value)
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        value = re.sub(r"\s+", " ", value)
        return value

    def _utc_now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()
