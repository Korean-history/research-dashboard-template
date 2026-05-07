"""Validate and build retrieval packs from arcs, tags, and source snippets."""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lib import core, report_paths

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

AUTHORITY_DIR = ROOT / "authority"
TAGS_PATH = AUTHORITY_DIR / "tags.yaml"
AUTHORITY_MANIFEST_PATH = AUTHORITY_DIR / "authority.yaml"
ENTITIES_PATH = AUTHORITY_DIR / "entities.csv"
TERMS_PATH = AUTHORITY_DIR / "terms.csv"
SOURCES_PATH = AUTHORITY_DIR / "sources.csv"
MATRIX_PATH = ROOT / "argument_matrix.csv"
SNIPPETS_PATH = ROOT / "source_snippets.yaml"
ARCS_PATH = ROOT / "argument_arcs.yaml"
RETRIEVAL_INDEX_PATH = ROOT / "RETRIEVAL_INDEX.md"
RETRIEVAL_INDEX_JSON_PATH = ROOT / "RETRIEVAL_INDEX.json"
RETRIEVAL_DIR = ROOT / "retrieval_packs"
ARC_DIR = RETRIEVAL_DIR / "arcs"

REQUIRED_SNIPPET_FIELDS = [
    "snippet_id", "title", "arc_ids", "claim_ids", "source_ids", "report_files",
    "entity_ids", "term_ids", "tags", "chapters", "source_locator", "language",
    "original_snippet", "translation_or_summary", "evidence_role",
    "evidence_type", "citation_status", "risk_level", "friction_notes", "notes",
]

VALID_CITATION_STATUSES = {"unverified", "report_verified", "source_verified", "print_ready"}
VALID_EVIDENCE_TYPES = {
    "primary_quote", "primary_paraphrase", "secondary_synthesis",
    "analytical_finding", "negative_finding", "friction",
}
SNIPPET_LIST_FIELDS = {"arc_ids", "claim_ids", "source_ids", "report_files", "entity_ids", "term_ids", "tags", "chapters"}


@dataclass
class RetrievalData:
    tags: dict[str, dict[str, str]]
    tag_categories: dict[str, list[str]]
    arcs: list[dict[str, Any]]
    snippets: list[dict[str, Any]]
    claims: dict[str, dict[str, str]]
    entities: dict[str, dict[str, str]]
    terms: dict[str, dict[str, str]]
    sources: dict[str, dict[str, str]]
    valid_chapters: set[str]
    valid_risks: set[str]


def yaml_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return core.split_values(value)
    return []


def text_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def snippet_list(snippet: dict[str, Any], field: str) -> list[str]:
    return yaml_list(snippet.get(field, []))


def snippet_text(snippet: dict[str, Any], field: str) -> str:
    return text_value(snippet.get(field, ""))


def load_tags() -> tuple[dict[str, dict[str, str]], dict[str, list[str]], list[str]]:
    errors: list[str] = []
    data = core.read_yaml(TAGS_PATH)
    if not isinstance(data, dict):
        return {}, {}, ["authority/tags.yaml must be a YAML mapping."]

    tags: dict[str, dict[str, str]] = {}
    categories: dict[str, list[str]] = {}
    for category in data.get("tag_categories", []):
        if not isinstance(category, dict):
            errors.append("authority/tags.yaml tag_categories entries must be mappings.")
            continue
        category_id = str(category.get("category_id", "")).strip()
        if not category_id:
            errors.append("authority/tags.yaml category missing category_id.")
            continue
        categories.setdefault(category_id, [])
        for tag in category.get("tags", []):
            if not isinstance(tag, dict):
                errors.append(f"authority/tags.yaml category {category_id} has non-mapping tag.")
                continue
            tag_id = str(tag.get("tag_id", "")).strip()
            if not tag_id:
                errors.append(f"authority/tags.yaml category {category_id} has tag missing tag_id.")
                continue
            if tag_id in tags:
                errors.append(f"Duplicate tag_id in authority/tags.yaml: {tag_id}")
            tags[tag_id] = {
                "category_id": category_id,
                "label": str(tag.get("label", "")).strip(),
                "aliases": "; ".join(yaml_list(tag.get("aliases", []))),
            }
            categories[category_id].append(tag_id)
    return tags, categories, errors


