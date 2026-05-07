"""Seed stable entity and idea skeleton cards from authority CSVs."""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lib import core

ENTITIES_PATH = ROOT / "authority" / "entities.csv"
TERMS_PATH = ROOT / "authority" / "terms.csv"
ENTITY_DIR = ROOT / "cards" / "entity"
IDEA_DIR = ROOT / "cards" / "idea"

CHAPTER_ALIASES = {
    "Intro": "Introduction",
    "Ch. 1": "Ch1",
    "Ch. 2": "Deleted Ch2 context",
    "Ch. 3": "Ch3",
    "Ch. 4": "Ch4",
    "Ch. 5": "Ch5",
    "Ch. 6": "Ch6",
    "Ch. 7": "Ch7",
    "Deleted_Ch2_Context": "Deleted Ch2 context",
    "Deleted Ch. 2 context": "Deleted Ch2 context",
    "deleted Ch. 2 context": "Deleted Ch2 context",
}


def split_values(value: str | None) -> list[str]:
    return core.split_values(value or "")


def normalize_chapter(value: str) -> str:
    text = value.strip()
    if text in CHAPTER_ALIASES:
        return CHAPTER_ALIASES[text]
    text = re.sub(r"^Ch\.\s*", "Ch", text)
    return CHAPTER_ALIASES.get(text, text)


def chapters(value: str | None) -> list[str]:
    normalized: list[str] = []
    for item in split_values(value):
        chapter = normalize_chapter(item)
        if chapter and chapter not in normalized:
            normalized.append(chapter)
    return normalized


def slug(value: str) -> str:
    text = value.strip()
    text = text.replace(":", ".")
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._")
    return text.lower()


def linked_cards() -> dict[str, list[str]]:
    return {
        "cites": [],
        "related": [],
        "contradicts": [],
        "supersedes": [],
    }


def yaml_text(data: dict[str, Any]) -> str:
    return "---\n" + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000) + "---\n"


def write_card(path: Path, frontmatter: dict[str, Any], body: str, dry_run: bool = False) -> bool:
    if path.exists():
        print(f"WARN: skip existing card: {path.relative_to(ROOT).as_posix()}")
        return False
    if dry_run:
        print(f"DRY-RUN: would write {path.relative_to(ROOT).as_posix()}")
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml_text(frontmatter) + body, encoding="utf-8")
    print(f"Wrote {path.relative_to(ROOT).as_posix()}")
    return True


def base_frontmatter(card_id: str, card_type: str, title: str, chapter_relevance: list[str], date: str) -> dict[str, Any]:
    return {
        "id": card_id,
        "card_type": card_type,
        "title": title,
        "created": date,
        "updated": date,
        "status": "draft",
        "chapter_relevance": chapter_relevance,
        "arc_ids": [],
        "tags": [],
        "linked_cards": linked_cards(),
        "template_instance": None,
    }


def entity_body(cautions: str) -> str:
    body = [
        "## Profile",
        "[To be authored by Claude during chapter extraction.]",
        "",
        "## Connected ideas",
        "[To be populated.]",
        "",
        "## Connected places",
        "[To be populated.]",
        "",
        "## Connected timeline",
        "[To be populated.]",
        "",
        "## Cautions",
        cautions.strip() if cautions.strip() else "[None recorded in authority CSV.]",
        "",
    ]
    return "\n".join(body)


def idea_body(canonical_form: str, variants: str) -> str:
    body = [
        "## Definition",
        f"[To be authored by Claude. CSV canonical_form: {canonical_form}; variants: {variants}.]",
        "",
        "## Why it matters",
        "[To be authored.]",
        "",
        "## Sub-clusters",
        "[To be populated.]",
        "",
        "## Anchoring sources",
        "[To be populated as source_snippet cards reference this idea.]",
        "",
    ]
    return "\n".join(body)


def seed_entities(date: str, dry_run: bool = False) -> int:
    rows, errors = core.read_csv(ENTITIES_PATH)
    if errors:
        raise RuntimeError("; ".join(errors))
    created = 0
    for row in rows:
        entity_id = (row.get("entity_id") or "").strip()
        if not entity_id:
            continue
        card_id = f"entity:{entity_id}"
        path = ENTITY_DIR / f"entity.{slug(entity_id)}.md"
        variants = split_values(row.get("variants", ""))
        frontmatter = base_frontmatter(
            card_id=card_id,
            card_type="entity",
            title=(row.get("canonical_label") or entity_id).strip(),
            chapter_relevance=chapters(row.get("chapters", "")),
            date=date,
        )
        frontmatter.update({
            "entity_id": entity_id,
            "entity_subtype": (row.get("type") or "").strip(),
            "canonical_label": (row.get("canonical_label") or "").strip(),
            "role_in_book": (row.get("notes") or "").strip() or "[To be authored.]",
            "romanization_primary": variants[0] if variants else "",
            "romanization_variants": variants,
            "chapters_appears": chapters(row.get("chapters", "")),
            "cautions": (row.get("cautions") or "").strip(),
            "notes": (row.get("notes") or "").strip(),
        })
        if write_card(path, frontmatter, entity_body(row.get("cautions", "")), dry_run=dry_run):
            created += 1
    return created


def seed_ideas(date: str, dry_run: bool = False) -> int:
    rows, errors = core.read_csv(TERMS_PATH)
    if errors:
        raise RuntimeError("; ".join(errors))
    created = 0
    seen_labels: set[str] = set()
    for row in rows:
        term_id = (row.get("term_id") or "").strip()
        canonical = (row.get("canonical_label") or "").strip()
        if not term_id or not canonical:
            continue
        canonical_key = canonical.lower()
        if canonical_key in seen_labels:
            print(f"WARN: skip duplicate canonical term row: {term_id}")
            continue
        seen_labels.add(canonical_key)
        card_id = f"idea:{term_id}"
        path = IDEA_DIR / f"idea.{slug(term_id)}.md"
        variants = split_values(row.get("variants", ""))
        frontmatter = base_frontmatter(
            card_id=card_id,
            card_type="idea",
            title=canonical,
            chapter_relevance=chapters(row.get("chapters", "")),
            date=date,
        )
        frontmatter.update({
            "term_id": term_id,
            "canonical_form": canonical,
            "term_variants": variants,
            "romanization": (row.get("romanization") or "").strip(),
            "register": [],
            "parent_idea": "",
            "sub_ideas": [],
            "cautions": (row.get("cautions") or "").strip(),
            "notes": (row.get("notes") or "").strip(),
        })
        if write_card(path, frontmatter, idea_body(canonical, row.get("variants", "")), dry_run=dry_run):
            created += 1
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed entity and idea skeleton cards from authority CSVs.")
    parser.add_argument("--date", default=dt.date.today().isoformat(), help="Created/updated date for new cards.")
    parser.add_argument("--dry-run", action="store_true", help="Show files that would be written without writing them.")
    args = parser.parse_args(argv)

    entity_count = seed_entities(args.date, dry_run=args.dry_run)
    idea_count = seed_ideas(args.date, dry_run=args.dry_run)
    print(f"Created {entity_count} entity cards and {idea_count} idea cards.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
