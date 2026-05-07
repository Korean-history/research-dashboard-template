"""Authority-file and source-to-argument controls for the book project."""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lib import core, report_paths
from tools.lib.card_id_resolver import load_resolver
from tools.lib.chain_role_aliases import VALID_ARGUMENT_ROLES, legacy_role_axes

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

AUTHORITY_DIR = ROOT / "authority"
AUTHORITY_MANIFEST_PATH = AUTHORITY_DIR / "authority.yaml"
ENTITIES_PATH = AUTHORITY_DIR / "entities.csv"
TERMS_PATH = AUTHORITY_DIR / "terms.csv"
SOURCES_PATH = AUTHORITY_DIR / "sources.csv"
MATRIX_PATH = ROOT / "argument_matrix.csv"
CATALOG_PATH = ROOT / "research_catalog.csv"
SOURCE_SNIPPETS_PATH = ROOT / "source_snippets.yaml"
ARGUMENT_CHAINS_PATH = ROOT / "argument_chains.yaml"
TICKETS_PATH = ROOT / "RESEARCH_TICKETS.md"
TICKETS_JSON_PATH = ROOT / "RESEARCH_TICKETS.json"
IMPACT_REPORT_PATH = ROOT / "CLAIM_DEPENDENCY_REPORT.md"
IMPACT_REPORT_JSON_PATH = ROOT / "CLAIM_DEPENDENCY_REPORT.json"

ENTITY_FIELDS = ["entity_id", "type", "canonical_label", "variants", "chapters", "status", "cautions", "notes"]
TERM_FIELDS = ["term_id", "canonical_label", "variants", "romanization", "chapters", "status", "cautions", "notes"]
SOURCE_FIELDS = ["source_id", "source_type", "title", "date_range", "authority_entities", "report_files", "status", "cautions", "notes"]
MATRIX_FIELDS = ["claim_id", "chapter", "claim", "claim_type", "source_ids", "report_files", "entity_ids", "term_ids", "depends_on", "strength", "status", "risk_level", "integration_target", "ticket_action", "notes"]

AUTHORITY_MANIFEST = core.read_yaml(AUTHORITY_MANIFEST_PATH)
if not isinstance(AUTHORITY_MANIFEST, dict):
    AUTHORITY_MANIFEST = {}


def manifest_mapping_keys(key: str) -> set[str]:
    value = AUTHORITY_MANIFEST.get(key, {})
    if not isinstance(value, dict):
        return set()
    return {str(item) for item in value}


def manifest_list_values(key: str) -> set[str]:
    value = AUTHORITY_MANIFEST.get(key, [])
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value}


VALID_CHAPTERS = manifest_list_values("canonical_chapters")
VALID_CLAIM_TYPES = manifest_mapping_keys("claim_types")
VALID_STRENGTHS = manifest_mapping_keys("strength_scale")
VALID_STATUSES = manifest_mapping_keys("epistemic_statuses")
VALID_RISKS = manifest_mapping_keys("risk_scale")
TICKET_STRENGTHS = {"weak", "unknown"}
TICKET_STATUSES = {"needs_verification", "draft", "correction_risk"}
TICKET_RISKS = {"high", "critical"}
VALID_CHAIN_STATUSES = {"simulation", "hybrid", "approved", "drafted"}
VALID_PROSE_POLICIES = {"quote_directly", "paraphrase", "background_context", "footnote_only", "do_not_use_directly"}
MISSING_CARD_ID = "MISSING_CARD"
REQUIRED_AUTHORITY_MANIFEST_KEYS = [
    "schema_version", "constitution", "authority_layers", "historical_philosophy",
    "epistemic_statuses", "strength_scale", "risk_scale", "claim_types",
    "canonical_chapters", "claim_rules", "dependency_doctrine", "ticket_doctrine",
    "agent_contract",
]
CARD_REPORT_REFERENCE_FIELDS = ["provenance_report", "derived_from_reports", "informed_by_reports", "report_files"]


@dataclass
class ControlData:
    entities: list[dict[str, str]]
    terms: list[dict[str, str]]
    sources: list[dict[str, str]]
    claims: list[dict[str, str]]


