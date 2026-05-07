"""Generate a compact authority quick-reference for card extraction."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lib import core

AUTHORITY_DIR = ROOT / "authority"
ENTITIES_PATH = AUTHORITY_DIR / "entities.csv"
TERMS_PATH = AUTHORITY_DIR / "terms.csv"
SOURCES_PATH = AUTHORITY_DIR / "sources.csv"
TAGS_PATH = AUTHORITY_DIR / "tags.yaml"
OUTPUT_PATH = ROOT / "cards" / "AUTHORITY_QUICKREF.md"


def variants(value: str, limit: int = 4) -> str:
    items = core.split_values(value)
    if not items:
        return ""
    shown = items[:limit]
    suffix = "" if len(items) <= limit else f" (+{len(items) - limit})"
    return "; ".join(shown) + suffix


def read_rows(path: Path) -> list[dict[str, str]]:
    rows, errors = core.read_csv(path)
    if errors:
        raise RuntimeError("; ".join(errors))
    return rows


def table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    return core.markdown_table(headers, rows)


def tag_rows() -> list[list[str]]:
    data = core.read_yaml(TAGS_PATH)
    if not isinstance(data, dict):
        return []
    rows: list[list[str]] = []
    for category in data.get("tag_categories", []):
        if not isinstance(category, dict):
            continue
        category_id = str(category.get("category_id", ""))
        for tag in category.get("tags", []):
            if not isinstance(tag, dict):
                continue
            tag_id = str(tag.get("tag_id", ""))
            if tag_id:
                rows.append([tag_id, str(tag.get("label", "")), category_id, variants("; ".join(tag.get("aliases", []) or []))])
    return sorted(rows, key=lambda row: row[0])


def build_quickref() -> str:
    entities = read_rows(ENTITIES_PATH)
    terms = read_rows(TERMS_PATH)
    sources = read_rows(SOURCES_PATH)

    lines: list[str] = [
        "# Authority Quick Reference",
        "",
        "Generated from `authority/*.csv` and `authority/tags.yaml`. Do not edit by hand; run `python tools/build_authority_quickref.py`.",
        "",
        "Use this before extracting cards to avoid inventing duplicate IDs.",
        "",
        "## Entities",
        "",
    ]
    lines.extend(table(
        ["ID", "Type", "Label", "Variants", "Chapters", "Status", "Cautions"],
        [
            [
                row.get("entity_id", ""),
                row.get("type", ""),
                row.get("canonical_label", ""),
                variants(row.get("variants", "")),
                row.get("chapters", ""),
                row.get("status", ""),
                row.get("cautions", ""),
            ]
            for row in sorted(entities, key=lambda row: row.get("entity_id", ""))
        ],
    ))

    lines.extend(["", "## Terms", ""])
    lines.extend(table(
        ["ID", "Label", "Variants", "Romanization", "Chapters", "Status", "Cautions"],
        [
            [
                row.get("term_id", ""),
                row.get("canonical_label", ""),
                variants(row.get("variants", "")),
                row.get("romanization", ""),
                row.get("chapters", ""),
                row.get("status", ""),
                row.get("cautions", ""),
            ]
            for row in sorted(terms, key=lambda row: row.get("term_id", ""))
        ],
    ))

    lines.extend(["", "## Sources", ""])
    lines.extend(table(
        ["ID", "Type", "Title", "Date", "Entities", "Status", "Cautions"],
        [
            [
                row.get("source_id", ""),
                row.get("source_type", ""),
                row.get("title", ""),
                row.get("date_range", ""),
                variants(row.get("authority_entities", "")),
                row.get("status", ""),
                row.get("cautions", ""),
            ]
            for row in sorted(sources, key=lambda row: row.get("source_id", ""))
        ],
    ))

    tags = tag_rows()
    lines.extend(["", "## Tags", ""])
    lines.extend(table(["ID", "Label", "Category", "Aliases"], tags))
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(build_quickref(), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH.relative_to(ROOT).as_posix()}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