def load_arcs() -> tuple[list[dict[str, Any]], list[str]]:
    data = core.read_yaml(ARCS_PATH)
    if not isinstance(data, dict) or not isinstance(data.get("arcs"), list):
        return [], ["argument_arcs.yaml must contain an arcs list."]
    return data["arcs"], []


def load_snippets() -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    data = core.read_yaml(SNIPPETS_PATH)
    if not isinstance(data, dict):
        return [], ["source_snippets.yaml must be a YAML mapping."]
    raw_snippets = data.get("snippets")
    if not isinstance(raw_snippets, list):
        return [], ["source_snippets.yaml must contain a snippets list."]

    snippets: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_snippets, 1):
        if not isinstance(raw, dict):
            errors.append(f"source_snippets.yaml snippet #{index} must be a mapping.")
            continue
        snippet: dict[str, Any] = dict(raw)
        owner = text_value(snippet.get("snippet_id")) or f"snippet #{index}"
        for field in REQUIRED_SNIPPET_FIELDS:
            if field not in snippet:
                errors.append(f"{owner} missing required field: {field}")
                snippet[field] = [] if field in SNIPPET_LIST_FIELDS else ""
        for field in SNIPPET_LIST_FIELDS:
            snippet[field] = yaml_list(snippet.get(field, []))
        for field in REQUIRED_SNIPPET_FIELDS:
            if field not in SNIPPET_LIST_FIELDS:
                snippet[field] = text_value(snippet.get(field, ""))
        snippets.append(snippet)
    return snippets, errors


def load_data() -> tuple[RetrievalData, list[str]]:
    errors: list[str] = []
    tags, tag_categories, tag_errors = load_tags()
    arcs, arc_errors = load_arcs()
    snippets, snippet_errors = load_snippets()
    claims_raw, claim_errors = core.read_csv(MATRIX_PATH)
    entities_raw, entity_errors = core.read_csv(ENTITIES_PATH)
    terms_raw, term_errors = core.read_csv(TERMS_PATH)
    sources_raw, source_errors = core.read_csv(SOURCES_PATH)
    manifest = core.read_yaml(AUTHORITY_MANIFEST_PATH)
    if not isinstance(manifest, dict):
        manifest = {}
        errors.append("authority/authority.yaml could not be parsed as a YAML mapping.")

    errors.extend(tag_errors + arc_errors + snippet_errors + claim_errors + entity_errors + term_errors + source_errors)
    return RetrievalData(
        tags=tags,
        tag_categories=tag_categories,
        arcs=arcs,
        snippets=snippets,
        claims=core.row_map(claims_raw, "claim_id"),
        entities=core.row_map(entities_raw, "entity_id"),
        terms=core.row_map(terms_raw, "term_id"),
        sources=core.row_map(sources_raw, "source_id"),
        valid_chapters=set(yaml_list(manifest.get("canonical_chapters", []))),
        valid_risks=set(manifest.get("risk_scale", {}).keys()) if isinstance(manifest.get("risk_scale"), dict) else set(),
    ), errors


def check_unique(rows: list[dict[str, Any]], key: str, errors: list[str], label: str) -> None:
    seen: set[str] = set()
    for row in rows:
        value = str(row.get(key, "")).strip()
        if not value:
            errors.append(f"{label} missing {key}.")
        elif value in seen:
            errors.append(f"Duplicate {label} {key}: {value}")
        seen.add(value)


def check_refs(owner: str, field: str, values: list[str], valid: set[str], errors: list[str]) -> None:
    for value in values:
        if value not in valid:
            errors.append(f"{owner} references unknown {field}: {value}")


def check_report_files(owner: str, refs: list[str], warnings: list[str]) -> None:
    for ref in refs:
        if not report_paths.resolve(ROOT, ref):
            warnings.append(f"{owner} references missing report file: {ref}")


