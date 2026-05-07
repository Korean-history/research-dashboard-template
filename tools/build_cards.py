"""Build and validate the parallel cards apparatus.

The cards layer is intentionally read-only from the pipeline's point of view:
this script compiles Markdown cards into JSON/CSV views and diagnostics, but it
never writes back to user-authored card files.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lib import core, report_paths
from tools.lib.card_dates import parse_date_or_range
from tools.lib.card_diagnostics import build_self_audit
from tools.lib.card_id_resolver import CardIdResolver, load_resolver
from tools.lib.promotion_gates import evaluate as evaluate_promotion_gate

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

CARDS_DIR = ROOT / "cards"
SCHEMA_PATH = ROOT / "authority" / "cards_schema.yaml"
ARCS_PATH = ROOT / "argument_arcs.yaml"
TAGS_PATH = ROOT / "authority" / "tags.yaml"
MATRIX_PATH = ROOT / "argument_matrix.csv"
ENTITIES_PATH = ROOT / "authority" / "entities.csv"
TERMS_PATH = ROOT / "authority" / "terms.csv"
SOURCES_PATH = ROOT / "authority" / "sources.csv"
CATALOG_PATH = ROOT / "research_catalog.csv"
INDEXES_DIR = ROOT / "indexes"

CARDS_INDEX_JSON = ROOT / "CARDS_INDEX.json"
CARDS_INDEX_CSV = ROOT / "cards_index.csv"
CARDS_TICKETS_MD = ROOT / "CARDS_TICKETS.md"
CARDS_TICKETS_JSON = ROOT / "CARDS_TICKETS.json"
CARDS_DIAGNOSTICS_JSON = ROOT / "CARDS_DIAGNOSTICS.json"
REPORT_TO_CARDS_INDEX_JSON = INDEXES_DIR / "report_to_cards_index.json"
PIVOT_CHAPTER_CSV = ROOT / "cards_by_chapter.csv"
PIVOT_ARC_CSV = ROOT / "cards_by_arc.csv"
PIVOT_ENTITY_CSV = ROOT / "cards_by_entity.csv"
PIVOT_TERM_CSV = ROOT / "cards_by_term.csv"

CARD_INDEX_FIELDS = [
    "card_id",
    "card_type",
    "title",
    "status",
    "chapters",
    "arcs",
    "tags",
    "template_instance",
    "incoming_link_count",
    "outgoing_link_count",
    "created",
    "updated",
    "path",
    "archival_validity",
    "analytical_credibility",
    "evidence_stage",
]

PIVOT_FIELDS = ["key", "card_id", "card_type", "title", "status", "path"]

REVERSE_EDGE_NAMES = {"cited_by", "cited_in", "causal_successors"}
LINK_FIELDS = {"cites", "related", "contradicts", "refutes", "supersedes", "complicates"}
LISTLIKE_FIELDS = {
    "chapter_relevance",
    "arc_ids",
    "tags",
    "report_files",
    "source_ids",
    "derived_from_reports",
    "informed_by_reports",
    "claim_ids",
    "entity_ids",
    "term_ids",
    "register",
    "sub_ideas",
    "depends_on",
    "evidence_bindings",
    "romanization_variants",
    "chapters_appears",
    "candidate_evidence",
    "blocking",
    "inputs",
    "output_claims",
    "upstream_claims",
    "downstream_claims",
    "position_holders",
    "refuting_synthesis",
    "refuting_snippets",
    "causal_predecessors",
    "evidence_cards",
    "occupants",
    "associated_events",
    "child_cards",
    "sub_arguments",
    "feeding_synthesis",
    "warning_flags",
}

FIELD_ENUMS = {
    "status": "status",
    "original_lang": "original_lang",
    "evidence_type": "evidence_type",
    "citation_status": "citation_status",
    "risk_level": "risk_level",
    "claim_type": "claim_type",
    "strength": "strength",
    "position_status": "counterargument_status",
    "question_status": "question_status",
    "priority": "priority",
    "date_precision": "date_precision",
    "moc_level": "moc_level",
    "archival_validity": "archival_validity",
    "analytical_credibility": "analytical_credibility",
    "date_relation": "date_relation",
    "evidence_stage": "evidence_stage",
}

SOURCE_SNIPPET_DEFAULTS = {
    "source_locator": "",
    "language": "",
    "evidence_role": "",
    "evidence_type": "primary_quote",
    "citation_status": "unverified",
    "risk_level": "medium",
    "friction_notes": "",
    "notes": "",
    "source_query": "",
    "arc_rationale": "",
    "strength": "unknown",
    "warning_flags": [],
}
PROMOTION_GATE_REQUIRED_FIELDS = ["provenance_report", "source_query", "arc_rationale", "strength"]
PROMOTION_GATE_PROMOTED_STATUSES = {"report_verified", "source_verified", "print_ready"}


@dataclass
class Card:
    card_id: str
    card_type: str
    title: str
    status: str
    path: Path
    rel_path: str
    metadata: dict[str, Any]
    body: str
    chapters: list[str]
    arcs: list[str]
    tags: list[str]
    template_instance: str
    outgoing_links: list[str]
    raw_outgoing_links_by_relation: dict[str, list[str]] = field(default_factory=dict)
    resolved_outgoing_links_by_relation: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    unresolved_outgoing_links: list[dict[str, Any]] = field(default_factory=list)


def configure_root(root: str | Path | None = None) -> Path:
    """Point module-level paths at a fixture or the live workspace."""
    global ROOT, CARDS_DIR, SCHEMA_PATH, ARCS_PATH, TAGS_PATH, MATRIX_PATH
    global ENTITIES_PATH, TERMS_PATH, SOURCES_PATH, CATALOG_PATH, INDEXES_DIR
    global CARDS_INDEX_JSON, CARDS_INDEX_CSV, CARDS_TICKETS_MD, CARDS_TICKETS_JSON
    global CARDS_DIAGNOSTICS_JSON, REPORT_TO_CARDS_INDEX_JSON
    global PIVOT_CHAPTER_CSV, PIVOT_ARC_CSV, PIVOT_ENTITY_CSV, PIVOT_TERM_CSV

    if root is not None:
        ROOT = Path(root)
    CARDS_DIR = ROOT / "cards"
    SCHEMA_PATH = ROOT / "authority" / "cards_schema.yaml"
    ARCS_PATH = ROOT / "argument_arcs.yaml"
    TAGS_PATH = ROOT / "authority" / "tags.yaml"
    MATRIX_PATH = ROOT / "argument_matrix.csv"
    ENTITIES_PATH = ROOT / "authority" / "entities.csv"
    TERMS_PATH = ROOT / "authority" / "terms.csv"
    SOURCES_PATH = ROOT / "authority" / "sources.csv"
    CATALOG_PATH = ROOT / "research_catalog.csv"
    INDEXES_DIR = ROOT / "indexes"
    CARDS_INDEX_JSON = ROOT / "CARDS_INDEX.json"
    CARDS_INDEX_CSV = ROOT / "cards_index.csv"
    CARDS_TICKETS_MD = ROOT / "CARDS_TICKETS.md"
    CARDS_TICKETS_JSON = ROOT / "CARDS_TICKETS.json"
    CARDS_DIAGNOSTICS_JSON = ROOT / "CARDS_DIAGNOSTICS.json"
    REPORT_TO_CARDS_INDEX_JSON = INDEXES_DIR / "report_to_cards_index.json"
    PIVOT_CHAPTER_CSV = ROOT / "cards_by_chapter.csv"
    PIVOT_ARC_CSV = ROOT / "cards_by_arc.csv"
    PIVOT_ENTITY_CSV = ROOT / "cards_by_entity.csv"
    PIVOT_TERM_CSV = ROOT / "cards_by_term.csv"
    return ROOT


def rel(path: Path) -> str:
    return core.nfc(path.relative_to(ROOT).as_posix())


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value).strip()


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, dict):
                candidate = item.get("id") or item.get("card_id") or item.get("claim_id") or item.get("entity_id") or item.get("term_id")
                if candidate:
                    out.append(as_text(candidate))
            else:
                text = as_text(item)
                if text:
                    out.append(text)
        return out
    text = as_text(value)
    if not text:
        return []
    return core.split_values(text) if ";" in text else [text]


def jsonable(value: Any) -> Any:
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    return value


def _field_type(field_name: str) -> str:
    if field_name in LISTLIKE_FIELDS:
        return "list"
    if field_name == "linked_cards":
        return "mapping"
    if field_name in FIELD_ENUMS:
        return "enum"
    if field_name in {"created", "updated", "extraction_date", "opened_date", "closed_date"}:
        return "date"
    if field_name in {"event_date", "document_date", "publication_date", "assertion_date", "date", "date_end"}:
        return "date_or_range"
    if field_name in {"extraction_verified"}:
        return "boolean"
    return "string"


def normalize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Support both the live schema shape and the compact test-fixture shape."""
    if not isinstance(schema.get("card_types"), dict):
        return schema

    raw_types: dict[str, Any] = schema.get("card_types", {})
    normalized = dict(schema)
    normalized["card_types"] = list(raw_types)
    normalized.setdefault("directories", {})
    normalized.setdefault("id_prefixes", {})
    normalized.setdefault("filename_prefixes", {})
    normalized.setdefault("core_fields", {"required": {}, "optional": {}})
    normalized.setdefault("type_fields", {})
    normalized.setdefault("allowed_values", {})

    for card_type, type_schema in raw_types.items():
        if not isinstance(type_schema, dict):
            continue
        normalized["directories"].setdefault(card_type, f"cards/{card_type}")
        prefix = as_text(type_schema.get("id_prefix") or card_type)
        if prefix and not prefix.endswith(":"):
            prefix = f"{prefix}:"
        normalized["id_prefixes"].setdefault(card_type, prefix)
        normalized["filename_prefixes"].setdefault(card_type, as_text(type_schema.get("filename_prefix")) or f"{card_type}.")

        required_core = type_schema.get("required_core") or []
        required_type = type_schema.get("required_type_specific") or []
        optional = type_schema.get("optional") or []
        for field_name in required_core:
            normalized["core_fields"]["required"].setdefault(field_name, _field_type(field_name))
        normalized["type_fields"].setdefault(card_type, {"required": {}, "optional": {}})
        for field_name in required_type:
            normalized["type_fields"][card_type]["required"].setdefault(field_name, _field_type(field_name))
        for field_name in optional:
            normalized["type_fields"][card_type]["optional"].setdefault(field_name, _field_type(field_name))

    type_allowed = normalized.get("type_allowed_values") or {}
    for enum_name, values in type_allowed.items():
        normalized["allowed_values"].setdefault(enum_name, values)
    normalized["allowed_values"].setdefault("chapters", ["Introduction", "Ch1", "Ch3", "Ch4", "Ch5", "Ch6", "Ch7", "Epilogue"])
    normalized["allowed_values"].setdefault("chapter_aliases", {})
    normalized["allowed_values"].setdefault("status", ["draft", "review", "stable", "superseded"])
    normalized["allowed_values"].setdefault("risk_level", ["low", "medium", "high", "critical"])
    normalized["allowed_values"].setdefault("strength", ["weak", "medium", "strong", "unknown"])
    normalized["linked_card_fields"] = normalized.get("linked_card_fields") or {"allowed": sorted(LINK_FIELDS)}
    return normalized


