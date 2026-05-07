"""Pre-populate capture-target aliases for unresolved card links."""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lib import core
from tools.lib.card_id_resolver import SKIP_CARD_DIRS, discover_card_ids, load_alias_file, read_frontmatter_fast, write_alias_file


LINK_FIELDS = {"cites", "related", "contradicts", "refutes", "supersedes", "complicates"}


def as_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [as_text(item) for item in value if as_text(item)]
    text = as_text(value)
    return [text] if text else []


def load_aliases(root: Path) -> dict[str, dict[str, Any]]:
    raw = load_alias_file(root)
    aliases = raw.get("aliases", {}) if isinstance(raw, dict) else {}
    if isinstance(aliases, dict):
        return {as_text(key): dict(value or {}) for key, value in aliases.items() if as_text(key)}
    out: dict[str, dict[str, Any]] = {}
    if isinstance(aliases, list):
        for entry in aliases:
            if not isinstance(entry, dict):
                continue
            alias = as_text(entry.get("alias"))
            if alias:
                item = dict(entry)
                item.pop("alias", None)
                out[alias] = item
    return out


def cards_from_index(root: Path) -> list[dict[str, Any]]:
    data = core.read_json(root / "CARDS_INDEX.json")
    if not isinstance(data, dict):
        return []
    cards = data.get("cards")
    if isinstance(cards, list):
        return [card for card in cards if isinstance(card, dict)]
    cards_by_id = data.get("cards_by_id")
    if isinstance(cards_by_id, dict):
        return [card for card in cards_by_id.values() if isinstance(card, dict)]
    return []


def unresolved_links(root: Path, card_ids: set[str], aliases: dict[str, dict[str, Any]]) -> set[str]:
    unresolved: set[str] = set()
    indexed_cards = cards_from_index(root)
    if indexed_cards:
        for card in indexed_cards:
            metadata = card.get("metadata") if isinstance(card.get("metadata"), dict) else {}
            linked = metadata.get("linked_cards")
            if not isinstance(linked, dict):
                continue
            for relation in LINK_FIELDS:
                for target in as_list(linked.get(relation)):
                    if target not in card_ids and target not in aliases:
                        unresolved.add(target)
        return unresolved

    cards_dir = root / "cards"
    if not cards_dir.exists():
        return unresolved
    for path in cards_dir.glob("*/*.md"):
        if path.parent.name in SKIP_CARD_DIRS:
            continue
        metadata = read_frontmatter_fast(path)
        linked = metadata.get("linked_cards")
        if not isinstance(linked, dict):
            continue
        for relation in LINK_FIELDS:
            for target in as_list(linked.get(relation)):
                if target not in card_ids and target not in aliases:
                    unresolved.add(target)
    return unresolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Populate authority/card_id_aliases.yaml with capture targets.")
    parser.add_argument("--root", default=".", help="Workspace root.")
    args = parser.parse_args(argv)
    root = Path(args.root)
    indexed_cards = cards_from_index(root)
    card_ids = {as_text(card.get("card_id") or (card.get("metadata") or {}).get("id")) for card in indexed_cards}
    card_ids = {card_id for card_id in card_ids if card_id} or discover_card_ids(root)
    aliases = load_aliases(root)

    today = dt.date.today().isoformat()
    for alias, entry in list(aliases.items()):
        if alias in card_ids and entry.get("canonical") is None:
            entry["canonical"] = alias
            entry["status"] = "deprecated_alias"
            entry["notes"] = as_text(entry.get("notes")) or "Promoted from capture target after physical card creation."

    for target in sorted(unresolved_links(root, card_ids, aliases)):
        aliases[target] = {
            "canonical": None,
            "status": "capture_target",
            "first_seen": today,
            "notes": "Auto-populated by tools/sweep_capture_targets.py from unresolved card links.",
        }

    write_alias_file(root, aliases)
    print(f"Capture targets: {sum(1 for item in aliases.values() if item.get('canonical') is None)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