def validate_data(data: RetrievalData) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    check_unique(data.arcs, "arc_id", errors, "arc")
    check_unique(data.snippets, "snippet_id", errors, "snippet")

    arc_ids = {str(arc.get("arc_id", "")).strip() for arc in data.arcs if arc.get("arc_id")}
    tag_ids = set(data.tags)
    claim_ids = set(data.claims)
    entity_ids = set(data.entities)
    term_ids = set(data.terms)
    source_ids = set(data.sources)
    snippet_claim_ids: set[str] = set()

    for arc in data.arcs:
        owner = str(arc.get("arc_id", "<unknown arc>"))
        if not owner.startswith("arc:"):
            errors.append(f"{owner} should use arc: namespace.")
        check_refs(owner, "tags", yaml_list(arc.get("tags", [])), tag_ids, errors)
        check_refs(owner, "claim_ids", yaml_list(arc.get("claim_ids", [])), claim_ids, errors)
        check_refs(owner, "chapters", yaml_list(arc.get("chapters", [])), data.valid_chapters, errors)
        check_report_files(owner, yaml_list(arc.get("report_files", [])), warnings)
        if not arc.get("core_question"):
            warnings.append(f"{owner} has no core_question.")
        if not arc.get("synthesis_note"):
            warnings.append(f"{owner} has no synthesis_note.")

    for snippet in data.snippets:
        owner = snippet_text(snippet, "snippet_id") or "<unknown snippet>"
        snippet_claim_ids.update(snippet_list(snippet, "claim_ids"))
        check_refs(owner, "arc_ids", snippet_list(snippet, "arc_ids"), arc_ids, errors)
        check_refs(owner, "claim_ids", snippet_list(snippet, "claim_ids"), claim_ids, errors)
        check_refs(owner, "source_ids", snippet_list(snippet, "source_ids"), source_ids, errors)
        check_refs(owner, "entity_ids", snippet_list(snippet, "entity_ids"), entity_ids, errors)
        check_refs(owner, "term_ids", snippet_list(snippet, "term_ids"), term_ids, errors)
        check_refs(owner, "tags", snippet_list(snippet, "tags"), tag_ids, errors)
        check_refs(owner, "chapters", snippet_list(snippet, "chapters"), data.valid_chapters, errors)
        check_report_files(owner, snippet_list(snippet, "report_files"), warnings)

        citation_status = snippet_text(snippet, "citation_status")
        evidence_type = snippet_text(snippet, "evidence_type")
        risk_level = snippet_text(snippet, "risk_level")
        if citation_status not in VALID_CITATION_STATUSES:
            errors.append(f"{owner} has invalid citation_status: {citation_status}")
        if evidence_type not in VALID_EVIDENCE_TYPES:
            errors.append(f"{owner} has invalid evidence_type: {evidence_type}")
        if risk_level not in data.valid_risks:
            errors.append(f"{owner} has invalid risk_level: {risk_level}")
        if not snippet_text(snippet, "original_snippet") and not snippet_text(snippet, "translation_or_summary"):
            errors.append(f"{owner} needs original_snippet or translation_or_summary.")
        if evidence_type == "primary_quote" and not snippet_text(snippet, "original_snippet"):
            errors.append(f"{owner} is primary_quote but lacks original_snippet.")
        if citation_status == "print_ready" and not snippet_text(snippet, "source_locator"):
            errors.append(f"{owner} is print_ready but lacks source_locator.")
        if citation_status in {"source_verified", "print_ready"} and not snippet_text(snippet, "source_locator"):
            warnings.append(f"{owner} is {citation_status} but lacks source_locator.")
        if risk_level in {"high", "critical"} and not snippet_text(snippet, "friction_notes"):
            warnings.append(f"{owner} is high/critical risk but lacks friction_notes.")

    missing_claims = sorted(claim_ids - snippet_claim_ids)
    if missing_claims:
        warnings.append("Claims without retrieval snippets: " + "; ".join(missing_claims))

    return errors, warnings


def snippets_by_arc(data: RetrievalData) -> dict[str, list[dict[str, Any]]]:
    by_arc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for snippet in data.snippets:
        for arc_id in snippet_list(snippet, "arc_ids"):
            by_arc[arc_id].append(snippet)
    return dict(by_arc)


def slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_") or "item"


def claim_rows(arc: dict[str, Any], data: RetrievalData) -> list[list[str]]:
    rows: list[list[str]] = []
    for claim_id in yaml_list(arc.get("claim_ids", [])):
        claim = data.claims.get(claim_id, {})
        rows.append([
            claim_id,
            claim.get("chapter", ""),
            claim.get("claim_type", ""),
            f"{claim.get('strength', '')} / {claim.get('status', '')} / {claim.get('risk_level', '')}",
            claim.get("claim", ""),
        ])
    return rows


