"""Extract searchable text from journal PDF attachments into journals.db."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sqlite3
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover - runtime dependency check
    fitz = None  # type: ignore

DEFAULT_DB_PATH = Path(r"C:\Endnote\journals.db")
DEFAULT_REPORT_PATH = Path(r"C:\Endnote\journals_full_text_extraction_report.csv")
MIN_TEXT_CHARS = 80
MIN_CHARS_PER_PAGE = 20

REPORT_FIELDS = [
    "reference_id",
    "status",
    "extraction_confidence",
    "extraction_method",
    "page_count",
    "char_count",
    "attachment_count",
    "title",
    "year",
    "journal",
    "normalized_paths",
    "reason",
]


@dataclass
class Attachment:
    reference_id: int
    path: Path


@dataclass
class ExtractionResult:
    reference_id: int
    text: str
    extraction_method: str
    extraction_confidence: str
    status: str
    page_count: int
    char_count: int
    reason: str
    attachment_paths: list[str]


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def compact_text(text: str) -> str:
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def count_chars(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def classify(char_count: int, page_count: int) -> tuple[str, str, str]:
    if page_count <= 0:
        return "needs_ocr", "low", "No readable pages found."
    chars_per_page = char_count / max(page_count, 1)
    if char_count < MIN_TEXT_CHARS or chars_per_page < MIN_CHARS_PER_PAGE:
        return "needs_ocr", "low", "Extracted text is too short; likely image-only or bad OCR."
    if char_count >= 1000 and chars_per_page >= 250:
        return "extracted", "high", "Substantial text layer extracted."
    if char_count >= 300 and chars_per_page >= 80:
        return "extracted", "medium", "Text layer extracted but short for page count."
    return "extracted", "low", "Text layer extracted but sparse; inspect before relying on it."


def extract_pdf(path: Path) -> tuple[str, int, str | None]:
    if fitz is None:
        return "", 0, "PyMuPDF is not installed."
    try:
        doc = fitz.open(path)
    except Exception as exc:
        return "", 0, f"Could not open PDF: {exc}"
    pieces: list[str] = []
    try:
        page_count = doc.page_count
        for page in doc:
            try:
                pieces.append(page.get_text("text") or "")
            except Exception as exc:
                pieces.append(f"\n[PAGE TEXT EXTRACTION ERROR: {exc}]\n")
        return compact_text("\n\n".join(pieces)), page_count, None
    except Exception as exc:
        return "", getattr(doc, "page_count", 0), f"PDF extraction error: {exc}"
    finally:
        doc.close()


def repair_attachment_paths(conn: sqlite3.Connection) -> int:
    """Repair XML-vs-disk filename drift for EndNote internal PDF folders."""
    rows = conn.execute(
        """
        SELECT attachment_id, normalized_path
        FROM attachments
        WHERE file_type='pdf'
          AND path_exists=0
          AND normalized_path IS NOT NULL
          AND normalized_path != ''
        """
    ).fetchall()
    repaired = 0
    for row in rows:
        expected = Path(row["normalized_path"])
        parent = expected.parent
        if not parent.exists():
            continue
        pdfs = list(parent.glob("*.pdf"))
        if len(pdfs) != 1:
            continue
        actual = pdfs[0]
        conn.execute(
            """
            UPDATE attachments
            SET normalized_path=?, path_exists=1, file_size=?
            WHERE attachment_id=?
            """,
            [str(actual), actual.stat().st_size if actual.is_file() else None, row["attachment_id"]],
        )
        repaired += 1
    return repaired


def candidate_attachments(conn: sqlite3.Connection, include_missing: bool = True) -> list[Attachment]:
    rows = conn.execute(
        """
        SELECT reference_id, normalized_path
        FROM attachments
        WHERE file_type='pdf'
          AND normalized_path IS NOT NULL
          AND normalized_path != ''
          AND (? OR path_exists=1)
        ORDER BY reference_id, attachment_id
        """,
        [1 if include_missing else 0],
    ).fetchall()
    return [Attachment(int(row["reference_id"]), Path(row["normalized_path"])) for row in rows]


def existing_full_text_ids(conn: sqlite3.Connection, retry_methods: set[str]) -> set[int]:
    if retry_methods:
        placeholders = ",".join("?" for _ in retry_methods)
        return {
            int(row["reference_id"])
            for row in conn.execute(
                f"SELECT reference_id FROM full_text WHERE extraction_method NOT IN ({placeholders})",
                sorted(retry_methods),
            )
        }
    return {
        int(row["reference_id"])
        for row in conn.execute("SELECT reference_id FROM full_text")
    }


def article_metadata(conn: sqlite3.Connection, reference_ids: list[int]) -> dict[int, sqlite3.Row]:
    if not reference_ids:
        return {}
    placeholders = ",".join("?" for _ in reference_ids)
    return {
        int(row["reference_id"]): row
        for row in conn.execute(
            f"SELECT reference_id, title, year, journal FROM journal_articles WHERE reference_id IN ({placeholders})",
            reference_ids,
        )
    }


def grouped_attachments(attachments: list[Attachment]) -> dict[int, list[Path]]:
    grouped: dict[int, list[Path]] = defaultdict(list)
    for attachment in attachments:
        if attachment.path not in grouped[attachment.reference_id]:
            grouped[attachment.reference_id].append(attachment.path)
    return dict(grouped)


def extract_reference(reference_id: int, paths: list[Path]) -> ExtractionResult:
    texts: list[str] = []
    total_pages = 0
    errors: list[str] = []
    existing_paths: list[Path] = [path for path in paths if path.exists()]
    if not existing_paths:
        return ExtractionResult(
            reference_id=reference_id,
            text="",
            extraction_method="missing_pdf",
            extraction_confidence="low",
            status="missing_pdf",
            page_count=0,
            char_count=0,
            reason="No attachment path exists on disk.",
            attachment_paths=[str(path) for path in paths],
        )

    for path in existing_paths:
        text, pages, error = extract_pdf(path)
        total_pages += pages
        if error:
            errors.append(f"{path}: {error}")
        if text:
            texts.append(text)

    combined = compact_text("\n\n".join(texts))
    chars = count_chars(combined)
    status, confidence, reason = classify(chars, total_pages)
    method = "endnote_pdf" if status == "extracted" else "needs_ocr"
    if errors and not combined:
        status = "extract_error"
        confidence = "low"
        method = "extract_error"
        reason = " | ".join(errors[:3])
    elif errors:
        reason = reason + " Partial extraction errors: " + " | ".join(errors[:2])

    return ExtractionResult(
        reference_id=reference_id,
        text=combined,
        extraction_method=method,
        extraction_confidence=confidence,
        status=status,
        page_count=total_pages,
        char_count=chars,
        reason=reason,
        attachment_paths=[str(path) for path in paths],
    )


def upsert_result(conn: sqlite3.Connection, result: ExtractionResult, extracted_at: str) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO full_text
            (reference_id, text, extraction_method, extraction_confidence, extracted_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            result.reference_id,
            result.text,
            result.extraction_method,
            result.extraction_confidence,
            extracted_at,
        ],
    )


def rebuild_full_text_fts(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO full_text_fts(full_text_fts) VALUES ('rebuild')")


def write_report(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def report_row(result: ExtractionResult, metadata: sqlite3.Row | None) -> dict[str, Any]:
    return {
        "reference_id": result.reference_id,
        "status": result.status,
        "extraction_confidence": result.extraction_confidence,
        "extraction_method": result.extraction_method,
        "page_count": result.page_count,
        "char_count": result.char_count,
        "attachment_count": len(result.attachment_paths),
        "title": metadata["title"] if metadata else "",
        "year": metadata["year"] if metadata else "",
        "journal": metadata["journal"] if metadata else "",
        "normalized_paths": " | ".join(result.attachment_paths),
        "reason": result.reason,
    }


def extract_full_text(
    db_path: Path,
    report_path: Path,
    limit: int | None = None,
    offset: int = 0,
    reset: bool = False,
    commit_every: int = 25,
    include_missing: bool = True,
    retry_methods: set[str] | None = None,
) -> int:
    started = time.time()
    extracted_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    with connect(db_path) as conn:
        if reset:
            conn.execute("DELETE FROM full_text")
            conn.commit()

        repaired = repair_attachment_paths(conn)
        if repaired:
            conn.commit()
            print(f"Repaired {repaired} attachment paths from single-PDF EndNote folders.")

        attachments = candidate_attachments(conn, include_missing=include_missing)
        grouped = grouped_attachments(attachments)
        existing = set() if reset else existing_full_text_ids(conn, retry_methods or set())
        reference_ids = [rid for rid in sorted(grouped) if rid not in existing]
        if offset:
            reference_ids = reference_ids[offset:]
        if limit is not None:
            reference_ids = reference_ids[:limit]

        metadata = article_metadata(conn, reference_ids)
        rows: list[dict[str, Any]] = []
        counts: dict[str, int] = defaultdict(int)
        total = len(reference_ids)
        print(f"Extracting {total} journal records into {db_path}")

        for index, reference_id in enumerate(reference_ids, 1):
            result = extract_reference(reference_id, grouped[reference_id])
            upsert_result(conn, result, extracted_at)
            rows.append(report_row(result, metadata.get(reference_id)))
            counts[result.status] += 1

            if index % commit_every == 0:
                conn.commit()
                print(
                    f"[{index}/{total}] extracted={counts['extracted']} "
                    f"needs_ocr={counts['needs_ocr']} missing={counts['missing_pdf']} "
                    f"errors={counts['extract_error']}",
                    flush=True,
                )

        conn.commit()
        rebuild_full_text_fts(conn)
        conn.commit()

    write_report(report_path, rows)
    elapsed = time.time() - started
    print(
        f"Done in {elapsed:.1f}s. extracted={counts['extracted']} "
        f"needs_ocr={counts['needs_ocr']} missing={counts['missing_pdf']} "
        f"errors={counts['extract_error']}"
    )
    print(f"Wrote report: {report_path}")
    return 0


def summarize(db_path: Path) -> int:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT extraction_method, extraction_confidence, COUNT(*) AS n,
                   SUM(LENGTH(COALESCE(text, ''))) AS text_bytes
            FROM full_text
            GROUP BY extraction_method, extraction_confidence
            ORDER BY n DESC
            """
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS n FROM full_text").fetchone()["n"]
        print(f"Full-text rows: {total}")
        for row in rows:
            print(dict(row))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract PDF text into the journals full_text table.")
    sub = parser.add_subparsers(dest="command", required=True)

    extract_parser = sub.add_parser("extract", help="Extract text from PDF attachments.")
    extract_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    extract_parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    extract_parser.add_argument("--limit", type=int, default=None)
    extract_parser.add_argument("--offset", type=int, default=0)
    extract_parser.add_argument("--reset", action="store_true", help="Delete existing full_text rows before extracting.")
    extract_parser.add_argument("--commit-every", type=int, default=25)
    extract_parser.add_argument("--skip-missing", action="store_true", help="Skip missing PDF paths instead of recording missing_pdf rows.")
    extract_parser.add_argument(
        "--retry-method",
        action="append",
        default=[],
        help="Re-extract rows whose existing extraction_method matches this value. May be repeated.",
    )

    summarize_parser = sub.add_parser("summarize", help="Summarize full-text extraction status.")
    summarize_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)

    args = parser.parse_args(argv)
    if args.command == "extract":
        return extract_full_text(
            db_path=args.db,
            report_path=args.report,
            limit=args.limit,
            offset=args.offset,
            reset=args.reset,
            commit_every=max(args.commit_every, 1),
            include_missing=not args.skip_missing,
            retry_methods=set(args.retry_method or []),
        )
    if args.command == "summarize":
        return summarize(args.db)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
