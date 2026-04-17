from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from app.storage.sqlite import get_connection


OUTPUT_DIR = Path("data/debug/difficult_coverage")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def normalize_spaces(value: str) -> str:
    return " ".join(value.split())


def dump_document(document_id: int, file_path: str, original_filename: str) -> None:
    pdf_path = Path(file_path)
    if not pdf_path.is_absolute():
        pdf_path = Path.cwd() / pdf_path

    reader = PdfReader(str(pdf_path))

    output_path = OUTPUT_DIR / f"{document_id}_{original_filename}.txt"

    lines_out: list[str] = []
    lines_out.append(f"document_id={document_id}")
    lines_out.append(f"original_filename={original_filename}")
    lines_out.append(f"pdf_path={pdf_path}")
    lines_out.append("")

    global_line_number = 1

    for page_index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        raw_lines = text.splitlines()

        lines_out.append("=" * 100)
        lines_out.append(f"PAGE {page_index}")
        lines_out.append("=" * 100)

        for raw_line in raw_lines:
            line = normalize_spaces(raw_line)
            if not line:
                continue

            lines_out.append(f"{global_line_number:06d} | {line}")
            global_line_number += 1

        lines_out.append("")

    output_path.write_text("\n".join(lines_out), encoding="utf-8")
    print(f"Dump generado: {output_path}")


def main() -> None:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                d.id AS document_id,
                dv.file_path,
                dv.original_filename
            FROM documents d
            JOIN document_versions dv
                ON dv.id = d.document_version_id
            WHERE d.doc_family = 'difficult_coverage_provisional'
            ORDER BY d.id
            """
        ).fetchall()

    for row in rows:
        dump_document(
            document_id=int(row["document_id"]),
            file_path=row["file_path"],
            original_filename=row["original_filename"],
        )


if __name__ == "__main__":
    main()