def load_data() -> tuple[ControlData, list[str]]:
    errors: list[str] = []
    entities, e_err = core.read_csv(ENTITIES_PATH, ENTITY_FIELDS)
    terms, t_err = core.read_csv(TERMS_PATH, TERM_FIELDS)
    sources, s_err = core.read_csv(SOURCES_PATH, SOURCE_FIELDS)
    claims, c_err = core.read_csv(MATRIX_PATH, MATRIX_FIELDS)
    errors.extend(e_err + t_err + s_err + c_err)
    return ControlData(entities, terms, sources, claims), errors


def validate_manifest(errors: list[str]) -> None:
    if not AUTHORITY_MANIFEST_PATH.exists():
        errors.append("Missing file: authority/authority.yaml")
        return
    manifest = AUTHORITY_MANIFEST
    if not isinstance(manifest, dict):
        errors.append("authority/authority.yaml could not be parsed as a YAML mapping.")
        return

    for key in REQUIRED_AUTHORITY_MANIFEST_KEYS:
        if key not in manifest:
            errors.append(f"authority/authority.yaml missing required key: {key}")

    def check_mapping(yaml_key: str) -> None:
        value = manifest.get(yaml_key)
        if not isinstance(value, dict):
            errors.append(f"authority/authority.yaml {yaml_key} must be a mapping.")
            return
        if not value:
            errors.append(f"authority/authority.yaml {yaml_key} must not be empty.")

    def check_list(yaml_key: str) -> None:
        value = manifest.get(yaml_key)
        if not isinstance(value, list):
            errors.append(f"authority/authority.yaml {yaml_key} must be a list.")
            return
        if not value:
            errors.append(f"authority/authority.yaml {yaml_key} must not be empty.")

    check_mapping("epistemic_statuses")
    check_mapping("strength_scale")
    check_mapping("risk_scale")
    check_mapping("claim_types")
    check_list("canonical_chapters")

    claim_rules = manifest.get("claim_rules")
    if isinstance(claim_rules, dict):
        load_bearing = claim_rules.get("load_bearing_claims", {})
        minimum_fields = load_bearing.get("minimum_fields", []) if isinstance(load_bearing, dict) else []
        if "claim_type" not in minimum_fields:
            errors.append("authority/authority.yaml claim_rules.load_bearing_claims.minimum_fields missing claim_type")


def id_set(rows: list[dict[str, str]], key: str, errors: list[str], label: str) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        value = row.get(key, "").strip()
        if not value:
            errors.append(f"{label} row missing {key}.")
            continue
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        errors.append(f"Duplicate {label} IDs: {', '.join(sorted(duplicates))}")
    return seen


def check_refs(row: dict[str, str], field: str, valid_ids: set[str], errors: list[str], owner_field: str) -> None:
    owner = row.get(owner_field, "<unknown>")
    for ref in core.split_values(row.get(field, "")):
        if ref not in valid_ids:
            errors.append(f"{owner} references unknown {field}: {ref}")


def check_report_files(row: dict[str, str], field: str, errors: list[str], owner_field: str) -> None:
    owner = row.get(owner_field, "<unknown>")
    for ref in core.split_values(row.get(field, "")):
        if not report_paths.resolve(ROOT, ref):
            errors.append(f"{owner} references missing report file: {ref}")


def as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [as_text(item) for item in value if as_text(item)]
    text = as_text(value)
    if not text:
        return []
    return core.split_values(text) if ";" in text else [text]


def rel(path: Path) -> str:
    return core.nfc(path.relative_to(ROOT).as_posix())


def load_catalog_files() -> tuple[set[str], list[str]]:
    rows, errors = core.read_csv(CATALOG_PATH)
    return report_paths.alias_set([as_text(row.get("file")) for row in rows if row.get("file")]), errors


def catalog_drift_warnings() -> tuple[list[str], list[str]]:
    catalog_files, errors = load_catalog_files()
    if errors:
        return [], errors

    missing: dict[str, list[str]] = {}
    cards_dir = ROOT / "cards"
    if not cards_dir.exists():
        return [], []

    for path in cards_dir.glob("**/*.md"):
        metadata, _body, parse_errors = core.read_markdown_card(path)
        if parse_errors:
            continue
        owner = rel(path)
        for field in CARD_REPORT_REFERENCE_FIELDS:
            for report_file in as_list(metadata.get(field)):
                if report_file and report_file not in catalog_files:
                    missing.setdefault(report_file, []).append(f"{owner}:{field}")

    warnings: list[str] = []
    for report_file, owners in sorted(missing.items()):
        preview = "; ".join(sorted(owners)[:8])
        if len(owners) > 8:
            preview += f"; +{len(owners) - 8} more"
        warnings.append(
            f"catalog drift: {report_file} is referenced by {len(owners)} card field(s) "
            f"but has no research_catalog.csv row: {preview}"
        )
    return warnings, []