def load_csv_map(path: Path, key: str) -> dict[str, dict[str, str]]:
    rows, errors = core.read_csv(path)
    if errors:
        print(f"WARN: {path.name}: {'; '.join(errors)}")
    return core.row_map(rows, key)


def load_catalog_files() -> set[str]:
    rows, errors = core.read_csv(CATALOG_PATH)
    if errors:
        print(f"WARN: {CATALOG_PATH.name}: {'; '.join(errors)}")
    return report_paths.alias_set([as_text(row.get("file")) for row in rows if row.get("file")])


def load_arc_ids() -> set[str]:
    data = core.read_yaml(ARCS_PATH)
    if not isinstance(data, dict):
        return set()
    return {as_text(arc.get("arc_id")) for arc in data.get("arcs", []) if isinstance(arc, dict) and arc.get("arc_id")}


def load_tag_ids() -> set[str]:
    data = core.read_yaml(TAGS_PATH)
    tag_ids: set[str] = set()
    if not isinstance(data, dict):
        return tag_ids
    for category in data.get("tag_categories", []):
        if not isinstance(category, dict):
            continue
        for tag in category.get("tags", []):
            if isinstance(tag, dict) and tag.get("tag_id"):
                tag_ids.add(as_text(tag.get("tag_id")))
    return tag_ids


def normalize_chapter(value: Any, aliases: dict[str, str], valid: set[str]) -> tuple[str, str | None]:
    raw = as_text(value)
    normalized = aliases.get(raw, raw)
    if normalized in valid:
        return normalized, None
    return normalized, f"unknown chapter: {raw}"


def normalize_arc(value: str, valid_arcs: set[str]) -> tuple[str, str | None, str | None]:
    if value in valid_arcs:
        return value, None, None
    prefixed = f"arc:{value}"
    if prefixed in valid_arcs:
        return prefixed, f"arc id {value} normalized to {prefixed}", None
    return value, None, f"unknown arc_id: {value}"


