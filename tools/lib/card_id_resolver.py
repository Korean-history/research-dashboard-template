"""Shared card-ID alias and capture-target resolver."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tools.lib import core

SKIP_CARD_DIRS = {"templates", "review_queues"}
__all__ = [
    "SKIP_CARD_DIRS",
    "CardIdResolver",
    "as_text",
    "discover_card_ids",
    "load_alias_file",
    "load_resolver",
    "read_frontmatter_fast",
    "write_alias_file",
]


def as_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def discover_card_ids(root: Path) -> set[str]:
    card_ids: set[str] = set()
    cards_dir = root / "cards"
    if not cards_dir.exists():
        return card_ids
    for path in cards_dir.glob("*/*.md"):
        if path.parent.name in SKIP_CARD_DIRS:
            continue
        if path.name.startswith("."):
            continue
        metadata = read_frontmatter_fast(path)
        if not metadata:
            continue
        card_id = as_text(metadata.get("id"))
        if card_id:
            card_ids.add(card_id)
    return card_ids


def read_frontmatter_fast(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    marker = "\n---"
    end = text.find(marker, 3)
    if end == -1:
        return {}
    frontmatter = text[3:end].strip()
    try:
        data = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


@dataclass
class _Resolution:
    raw: str
    canonical: str | None
    status: str
    alias: str | None = None
    entry: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "alias": self.alias,
            "canonical": self.canonical,
            "status": self.status,
        }


class CardIdResolver:
    def __init__(self, root: Path, aliases: dict[str, dict[str, Any]], card_ids: set[str]):
        self.root = root
        self.aliases = aliases
        self.card_ids = set(card_ids)
        self.errors: list[str] = []
        self.cleanup_infos: list[str] = []
        self._validate()

    def _validate(self) -> None:
        for alias, entry in sorted(self.aliases.items()):
            canonical = entry.get("canonical")
            if alias in self.card_ids:
                if canonical is None:
                    self.errors.append(
                        f"alias_shadows_card: alias key {alias} shadows a physically-present card"
                    )
                elif as_text(canonical) != alias:
                    self.errors.append(
                        f"alias_shadows_card: alias key {alias} redirects a physically-present card to {canonical}"
                    )
                else:
                    self.cleanup_infos.append(f"self_canonical_alias: {alias}")
            if canonical is not None:
                canonical_text = as_text(canonical)
                if canonical_text and canonical_text != alias and canonical_text not in self.card_ids:
                    self.errors.append(
                        f"alias_unknown_canonical: alias key {alias} points to missing canonical {canonical_text}"
                    )

        for alias in sorted(self.aliases):
            seen = {alias}
            current = as_text(self.aliases[alias].get("canonical"))
            while current in self.aliases and current not in self.card_ids:
                if current in seen:
                    self.errors.append(f"alias_cycle: {' -> '.join([*seen, current])}")
                    break
                seen.add(current)
                current = as_text(self.aliases[current].get("canonical"))

    def resolve(self, raw_id: str) -> dict[str, Any]:
        raw = as_text(raw_id)
        if raw in self.aliases:
            entry = self.aliases[raw]
            canonical = entry.get("canonical")
            canonical_text = as_text(canonical) if canonical is not None else None
            status = as_text(entry.get("status")) or ("capture_target" if canonical is None else "deprecated_alias")
            return _Resolution(raw=raw, alias=raw, canonical=canonical_text, status=status, entry=entry).to_dict()
        if raw in self.card_ids:
            return _Resolution(raw=raw, canonical=raw, status="canonical").to_dict()
        return _Resolution(raw=raw, canonical=None, status="unknown").to_dict()

    def incoming_aliases(self, canonical_id: str) -> list[str]:
        return sorted(
            alias
            for alias, entry in self.aliases.items()
            if as_text(entry.get("canonical")) == canonical_id and alias != canonical_id
        )


def _normalize_aliases(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    aliases = raw.get("aliases", {})
    if isinstance(aliases, dict):
        return {
            as_text(alias): (dict(entry) if isinstance(entry, dict) else {"canonical": entry})
            for alias, entry in aliases.items()
            if as_text(alias)
        }
    if isinstance(aliases, list):
        out: dict[str, dict[str, Any]] = {}
        for entry in aliases:
            if not isinstance(entry, dict):
                continue
            alias = as_text(entry.get("alias"))
            if not alias:
                continue
            item = dict(entry)
            item.pop("alias", None)
            out[alias] = item
        return out
    return {}


def load_alias_file(root: Path) -> dict[str, Any]:
    path = root / "authority" / "card_id_aliases.yaml"
    data = core.read_yaml(path)
    if isinstance(data, dict):
        return data
    return {"schema_version": 1, "aliases": {}}


def load_resolver(root: str | Path, card_ids: set[str] | None = None) -> CardIdResolver:
    root_path = Path(root)
    ids = discover_card_ids(root_path) if card_ids is None else set(card_ids)
    raw = load_alias_file(root_path)
    return CardIdResolver(root_path, _normalize_aliases(raw), ids)


def write_alias_file(root: Path, aliases: dict[str, dict[str, Any]]) -> None:
    path = root / "authority" / "card_id_aliases.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "purpose": "Resolve legacy and generated card IDs to canonical IDs.",
        "aliases": {key: aliases[key] for key in sorted(aliases)},
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