def chain_item_axes(item: dict[str, Any]) -> tuple[str, str]:
    role, prose_policy, _warnings = legacy_role_axes(item)
    return role, prose_policy


def load_card_index() -> dict[str, dict[str, Any]]:
    cards: dict[str, dict[str, Any]] = {}
    cards_dir = ROOT / "cards"
    if not cards_dir.exists():
        return cards
    for path in cards_dir.glob("**/*.md"):
        metadata, _body, parse_errors = core.read_markdown_card(path)
        if parse_errors:
            continue
        card_id = str(metadata.get("id", "")).strip()
        if card_id:
            cards[card_id] = metadata
    return cards


def load_snippet_index(errors: list[str]) -> dict[str, dict[str, Any]]:
    raw = core.read_yaml(SOURCE_SNIPPETS_PATH)
    if raw is None:
        errors.append("Missing file: source_snippets.yaml")
        return {}
    if not isinstance(raw, dict):
        errors.append("source_snippets.yaml must be a YAML mapping.")
        return {}
    snippets = raw.get("snippets", [])
    if not isinstance(snippets, list):
        errors.append("source_snippets.yaml snippets must be a list.")
        return {}
    by_id: dict[str, dict[str, Any]] = {}
    for index, snippet in enumerate(snippets, 1):
        if not isinstance(snippet, dict):
            errors.append(f"source_snippets.yaml snippet #{index} must be a mapping.")
            continue
        snippet_id = str(snippet.get("snippet_id", "")).strip()
        if not snippet_id:
            errors.append(f"source_snippets.yaml snippet #{index} missing snippet_id.")
            continue
        by_id[snippet_id] = snippet
    return by_id


