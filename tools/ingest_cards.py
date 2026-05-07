"""Validate and ingest Harvester card batches from inbox Markdown."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import importlib.util

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lib import report_paths


def _load_sibling_attr(module_name: str, relative_path: str, attr_name: str) -> Any:
    if module_name in sys.modules:
        return getattr(sys.modules[module_name], attr_name)
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
    if not spec or not spec.loader:
        raise ImportError(f"Cannot load {module_name} from {relative_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return getattr(module, attr_name)


try:
    from tools.lib.promotion_gates import validate_inbox_block
    from tools.lib.telemetry import append_card_event, ensure_session_id
except ModuleNotFoundError:
    validate_inbox_block = _load_sibling_attr(
        "_ingest_cards_promotion_gates",
        "tools/lib/promotion_gates.py",
        "validate_inbox_block",
    )
    append_card_event = _load_sibling_attr(
        "_ingest_cards_telemetry",
        "tools/lib/telemetry.py",
        "append_card_event",
    )
    ensure_session_id = _load_sibling_attr(
        "_ingest_cards_telemetry",
        "tools/lib/telemetry.py",
        "ensure_session_id",
    )


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass


INJECTED_FIELDS = {"id", "created", "updated"}
CORE_ORDER = ["id", "title", "card_type", "created", "updated", "status", "chapter_relevance"]
TAIL_FIELDS = ["linked_cards", "tags"]
BLOCK_SCALAR_FIELDS = {
    "original_snippet",
    "translation_or_summary",
    "claim_text",
    "synthesis_text",
    "bridge_text",
    "position_text",
    "cautions",
    "friction_notes",
    "arc_rationale",
    "notes",
}
DEFAULT_LINK_KEYS = {"cites", "related", "contradicts", "refutes", "supersedes", "complicates"}
PROMOTION_GATE_STATUSES = {"report_verified", "source_verified", "print_ready"}


@dataclass(frozen=True)
class InboxBlock:
    index: int
    start_line: int
    end_line: int
    metadata: dict[str, Any]


class LiteralDumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.SafeDumper, value: str) -> yaml.nodes.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


LiteralDumper.add_representer(str, _str_representer)


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value).strip()


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            text = as_text(item)
            if text:
                out.append(text)
        return out
    text = as_text(value)
    return [text] if text else []


def read_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def read_csv_ids(path: Path, key: str) -> set[str]:
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return {row.get(key, "").strip() for row in reader if row.get(key, "").strip()}


def schema_card_types(schema: dict[str, Any]) -> list[str]:
    raw = schema.get("card_types", {})
    if isinstance(raw, dict):
        return list(raw)
    return list(raw or [])


def type_definition(schema: dict[str, Any], card_type: str) -> dict[str, Any]:
    raw = schema.get("card_types", {})
    if isinstance(raw, dict):
        return raw.get(card_type, {}) or {}
    return {
        "filename_prefix": schema.get("filename_prefixes", {}).get(card_type, f"{card_type}."),
        "id_prefix": schema.get("id_prefixes", {}).get(card_type, card_type),
        "required_core": list((schema.get("core_fields", {}).get("required") or {}).keys()),
        "required_type_specific": list((schema.get("type_fields", {}).get(card_type, {}).get("required") or {}).keys()),
        "optional": list((schema.get("core_fields", {}).get("optional") or {}).keys())
        + list((schema.get("type_fields", {}).get(card_type, {}).get("optional") or {}).keys()),
    }


def filename_prefix(schema: dict[str, Any], card_type: str) -> str:
    prefix = as_text(type_definition(schema, card_type).get("filename_prefix"))
    return prefix or f"{card_type}."


def id_prefix(schema: dict[str, Any], card_type: str) -> str:
    prefix = as_text(type_definition(schema, card_type).get("id_prefix"))
    if not prefix:
        prefix = card_type
    return prefix if prefix.endswith(":") else f"{prefix}:"


def required_fields(schema: dict[str, Any], card_type: str) -> list[str]:
    definition = type_definition(schema, card_type)
    fields = list(definition.get("required_core", []) or [])
    fields.extend(definition.get("required_type_specific", []) or [])
    return fields


def allowed_fields(schema: dict[str, Any], card_type: str) -> set[str]:
    definition = type_definition(schema, card_type)
    fields = set(definition.get("required_core", []) or [])
    fields.update(definition.get("required_type_specific", []) or [])
    fields.update(definition.get("optional", []) or [])
    fields.update({"id", "created", "updated", "status"})
    fields.update((schema.get("field_enums") or {}).keys())
    return fields


def enum_values(schema: dict[str, Any], field: str) -> set[str]:
    field_enums = schema.get("field_enums", {})
    if field in field_enums:
        return {as_text(item) for item in field_enums[field]}

    mapping = {
        "status": "status",
        "chapter_relevance": "chapters",
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
    enum_name = mapping.get(field, field)
    values = schema.get("allowed_values", {}).get(enum_name)
    if values is None:
        values = schema.get("type_allowed_values", {}).get(enum_name)
    return {as_text(item) for item in values or []}


def linked_card_keys(schema: dict[str, Any]) -> set[str]:
    if schema.get("linked_cards_keys"):
        return {as_text(item) for item in schema.get("linked_cards_keys", [])}
    return set(schema.get("linked_card_fields", {}).get("allowed", []) or DEFAULT_LINK_KEYS)


def alias_card_ids(root: Path) -> set[str]:
    path = root / "authority" / "card_id_aliases.yaml"
    if not path.exists():
        return set()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    aliases = raw.get("aliases", {}) if isinstance(raw, dict) else {}
    if isinstance(aliases, dict):
        return {as_text(key) for key in aliases if as_text(key)}
    if isinstance(aliases, list):
        return {as_text(item.get("alias")) for item in aliases if isinstance(item, dict) and as_text(item.get("alias"))}
    return set()


def parse_inbox(path: Path) -> tuple[list[InboxBlock], list[str]]:
    if not path.exists():
        return [], [f"Missing inbox: {path}"]
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    delimiter_indexes = [idx for idx, line in enumerate(lines) if line.strip() == "---"]
    if not delimiter_indexes:
        return [], ["No YAML card blocks found. Expected --- delimiters."]

    errors: list[str] = []
    blocks: list[InboxBlock] = []

    def parse_segment(
        start_index: int,
        end_index: int,
        opening_line: int,
        closing_line: int,
        allow_filler: bool = False,
    ) -> None:
        yaml_text = "\n".join(lines[start_index:end_index])
        if not yaml_text.strip():
            return
        try:
            data = yaml.safe_load(yaml_text) or {}
        except yaml.YAMLError as exc:
            looks_like_card = re.search(r"^\s*(title|card_type|chapter_relevance):", yaml_text, re.MULTILINE)
            if allow_filler and not looks_like_card:
                return
            errors.append(f"Block {len(blocks) + 1} lines {opening_line}-{closing_line}: invalid YAML: {exc}")
            return
        if isinstance(data, dict) and data.get("card_type"):
            blocks.append(InboxBlock(len(blocks) + 1, opening_line, closing_line, data))
            return
        looks_like_card = isinstance(data, dict) and any(
            key in data for key in ("title", "card_type", "chapter_relevance")
        )
        if allow_filler and not looks_like_card:
            return
        if isinstance(data, dict):
            errors.append(
                f"Text between card blocks before line {closing_line}; "
                "move notes outside the batch or into a schema field."
            )
        else:
            errors.append(f"Block {len(blocks) + 1}: YAML frontmatter must be a mapping.")

    for index, opening in enumerate(delimiter_indexes[:-1]):
        closing = delimiter_indexes[index + 1]
        parse_segment(opening + 1, closing, opening + 1, closing + 1)

    final_opening = delimiter_indexes[-1]
    parse_segment(final_opening + 1, len(lines), final_opening + 1, len(lines), allow_filler=True)
    if not blocks and not errors:
        errors.append("No YAML card blocks found. Expected card YAML after --- delimiters.")
    return blocks, errors


def load_arc_ids(root: Path) -> set[str]:
    data = read_yaml(root / "argument_arcs.yaml")
    if not isinstance(data, dict):
        return set()
    arcs = data.get("arcs", [])
    if isinstance(arcs, dict):
        return {as_text(key) for key in arcs}
    return {as_text(item.get("arc_id")) for item in arcs if isinstance(item, dict) and item.get("arc_id")}


def load_tag_ids(root: Path) -> set[str]:
    data = read_yaml(root / "authority" / "tags.yaml")
    if not isinstance(data, dict):
        return set()
    if isinstance(data.get("tags"), list):
        return {as_text(item) if not isinstance(item, dict) else as_text(item.get("tag_id")) for item in data["tags"] if as_text(item) or isinstance(item, dict)}
    out: set[str] = set()
    for category in data.get("tag_categories", []):
        if not isinstance(category, dict):
            continue
        for tag in category.get("tags", []):
            if isinstance(tag, dict) and tag.get("tag_id"):
                out.add(as_text(tag.get("tag_id")))
    return out


def load_report_files(root: Path) -> set[str]:
    path = root / "research_catalog.csv"
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        key = "file" if "file" in (reader.fieldnames or []) else "report_path"
        return report_paths.alias_set([row.get(key, "").strip() for row in reader if row.get(key, "").strip()])


def report_refresh_hint(root: Path, report: str) -> str:
    if report and report_paths.resolve(root, report):
        return " (file exists but is missing from research_catalog.csv; run `python tools\\research_metadata.py refresh`.)"
    return ""


def existing_card_ids(root: Path) -> set[str]:
    ids: set[str] = set()
    for path in (root / "cards").glob("**/*.md"):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^id:\s*(.+?)\s*$", text, re.MULTILINE)
        if match:
            ids.add(match.group(1).strip().strip('"').strip("'"))
    return ids


def validate_links(owner: str, value: Any, valid_keys: set[str], valid_card_ids: set[str], errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        errors.append(f"{owner} field linked_cards must be a mapping.")
        return
    for key, targets in value.items():
        if key not in valid_keys:
            errors.append(f"{owner} linked_cards has invalid key: {key}")
            continue
        for target in as_list(targets):
            if target == "MISSING_CARD":
                errors.append(f"{owner} linked_cards may not contain MISSING_CARD.")
            elif target not in valid_card_ids:
                errors.append(f"{owner} links to unknown card: {target}")


def validate_block(
    block: InboxBlock,
    metadata: dict[str, Any],
    schema: dict[str, Any],
    root: Path,
    valid_card_ids: set[str],
    strict_promotion_gates: bool = False,
) -> list[str]:
    errors: list[str] = []
    owner = f"Block {block.index} lines {block.start_line}-{block.end_line}"
    card_type = as_text(metadata.get("card_type"))
    if card_type not in schema_card_types(schema):
        return [f"{owner}: invalid card_type: {card_type}"]

    for field in INJECTED_FIELDS:
        if field in block.metadata:
            errors.append(f"{owner}: field {field} is generated by ingestor; remove it from inbox.")

    allowed = allowed_fields(schema, card_type)
    for field in metadata:
        if field not in allowed:
            errors.append(f"{owner}: unknown frontmatter field: {field}")

    for field in required_fields(schema, card_type):
        if field not in metadata:
            errors.append(f"{owner}: missing required field: {field}")
        elif metadata.get(field) in (None, ""):
            errors.append(f"{owner}: field {field} cannot be blank.")

    for field in ["status", "chapter_relevance", "original_lang", "evidence_type", "citation_status", "risk_level", "claim_type", "strength", "position_status", "archival_validity", "analytical_credibility", "date_relation", "evidence_stage"]:
        values = enum_values(schema, field)
        if not values or field not in metadata:
            continue
        raw_values = as_list(metadata.get(field)) if field == "chapter_relevance" else [as_text(metadata.get(field))]
        for raw in raw_values:
            if raw not in values:
                errors.append(f"{owner}: field {field} has invalid value: {raw}")

    for field in [
        "arc_ids",
        "tags",
        "claim_ids",
        "entity_ids",
        "term_ids",
        "inputs",
        "depends_on",
        "evidence_bindings",
        "derived_from_reports",
        "informed_by_reports",
        "source_ids",
        "warning_flags",
        "candidate_evidence",
    ]:
        if field in metadata and metadata.get(field) is not None and not isinstance(metadata.get(field), list):
            errors.append(f"{owner}: field {field} must be a list.")

    validate_links(owner, metadata.get("linked_cards"), linked_card_keys(schema), valid_card_ids, errors)

    valid_sources = read_csv_ids(root / "authority" / "sources.csv", "source_id")
    valid_reports = load_report_files(root)
    valid_arcs = load_arc_ids(root)
    valid_tags = load_tag_ids(root)

    if metadata.get("source_id") and valid_sources and as_text(metadata.get("source_id")) not in valid_sources:
        errors.append(f"{owner}: unknown source_id: {metadata.get('source_id')}")
    for source_id in as_list(metadata.get("source_ids")):
        if valid_sources and source_id not in valid_sources:
            errors.append(f"{owner}: unknown source_ids entry: {source_id}")
    for field in ["provenance_report", "derived_from_reports", "informed_by_reports"]:
        for report in as_list(metadata.get(field)):
            if valid_reports and report not in valid_reports:
                errors.append(f"{owner}: unknown {field}: {report}{report_refresh_hint(root, report)}")
    for arc_id in as_list(metadata.get("arc_ids")):
        if valid_arcs and arc_id not in valid_arcs:
            errors.append(f"{owner}: unknown arc_id: {arc_id}")
    for tag in as_list(metadata.get("tags")):
        if valid_tags and tag not in valid_tags:
            errors.append(f"{owner}: unknown tag: {tag}")
    if card_type == "question":
        for evidence in as_list(metadata.get("candidate_evidence")):
            if evidence not in valid_card_ids:
                errors.append(
                    f"{owner}: candidate_evidence must reference an existing card ID; "
                    f"move retrieval pointers, URLs, EndNote refs, and search instructions into notes: {evidence}"
                )

    if strict_promotion_gates and card_type == "source_snippet":
        errors.extend(validate_promotion_gates(owner, metadata))

    return errors


def validate_promotion_gates(owner: str, metadata: dict[str, Any]) -> list[str]:
    return validate_inbox_block(owner, metadata)


def next_suffixes(root: Path, schema: dict[str, Any], timestamp: str) -> dict[str, int]:
    counters: dict[str, int] = {}
    for card_type in schema_card_types(schema):
        directory = root / "cards" / card_type
        prefix = filename_prefix(schema, card_type)
        highest = 0
        if directory.exists():
            pattern = re.compile(rf"^{re.escape(prefix)}{timestamp}_(\d{{2}})\.md$")
            for path in directory.glob("*.md"):
                match = pattern.match(path.name)
                if match:
                    highest = max(highest, int(match.group(1)))
        counters[card_type] = highest + 1
    return counters


def ordered_metadata(metadata: dict[str, Any], schema: dict[str, Any], card_type: str) -> dict[str, Any]:
    ordered: dict[str, Any] = {}
    for field in CORE_ORDER:
        if field in metadata:
            ordered[field] = metadata[field]
    for field in required_fields(schema, card_type):
        if field not in ordered and field in metadata and field not in TAIL_FIELDS:
            ordered[field] = metadata[field]
    for field in metadata:
        if field not in ordered and field not in TAIL_FIELDS:
            ordered[field] = metadata[field]
    for field in TAIL_FIELDS:
        if field in metadata:
            ordered[field] = metadata[field]
    return ordered


def render_card(metadata: dict[str, Any], schema: dict[str, Any], card_type: str) -> str:
    ordered = ordered_metadata(metadata, schema, card_type)
    for field in BLOCK_SCALAR_FIELDS:
        value = ordered.get(field)
        if isinstance(value, str) and "\n" in value and not value.endswith("\n"):
            ordered[field] = value + "\n"
    yaml_text = yaml.dump(ordered, Dumper=LiteralDumper, sort_keys=False, allow_unicode=True, width=1000)
    return f"---\n{yaml_text}---\n"


def write_errors(inbox_path: Path, errors: list[str]) -> None:
    errors_path = inbox_path.with_name(f"{inbox_path.name}.errors.txt")
    errors_path.write_text("\n".join(errors) + "\n", encoding="utf-8")


def move_with_retry(source: Path, target: Path) -> None:
    for attempt in range(3):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(0.1)


def rmtree_with_retry(path: Path, attempts: int = 6, delay_seconds: float = 0.2) -> None:
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay_seconds * (attempt + 1))


def cleanup_staging(staging: Path) -> list[str]:
    warnings: list[str] = []
    if not staging.exists():
        return warnings
    try:
        rmtree_with_retry(staging)
    except PermissionError as exc:
        warnings.append(f"WARN: could not remove staging directory {staging}: {exc}")
    return warnings


def write_batch(root: Path, inbox_path: Path, cards: list[tuple[str, Path, str]]) -> list[str]:
    staging_root = root / "cards" / ".staging"
    staging = staging_root / f"ingest_{os.getpid()}_{time.time_ns()}"
    staging.mkdir(parents=True, exist_ok=False)
    moved: list[Path] = []
    cleanup_warnings: list[str] = []
    try:
        staged: list[tuple[Path, Path]] = []
        for card_type, target, text in cards:
            stage_dir = staging / card_type
            stage_dir.mkdir(parents=True, exist_ok=True)
            stage_path = stage_dir / target.name
            stage_path.write_text(text, encoding="utf-8", newline="\n")
            staged.append((stage_path, target))
        for stage_path, target in staged:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise FileExistsError(f"Target already exists: {target}")
            move_with_retry(stage_path, target)
            moved.append(target)
    except Exception:
        for target in moved:
            if target.exists():
                target.unlink()
        raise
    finally:
        cleanup_warnings.extend(cleanup_staging(staging))
        try:
            staging_root.rmdir()
        except OSError:
            pass

    inbox_path.unlink()
    errors_path = inbox_path.with_name(f"{inbox_path.name}.errors.txt")
    if errors_path.exists():
        errors_path.unlink()
    return cleanup_warnings


def prepare_cards(
    root: Path,
    blocks: list[InboxBlock],
    schema: dict[str, Any],
    strict_promotion_gates: bool = False,
) -> tuple[list[tuple[str, Path, str]], list[str], Counter[str]]:
    timestamp = dt.datetime.now().strftime("%Y%m%d%H%M")
    created = dt.date.today().isoformat()
    counters = next_suffixes(root, schema, timestamp)
    prepared: list[tuple[str, Path, str]] = []
    errors: list[str] = []
    counts: Counter[str] = Counter()
    valid_card_ids = existing_card_ids(root) | alias_card_ids(root)

    enriched_blocks: list[tuple[InboxBlock, dict[str, Any]]] = []
    for block in blocks:
        card_type = as_text(block.metadata.get("card_type"))
        suffix = counters.get(card_type, 1)
        counters[card_type] = suffix + 1
        id_stem = f"{timestamp}_{suffix:02d}"
        metadata = dict(block.metadata)
        metadata["id"] = f"{id_prefix(schema, card_type)}{id_stem}"
        metadata["created"] = created
        metadata["updated"] = created
        metadata.setdefault("status", "draft")
        enriched_blocks.append((block, metadata))

    for block, metadata in enriched_blocks:
        errors.extend(validate_block(block, metadata, schema, root, valid_card_ids, strict_promotion_gates))

    if errors:
        return [], errors, counts

    for _, metadata in enriched_blocks:
        card_type = as_text(metadata["card_type"])
        id_stem = as_text(metadata["id"]).split(":", 1)[-1]
        target = root / "cards" / card_type / f"{filename_prefix(schema, card_type)}{id_stem}.md"
        if target.exists():
            errors.append(f"Target already exists: {target}")
        prepared.append((card_type, target, render_card(metadata, schema, card_type)))
        counts[card_type] += 1
    return ([] if errors else prepared), errors, counts


def run(inbox_arg: str, check: bool, quiet: bool, strict_promotion_gates: bool = False, session_id: str | None = None) -> int:
    root = Path.cwd()
    telemetry_session = ensure_session_id(session_id) if session_id else ""
    inbox_path = (root / inbox_arg).resolve() if not Path(inbox_arg).is_absolute() else Path(inbox_arg)
    schema_path = root / "authority" / "cards_schema.yaml"
    try:
        schema = read_yaml(schema_path)
        if not isinstance(schema, dict):
            raise RuntimeError(f"Invalid or missing schema: {schema_path}")
        blocks, parse_errors = parse_inbox(inbox_path)
        if parse_errors:
            write_errors(inbox_path, parse_errors)
            if not quiet:
                for error in parse_errors:
                    print(f"ERROR: {error}", file=sys.stderr)
            return 1
        prepared, errors, counts = prepare_cards(root, blocks, schema, strict_promotion_gates)
        if errors:
            write_errors(inbox_path, errors)
            if telemetry_session:
                append_card_event(telemetry_session, "card_ingest_failed", root=root, inbox=str(inbox_path), errors=errors)
            if not quiet:
                for error in errors:
                    print(f"ERROR: {error}", file=sys.stderr)
            return 1
        write_warnings: list[str] = []
        if not check:
            write_warnings = write_batch(root, inbox_path, prepared)
        if telemetry_session:
            append_card_event(
                telemetry_session,
                "card_batch_validated" if check else "card_batch_ingested",
                root=root,
                inbox=str(inbox_path),
                card_count=sum(counts.values()),
                counts=dict(counts),
                strict_promotion_gates=strict_promotion_gates,
            )
        if not quiet:
            action = "validated" if check else "ingested"
            for _, target, _ in prepared:
                print(f"[OK] {target.relative_to(root).as_posix()}")
            for warning in write_warnings:
                print(warning, file=sys.stderr)
            summary = ", ".join(f"{count} {card_type}" for card_type, count in sorted(counts.items()))
            print(f"SUMMARY: {sum(counts.values())} cards {action} ({summary}).")
        else:
            print(f"SUMMARY: {sum(counts.values())} cards {'validated' if check else 'ingested'}.")
        return 0
    except Exception as exc:
        try:
            write_errors(inbox_path, [f"I/O failure: {exc}"])
        except Exception:
            pass
        print(f"ERROR: {exc}", file=sys.stderr)
        if telemetry_session:
            append_card_event(telemetry_session, "card_ingest_error", root=root, inbox=str(inbox_path), error=str(exc))
        return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and ingest card blocks from inbox.md.")
    parser.add_argument("inbox", nargs="?", default="inbox.md")
    parser.add_argument("--check", action="store_true", help="Validate only; do not write cards or delete inbox.")
    parser.add_argument("--quiet", action="store_true", help="Only print the summary line.")
    parser.add_argument(
        "--strict-promotion-gates",
        action="store_true",
        help="Require report-to-card provenance fields for source_snippet cards.",
    )
    parser.add_argument("--session-id", help="Append card ingest telemetry to this session UUID.")
    args = parser.parse_args(argv)
    return run(args.inbox, args.check, args.quiet, args.strict_promotion_gates, args.session_id)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
