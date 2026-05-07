"""Import EndNote XML into the local journals SQLite database."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import re
import sqlite3
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lib import core

DEFAULT_XML_PATH = ROOT / "databases" / "journals" / "endnote_export.xml"
DEFAULT_DB_PATH = ROOT / "databases" / "journals" / "journals.db"

JOURNAL_LIKE_TYPES = {
    "journal article",
    "magazine article",
    "newspaper article",
    "electronic article",
}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def compact_text(value: str | None) -> str:
    return core.nfc(re.sub(r"\s+", " ", value or "").strip())


def element_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return compact_text(" ".join(part for part in element.itertext()))


def direct_child(parent: ET.Element | None, names: set[str]) -> ET.Element | None:
    if parent is None:
        return None
    for child in list(parent):
        if local_name(child.tag) in names:
            return child
    return None


def first_descendant(parent: ET.Element | None, names: set[str]) -> ET.Element | None:
    if parent is None:
        return None
    for child in parent.iter():
        if child is not parent and local_name(child.tag) in names:
            return child
    return None


def first_text(parent: ET.Element, names: list[str]) -> str:
    node = first_descendant(parent, set(names))
    return element_text(node)


def container_text(parent: ET.Element, container_name: str, child_names: list[str]) -> str:
    container = first_descendant(parent, {container_name})
    child = direct_child(container, set(child_names))
    return element_text(child)


def title_text(record: ET.Element) -> str:
    title = container_text(record, "titles", ["title", "short-title", "translated-title"])
    return title or first_text(record, ["title"])


def journal_text(record: ET.Element) -> str:
    periodical = first_descendant(record, {"periodical"})
    if periodical is not None:
        text = element_text(direct_child(periodical, {"full-title", "abbr-1", "abbr-2", "abbr-3"}))
        if text:
            return text
    return (
        container_text(record, "titles", ["secondary-title", "periodical-title"])
        or first_text(record, ["journal", "secondary-title", "full-title"])
    )


def record_type(record: ET.Element) -> str:
    node = first_descendant(record, {"ref-type", "reference-type"})
    if node is None:
        return ""
    name = compact_text(node.attrib.get("name") or node.attrib.get("type"))
    if name:
        return name
    text = element_text(node)
    return "" if text.isdigit() else text


def rec_number(record: ET.Element, fallback: int) -> int:
    text = first_text(record, ["rec-number", "record-number", "id"])
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else fallback


def parse_year(value: str) -> int | None:
    match = re.search(r"\d{4}", value or "")
    return int(match.group(0)) if match else None


def year_value(record: ET.Element) -> int | None:
    return parse_year(first_text(record, ["year", "date", "pub-date"]))


def contributor_values(record: ET.Element) -> list[str]:
    contributors = first_descendant(record, {"contributors"})
    authors_container = first_descendant(contributors, {"authors"}) if contributors is not None else None
    root = authors_container if authors_container is not None else (contributors if contributors is not None else record)
    values: list[str] = []
    for node in root.iter():
        if node is root:
            continue
        if local_name(node.tag) in {"author", "secondary-author", "tertiary-author"}:
            text = element_text(node)
            if text and text not in values:
                values.append(text)
    return values


def keyword_values(record: ET.Element) -> list[str]:
    values: list[str] = []
    for node in record.iter():
        if local_name(node.tag) == "keyword":
            text = element_text(node)
            if text and text not in values:
                values.append(text)
    return values


def semicolon_join(values: list[str]) -> str:
    return "; ".join(compact_text(value) for value in values if compact_text(value))


def simple_field(record: ET.Element, names: list[str]) -> str:
    return first_text(record, names)


def date_field(record: ET.Element, names: list[str]) -> str | None:
    text = first_text(record, names)
    return text or None


def language_from_endnote(value: str) -> str:
    normalized = compact_text(value).lower()
    if not normalized:
        return ""
    if normalized in {"ko", "kor", "korean", "한국어", "조선어"}:
        return "ko"
    if normalized in {"ja", "jpn", "japanese", "日本語"}:
        return "ja"
    if normalized in {"en", "eng", "english"}:
        return "en"
    if normalized in {"mixed", "multi", "multiple"}:
        return "mixed"
    if normalized in {"unknown", "undetermined"}:
        return "unknown"
    return normalized if normalized in {"ko", "ja", "en", "mixed", "unknown"} else "unknown"


def infer_language(title: str, abstract: str = "") -> str:
    # Prefer the title for script inference. Abstracts in fixtures and live
    # exports often contain English summaries even when the article itself is
    # Korean or Japanese.
    text = title or abstract
    hangul = len(re.findall(r"[\uac00-\ud7af]", text))
    kana = len(re.findall(r"[\u3040-\u30ff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    if hangul:
        return "ko"
    if kana:
        return "ja"
    if latin and not cjk:
        return "en"
    if latin and cjk:
        return "mixed"
    if cjk:
        return "unknown"
    return "unknown"


def source_fingerprint(title: str | None, year: int | None, authors: str | None, journal: str | None) -> str:
    first_author = (authors or "").split(";")[0].strip()
    canonical = "|".join([
        (title or "").strip().lower(),
        str(year or ""),
        first_author.lower(),
        (journal or "").strip().lower(),
    ])
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def file_type_for(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
        return "image"
    if suffix in {".doc", ".docx", ".rtf", ".txt"}:
        return "doc"
    return suffix.lstrip(".") or "unknown"


def normalize_attachment_path(raw: str, xml_path: Path, library_data_dir: Path | None = None) -> str:
    text = urllib.parse.unquote(compact_text(raw))
    for prefix in ["internal-pdf://", "file:///", "file://"]:
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.replace("/", "\\") if re.match(r"^[A-Za-z]:/", text) else text
    candidate = Path(text)
    if candidate.is_absolute():
        return str(candidate)
    if library_data_dir:
        return str((library_data_dir / text).resolve())
    return str((xml_path.parent / text).resolve())


def resolve_existing_pdf_path(path: Path) -> Path:
    """Resolve EndNote XML filename drift when a folder contains one PDF.

    EndNote XML sometimes normalizes Korean/Japanese filenames differently than
    the on-disk .Data/PDF filename. The internal-pdf folder id is reliable; when
    the expected file is absent but the folder has exactly one PDF, use it.
    """
    if path.exists():
        return path
    parent = path.parent
    if parent.exists():
        pdfs = list(parent.glob("*.pdf"))
        if len(pdfs) == 1:
            return pdfs[0]
    return path


def attachment_values(record: ET.Element, xml_path: Path, library_data_dir: Path | None) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for node in record.iter():
        name = local_name(node.tag)
        if name not in {"url", "attachment", "pdf", "file"}:
            continue
        raw = element_text(node)
        if not raw:
            continue
        lowered = raw.lower()
        if not (
            ".pdf" in lowered
            or lowered.startswith("internal-pdf://")
            or lowered.startswith("file://")
            or re.match(r"^[A-Za-z]:[\\/]", raw)
        ):
            continue
        normalized = normalize_attachment_path(raw, xml_path, library_data_dir)
        path = resolve_existing_pdf_path(Path(normalized))
        values.append({
            "original_path": raw,
            "normalized_path": str(path),
            "path_exists": 1 if path.exists() else 0,
            "file_type": file_type_for(str(path)),
            "file_size": path.stat().st_size if path.exists() and path.is_file() else None,
            "sha256": None,
        })
    return values


def full_text_value(record: ET.Element) -> str:
    return first_text(record, ["full-text", "full_text", "fulltext", "full-text-payload"])


def record_payload(record: ET.Element, index: int, xml_path: Path, imported_at: str, library_data_dir: Path | None) -> dict[str, Any]:
    title = title_text(record)
    abstract = simple_field(record, ["abstract"])
    authors = semicolon_join(contributor_values(record))
    year = year_value(record)
    journal = journal_text(record)
    lang = language_from_endnote(simple_field(record, ["language"])) or infer_language(title, abstract)
    rtype = record_type(record) or "Unknown"
    keywords = semicolon_join(keyword_values(record))
    return {
        "reference_id": rec_number(record, index),
        "source_fingerprint": source_fingerprint(title, year, authors, journal),
        "record_type": rtype,
        "is_journal_article": 1 if rtype.lower() in JOURNAL_LIKE_TYPES else 0,
        "title": title,
        "title_translit": simple_field(record, ["translated-title", "transliterated-title", "title-translit"]),
        "authors": authors,
        "year": year,
        "journal": journal,
        "volume": simple_field(record, ["volume"]),
        "issue": simple_field(record, ["issue", "number"]),
        "pages": simple_field(record, ["pages"]),
        "language": lang,
        "abstract": abstract,
        "keywords": keywords,
        "doi": simple_field(record, ["doi", "electronic-resource-num"]),
        "notes": simple_field(record, ["notes", "research-notes"]),
        "created": date_field(record, ["created", "date-added"]),
        "updated": date_field(record, ["updated", "date-modified"]),
        "imported_at": imported_at,
        "full_text": full_text_value(record),
        "attachments": attachment_values(record, xml_path, library_data_dir),
    }


def endnote_records(xml_path: Path) -> list[ET.Element]:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    return [node for node in root.iter() if local_name(node.tag) == "record"]


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    statements = [
        "DROP TABLE IF EXISTS full_text_fts",
        "DROP TABLE IF EXISTS journal_articles_fts",
        "DROP TABLE IF EXISTS attachments",
        "DROP TABLE IF EXISTS full_text",
        "DROP TABLE IF EXISTS journal_articles",
        """
        CREATE TABLE journal_articles (
            reference_id INTEGER PRIMARY KEY,
            source_fingerprint TEXT NOT NULL,
            record_type TEXT,
            is_journal_article INTEGER NOT NULL DEFAULT 0,
            title TEXT,
            title_translit TEXT,
            authors TEXT,
            year INTEGER,
            journal TEXT,
            volume TEXT,
            issue TEXT,
            pages TEXT,
            language TEXT,
            abstract TEXT,
            keywords TEXT,
            doi TEXT,
            notes TEXT,
            created TEXT,
            updated TEXT,
            imported_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE full_text (
            reference_id INTEGER PRIMARY KEY REFERENCES journal_articles(reference_id) ON DELETE CASCADE,
            text TEXT,
            extraction_method TEXT,
            extraction_confidence TEXT,
            extracted_at TEXT
        )
        """,
        """
        CREATE TABLE attachments (
            attachment_id INTEGER PRIMARY KEY,
            reference_id INTEGER REFERENCES journal_articles(reference_id) ON DELETE CASCADE,
            original_path TEXT,
            normalized_path TEXT,
            path_exists INTEGER,
            file_type TEXT,
            file_size INTEGER,
            sha256 TEXT
        )
        """,
        """
        CREATE VIRTUAL TABLE journal_articles_fts USING fts5(
            title, authors, journal, abstract, keywords, notes,
            content='journal_articles', content_rowid='reference_id',
            tokenize='trigram'
        )
        """,
        """
        CREATE VIRTUAL TABLE full_text_fts USING fts5(
            text,
            content='full_text', content_rowid='reference_id',
            tokenize='trigram'
        )
        """,
    ]
    for statement in statements:
        conn.execute(statement)


def insert_record(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    article_fields = [
        "reference_id", "source_fingerprint", "record_type", "is_journal_article",
        "title", "title_translit", "authors", "year", "journal", "volume", "issue",
        "pages", "language", "abstract", "keywords", "doi", "notes", "created",
        "updated", "imported_at",
    ]
    conn.execute(
        f"INSERT INTO journal_articles ({', '.join(article_fields)}) VALUES ({', '.join('?' for _ in article_fields)})",
        [payload.get(field) for field in article_fields],
    )
    if payload.get("full_text"):
        conn.execute(
            """
            INSERT INTO full_text
                (reference_id, text, extraction_method, extraction_confidence, extracted_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [payload["reference_id"], payload["full_text"], "manual", "high", payload["imported_at"]],
        )
    for attachment in payload.get("attachments", []):
        conn.execute(
            """
            INSERT INTO attachments
                (reference_id, original_path, normalized_path, path_exists, file_type, file_size, sha256)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                payload["reference_id"],
                attachment.get("original_path"),
                attachment.get("normalized_path"),
                attachment.get("path_exists"),
                attachment.get("file_type"),
                attachment.get("file_size"),
                attachment.get("sha256"),
            ],
        )


def rebuild_fts(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO journal_articles_fts(journal_articles_fts) VALUES ('rebuild')")
    conn.execute("INSERT INTO full_text_fts(full_text_fts) VALUES ('rebuild')")


def import_xml(xml_path: Path, db_path: Path, library_data_dir: Path | None = None) -> int:
    if not xml_path.exists():
        print(f"ERROR: missing XML export: {xml_path}")
        return 1
    imported_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    records = endnote_records(xml_path)
    if not records:
        print(f"ERROR: no EndNote <record> elements found in {xml_path}")
        return 1

    with connect(db_path) as conn:
        create_schema(conn)
        seen_ids: set[int] = set()
        fingerprints: dict[str, list[int]] = {}
        for index, record in enumerate(records, 1):
            payload = record_payload(record, index, xml_path, imported_at, library_data_dir)
            reference_id = int(payload["reference_id"])
            if reference_id in seen_ids:
                print(f"ERROR: duplicate reference_id in XML export: {reference_id}")
                return 1
            seen_ids.add(reference_id)
            fingerprints.setdefault(payload["source_fingerprint"], []).append(reference_id)
            insert_record(conn, payload)
        rebuild_fts(conn)

    collisions = {key: ids for key, ids in fingerprints.items() if len(ids) > 1}
    print(f"Imported {len(records)} EndNote records into {db_path}")
    if collisions:
        print(f"WARN: {len(collisions)} source_fingerprint collisions detected")
    return 0


def validate_db(db_path: Path) -> int:
    if not db_path.exists():
        print(f"ERROR: missing database: {db_path}")
        return 1
    required = {"journal_articles", "full_text", "attachments", "journal_articles_fts", "full_text_fts"}
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = required - tables
        if missing:
            print("ERROR: missing tables: " + ", ".join(sorted(missing)))
            return 1
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            print(f"ERROR: integrity_check failed: {integrity}")
            return 1
        count = conn.execute("SELECT COUNT(*) FROM journal_articles").fetchone()[0]
    print(f"OK: {db_path} contains {count} journal records.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import EndNote XML into a journals SQLite database.")
    sub = parser.add_subparsers(dest="command", required=True)

    import_parser = sub.add_parser("import", help="Wipe and rebuild journals.db from EndNote XML.")
    import_parser.add_argument("--xml", type=Path, default=DEFAULT_XML_PATH)
    import_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    import_parser.add_argument("--library-data-dir", type=Path, default=None)

    validate_parser = sub.add_parser("validate", help="Validate database structure and integrity.")
    validate_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)

    args = parser.parse_args(argv)
    if args.command == "import":
        return import_xml(args.xml, args.db, args.library_data_dir)
    if args.command == "validate":
        return validate_db(args.db)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