def validate_argument_chains(errors: list[str], warnings: list[str]) -> int:
    raw = core.read_yaml(ARGUMENT_CHAINS_PATH)
    if raw is None:
        errors.append("Missing file: argument_chains.yaml")
        return 0
    if not isinstance(raw, dict):
        errors.append("argument_chains.yaml must be a YAML mapping.")
        return 0
    chains = raw.get("chains", [])
    if not isinstance(chains, list):
        errors.append("argument_chains.yaml chains must be a list.")
        return 0

    snippets_by_id = load_snippet_index(errors)
    cards_by_id = load_card_index()
    resolver = load_resolver(ROOT, set(cards_by_id))
    seen: set[str] = set()
    for chain_index, chain in enumerate(chains, 1):
        if not isinstance(chain, dict):
            errors.append(f"argument_chains.yaml chain #{chain_index} must be a mapping.")
            continue
        chain_id = str(chain.get("chain_id") or chain.get("id") or "").strip()
        owner = chain_id or f"argument_chains.yaml chain #{chain_index}"
        if not chain_id:
            errors.append(f"{owner} missing chain_id.")
        elif chain_id in seen:
            errors.append(f"Duplicate argument chain ID: {chain_id}")
        seen.add(chain_id)

        status = str(chain.get("status", "simulation") or "simulation")
        if status not in VALID_CHAIN_STATUSES:
            errors.append(f"{owner} has invalid status: {status}")
        if not chain.get("title"):
            warnings.append(f"{owner} has no title.")
        if chain_id and not chain.get("source_agent"):
            warnings.append(f"{owner} has no source_agent.")
        if chain_id and not chain.get("chapter"):
            warnings.append(f"{owner} has no chapter.")

        raw_items = chain.get("items", chain.get("cards", []))
        if not isinstance(raw_items, list):
            errors.append(f"{owner} items must be a list.")
            continue
        if not raw_items:
            warnings.append(f"{owner} has no chain items.")

        for item_index, item in enumerate(raw_items, 1):
            item_owner = f"{owner} item #{item_index}"
            if not isinstance(item, dict):
                errors.append(f"{item_owner} must be a mapping.")
                continue
            missing_evidence = str(item.get("missing_evidence_needed", "") or "").strip()
            cited_card_ids = [str(value).strip() for value in item.get("cited_card_ids", []) if str(value).strip()] if isinstance(item.get("cited_card_ids"), list) else []
            snippet_id = str(item.get("snippet_id", "") or item.get("card_id", "") or "").strip()
            if snippet_id and snippet_id not in cited_card_ids:
                cited_card_ids.append(snippet_id)
            if not cited_card_ids and missing_evidence:
                cited_card_ids = [MISSING_CARD_ID]
            if not cited_card_ids:
                errors.append(f"{item_owner} missing cited_card_ids.")
                continue

            argument_role, prose_policy = chain_item_axes(item)
            if "role" in item and ("argument_role" not in item or "prose_policy" not in item):
                warnings.append(f"{item_owner} uses legacy role; export with argument_role and prose_policy.")
            if argument_role not in VALID_ARGUMENT_ROLES:
                errors.append(f"{item_owner} has invalid argument_role: {argument_role}")
            if prose_policy not in VALID_PROSE_POLICIES:
                errors.append(f"{item_owner} has invalid prose_policy: {prose_policy}")

            if MISSING_CARD_ID in cited_card_ids:
                if not missing_evidence:
                    errors.append(f"{item_owner} uses MISSING_CARD without missing_evidence_needed.")
                if status in {"approved", "drafted"}:
                    errors.append(f"{owner} cannot be {status} while it contains MISSING_CARD.")
                continue

            for card_id in cited_card_ids:
                raw_card_id = card_id
                resolved = resolver.resolve(raw_card_id)
                if resolved.get("canonical"):
                    card_id = str(resolved["canonical"])
                elif raw_card_id.startswith("snip:") and status in {"approved", "drafted"}:
                    errors.append(f"{item_owner} references legacy snip id while {status}: {raw_card_id}")
                    continue
                card = cards_by_id.get(card_id)
                snippet = snippets_by_id.get(card_id)
                if not card and not snippet:
                    errors.append(f"{item_owner} references unknown card id: {card_id}")
                    continue
                metadata = card or snippet or {}
                if status in {"approved", "drafted"}:
                    citation_status = str(metadata.get("citation_status", ""))
                    risk_level = str(metadata.get("risk_level", ""))
                    if citation_status and citation_status != "print_ready":
                        warnings.append(f"{owner} is {status} but uses non-print-ready card: {card_id} ({citation_status})")
                    if risk_level in {"high", "critical"} and not item.get("override_rationale"):
                        warnings.append(f"{owner} is {status} but uses high-risk card: {card_id} ({risk_level})")
    return len(chains)


def argument_chain_count() -> int:
    raw = core.read_yaml(ARGUMENT_CHAINS_PATH)
    if not isinstance(raw, dict):
        return 0
    chains = raw.get("chains", [])
    return len(chains) if isinstance(chains, list) else 0


def ticket_needed(row: dict[str, str]) -> bool:
    return (row.get("strength") in TICKET_STRENGTHS or row.get("status") in TICKET_STATUSES or row.get("risk_level") in TICKET_RISKS)


def priority(row: dict[str, str]) -> str:
    if row.get("risk_level") == "critical":
        return "CRITICAL"
    if row.get("risk_level") == "high":
        return "HIGH"
    if row.get("strength") in TICKET_STRENGTHS:
        return "MEDIUM"
    return "LOW"


def dependency_edges(claims: list[dict[str, str]]) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    for claim in claims:
        claim_id = claim.get("claim_id", "")
        for dependency in core.split_values(claim.get("depends_on", "")):
            edges.append((dependency, claim_id))
    return edges


def downstream_index(claims: list[dict[str, str]]) -> dict[str, set[str]]:
    downstream: dict[str, set[str]] = {}
    for dependency, dependent in dependency_edges(claims):
        downstream.setdefault(dependency, set()).add(dependent)
    return downstream