def filename_expected(card_type: str, schema: dict[str, Any]) -> str:
    return str(schema.get("filename_prefixes", {}).get(card_type, f"{card_type}."))


def allowed_fields_for(card_type: str, schema: dict[str, Any]) -> set[str]:
    core_fields = set((schema.get("core_fields", {}).get("required") or {}).keys())
    core_fields.update((schema.get("core_fields", {}).get("optional") or {}).keys())
    type_schema = schema.get("type_fields", {}).get(card_type, {})
    fields = set((type_schema.get("required") or {}).keys())
    fields.update((type_schema.get("optional") or {}).keys())
    return core_fields | fields


def enum_values(field: str, schema: dict[str, Any]) -> set[str]:
    allowed = schema.get("allowed_values", {})
    type_allowed = schema.get("type_allowed_values", {})
    enum_name = FIELD_ENUMS.get(field)
    if not enum_name:
        return set()
    return set(allowed.get(enum_name, type_allowed.get(enum_name, [])))


def validate_type(owner: str, field: str, expected: str, value: Any, schema: dict[str, Any], errors: list[str]) -> None:
    if value is None:
        return
    if expected in {"string", "string_or_null"}:
        if expected == "string_or_null" and value is None:
            return
        if isinstance(value, (dict, list)):
            errors.append(f"{owner} field {field} must be a scalar string.")
    elif expected == "boolean" and not isinstance(value, bool):
        errors.append(f"{owner} field {field} must be boolean.")
    elif expected == "integer" and not isinstance(value, int):
        errors.append(f"{owner} field {field} must be integer.")
    elif expected == "list" and not isinstance(value, list):
        errors.append(f"{owner} field {field} must be a list.")
    elif expected == "mapping" and not isinstance(value, dict):
        errors.append(f"{owner} field {field} must be a mapping.")
    elif expected in {"date", "date_or_null"}:
        if expected == "date_or_null" and value is None:
            return
        text = as_text(value)
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
            errors.append(f"{owner} field {field} must be YYYY-MM-DD.")
    elif expected == "date_or_range":
        try:
            parse_date_or_range(value)
        except ValueError as exc:
            errors.append(f"{owner} field {field} has invalid date_or_range: {exc}")
    elif expected == "enum":
        values = enum_values(field, schema)
        text = as_text(value)
        if values and text not in values:
            errors.append(f"{owner} field {field} has invalid value: {text}")
    elif expected == "chapter":
        chapters = set(schema.get("allowed_values", {}).get("chapters", []))
        aliases = {str(k): str(v) for k, v in schema.get("allowed_values", {}).get("chapter_aliases", {}).items()}
        _, err = normalize_chapter(value, aliases, chapters)
        if err:
            errors.append(f"{owner} field {field} has {err}.")


def validate_required_fields(owner: str, metadata: dict[str, Any], card_type: str, schema: dict[str, Any], errors: list[str]) -> None:
    required: dict[str, str] = dict(schema.get("core_fields", {}).get("required") or {})
    required.update(schema.get("type_fields", {}).get(card_type, {}).get("required") or {})
    for field, expected in required.items():
        if field not in metadata:
            errors.append(f"{owner} missing required field: {field}")
            continue
        validate_type(owner, field, expected, metadata.get(field), schema, errors)
        if expected in {"string", "enum", "chapter", "date"} and not as_text(metadata.get(field)):
            errors.append(f"{owner} field {field} cannot be blank.")
        if expected == "list" and not isinstance(metadata.get(field), list):
            errors.append(f"{owner} field {field} must be a list.")


def validate_all_field_types(owner: str, metadata: dict[str, Any], card_type: str, schema: dict[str, Any], errors: list[str]) -> None:
    expected: dict[str, str] = dict(schema.get("core_fields", {}).get("required") or {})
    expected.update(schema.get("core_fields", {}).get("optional") or {})
    type_schema = schema.get("type_fields", {}).get(card_type, {})
    expected.update(type_schema.get("required") or {})
    expected.update(type_schema.get("optional") or {})
    for field, field_type in expected.items():
        if field in metadata:
            validate_type(owner, field, field_type, metadata.get(field), schema, errors)


def preview_paths(paths: list[str], limit: int = 8) -> str:
    shown = paths[:limit]
    suffix = f"; +{len(paths) - limit} more" if len(paths) > limit else ""
    return "; ".join(shown) + suffix


def schema_drift_warnings(cards: list[Card], schema: dict[str, Any]) -> list[str]:
    """Summarize non-fatal schema drift without rewriting cards."""
    unknown_fields: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    bad_registers: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    allowed_registers = {as_text(item) for item in schema.get("allowed_values", {}).get("registers", [])}

    for card in cards:
        allowed = allowed_fields_for(card.card_type, schema)
        for field in card.metadata:
            if field not in allowed:
                unknown_fields[card.card_type][field].append(card.rel_path)

        if allowed_registers and "register" in card.metadata:
            for value in as_list(card.metadata.get("register")):
                if value and value not in allowed_registers:
                    bad_registers[card.card_type][value].append(card.rel_path)

    warnings: list[str] = []
    for card_type in sorted(unknown_fields):
        for field, paths in sorted(unknown_fields[card_type].items()):
            warnings.append(
                f"schema drift: {card_type} has unknown field {field} on "
                f"{len(paths)} card(s): {preview_paths(sorted(paths))}"
            )

    allowed_text = ", ".join(sorted(allowed_registers))
    for card_type in sorted(bad_registers):
        for value, paths in sorted(bad_registers[card_type].items()):
            warnings.append(
                f"schema drift: {card_type} register value {value} is outside "
                f"the controlled set ({allowed_text}) on {len(paths)} card(s): "
                f"{preview_paths(sorted(paths))}"
            )
    return warnings


def card_files(schema: dict[str, Any]) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for card_type in schema.get("card_types", []):
        directory = ROOT / schema.get("directories", {}).get(card_type, f"cards/{card_type}")
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            if path.name.startswith("."):
                continue
            files.append((card_type, path))
    return files


def load_template_files() -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    templates: dict[str, dict[str, Any]] = {}
    template_dir = CARDS_DIR / "templates"
    if not template_dir.exists():
        return templates, warnings, []
    for path in sorted(template_dir.glob("*.md")):
        metadata, body, parse_errors = core.read_markdown_card(path)
        errors.extend(parse_errors)
        if parse_errors:
            continue
        name = as_text(metadata.get("template_name"))
        if not name:
            errors.append(f"{rel(path)} missing template_name.")
            continue
        if name in templates:
            errors.append(f"Duplicate template_name: {name}")
        metadata["body"] = body
        metadata["path"] = rel(path)
        templates[name] = metadata
    return templates, warnings, errors


