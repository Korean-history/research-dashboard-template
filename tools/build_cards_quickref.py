"""Generate docs/prompts/cards_schema_quickref.md from authority/cards_schema.yaml."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "authority" / "cards_schema.yaml"
OUTPUT_PATH = ROOT / "docs" / "prompts" / "cards_schema_quickref.md"
INGESTOR_MANAGED_FIELDS = {"id", "created", "updated", "status"}
CARD_REFERENCE_LIST_FIELDS = {
    "depends_on",
    "inputs",
    "output_claims",
    "upstream_claims",
    "downstream_claims",
    "refuting_synthesis",
    "refuting_snippets",
    "candidate_evidence",
    "causal_predecessors",
    "evidence_cards",
    "child_cards",
    "sub_arguments",
    "feeding_synthesis",
}

CROSS_CARD_REFERENCE_NOTES = [
    ("linked_cards", "mapping; values are existing card IDs under cites/related/contradicts/supersedes"),
    ("claim.depends_on", "claim IDs or existing card IDs"),
    ("synthesis.inputs", "existing source, idea, claim, or synthesis card IDs"),
    ("synthesis.output_claims", "claim IDs or existing card IDs"),
    ("bridge.upstream_claims", "claim IDs or existing card IDs"),
    ("bridge.downstream_claims", "claim IDs or existing card IDs"),
    ("counterargument.refuting_synthesis", "existing synthesis card IDs"),
    ("counterargument.refuting_snippets", "existing source_snippet card IDs"),
    ("question.candidate_evidence", "existing card IDs only; put URLs, file paths, EndNote refs, and retrieval instructions in notes"),
    ("timeline.causal_predecessors", "existing timeline/card IDs"),
    ("timeline.evidence_cards", "existing source_snippet card IDs"),
    ("moc.parent_moc", "existing MOC/card ID"),
    ("moc.child_cards", "existing card IDs"),
    ("scaffold.sub_arguments", "claim IDs or existing card IDs"),
    ("scaffold.feeding_synthesis", "existing synthesis/card IDs"),
]


def load_schema() -> dict[str, Any]:
    return yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8")) or {}


def card_types(schema: dict[str, Any]) -> list[str]:
    raw = schema.get("card_types", {})
    return list(raw) if isinstance(raw, dict) else list(raw or [])


def required_fields(schema: dict[str, Any], card_type: str) -> list[str]:
    raw = schema.get("card_types", {})
    if isinstance(raw, dict):
        item = raw.get(card_type, {}) or {}
        return list(item.get("required_core", [])) + list(item.get("required_type_specific", []))
    return list((schema.get("core_fields", {}).get("required") or {}).keys()) + list((schema.get("type_fields", {}).get(card_type, {}).get("required") or {}).keys())


def inbox_required_fields(schema: dict[str, Any], card_type: str) -> list[str]:
    return [field for field in required_fields(schema, card_type) if field not in INGESTOR_MANAGED_FIELDS]


def ingestor_managed_required_fields(schema: dict[str, Any], card_type: str) -> list[str]:
    return [field for field in required_fields(schema, card_type) if field in INGESTOR_MANAGED_FIELDS]


def optional_fields(schema: dict[str, Any], card_type: str) -> list[str]:
    raw = schema.get("card_types", {})
    if isinstance(raw, dict):
        return list((raw.get(card_type, {}) or {}).get("optional", []))
    return list((schema.get("core_fields", {}).get("optional") or {}).keys()) + list((schema.get("type_fields", {}).get(card_type, {}).get("optional") or {}).keys())


def filename_prefix(schema: dict[str, Any], card_type: str) -> str:
    raw = schema.get("card_types", {})
    if isinstance(raw, dict):
        return str((raw.get(card_type, {}) or {}).get("filename_prefix", f"{card_type}."))
    return str(schema.get("filename_prefixes", {}).get(card_type, f"{card_type}."))


def id_prefix(schema: dict[str, Any], card_type: str) -> str:
    raw = schema.get("card_types", {})
    if isinstance(raw, dict):
        prefix = str((raw.get(card_type, {}) or {}).get("id_prefix", card_type))
    else:
        prefix = str(schema.get("id_prefixes", {}).get(card_type, card_type))
    return prefix if prefix.endswith(":") else f"{prefix}:"


def example_for(schema: dict[str, Any], card_type: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for field in inbox_required_fields(schema, card_type):
        if field == "id":
            data[field] = f"{id_prefix(schema, card_type)}YYYYMMDDHHMM_01"
        elif field == "card_type":
            data[field] = card_type
        elif field in {"created", "updated", "extraction_date", "opened_date"}:
            data[field] = "2026-04-26"
        elif field == "status":
            data[field] = "draft"
        elif field == "chapter_relevance":
            data[field] = ["Ch6"]
        elif field == "arc_ids":
            data[field] = ["arc:example"]
        elif field == "tags":
            data[field] = ["ch:6"]
        elif field == "linked_cards":
            data[field] = {}
        elif field.endswith("_verified"):
            data[field] = True
        elif field in CARD_REFERENCE_LIST_FIELDS:
            data[field] = []
        elif field == "register":
            data[field] = ["example"]
        else:
            data[field] = f"<{field}>"
    return data


def build() -> str:
    schema = load_schema()
    lines = [
        "# Cards Schema Quick Reference",
        "",
        "Generated from `authority/cards_schema.yaml`. Do not edit the generated block by hand; run `python tools/build_cards_quickref.py`.",
        "",
        "<!-- BEGIN GENERATED FROM authority/cards_schema.yaml -->",
        "",
        "## Inbox Authoring Rules",
        "",
        "- The per-type lists below are split for Harvester inbox authoring. Persisted cards contain every schema-required field, but inbox batches omit ingestor-managed fields.",
        "- Ingestor-managed/defaulted fields: `id`, `created`, `updated`, and `status`. Do not include them in normal inbox cards; `tools/ingest_cards.py` injects IDs and dates and defaults absent `status` to `draft`.",
        "- Every inbox card still supplies the operator-required core fields: `title`, `card_type`, `chapter_relevance`, `arc_ids`, `tags`, and `linked_cards`, plus the type-specific fields listed for that card type.",
        "- The YAML examples are inbox blocks, not persisted card files.",
        "",
        "## Cross-Card Reference Fields",
        "",
        "Use only real persisted card IDs in cross-card reference fields. Leave the field empty or omit it until the target card exists; do not use descriptive placeholders, same-batch future IDs, or `MISSING_CARD` in card frontmatter.",
        "",
        "`linked_cards` is the general relation map and is checked during ingest. The type-specific reference fields below are also real references and are checked by the card build validator:",
        "",
    ]
    for field, meaning in CROSS_CARD_REFERENCE_NOTES:
        lines.append(f"- `{field}`: {meaning}")
    lines.extend([
        "",
        "For research gaps, create a `question` or `scaffold` card. Reserve `MISSING_CARD` for `argument_chains.yaml` chain items only.",
        "For `question` cards, `candidate_evidence` is a card-reference field. Put retrieval pointers, URLs, local file paths, EndNote record numbers, and search instructions in `notes:` unless they are already persisted card IDs.",
        "",
        "## Card Types",
        "",
    ])
    for card_type in card_types(schema):
        persisted_required = ", ".join(required_fields(schema, card_type))
        inbox_required = ", ".join(inbox_required_fields(schema, card_type)) or "None"
        managed_required = ", ".join(ingestor_managed_required_fields(schema, card_type)) or "None"
        optional = ", ".join(optional_fields(schema, card_type)) or "None"
        lines.extend([
            f"### `{card_type}`",
            "",
            f"- Filename prefix: `{filename_prefix(schema, card_type)}`",
            f"- ID prefix: `{id_prefix(schema, card_type)}`",
            f"- Required in inbox/operator-supplied: `{inbox_required}`",
            f"- Required in persisted card/ingestor-managed: `{managed_required}`",
            f"- Required in persisted card/complete schema: `{persisted_required}`",
            f"- Optional fields: `{optional}`",
            "",
            "```yaml",
            yaml.safe_dump(example_for(schema, card_type), sort_keys=False, allow_unicode=True).strip(),
            "```",
            "",
        ])
    lines.extend([
        "## Hybrid Templates",
        "",
        "The schema declares template stubs for composite historical arguments. Use them as structure, not as card-frontmatter fields.",
        "",
        "<!-- END GENERATED -->",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(build(), encoding="utf-8", newline="\n")
    print(f"Wrote {OUTPUT_PATH.relative_to(ROOT).as_posix()}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