def transitive_downstream(claim_id: str, downstream: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(downstream.get(claim_id, set()))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(sorted(downstream.get(current, set())))
    return seen


def cycle_paths(claims: list[dict[str, str]]) -> list[list[str]]:
    claim_ids = {claim.get("claim_id", "") for claim in claims if claim.get("claim_id")}
    dependency_map = {claim.get("claim_id", ""): [dep for dep in core.split_values(claim.get("depends_on", "")) if dep in claim_ids] for claim in claims}
    cycles: list[list[str]] = []
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(claim_id: str) -> None:
        if claim_id in visiting:
            cycles.append(visiting[visiting.index(claim_id):] + [claim_id])
            return
        if claim_id in visited:
            return
        visiting.append(claim_id)
        for dependency in dependency_map.get(claim_id, []):
            visit(dependency)
        visiting.pop()
        visited.add(claim_id)

    for claim_id in sorted(claim_ids):
        visit(claim_id)
    return cycles


def validate_data(data: ControlData) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    validate_manifest(errors)

    entity_ids = id_set(data.entities, "entity_id", errors, "entity")
    term_ids = id_set(data.terms, "term_id", errors, "term")
    source_ids = id_set(data.sources, "source_id", errors, "source")
    claim_ids = id_set(data.claims, "claim_id", errors, "claim")

    for source in data.sources:
        check_refs(source, "authority_entities", entity_ids, errors, "source_id")
        check_report_files(source, "report_files", errors, "source_id")
        if source.get("status") not in VALID_STATUSES:
            errors.append(f"{source.get('source_id')} has invalid status: {source.get('status')}")
        if source.get("status") == "needs_verification" and not source.get("cautions"):
            warnings.append(f"{source.get('source_id')} needs verification but has no caution note.")

    for claim in data.claims:
        check_refs(claim, "source_ids", source_ids, errors, "claim_id")
        check_refs(claim, "entity_ids", entity_ids, errors, "claim_id")
        check_refs(claim, "term_ids", term_ids, errors, "claim_id")
        check_refs(claim, "depends_on", claim_ids, errors, "claim_id")
        check_report_files(claim, "report_files", errors, "claim_id")

        if claim.get("chapter") and claim.get("chapter") not in VALID_CHAPTERS:
            errors.append(f"{claim.get('claim_id')} has invalid chapter: {claim.get('chapter')}")
        if claim.get("claim_type") and claim.get("claim_type") not in VALID_CLAIM_TYPES:
            errors.append(f"{claim.get('claim_id')} has invalid claim_type: {claim.get('claim_type')}")
        if claim.get("strength") not in VALID_STRENGTHS:
            errors.append(f"{claim.get('claim_id')} has invalid strength: {claim.get('strength')}")
        if claim.get("status") not in VALID_STATUSES:
            errors.append(f"{claim.get('claim_id')} has invalid status: {claim.get('status')}")
        if claim.get("risk_level") not in VALID_RISKS:
            errors.append(f"{claim.get('claim_id')} has invalid risk_level: {claim.get('risk_level')}")
        if ticket_needed(claim) and not claim.get("ticket_action"):
            warnings.append(f"{claim.get('claim_id')} needs a ticket action.")
        if claim.get("status") == "stable" and claim.get("strength") in TICKET_STRENGTHS:
            warnings.append(f"{claim.get('claim_id')} is stable but has weak/unknown evidence.")

    cycles = cycle_paths(data.claims)
    if cycles:
        for cycle in cycles:
            errors.append("Dependency cycle detected: " + " -> ".join(cycle))

    validate_argument_chains(errors, warnings)

    return errors, warnings


def validate(check_catalog: bool = False) -> int:
    data, load_errors = load_data()
    errors, warnings = validate_data(data)
    errors = load_errors + errors
    catalog_warnings: list[str] = []
    if check_catalog:
        catalog_warnings, catalog_errors = catalog_drift_warnings()
        warnings.extend(catalog_warnings)
        errors.extend(catalog_errors)

    print(f"Entities: {len(data.entities)}")
    print(f"Terms: {len(data.terms)}")
    print(f"Sources: {len(data.sources)}")
    print(f"Claims: {len(data.claims)}")
    print(f"Argument chains: {argument_chain_count()}")
    if check_catalog and not catalog_warnings:
        print("Catalog drift: none")

    if warnings:
        print("\nWARNINGS")
        for warning in warnings:
            print(f"- {warning}")

    if errors:
        print("\nERRORS")
        for error in errors:
            print(f"- {error}")
        return 1

    print("\nOK: authority files and source-to-argument matrix are structurally consistent.")
    return 0


def write_tickets() -> int:
    data, load_errors = load_data()
    errors, warnings = validate_data(data)
    errors = load_errors + errors
    if errors:
        print("ERROR: cannot generate tickets until validation errors are fixed.")
        for error in errors:
            print(f"- {error}")
        return 1

    sortable = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    ticket_rows = sorted(
        [claim for claim in data.claims if ticket_needed(claim)],
        key=lambda row: (sortable[priority(row)], row.get("chapter", ""), row.get("claim_id", "")),
    )

    lines = [
        "# Research Tickets", "",
        "Generated from current backend state.", "",
        "These tickets are generated from `argument_matrix.csv`. Edit the matrix first, then regenerate this file.", "",
    ]

    json_payload = []
    if not ticket_rows:
        lines.append("No open research-integrity tickets.")
    else:
        for number, row in enumerate(ticket_rows, 1):
            t_num = f"TICKET-{number:03d}"
            lines.extend([
                f"## {t_num} [{priority(row)}] {row.get('claim_id')}", "",
                f"**Chapter:** {row.get('chapter')}",
                f"**Strength / Status / Risk:** {row.get('strength')} / {row.get('status')} / {row.get('risk_level')}",
                f"**Claim:** {row.get('claim')}",
                f"**Action:** {row.get('ticket_action') or 'Add a concrete ticket_action in argument_matrix.csv.'}",
                f"**Sources:** {row.get('source_ids')}",
                f"**Reports:** {row.get('report_files')}",
                f"**Depends on:** {row.get('depends_on') or 'None'}", "",
            ])
            json_payload.append({
                "number": t_num,
                "priority": priority(row),
                "claim_id": row.get("claim_id"),
                "chapter": row.get("chapter"),
                "strength": row.get("strength"),
                "status": row.get("status"),
                "risk_level": row.get("risk_level"),
                "claim": row.get("claim"),
                "action": row.get("ticket_action") or "",
                "source_ids": core.split_values(row.get("source_ids", "")),
                "report_files": core.split_values(row.get("report_files", "")),
                "depends_on": core.split_values(row.get("depends_on", "")),
            })

    while lines and lines[-1] == "":
        lines.pop()
    TICKETS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    core.write_json(TICKETS_JSON_PATH, json_payload)
    print(f"Wrote {TICKETS_PATH.name} and JSON sidecar with {len(ticket_rows)} tickets.")
    if warnings:
        print("\nWARNINGS")
        for warning in warnings:
            print(f"- {warning}")
    return 0


def mermaid_id(claim_id: str) -> str:
    return claim_id.replace(":", "_").replace("-", "_")


def write_impact_report() -> int:
    data, load_errors = load_data()
    errors, warnings = validate_data(data)
    errors = load_errors + errors
    if errors:
        print("ERROR: cannot generate impact report until validation errors are fixed.")
        for error in errors:
            print(f"- {error}")
        return 1
    if warnings:
        print("\nWARNINGS")
        for warning in warnings:
            print(f"- {warning}")

    claims_by_id = {claim.get("claim_id", ""): claim for claim in data.claims if claim.get("claim_id")}
    edges = dependency_edges(data.claims)
    downstream = downstream_index(data.claims)
    claims_with_dependencies = [claim for claim in data.claims if core.split_values(claim.get("depends_on", ""))]
    claims_with_downstream = sorted(downstream)
    blocked_claims = [
        claim for claim in claims_with_dependencies
        if any(ticket_needed(claims_by_id.get(dep, {})) for dep in core.split_values(claim.get("depends_on", "")))
    ]

    hotspot_rows: list[list[str]] = []
    for claim_id in claims_with_downstream:
        claim = claims_by_id.get(claim_id, {})
        direct = sorted(downstream.get(claim_id, set()))
        transitive = sorted(transitive_downstream(claim_id, downstream))
        hotspot_rows.append([
            claim_id,
            f"{claim.get('strength', '')} / {claim.get('status', '')} / {claim.get('risk_level', '')}",
            priority(claim) if ticket_needed(claim) else "None",
            str(len(direct)),
            str(len(transitive)),
            "; ".join(transitive),
        ])
    hotspot_rows.sort(key=lambda row: (-int(row[4]), row[0]))

    blocked_rows = [
        [
            claim.get("claim_id", ""),
            claim.get("chapter", ""),
            claim.get("risk_level", ""),
            "; ".join(dep for dep in core.split_values(claim.get("depends_on", "")) if ticket_needed(claims_by_id.get(dep, {}))),
            claim.get("ticket_action", ""),
        ] for claim in blocked_claims
    ]

    edge_rows = [
        [
            dependency,
            dependent,
            claims_by_id.get(dependency, {}).get("chapter", ""),
            claims_by_id.get(dependent, {}).get("chapter", ""),
        ] for dependency, dependent in sorted(edges)
    ]

    lines = [
        "# Claim Dependency Report", "",
        "Generated from current backend state.", "",
        "This report turns `argument_matrix.csv:depends_on` into a cascading-impact view. Edit the matrix first, then regenerate.", "",
        "## Summary", "",
        f"- Claims: {len(data.claims)}",
        f"- Dependency edges: {len(edges)}",
        f"- Claims with downstream dependents: {len(claims_with_downstream)}",
        f"- Claims currently depending on ticketed upstream claims: {len(blocked_claims)}", "",
        "## Cascading Hotspots", "",
    ]
    lines.extend(core.markdown_table(["Upstream Claim", "Strength / Status / Risk", "Ticket Priority", "Direct", "Transitive", "Impacted Claims"], hotspot_rows))

    lines.extend(["", "## Blocked Or Shaky Downstream Claims", ""])
    lines.extend(core.markdown_table(["Claim", "Chapter", "Risk", "Ticketed Dependencies", "Action"], blocked_rows))

    lines.extend(["", "## Dependency Edges", ""])
    lines.extend(core.markdown_table(["Dependency", "Dependent", "Dependency Chapter", "Dependent Chapter"], edge_rows))

    lines.extend(["", "## Mermaid Graph", "", "```mermaid", "flowchart TD"])
    if not edges:
        lines.append('  none["No claim dependencies recorded"]')
    else:
        edge_claims = sorted({item for edge in edges for item in edge})
        for claim_id in edge_claims:
            claim = claims_by_id.get(claim_id, {})
            label = f"{claim_id}\\n{claim.get('chapter', '')}\\n{claim.get('strength', '')}/{claim.get('status', '')}/{claim.get('risk_level', '')}"
            lines.append(f'  {mermaid_id(claim_id)}["{label}"]')
        for dependency, dependent in sorted(edges):
            lines.append(f"  {mermaid_id(dependency)} --> {mermaid_id(dependent)}")
    lines.extend(["```", ""])

    json_payload = {
        "hotspots": [{"claim_id": r[0], "status": r[1], "priority": r[2], "direct": int(r[3]), "transitive": int(r[4]), "impacted": core.split_values(r[5])} for r in hotspot_rows],
        "blocked": [{"claim_id": r[0], "chapter": r[1], "risk": r[2], "dependencies": core.split_values(r[3]), "action": r[4]} for r in blocked_rows],
        "edges": [{"dependency": r[0], "dependent": r[1], "dependency_chapter": r[2], "dependent_chapter": r[3]} for r in edge_rows],
    }

    while lines and lines[-1] == "":
        lines.pop()
    IMPACT_REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    core.write_json(IMPACT_REPORT_JSON_PATH, json_payload)
    print(f"Wrote {IMPACT_REPORT_PATH.name} and JSON sidecar with {len(edges)} dependency edges.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate truth-control authority files and claim matrix.")
    sub = parser.add_subparsers(dest="command", required=True)
    validate_parser = sub.add_parser("validate", help="Validate authority files and source-to-argument matrix.")
    validate_parser.add_argument(
        "--check-catalog",
        action="store_true",
        help="Report card provenance_report/report_files references missing from research_catalog.csv.",
    )
    sub.add_parser("tickets", help="Regenerate RESEARCH_TICKETS.md from risky matrix rows.")
    sub.add_parser("impact", help="Regenerate CLAIM_DEPENDENCY_REPORT.md from claim dependencies.")
    args = parser.parse_args(argv)

    if args.command == "validate":
        return validate(check_catalog=args.check_catalog)
    if args.command == "tickets":
        return write_tickets()
    if args.command == "impact":
        return write_impact_report()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