def load_cards(schema: dict[str, Any]) -> tuple[list[Card], list[dict[str, Any]], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    raw_cards: list[dict[str, Any]] = []
    allowed_types = set(schema.get("card_types", []))
    id_prefixes = schema.get("id_prefixes", {})
    valid_chapters = set(schema.get("allowed_values", {}).get("chapters", []))
    chapter_aliases = {str(k): str(v) for k, v in schema.get("allowed_values", {}).get("chapter_aliases", {}).items()}
    valid_arcs = load_arc_ids()
    valid_tags = load_tag_ids()

    for directory_type, path in card_files(schema):
        metadata, body, parse_errors = core.read_markdown_card(path)
        owner = rel(path)
        errors.extend(parse_errors)
        if parse_errors:
            continue

        card_type = as_text(metadata.get("card_type"))
        card_id = as_text(metadata.get("id"))
        if card_type not in allowed_types:
            errors.append(f"{owner} has invalid card_type: {card_type}")
            continue
        if card_type != directory_type:
            errors.append(f"{owner} is in {directory_type}/ but declares card_type {card_type}.")
        prefix = as_text(id_prefixes.get(card_type))
        if prefix and not card_id.startswith(prefix):
            errors.append(f"{owner} id must start with {prefix}: {card_id}")
        if ":" in path.name:
            errors.append(f"{owner} filename must not contain colon.")
        expected_filename_prefix = filename_expected(card_type, schema)
        if expected_filename_prefix and not path.name.startswith(expected_filename_prefix):
            warnings.append(f"{owner} filename should start with {expected_filename_prefix}.")

        validate_required_fields(owner, metadata, card_type, schema, errors)
        validate_all_field_types(owner, metadata, card_type, schema, errors)

        link_map = metadata.get("linked_cards")
        if isinstance(link_map, dict):
            for forbidden in REVERSE_EDGE_NAMES:
                if forbidden in link_map:
                    errors.append(f"{owner} uses forbidden inverse link field: linked_cards.{forbidden}")
            for field in link_map:
                if field not in LINK_FIELDS and field not in REVERSE_EDGE_NAMES:
                    warnings.append(f"{owner} has unknown linked_cards field: {field}")
        else:
            link_map = {}

        normalized_chapters: list[str] = []
        for chapter in as_list(metadata.get("chapter_relevance")):
            normalized, err = normalize_chapter(chapter, chapter_aliases, valid_chapters)
            if err:
                errors.append(f"{owner} has {err}.")
            elif normalized not in normalized_chapters:
                normalized_chapters.append(normalized)

        normalized_arcs: list[str] = []
        for arc_id in as_list(metadata.get("arc_ids")):
            normalized, warning, err = normalize_arc(arc_id, valid_arcs)
            if warning:
                warnings.append(f"{owner}: {warning}")
            if err:
                errors.append(f"{owner} has {err}.")
            elif normalized not in normalized_arcs:
                normalized_arcs.append(normalized)

        normalized_tags: list[str] = []
        for tag in as_list(metadata.get("tags")):
            if tag not in valid_tags:
                errors.append(f"{owner} references unknown tag: {tag}")
            elif tag not in normalized_tags:
                normalized_tags.append(tag)

        raw_outgoing_links_by_relation: dict[str, list[str]] = {}
        outgoing_links: list[str] = []
        for field in LINK_FIELDS:
            raw_outgoing_links_by_relation[field] = []
            for target in as_list(link_map.get(field)):
                if target and target not in raw_outgoing_links_by_relation[field]:
                    raw_outgoing_links_by_relation[field].append(target)
                if target and target not in outgoing_links:
                    outgoing_links.append(target)

        raw_cards.append({
            "card_id": card_id,
            "card_type": card_type,
            "title": as_text(metadata.get("title")),
            "status": as_text(metadata.get("status")),
            "path": path,
            "rel_path": owner,
            "metadata": metadata,
            "body": body,
            "chapters": normalized_chapters,
            "arcs": normalized_arcs,
            "tags": normalized_tags,
            "template_instance": as_text(metadata.get("template_instance")),
            "outgoing_links": outgoing_links,
            "raw_outgoing_links_by_relation": {
                key: values for key, values in sorted(raw_outgoing_links_by_relation.items()) if values
            },
        })

    card_ids = [item["card_id"] for item in raw_cards if item["card_id"]]
    duplicates = sorted({card_id for card_id in card_ids if card_ids.count(card_id) > 1})
    for card_id in duplicates:
        errors.append(f"Duplicate card id: {card_id}")

    cards = [
        Card(**item)
        for item in raw_cards
        if item["card_id"] and item["card_type"] in allowed_types
    ]
    return cards, raw_cards, warnings, errors


def validate_authority_refs(cards: list[Card], schema: dict[str, Any]) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    card_ids = {card.card_id for card in cards}
    sources = set(load_csv_map(SOURCES_PATH, "source_id"))
    claims = set(load_csv_map(MATRIX_PATH, "claim_id"))
    entities = set(load_csv_map(ENTITIES_PATH, "entity_id"))
    terms = set(load_csv_map(TERMS_PATH, "term_id"))
    reports = load_catalog_files()

    def check_refs(owner: str, label: str, values: list[str], valid: set[str], severity: str = "error") -> None:
        if not valid:
            return
        for value in values:
            if value not in valid:
                message = f"{owner} references unknown {label}: {value}"
                (warnings if severity == "warning" else errors).append(message)

    for card in cards:
        owner = card.rel_path
        meta = card.metadata
        for target in card.outgoing_links:
            if target not in card_ids:
                errors.append(f"{owner} links to unknown card: {target}")

        if card.card_type == "source_snippet":
            if as_text(meta.get("source_id")):
                check_refs(owner, "source_id", [as_text(meta.get("source_id"))], sources)
            check_refs(owner, "source_ids", as_list(meta.get("source_ids")), sources)
            check_refs(owner, "provenance_report", as_list(meta.get("provenance_report")), reports)
            check_refs(owner, "claim_id", as_list(meta.get("claim_ids")), claims)
            check_refs(owner, "entity_id", as_list(meta.get("entity_ids")), entities)
            check_refs(owner, "term_id", as_list(meta.get("term_ids")), terms)
            if not meta.get("evidence_type"):
                warnings.append(f"{owner} has no evidence_type; dashboard will default to primary_quote.")
            if not meta.get("citation_status"):
                warnings.append(f"{owner} has no citation_status; dashboard will default to unverified.")
            if not meta.get("risk_level"):
                warnings.append(f"{owner} has no risk_level; dashboard will default to medium.")
        elif card.card_type == "idea":
            term_id = as_text(meta.get("term_id"))
            if term_id:
                check_refs(owner, "term_id", [term_id], terms)
            check_refs(owner, "parent_idea", as_list(meta.get("parent_idea")), card_ids)
            check_refs(owner, "sub_ideas", as_list(meta.get("sub_ideas")), card_ids)
        elif card.card_type == "claim":
            matrix_row_id = as_text(meta.get("matrix_row_id"))
            if matrix_row_id:
                check_refs(owner, "matrix_row_id", [matrix_row_id], claims)
            check_refs(owner, "depends_on", as_list(meta.get("depends_on")), claims | card_ids)
        elif card.card_type == "entity":
            check_refs(owner, "entity_id", [as_text(meta.get("entity_id"))], entities)
        elif card.card_type == "question":
            check_refs(owner, "candidate_evidence", as_list(meta.get("candidate_evidence")), card_ids)
        elif card.card_type == "synthesis":
            check_refs(owner, "inputs", as_list(meta.get("inputs")), card_ids)
            check_refs(owner, "output_claims", as_list(meta.get("output_claims")), claims | card_ids)
            check_refs(owner, "derived_from_reports", as_list(meta.get("derived_from_reports")), reports)
        elif card.card_type == "bridge":
            check_refs(owner, "upstream_claims", as_list(meta.get("upstream_claims")), claims | card_ids)
            check_refs(owner, "downstream_claims", as_list(meta.get("downstream_claims")), claims | card_ids)
            check_refs(owner, "informed_by_reports", as_list(meta.get("informed_by_reports")), reports)
        elif card.card_type == "counterargument":
            check_refs(owner, "position_holders", as_list(meta.get("position_holders")), entities, severity="warning")
            check_refs(owner, "refuting_synthesis", as_list(meta.get("refuting_synthesis")), card_ids)
            check_refs(owner, "refuting_snippets", as_list(meta.get("refuting_snippets")), card_ids)
            check_refs(owner, "informed_by_reports", as_list(meta.get("informed_by_reports")), reports)
        elif card.card_type == "timeline":
            check_refs(owner, "causal_predecessors", as_list(meta.get("causal_predecessors")), card_ids)
            check_refs(owner, "evidence_cards", as_list(meta.get("evidence_cards")), card_ids)
        elif card.card_type == "place":
            check_refs(owner, "occupants", as_list(meta.get("occupants")), entities)
            check_refs(owner, "associated_events", as_list(meta.get("associated_events")), card_ids)
        elif card.card_type == "moc":
            check_refs(owner, "parent_moc", as_list(meta.get("parent_moc")), card_ids)
            check_refs(owner, "child_cards", as_list(meta.get("child_cards")), card_ids)
        elif card.card_type == "scaffold":
            check_refs(owner, "sub_arguments", as_list(meta.get("sub_arguments")), claims | card_ids)
            check_refs(owner, "feeding_synthesis", as_list(meta.get("feeding_synthesis")), card_ids)
            check_refs(owner, "informed_by_reports", as_list(meta.get("informed_by_reports")), reports)

    return warnings, errors


def resolve_card_id(raw_id: str, *, root: str | Path | None = None) -> str | None:
    resolver = load_resolver(Path(root) if root is not None else ROOT)
    return resolver.resolve(raw_id).get("canonical")


def apply_alias_resolution(
    cards: list[Card],
    resolver: CardIdResolver,
    *,
    strict_aliases: bool = False,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    warnings: list[str] = []
    errors: list[str] = []
    tickets: list[dict[str, Any]] = []
    card_ids = {card.card_id for card in cards}

    for error in resolver.errors:
        errors.append(error)
        subject = error.split(":", 1)[1].strip().split()[0] if ":" in error else ""
        tickets.append(ticket("critical", "alias_shadows_card" if "alias_shadows_card" in error else "alias_resolution_error", subject, error, severity="error"))
    for info in resolver.cleanup_infos:
        subject = info.split(":", 1)[1].strip() if ":" in info else ""
        tickets.append(ticket("info", "self_canonical_alias", subject, info, severity="info"))

    for card in cards:
        resolved_by_relation: dict[str, list[dict[str, Any]]] = {}
        unresolved: list[dict[str, Any]] = []
        canonical_outgoing: list[str] = []
        for relation in sorted(card.raw_outgoing_links_by_relation):
            entries: list[dict[str, Any]] = []
            for raw in card.raw_outgoing_links_by_relation.get(relation, []):
                resolution = resolver.resolve(raw)
                canonical = resolution.get("canonical")
                status = resolution.get("status")
                if canonical and canonical in card_ids:
                    entry = {
                        "raw": raw,
                        "alias": resolution.get("alias") or raw,
                        "canonical": canonical,
                        "status": status,
                    }
                    entries.append(entry)
                    if canonical not in canonical_outgoing:
                        canonical_outgoing.append(canonical)
                    if status != "canonical":
                        tickets.append(ticket(
                            "info",
                            "alias_resolution_succeeded_with_deprecated_alias",
                            card.card_id,
                            f"{card.card_id} linked {raw} resolved to {canonical}",
                            severity="info",
                            evidence={"relation": relation, "raw": raw, "canonical": canonical},
                        ))
                elif status == "capture_target":
                    item = {"relation": relation, "raw": raw, "status": status, "canonical": None}
                    unresolved.append(item)
                    message = f"{card.card_id} links to capture target {raw}"
                    warnings.append(message)
                    tickets.append(ticket(
                        "medium",
                        "unresolved_link_with_capture_target",
                        card.card_id,
                        message,
                        severity="warning",
                        evidence=item,
                    ))
                    if strict_aliases:
                        errors.append(message)
                else:
                    message = f"{card.rel_path} links to unknown card: {raw}"
                    errors.append(message)
                    tickets.append(ticket(
                        "critical",
                        "unresolved_link",
                        card.card_id,
                        message,
                        severity="error",
                        evidence={"relation": relation, "raw": raw},
                    ))
            if entries:
                resolved_by_relation[relation] = entries
        card.resolved_outgoing_links_by_relation = resolved_by_relation
        card.unresolved_outgoing_links = unresolved
        card.outgoing_links = sorted(canonical_outgoing)

    return warnings, errors, tickets


def reverse_links(cards: list[Card]) -> dict[str, list[str]]:
    incoming: dict[str, list[str]] = defaultdict(list)
    for card in cards:
        for target in card.outgoing_links:
            incoming[target].append(card.card_id)
    return {key: sorted(values) for key, values in incoming.items()}


def validate_templates(cards: list[Card], schema: dict[str, Any], templates: dict[str, dict[str, Any]]) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    schema_templates = schema.get("template_stubs", {})
    known_templates = set(schema_templates) | set(templates)
    cards_by_id = {card.card_id: card for card in cards}
    for card in cards:
        template_name = card.template_instance
        if not template_name:
            continue
        if template_name not in known_templates:
            errors.append(f"{card.rel_path} references unknown template_instance: {template_name}")
            continue
        template = schema_templates.get(template_name, {})
        aggregator_type = as_text(template.get("aggregator_card_type"))
        if aggregator_type and card.card_type != aggregator_type:
            errors.append(f"{card.rel_path} uses template {template_name} but card_type is {card.card_type}; expected aggregator {aggregator_type}.")
            continue
        participants = [card]
        for target_id in card.outgoing_links:
            target = cards_by_id.get(target_id)
            if target:
                participants.append(target)
        for component in template.get("required_components", []):
            if not isinstance(component, dict):
                continue
            component_type = as_text(component.get("card_type"))
            cardinality = as_text(component.get("cardinality"))
            count = sum(1 for participant in participants if participant.card_type == component_type)
            if cardinality == "exactly_one" and count != 1:
                errors.append(f"{card.rel_path} template {template_name} needs exactly one {component_type}; found {count}.")
            elif cardinality == "one_or_more" and count < 1:
                errors.append(f"{card.rel_path} template {template_name} needs one or more {component_type}; found {count}.")
            elif cardinality == "two_or_more" and count < 2:
                errors.append(f"{card.rel_path} template {template_name} needs two or more {component_type}; found {count}.")
            elif cardinality == "three_or_more" and count < 3:
                errors.append(f"{card.rel_path} template {template_name} needs three or more {component_type}; found {count}.")
    return warnings, errors


def parse_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = as_text(value)
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def source_ids_for(meta: dict[str, Any]) -> list[str]:
    values: list[str] = []
    source_id = as_text(meta.get("source_id"))
    if source_id:
        values.append(source_id)
    for item in as_list(meta.get("source_ids")):
        if item not in values:
            values.append(item)
    return values


def promotion_gate_state(card: Card) -> dict[str, Any]:
    return evaluate_promotion_gate(card.metadata, card.card_type, card.status).to_dict()


def promotion_gate_summary(cards: list[Card]) -> dict[str, Any]:
    states = [
        {"card_id": card.card_id, **promotion_gate_state(card)}
        for card in cards
        if card.card_type == "source_snippet"
    ]
    return {
        "ready": sum(1 for state in states if state["ready"]),
        "blocked": sum(1 for state in states if not state["ready"]),
        "blocked_cards": [state for state in states if not state["ready"]],
    }


def diagnostics(cards: list[Card], incoming: dict[str, list[str]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tickets: list[dict[str, Any]] = []
    today = dt.date.today()
    cards_by_chapter: dict[str, list[Card]] = defaultdict(list)
    cards_by_arc: dict[str, list[Card]] = defaultdict(list)
    for card in cards:
        for chapter in card.chapters:
            cards_by_chapter[chapter].append(card)
        for arc in card.arcs:
            cards_by_arc[arc].append(card)

    synthesis_ratio: dict[str, dict[str, Any]] = {"chapters": {}, "arcs": {}}
    for label, grouped in [("chapters", cards_by_chapter), ("arcs", cards_by_arc)]:
        for key, items in grouped.items():
            snippets = sum(1 for card in items if card.card_type == "source_snippet")
            syntheses = sum(1 for card in items if card.card_type == "synthesis")
            ratio = None if syntheses == 0 else round(snippets / syntheses, 2)
            synthesis_ratio[label][key] = {"source_snippets": snippets, "synthesis": syntheses, "ratio": ratio}
            if snippets >= 3 and syntheses == 0:
                tickets.append(ticket("info", "synthesis_ratio", key, f"{key} has {snippets} source snippets and no synthesis cards."))
            elif syntheses > 0 and snippets / syntheses < 3:
                tickets.append(ticket("info", "synthesis_ratio", key, f"{key} source_snippet:synthesis ratio is below 3:1 ({snippets}:{syntheses})."))

    orphans: list[str] = []
    for card in cards:
        updated = parse_date(card.metadata.get("updated"))
        is_old = bool(updated and (today - updated).days > 28)
        if is_old and not card.outgoing_links and not incoming.get(card.card_id):
            orphans.append(card.card_id)
            tickets.append(ticket("low", "orphan", card.card_id, f"{card.card_id} has no incoming or outgoing links and has not been updated in 4+ weeks."))
        gate = promotion_gate_state(card)
        if card.card_type == "source_snippet" and not gate["ready"]:
            priority = "high" if gate["promoted"] else "medium"
            issue_text = ", ".join(gate["missing"] + gate["warnings"])
            tickets.append(ticket(priority, "promotion_gate", card.card_id, f"{card.card_id} is missing report-to-card promotion gate fields: {issue_text}"))

    moc_warnings: list[dict[str, Any]] = []
    for card in cards:
        if card.card_type != "moc":
            continue
        max_children = card.metadata.get("max_children_warning", 15)
        try:
            max_children_int = int(max_children)
        except (TypeError, ValueError):
            max_children_int = 15
        child_count = len(as_list(card.metadata.get("child_cards")))
        if child_count > max_children_int:
            moc_warnings.append({"card_id": card.card_id, "child_count": child_count, "max_children_warning": max_children_int})
            tickets.append(ticket("medium", "moc_child_count", card.card_id, f"{card.card_id} has {child_count} children; split into sub-MOCs."))

    return {
        "synthesis_ratio": synthesis_ratio,
        "orphans": orphans,
        "moc_warnings": moc_warnings,
        "promotion_gate": promotion_gate_summary(cards),
    }, tickets


def ticket(
    priority: str,
    kind: str,
    card_id: str,
    message: str,
    *,
    severity: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import hashlib

    basis = f"{kind}|{card_id}|{message}|{evidence or {}}"
    item = {
        "id": "cards:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16],
        "source": "cards",
        "priority": priority,
        "kind": kind,
        "category": kind,
        "card_id": card_id,
        "card_or_claim_id": card_id,
        "message": message,
    }
    if severity:
        item["severity"] = severity
    if evidence is not None:
        item["evidence"] = evidence
    return item


def card_index_row(card: Card, incoming: dict[str, list[str]]) -> dict[str, str]:
    return {
        "card_id": card.card_id,
        "card_type": card.card_type,
        "title": card.title,
        "status": card.status,
        "chapters": "; ".join(card.chapters),
        "arcs": "; ".join(card.arcs),
        "tags": "; ".join(card.tags),
        "template_instance": card.template_instance,
        "incoming_link_count": str(len(incoming.get(card.card_id, []))),
        "outgoing_link_count": str(len(card.outgoing_links)),
        "created": as_text(card.metadata.get("created")),
        "updated": as_text(card.metadata.get("updated")),
        "path": card.rel_path,
        "archival_validity": as_text(card.metadata.get("archival_validity")),
        "analytical_credibility": as_text(card.metadata.get("analytical_credibility")),
        "evidence_stage": as_text(card.metadata.get("evidence_stage")),
    }


def card_payload(card: Card, incoming: dict[str, list[str]], self_audit: dict[str, Any] | None = None) -> dict[str, Any]:
    card_diag = ((self_audit or {}).get("cards") or {}).get(card.card_id, {})
    payload = {
        "card_id": card.card_id,
        "card_type": card.card_type,
        "title": card.title,
        "status": card.status,
        "path": card.rel_path,
        "created": as_text(card.metadata.get("created")),
        "updated": as_text(card.metadata.get("updated")),
        "chapters": card.chapters,
        "arc_ids": card.arcs,
        "tags": card.tags,
        "template_instance": card.template_instance,
        "linked_cards": {
            "outgoing": card.outgoing_links,
            "incoming": incoming.get(card.card_id, []),
        },
        "incoming_link_count": len(incoming.get(card.card_id, [])),
        "outgoing_link_count": len(card.outgoing_links),
        "metadata": jsonable(card.metadata),
        "body": card.body,
    }
    for key in [
        "alias_resolution",
        "reliability_profile",
        "conflict_profile",
        "chronology_profile",
        "evidence_stage",
        "evidence_stage_inferred",
    ]:
        if key in card_diag:
            payload[key] = card_diag[key]
    return payload


def snippet_payload(card: Card) -> dict[str, Any]:
    meta = card.metadata
    return {
        "snippet_id": card.card_id,
        "card_id": card.card_id,
        "title": card.title,
        "arc_ids": card.arcs,
        "claim_ids": as_list(meta.get("claim_ids")),
        "source_ids": source_ids_for(meta),
        "report_files": as_list(meta.get("report_files")),
        "entity_ids": as_list(meta.get("entity_ids")),
        "term_ids": as_list(meta.get("term_ids")),
        "tags": card.tags,
        "chapters": card.chapters,
        "source_locator": as_text(meta.get("source_locator") or meta.get("page_or_line")),
        "language": as_text(meta.get("original_lang")),
        "original_snippet": as_text(meta.get("original_snippet")),
        "translation_or_summary": as_text(meta.get("translation_or_summary")),
        "evidence_role": as_text(meta.get("evidence_role")),
        "evidence_type": as_text(meta.get("evidence_type")) or SOURCE_SNIPPET_DEFAULTS["evidence_type"],
        "citation_status": as_text(meta.get("citation_status")) or SOURCE_SNIPPET_DEFAULTS["citation_status"],
        "risk_level": as_text(meta.get("risk_level")) or SOURCE_SNIPPET_DEFAULTS["risk_level"],
        "friction_notes": as_text(meta.get("friction_notes")),
        "notes": as_text(meta.get("notes")),
        "source_query": as_text(meta.get("source_query")) or SOURCE_SNIPPET_DEFAULTS["source_query"],
        "arc_rationale": as_text(meta.get("arc_rationale")) or SOURCE_SNIPPET_DEFAULTS["arc_rationale"],
        "strength": as_text(meta.get("strength")) or SOURCE_SNIPPET_DEFAULTS["strength"],
        "warning_flags": as_list(meta.get("warning_flags")),
        "promotion_gate": promotion_gate_state(card),
        "card_status": card.status,
        "card_path": card.rel_path,
        "provenance_report": as_text(meta.get("provenance_report")),
        "source_layer": "cards",
    }


def report_reference_fields(card: Card) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    meta = card.metadata
    seen: set[str] = set()
    for field in ["provenance_report", "report_files", "derived_from_reports", "informed_by_reports"]:
        for report_file in as_list(meta.get(field)):
            if not report_file or report_file in seen:
                continue
            seen.add(report_file)
            refs.append((report_file, field))
    return refs


def report_to_cards_payload(cards: list[Card]) -> dict[str, Any]:
    reports: dict[str, list[dict[str, str]]] = defaultdict(list)
    card_refs: list[dict[str, str]] = []
    for card in cards:
        for report_file, field in report_reference_fields(card):
            item = {
                "report_file": report_file,
                "card_id": card.card_id,
                "card_type": card.card_type,
                "title": card.title,
                "path": card.rel_path,
                "field": field,
            }
            reports[report_file].append(item)
            card_refs.append(item)
    return {
        "schema_version": 1,
        "reports": {key: sorted(values, key=lambda item: item["card_id"]) for key, values in sorted(reports.items())},
        "cards": sorted(card_refs, key=lambda item: (item["report_file"], item["card_id"], item["field"])),
    }


def pivot_rows(cards: list[Card], key_getter) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for card in cards:
        for key in key_getter(card):
            rows.append({
                "key": key,
                "card_id": card.card_id,
                "card_type": card.card_type,
                "title": card.title,
                "status": card.status,
                "path": card.rel_path,
            })
    return rows


def write_tickets(tickets: list[dict[str, Any]]) -> None:
    ordered = sorted(tickets, key=lambda item: (item.get("id", ""), item.get("kind", ""), item.get("card_id", "")))
    core.write_json(CARDS_TICKETS_JSON, {"schema_version": 1, "tickets": ordered})
    lines = [
        "# Cards Tickets",
        "",
        "Generated from the card apparatus. Do not edit directly; edit cards or schema, then run `python tools/build_cards.py`.",
        "",
    ]
    if not tickets:
        lines.append("No cards tickets.")
    else:
        rows = [
            [item.get("priority", ""), item.get("kind", ""), item.get("card_id", ""), item.get("message", "")]
            for item in ordered
        ]
        lines.extend(core.markdown_table(["Priority", "Kind", "Card", "Message"], rows))
    lines.append("")
    CARDS_TICKETS_MD.write_text("\n".join(lines), encoding="utf-8")


def write_outputs(cards: list[Card], templates: dict[str, dict[str, Any]], diagnostics_payload: dict[str, Any], tickets: list[dict[str, Any]]) -> None:
    incoming = reverse_links(cards)
    INDEXES_DIR.mkdir(parents=True, exist_ok=True)
    core.write_csv(CARDS_INDEX_CSV, [card_index_row(card, incoming) for card in cards], CARD_INDEX_FIELDS)
    core.write_csv(PIVOT_CHAPTER_CSV, pivot_rows(cards, lambda card: card.chapters), PIVOT_FIELDS)
    core.write_csv(PIVOT_ARC_CSV, pivot_rows(cards, lambda card: card.arcs), PIVOT_FIELDS)
    core.write_csv(PIVOT_ENTITY_CSV, pivot_rows(cards, lambda card: as_list(card.metadata.get("entity_ids")) + as_list(card.metadata.get("entity_id")) + as_list(card.metadata.get("position_holders")) + as_list(card.metadata.get("occupants"))), PIVOT_FIELDS)
    core.write_csv(PIVOT_TERM_CSV, pivot_rows(cards, lambda card: as_list(card.metadata.get("term_ids")) + as_list(card.metadata.get("term_id"))), PIVOT_FIELDS)

    by_type: dict[str, int] = defaultdict(int)
    for card in cards:
        by_type[card.card_type] += 1

    payload = {
        "schema_version": 1,
        "generated_at_utc": diagnostics_payload.get("generated_at_utc") or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "summary": {
            "cards": len(cards),
            "cards_by_type": dict(sorted(by_type.items())),
            "source_snippets": sum(1 for card in cards if card.card_type == "source_snippet"),
            "templates": len(templates),
            "tickets": len(tickets),
        },
        "cards": [card_payload(card, incoming, diagnostics_payload) for card in sorted(cards, key=lambda item: item.card_id)],
        "cards_by_id": {card.card_id: card_payload(card, incoming, diagnostics_payload) for card in sorted(cards, key=lambda item: item.card_id)},
        "reverse_links": incoming,
        "source_snippets": [snippet_payload(card) for card in cards if card.card_type == "source_snippet"],
        "templates": {name: jsonable(metadata) for name, metadata in templates.items()},
        "diagnostics": diagnostics_payload,
        "tickets": tickets,
        "generated_files": {
            "cards_index_csv": CARDS_INDEX_CSV.name,
            "cards_tickets_md": CARDS_TICKETS_MD.name,
            "cards_tickets_json": CARDS_TICKETS_JSON.name,
            "cards_by_chapter_csv": PIVOT_CHAPTER_CSV.name,
            "cards_by_arc_csv": PIVOT_ARC_CSV.name,
            "cards_by_entity_csv": PIVOT_ENTITY_CSV.name,
            "cards_by_term_csv": PIVOT_TERM_CSV.name,
            "report_to_cards_index_json": REPORT_TO_CARDS_INDEX_JSON.relative_to(ROOT).as_posix(),
        },
    }
    core.write_json(CARDS_INDEX_JSON, payload)
    core.write_json(CARDS_DIAGNOSTICS_JSON, diagnostics_payload)
    core.write_json(REPORT_TO_CARDS_INDEX_JSON, report_to_cards_payload(cards))
    write_tickets(tickets)


def load_schema() -> tuple[dict[str, Any], list[str]]:
    data = core.read_yaml(SCHEMA_PATH)
    if not isinstance(data, dict):
        return {}, [f"{rel(SCHEMA_PATH)} must be a YAML mapping."]
    return normalize_schema(data), []


def run_build(validate_only: bool = False, *, root: str | Path | None = None, strict_aliases: bool = False) -> int:
    configure_root(root)
    schema, schema_errors = load_schema()
    if schema_errors:
        for error in schema_errors:
            print(f"ERROR: {error}")
        return 1

    templates, template_warnings, template_errors = load_template_files()
    cards, _, card_warnings, card_errors = load_cards(schema)
    resolver = load_resolver(ROOT, {card.card_id for card in cards})
    alias_warnings, alias_errors, alias_tickets = apply_alias_resolution(cards, resolver, strict_aliases=strict_aliases)
    drift_warnings = schema_drift_warnings(cards, schema)
    authority_warnings, authority_errors = validate_authority_refs(cards, schema)
    template_card_warnings, template_card_errors = validate_templates(cards, schema, templates)
    incoming = reverse_links(cards)
    legacy_diagnostics_payload, legacy_diagnostic_tickets = diagnostics(cards, incoming)
    self_audit_payload, self_audit_tickets = build_self_audit(cards, resolver)
    diagnostics_payload = dict(legacy_diagnostics_payload)
    diagnostics_payload.update(self_audit_payload)

    non_drift_warnings = template_warnings + card_warnings + alias_warnings + authority_warnings + template_card_warnings
    warnings = non_drift_warnings + drift_warnings
    errors = template_errors + card_errors + alias_errors + authority_errors + template_card_errors

    tickets = alias_tickets + legacy_diagnostic_tickets + self_audit_tickets
    tickets.extend(ticket("high" if "unknown" in warning else "medium", "warning", "", warning) for warning in non_drift_warnings)
    tickets.extend(ticket("high", "schema_drift", "", warning) for warning in drift_warnings)
    tickets.extend(ticket("critical", "validation_error", "", error) for error in errors)
    tickets = sorted(tickets, key=lambda item: (item.get("id", ""), item.get("kind", ""), item.get("card_id", "")))

    if not validate_only:
        write_outputs(cards, templates, diagnostics_payload, tickets)
    else:
        write_tickets(tickets)

    print(f"Cards: {len(cards)}")
    print(f"Templates: {len(templates)}")
    print(f"Tickets: {len(tickets)}")

    if drift_warnings:
        print("\nSCHEMA DRIFT")
        for warning in drift_warnings[:80]:
            print(f"- {warning}")
        if len(drift_warnings) > 80:
            print(f"- ...and {len(drift_warnings) - 80} more schema-drift warnings")

    if non_drift_warnings:
        print("\nWARNINGS")
        for warning in non_drift_warnings[:80]:
            print(f"- {warning}")
        if len(non_drift_warnings) > 80:
            print(f"- ...and {len(non_drift_warnings) - 80} more warnings")

    if errors:
        print("\nERRORS")
        for error in errors:
            print(f"- {error}")
        return 1

    if not validate_only:
        print(f"\nWrote {CARDS_INDEX_JSON.name}, {CARDS_INDEX_CSV.name}, and {CARDS_TICKETS_MD.name}.")
    print("\nOK: cards apparatus scaffold is structurally consistent.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and build the cards apparatus.")
    parser.add_argument("--root", help="Workspace root to build; defaults to the live repo.")
    parser.add_argument("--validate-only", action="store_true", help="Validate cards and write tickets without rebuilding indexes.")
    parser.add_argument("--strict-aliases", action="store_true", help="Promote capture-target alias warnings to hard errors.")
    args = parser.parse_args(argv)
    return run_build(validate_only=args.validate_only, root=args.root, strict_aliases=args.strict_aliases)


if __name__ == "__main__":
    raise SystemExit(main())