def snippet_rows(snippets: list[dict[str, Any]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for snippet in snippets:
        rows.append([
            snippet_text(snippet, "snippet_id"),
            snippet_text(snippet, "title"),
            snippet_text(snippet, "evidence_type"),
            snippet_text(snippet, "source_locator"),
            snippet_text(snippet, "original_snippet"),
            snippet_text(snippet, "translation_or_summary"),
            snippet_text(snippet, "citation_status"),
            snippet_text(snippet, "risk_level"),
            snippet_text(snippet, "friction_notes"),
        ])
    return rows


def readiness_for_arc(arc: dict[str, Any], arc_snippets: list[dict[str, Any]]) -> dict[str, Any]:
    claim_ids = yaml_list(arc.get("claim_ids", []))
    covered_claim_ids = {
        claim_id
        for snippet in arc_snippets
        for claim_id in snippet_list(snippet, "claim_ids")
        if claim_id in claim_ids
    }
    missing_claim_ids = sorted(set(claim_ids) - covered_claim_ids)
    needs_print = [
        snippet_text(snippet, "snippet_id")
        for snippet in arc_snippets
        if snippet_text(snippet, "citation_status") != "print_ready"
    ]
    high_risk = [
        snippet_text(snippet, "snippet_id")
        for snippet in arc_snippets
        if snippet_text(snippet, "risk_level") in {"high", "critical"}
    ]
    missing_friction = [
        snippet_text(snippet, "snippet_id")
        for snippet in arc_snippets
        if snippet_text(snippet, "risk_level") in {"high", "critical"} and not snippet_text(snippet, "friction_notes")
    ]
    evidence_types: dict[str, int] = defaultdict(int)
    for snippet in arc_snippets:
        evidence_types[snippet_text(snippet, "evidence_type")] += 1
    return {
        "total_claims": len(claim_ids),
        "total_snippets": len(arc_snippets),
        "claims_without_snippets_count": len(missing_claim_ids),
        "claims_without_snippets": missing_claim_ids,
        "snippets_needing_print_verification_count": len(needs_print),
        "snippets_needing_print_verification": needs_print,
        "high_or_critical_risk_snippets_count": len(high_risk),
        "high_or_critical_risk_snippets": high_risk,
        "high_risk_without_friction_notes_count": len(missing_friction),
        "high_risk_without_friction_notes": missing_friction,
        "evidence_types": dict(sorted(evidence_types.items())),
    }


def readiness_rows(readiness: dict[str, Any]) -> list[list[str]]:
    return [
        ["Claims without snippets", str(readiness["claims_without_snippets_count"]), "; ".join(readiness["claims_without_snippets"]) or "None"],
        ["Snippets needing print verification", str(readiness["snippets_needing_print_verification_count"]), "; ".join(readiness["snippets_needing_print_verification"]) or "None"],
        ["High/critical risk snippets", str(readiness["high_or_critical_risk_snippets_count"]), "; ".join(readiness["high_or_critical_risk_snippets"]) or "None"],
        ["High-risk snippets missing friction notes", str(readiness["high_risk_without_friction_notes_count"]), "; ".join(readiness["high_risk_without_friction_notes"]) or "None"],
        ["Evidence types", str(sum(readiness["evidence_types"].values())), "; ".join(f"{key}: {value}" for key, value in readiness["evidence_types"].items()) or "None"],
    ]


def render_arc_pack(arc: dict[str, Any], data: RetrievalData, arc_snippets: list[dict[str, Any]]) -> str:
    arc_id = str(arc.get("arc_id", ""))
    report_files = yaml_list(arc.get("report_files", []))
    tags = yaml_list(arc.get("tags", []))
    readiness = readiness_for_arc(arc, arc_snippets)
    lines = [
        f"# {arc.get('title', arc_id)}",
        "",
        "Generated from current retrieval state.",
        "",
        f"**Arc ID:** `{arc_id}`",
        f"**Chapters:** {'; '.join(yaml_list(arc.get('chapters', []))) or 'None'}",
        f"**Tags:** {'; '.join(tags) or 'None'}",
        "",
        "## Readiness Score",
        "",
    ]
    lines.extend(core.markdown_table(["Diagnostic", "Count", "Details"], readiness_rows(readiness)))
    lines.extend([
        "",
        "## Core Question",
        "",
        str(arc.get("core_question", "")) or "None.",
        "",
        "## Synthesis Note",
        "",
        str(arc.get("synthesis_note", "")) or "None.",
        "",
        "## Linked Claims",
        "",
    ])
    lines.extend(core.markdown_table(["Claim ID", "Chapter", "Type", "Strength / Status / Risk", "Claim"], claim_rows(arc, data)))
    lines.extend(["", "## Source Snippets", ""])
    lines.extend(core.markdown_table(["Snippet ID", "Title", "Type", "Locator", "Original", "Summary / Translation", "Citation", "Risk", "Friction"], snippet_rows(arc_snippets)))
    lines.extend(["", "## Report Pull List", ""])
    if report_files:
        lines.extend(f"- `{report}`" for report in report_files)
    else:
        lines.append("None.")
    extra_reports = sorted({report for snippet in arc_snippets for report in snippet_list(snippet, "report_files")} - set(report_files))
    if extra_reports:
        lines.extend(["", "## Additional Snippet Reports", ""])
        lines.extend(f"- `{report}`" for report in extra_reports)
    lines.extend([
        "",
        "## Claude Use",
        "",
        "- Start from the snippets table before brute-force searching reports.",
        "- Open report files only when the snippet locator or citation status requires verification.",
        "- Preserve anti-flattening cautions from authority files and claim risk levels.",
        "",
    ])
    return "\n".join(lines).rstrip() + "\n"


def render_index(data: RetrievalData) -> str:
    by_arc = snippets_by_arc(data)
    arc_rows = []
    for arc in data.arcs:
        arc_id = str(arc.get("arc_id", ""))
        readiness = readiness_for_arc(arc, by_arc.get(arc_id, []))
        arc_rows.append([
            arc_id,
            arc.get("title", ""),
            "; ".join(yaml_list(arc.get("chapters", []))),
            str(len(yaml_list(arc.get("claim_ids", [])))),
            str(len(by_arc.get(arc_id, []))),
            str(readiness["claims_without_snippets_count"]),
            str(readiness["snippets_needing_print_verification_count"]),
            str(readiness["high_or_critical_risk_snippets_count"]),
            "; ".join(yaml_list(arc.get("tags", []))),
        ])

    tag_rows = []
    for category, tag_list in data.tag_categories.items():
        for tag_id in tag_list:
            tag = data.tags.get(tag_id, {})
            tag_rows.append([category, tag_id, tag.get("label", ""), tag.get("aliases", "")])

    lines = [
        "# Retrieval Index",
        "",
        "Generated from `argument_arcs.yaml`, `source_snippets.yaml`, `authority/tags.yaml`, and the authority/matrix backend.",
        "",
        "Use this as the entry point for research assembly. It is designed to keep Claude from brute-force searching every report from scratch.",
        "",
        "## Summary",
        "",
        f"- Argument arcs: {len(data.arcs)}",
        f"- Controlled tags: {len(data.tags)}",
        f"- Source snippets: {len(data.snippets)}",
        "",
        "## Argument Arcs",
        "",
    ]
    lines.extend(core.markdown_table(["Arc ID", "Title", "Chapters", "Claims", "Snippets", "Missing Claims", "Needs Print", "High/Critical", "Tags"], arc_rows))
    lines.extend(["", "## Controlled Tags", ""])
    lines.extend(core.markdown_table(["Category", "Tag ID", "Label", "Aliases"], tag_rows))
    lines.extend([
        "",
        "## Generated Arc Packs",
        "",
    ])
    for arc in data.arcs:
        arc_id = str(arc.get("arc_id", ""))
        slugged = slug(arc_id)
        lines.append(f"- `retrieval_packs/arcs/{slugged}.md` and `retrieval_packs/arcs/{slugged}.json`")
    lines.extend([
        "",
        "## Workflow",
        "",
        "```powershell",
        "python tools/research_retrieval.py validate",
        "python tools/research_retrieval.py build",
        "python tools/build.py",
        "```",
        "",
    ])
    return "\n".join(lines).rstrip() + "\n"


def write_json_index(data: RetrievalData) -> None:
    by_arc = snippets_by_arc(data)
    arc_payload = []
    for arc in data.arcs:
        arc_id = str(arc.get("arc_id", ""))
        slugged = slug(arc_id)
        arc_payload.append({
            **arc,
            "readiness": readiness_for_arc(arc, by_arc.get(arc_id, [])),
            "pack_markdown": f"retrieval_packs/arcs/{slugged}.md",
            "pack_json": f"retrieval_packs/arcs/{slugged}.json",
        })
    payload = {
        "arcs": arc_payload,
        "snippets": [snippet_json(snippet) for snippet in data.snippets],
        "tags": data.tags,
    }
    core.write_json(RETRIEVAL_INDEX_JSON_PATH, payload)


def snippet_json(snippet: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (snippet_list(snippet, key) if key in SNIPPET_LIST_FIELDS else snippet_text(snippet, key))
        for key in REQUIRED_SNIPPET_FIELDS
    }


def arc_json_payload(arc: dict[str, Any], data: RetrievalData, arc_snippets: list[dict[str, Any]]) -> dict[str, Any]:
    arc_id = str(arc.get("arc_id", ""))
    return {
        "arc": arc,
        "readiness": readiness_for_arc(arc, arc_snippets),
        "claims": [
            {"claim_id": row[0], "chapter": row[1], "claim_type": row[2], "state": row[3], "claim": row[4]}
            for row in claim_rows(arc, data)
        ],
        "snippets": [snippet_json(snippet) for snippet in arc_snippets],
        "report_files": yaml_list(arc.get("report_files", [])),
        "additional_snippet_reports": sorted({
            report
            for snippet in arc_snippets
            for report in snippet_list(snippet, "report_files")
        } - set(yaml_list(arc.get("report_files", [])))),
    }


def validate() -> int:
    data, load_errors = load_data()
    errors, warnings = validate_data(data)
    errors = load_errors + errors

    print(f"Argument arcs: {len(data.arcs)}")
    print(f"Controlled tags: {len(data.tags)}")
    print(f"Source snippets: {len(data.snippets)}")

    if warnings:
        print("\nWARNINGS")
        for warning in warnings:
            print(f"- {warning}")

    if errors:
        print("\nERRORS")
        for error in errors:
            print(f"- {error}")
        return 1

    print("\nOK: retrieval arcs, tags, and source snippets are structurally consistent.")
    return 0


def build() -> int:
    data, load_errors = load_data()
    errors, warnings = validate_data(data)
    errors = load_errors + errors
    if errors:
        print("ERROR: cannot build retrieval packs until validation errors are fixed.")
        for error in errors:
            print(f"- {error}")
        return 1

    RETRIEVAL_DIR.mkdir(exist_ok=True)
    core.safe_clear_generated_dir(ARC_DIR, ROOT)
    by_arc = snippets_by_arc(data)
    for arc in data.arcs:
        arc_id = str(arc.get("arc_id", ""))
        arc_snippets = by_arc.get(arc_id, [])
        slugged = slug(arc_id)
        (ARC_DIR / f"{slugged}.md").write_text(render_arc_pack(arc, data, arc_snippets), encoding="utf-8")
        core.write_json(ARC_DIR / f"{slugged}.json", arc_json_payload(arc, data, arc_snippets))
    (RETRIEVAL_DIR / "README.md").write_text(
        "# Retrieval Packs\n\nGenerated arc-centered retrieval packs. Edit `argument_arcs.yaml`, `source_snippets.yaml`, or `authority/tags.yaml`, then regenerate. Markdown files are for human/agent reading; JSON sidecars are for structured agent ingestion.\n",
        encoding="utf-8",
    )
    RETRIEVAL_INDEX_PATH.write_text(render_index(data), encoding="utf-8")
    write_json_index(data)

    print(f"Wrote retrieval index and {len(data.arcs)} arc packs.")
    if warnings:
        print("\nWARNINGS")
        for warning in warnings:
            print(f"- {warning}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and build retrieval packs.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="Validate arcs, tags, and source snippets.")
    sub.add_parser("build", help="Build retrieval index and arc packs.")
    args = parser.parse_args(argv)

    if args.command == "validate":
        return validate()
    if args.command == "build":
        return build()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
