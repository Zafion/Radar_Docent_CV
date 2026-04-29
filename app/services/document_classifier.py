from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import unicodedata
from typing import Optional

from pypdf import PdfReader


DATE_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")


@dataclass(slots=True)
class ClassifiedDocument:
    doc_family: str
    list_scope: Optional[str]
    title: str | None
    document_date_text: str | None
    document_date_iso: str | None
    classifier_version: str
    signals: str


class DocumentClassifierService:
    classifier_version = "0.6.0"

    def classify(
        self,
        *,
        file_path: str,
        original_filename: str,
        asset_title: str | None,
        asset_role: str,
        source_key: str,
        source_label: str | None,
        section: str | None,
        publication_label: str | None,
        publication_date_text: str | None,
    ) -> ClassifiedDocument:
        preview_text = self._read_pdf_preview(file_path)
        normalized_preview = self._normalize_match_text(preview_text)

        title = asset_title or publication_label or section or original_filename
        normalized_title = self._normalize_match_text(title or "")
        normalized_section = self._normalize_match_text(section or "")
        normalized_source = self._normalize_match_text(source_key or "")
        normalized_filename = self._normalize_match_text(original_filename)

        combined_text = " ".join(
            filter(
                None,
                [
                    normalized_title,
                    normalized_section,
                    normalized_source,
                    self._normalize_match_text(publication_label or ""),
                    normalized_filename,
                    normalized_preview[:5000],
                ],
            )
        )

        document_date_text, document_date_iso = self._infer_document_date(
            preview_text=preview_text,
            publication_date_text=publication_date_text,
            original_filename=original_filename,
        )

        legacy = self._legacy_classification(
            asset_role=asset_role,
            source_key=source_key,
            asset_title=title,
        )
        if legacy is not None:
            doc_family, list_scope = legacy
            return ClassifiedDocument(
                doc_family=doc_family,
                list_scope=list_scope,
                title=title,
                document_date_text=document_date_text,
                document_date_iso=document_date_iso,
                classifier_version=self.classifier_version,
                signals=f"legacy_asset_role={asset_role}",
            )


        non_docent = self._classify_non_docent_document(
            asset_role=asset_role,
            source_key=source_key,
            filename=normalized_filename,
            title=normalized_title,
            combined_text=combined_text,
        )
        if non_docent is not None:
            doc_family, list_scope, signals = non_docent
            return ClassifiedDocument(
                doc_family=doc_family,
                list_scope=list_scope,
                title=title,
                document_date_text=document_date_text,
                document_date_iso=document_date_iso,
                classifier_version=self.classifier_version,
                signals=signals,
            )

        if self._looks_like_ignored_document(
            title=normalized_title,
            combined_text=combined_text,
            filename=normalized_filename,
        ):
            return ClassifiedDocument(
                doc_family="ignored",
                list_scope=None,
                title=title,
                document_date_text=document_date_text,
                document_date_iso=document_date_iso,
                classifier_version=self.classifier_version,
                signals="content=ignored",
            )

        filename_heuristic = self._classify_by_filename(
            filename=normalized_filename,
            title=normalized_title,
            combined_text=combined_text,
        )
        if filename_heuristic is not None:
            doc_family, list_scope, signals = filename_heuristic
            return ClassifiedDocument(
                doc_family=doc_family,
                list_scope=list_scope,
                title=title,
                document_date_text=document_date_text,
                document_date_iso=document_date_iso,
                classifier_version=self.classifier_version,
                signals=signals,
            )

        if self._looks_like_difficult_coverage_provisional(combined_text):
            return ClassifiedDocument(
                doc_family="difficult_coverage_provisional",
                list_scope="dificil_cobertura",
                title=title,
                document_date_text=document_date_text,
                document_date_iso=document_date_iso,
                classifier_version=self.classifier_version,
                signals="content=difficult_coverage_provisional",
            )

        if self._looks_like_difficult_coverage_offered_positions(combined_text):
            return ClassifiedDocument(
                doc_family="offered_positions",
                list_scope="dificil_cobertura",
                title=title,
                document_date_text=document_date_text,
                document_date_iso=document_date_iso,
                classifier_version=self.classifier_version,
                signals="content=offered_positions_dificil_cobertura",
            )

        if self._looks_like_final_award_listing(combined_text):
            list_scope = self._infer_final_award_scope(combined_text)
            return ClassifiedDocument(
                doc_family="final_award_listing",
                list_scope=list_scope,
                title=title,
                document_date_text=document_date_text,
                document_date_iso=document_date_iso,
                classifier_version=self.classifier_version,
                signals=f"content=final_award_listing; scope={list_scope}",
            )

        if self._looks_like_offered_positions(combined_text):
            list_scope = self._infer_offered_positions_scope(combined_text)
            return ClassifiedDocument(
                doc_family="offered_positions",
                list_scope=list_scope,
                title=title,
                document_date_text=document_date_text,
                document_date_iso=document_date_iso,
                classifier_version=self.classifier_version,
                signals=f"content=offered_positions; scope={list_scope}",
            )

        if self._looks_like_resolution_text(preview_text=preview_text, title=title):
            return ClassifiedDocument(
                doc_family="resolution_text",
                list_scope=None,
                title=title,
                document_date_text=document_date_text,
                document_date_iso=document_date_iso,
                classifier_version=self.classifier_version,
                signals="content=resolution_text",
            )

        return ClassifiedDocument(
            doc_family="unknown",
            list_scope=None,
            title=title,
            document_date_text=document_date_text,
            document_date_iso=document_date_iso,
            classifier_version=self.classifier_version,
            signals=f"content=unknown; source_key={source_key}",
        )

    def _legacy_classification(
        self,
        *,
        asset_role: str,
        source_key: str,
        asset_title: str | None,
    ) -> tuple[str, Optional[str]] | None:
        normalized_title = self._normalize_match_text(asset_title or "")

        if asset_role == "resolucion_pdf":
            return "resolution_text", None

        if asset_role == "listado_maestros_pdf":
            return "final_award_listing", "maestros"

        if asset_role == "listado_secundaria_pdf":
            return "final_award_listing", "secundaria_otros"

        if asset_role in {"puestos_pdf", "puestos_definitivos_pdf"}:
            if "credencial" in normalized_title:
                return "ignored", None

            if source_key == "adjudicacion3":
                return "offered_positions", "inicio_curso"
            if source_key == "resolucion":
                return "offered_positions", "continua"
            if source_key == "resolucion1":
                return "offered_positions", "dificil_cobertura"
            return "offered_positions", None

        if asset_role == "provisional_listado_pdf":
            return "difficult_coverage_provisional", "dificil_cobertura"

        return None

    def _classify_by_filename(
        self,
        *,
        filename: str,
        title: str,
        combined_text: str,
    ) -> tuple[str, Optional[str], str] | None:
        if "_adj_" in filename and "_lis_mae" in filename:
            return "final_award_listing", "maestros", "filename=adj_lis_mae"

        if "_adj_" in filename and "_lis_sec" in filename:
            return "final_award_listing", "secundaria_otros", "filename=adj_lis_sec"

        if "_adj_int_lis_mae" in filename:
            return "final_award_listing", "maestros", "filename=adj_int_lis_mae"

        if "_adj_int_lis_sec" in filename:
            return "final_award_listing", "secundaria_otros", "filename=adj_int_lis_sec"

        if "_adj_rpp_lis_mae" in filename:
            return "final_award_listing", "maestros", "filename=adj_rpp_lis_mae"

        if "_adj_rpp_lis_sec" in filename:
            return "final_award_listing", "secundaria_otros", "filename=adj_rpp_lis_sec"

        if "_sup_" in filename and "_adj_lis_mae" in filename:
            return "final_award_listing", "maestros", "filename=sup_adj_lis_mae"

        if "_sup_" in filename and "_adj_lis_sec" in filename:
            return "final_award_listing", "secundaria_otros", "filename=sup_adj_lis_sec"

        if "_pue_" in filename or "puestos ofertados" in title or "puestos ofertados" in combined_text:
            if "dificil cobertura" in combined_text or "dificil provision" in combined_text:
                return "offered_positions", "dificil_cobertura", "filename_or_title=pue_dificil"

            if "inicio de curso" in combined_text or "inici de curs" in combined_text or "vacantes" in combined_text:
                return "offered_positions", "inicio_curso", "filename_or_title=pue_inicio_curso"

            return "offered_positions", "continua", "filename_or_title=pue"

        if "listado de adjudicacion maestros" in combined_text:
            return "final_award_listing", "maestros", "title=listado_adjudicacion_maestros"

        if "listado de adjudicacion secundaria" in combined_text and "listado de adjudicacion" in combined_text:
            return "final_award_listing", "secundaria_otros", "title=listado_adjudicacion_secundaria"

        if "listado cuerpo de maestros" in combined_text and "adj" in filename:
            return "final_award_listing", "maestros", "title=listado_cuerpo_maestros_adj"

        if "listado cuerpos de secundaria y otros cuerpos" in combined_text and "adj" in filename:
            return "final_award_listing", "secundaria_otros", "title=listado_cuerpos_sec_adj"

        return None


    def _classify_non_docent_document(
        self,
        *,
        asset_role: str,
        source_key: str,
        filename: str,
        title: str,
        combined_text: str,
    ) -> tuple[str, Optional[str], str] | None:
        source_or_role = f"{source_key} {asset_role}"
        is_non_docent_source = "non_docent" in source_or_role

        if not is_non_docent_source and not (
            filename.startswith("adc_edu_")
            or filename.startswith("listadodefinitivo_adc_edu_")
            or filename.startswith("listadobolsa_")
            or "listado_definitivo" in filename
            or "ldefinitiva" in filename
            or "listadoaprobados" in filename
        ):
            return None

        if asset_role == "non_docent_adc_call_pdf" or filename.startswith("adc_edu_"):
            return "non_docent_adc_call", "no_docente", "non_docent=adc_call"

        if (
            asset_role == "non_docent_adc_award_pdf"
            or filename.startswith("listadodefinitivo_adc_edu_")
            or "adjudicacion edu-" in combined_text
            or "adjudicacio edu-" in combined_text
        ):
            return "non_docent_adc_award", "no_docente", "non_docent=adc_award"

        if (
            asset_role == "non_docent_bag_update_pdf"
            or filename.startswith("listadobolsa_")
            or "llista d'actualizacio mensual" in combined_text
            or "llista d'actualitzacio mensual" in combined_text
        ):
            return "non_docent_bag_update", "no_docente", "non_docent=bag_update"

        if (
            asset_role == "non_docent_funcion_publica_bag_pdf"
            or "llista definitiva de la borsa d'ocupacio temporal" in combined_text
            or "lista definitiva de la bolsa de ocupacion temporal" in combined_text
            or "listado definitivo" in filename
            or "ldefinitiva" in filename
            or "listadoaprobados" in filename
            or "listadobolsa_" in filename
        ):
            return "non_docent_funcion_publica_bag", "no_docente", "non_docent=funcion_publica_bag"

        return None


    def _looks_like_ignored_document(
        self,
        *,
        title: str,
        combined_text: str,
        filename: str,
    ) -> bool:
        # Nota futura:
        # Si en una siguiente fase interesa dar soporte a "personal experto"
        # y "profesorado especialista", este es el punto donde habrá que
        # relajar exclusiones y crear familias/parsers propios.

        if re.match(r"^\d{3}\s+[a-z]", title):
            return True

        core_operational_markers = (
            "listado de adjudicacion",
            "listat d'adjudicacio",
            "puestos ofertados",
            "llocs ofertats",
            "puestos provisionales ofertados",
            "puestos definitivos ofertados",
            "listado de vacantes",
            "vacantes definitivas",
            "puesto asignado provisionalmente",
            "lloc assignat provisionalment",
            "_adj_",
            "_pue_",
            "_lis_mae",
            "_lis_sec",
            "_par.",
            "_par_",
        )
        if any(marker in combined_text or marker in filename for marker in core_operational_markers):
            return False

        generic_ignored_titles = (
            "adjudicacion",
            "adjudicados",
        )
        if title in generic_ignored_titles:
            return True

        ignored_markers = (
            # 1) Personal experto
            "personal experto",
            "sectores productivos",
            "vacantes de expertos",
            "adjudicacion expertos",
            "adjudicados expertos",

            # 2) Profesorado especialista
            "profesorado especialista",
            "personal especialista",
            "adjudicacion especialistas",
            "adjudicados especialistas",
            "vacantes provisionales especialistas",

            # 3) Normativa histórica
            "se regulan los procedimientos",
            "se modifica la resolucion",
            "se establecen criterios",
            "criterios de clasificacion y provision",
            "criterios",
            "resolucion de 23 de enero de 2018",
            "resolucion de 31 de julio de 2020",
            "resolucion de 27 de octubre de 2020",
            "resolucion de 26 de julio de 2022",
            "comisiones de servicio",
            "lengua extranjera areas, materias o modulos no linguisticos",

            # 4) Anexos auxiliares
            "anexo i - centros de dificil provision",
            "centros de dificil provision",
            "centros de dificil provisio",
            "especialidades que requieren entrevista previa",
            "ambitos y catedraticos de musica",
            "preferencia por prorrog",
            "puesto de dificil provision",
            "turnos",

            # 5) Documentación de soporte
            "documento de delegacion",
            "documentacion adicional",
            "documento de requisitos tecnicos",
            "funcionarios en practicas",
            "titulaciones requeridas para acceder a puestos de dificil cobertura",
            "resolucion de la convocatoria y anexos",
            "documentacion a aportar",
            "credencial",
            "credenciales",

            # Ruido general no operativo
            "oferta de empleo publico",
            "procedimiento selectivo",
            "concurso oposicion",
            "concurso-oposicion",
            "concurso de meritos",
            "estabilizacion",
            "organos de seleccion",
            "tribunales",
            "especialidades y titulaciones",
            "especialitats i titulacions",
            "compatibilidad",
            "compatibilidades",
            "muface",
            "excedencia",
            "acoso",
            "delitos sexuales",
            "delincuentes sexuales",
            "manual de ayuda",
            "preguntas frecuentes",
            "faq",
            "declaracion jurada",
            "declaracion responsable",
            "protocolo",
            "certificado acreditativo",
            "boe-a-",
            "decreto ",
            "orden ",
            "correccion de errores de la orden",
            "correccion de errores de la resolucion",
        )

        return any(marker in combined_text or marker in filename for marker in ignored_markers)

    def _looks_like_difficult_coverage_provisional(self, text: str) -> bool:
        return (
            "participantes y puesto asignado provisionalmente" in text
            or "participants i lloc assignat provisionalment" in text
        )

    def _looks_like_difficult_coverage_offered_positions(self, text: str) -> bool:
        return (
            "puestos de dificil cobertura convocados" in text
            or "llocs de dificil cobertura convocats" in text
            or (
                "puestos provisionales ofertados" in text and "dificil cobertura" in text
            )
            or (
                "puestos definitivos ofertados" in text and "dificil cobertura" in text
            )
        )

    def _looks_like_final_award_listing(self, text: str) -> bool:
        if "listado de adjudicacion" in text or "listat d'adjudicacio" in text:
            return True

        statuses = (
            "adjudicat",
            "no adjudicat",
            "ha participat",
            "no ha participat",
            "desactivat",
            "adjudicado",
            "no adjudicado",
            "ha participado",
            "no ha participado",
            "desactivado",
        )
        matches = sum(1 for status in statuses if status in text)
        return matches >= 3

    def _infer_final_award_scope(self, text: str) -> str:
        secundaria_markers = (
            "altres cossos",
            "otros cuerpos",
            "secundaria y otros cuerpos",
            "especialidad y numero de orden",
            "professors especialistes en sectors singulars",
            "profesores especialistas en sectores singulares",
            "catedratics",
            "catedraticos",
        )
        if any(marker in text for marker in secundaria_markers):
            return "secundaria_otros"

        return "maestros"

    def _looks_like_offered_positions(self, text: str) -> bool:
        if "credencial" in text or "credenciales" in text:
            return False

        return (
            "puestos ofertados" in text
            or "llocs ofertats" in text
            or "puestos provisionales ofertados" in text
            or "puestos definitivos ofertados" in text
            or "listado de vacantes" in text
            or "vacantes definitivas" in text
            or (
                "cuerpo/cos" in text
                and (
                    "provincia/provincia" in text
                    or "provincia/província" in text
                    or "provincia/provincia:" in text
                )
                and "localidad / localitat" in text
            )
        )

    def _infer_offered_positions_scope(self, text: str) -> str:
        if "dificil cobertura" in text or "dificil provisio" in text:
            return "dificil_cobertura"

        if "inicio de curso" in text or "inici de curs" in text or "vacantes" in text:
            return "inicio_curso"

        return "continua"

    def _looks_like_resolution_text(self, *, preview_text: str, title: str | None) -> bool:
        normalized_preview = self._normalize_match_text(preview_text[:1500])
        normalized_title = self._normalize_match_text(title or "")

        if not normalized_title.startswith("resolucion") and not normalized_title.startswith("resolucio"):
            return False

        formal_markers = (
            "resolucion de",
            "resolucio de",
            "direccion general de personal docente",
            "direccion territorial de educacion",
            "resuelvo",
        )
        return sum(1 for marker in formal_markers if marker in normalized_preview) >= 2

    def _read_pdf_preview(self, file_path: str, max_pages: int = 3) -> str:
        try:
            reader = PdfReader(str(Path(file_path)))
            chunks: list[str] = []
            for page in reader.pages[:max_pages]:
                chunks.append(page.extract_text() or "")
            return "\n".join(chunks)
        except Exception:
            return ""

    def _infer_document_date(
        self,
        *,
        preview_text: str,
        publication_date_text: str | None,
        original_filename: str,
    ) -> tuple[Optional[str], Optional[str]]:
        bag_month_match = re.search(
            r"listadobolsa_\d{3}_(?P<mm>\d{2})(?P<yy>\d{2})",
            original_filename,
            flags=re.IGNORECASE,
        )
        if bag_month_match:
            date_text = f"01/{bag_month_match.group('mm')}/20{bag_month_match.group('yy')}"
            return date_text, self._parse_ddmmyyyy_to_iso(date_text)

        preview_match = DATE_RE.search(preview_text)
        if preview_match:
            date_text = preview_match.group(1)
            return date_text, self._parse_ddmmyyyy_to_iso(date_text)

        if publication_date_text:
            return publication_date_text, self._parse_ddmmyyyy_to_iso(publication_date_text)

        filename_match = re.match(
            r"^(?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{2})_",
            original_filename,
        )
        if filename_match:
            dd = filename_match.group("dd")
            mm = filename_match.group("mm")
            yy = filename_match.group("yy")
            date_text = f"{dd}/{mm}/20{yy}"
            return date_text, self._parse_ddmmyyyy_to_iso(date_text)

        return None, None

    def _parse_ddmmyyyy_to_iso(self, value: str) -> Optional[str]:
        try:
            return datetime.strptime(value, "%d/%m/%Y").date().isoformat()
        except ValueError:
            return None

    def _normalize_match_text(self, value: str) -> str:
        value = value.strip().lower()
        value = unicodedata.normalize("NFKD", value)
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        value = re.sub(r"\s+", " ", value)
        return value