"""Generate evidence packs from the authority files and argument matrix."""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lib import core

AUTHORITY_DIR = ROOT / "authority"
EVIDENCE_DIR = ROOT / "evidence_packs"
CHAPTER_DIR = EVIDENCE_DIR / "chapters"
CLAIM_DIR = EVIDENCE_DIR / "claims"

MATRIX_PATH = ROOT / "argument_matrix.csv"
ENTITIES_PATH = AUTHORITY_DIR / "entities.csv"
TERMS_PATH = AUTHORITY_DIR / "terms.csv"
SOURCES_PATH = AUTHORITY_DIR / "sources.csv"
CATALOG_PATH = ROOT / "research_catalog.csv"

TICKETS_JSON_PATH = ROOT / "RESEARCH_TICKETS.json"
AUDIT_JSON_PATH = ROOT / "MANUSCRIPT_RISK_AUDIT.json"

CHAPTER_FILES = {
    "Introduction": "Intro_Evidence_Pack.md",
    "Ch1": "Ch1_Ugaki_Evidence_Pack.md",
    "Ch3": "Ch3_Naisen_Hakko_Evidence_Pack.md",
    "Ch4": "Ch4_Visuality_Evidence_Pack.md",
    "Ch5": "Ch5_Sincerity_Evidence_Pack.md",
    "Ch6": "Ch6_Rensei_Yamato_Juku_Evidence_Pack.md",
    "Ch7": "Ch7_Empire_of_Hunger_Evidence_Pack.md",
    "Epilogue": "Epilogue_Evidence_Pack.md",
    "Deleted Ch2 context": "Deleted_Ch2_Context_Evidence_Pack.md",
}

CHAPTER_LABELS = {
    "Introduction": "Introduction",
    "Ch1": "Chapter 1 - Ugaki / Phase Zero",
    "Ch3": "Chapter 3 - Naisen Ittai / Hakko Ichiu",
    "Ch4": "Chapter 4 - Fascist Visuality",
    "Ch5": "Chapter 5 - Sincerity / Homefront / Buyeo",
    "Ch6": "Chapter 6 - Tenko / Rensei / Yamato Juku",
    "Ch7": "Chapter 7 - Empire of Hunger",
    "Epilogue": "Epilogue",
    "Deleted Ch2 context": "Deleted Ch. 2 Context - Mindo / Ontological Drift",
}

CHAPTER_ORDER = list(CHAPTER_FILES)
RISK_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "": 4}
PRIORITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "": 4}


@dataclass(frozen=True)
class Ticket:
    number: str
    priority: str
    action: str


@dataclass(frozen=True)
class AuditHit:
    severity: str
    rule: str
    part: str
    paragraph: str
    section: str
    claim_id: str
    ticket: str
    fix: str
    context: str


@dataclass
class EvidenceData:
    claims: list[dict[str, str]]
    entities: dict[str, dict[str, str]]
    terms: dict[str, dict[str, str]]
    sources: dict[str, dict[str, str]]
    catalog: dict[str, dict[str, str]]
    tickets: dict[str, list[Ticket]]
    audit_hits: dict[str, list[AuditHit]]
    downstream: dict[str, list[str]]


def slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return cleaned or "item"


def chapter_filename(chapter: str) -> str:
    return CHAPTER_FILES.get(chapter, f"{slug(chapter)}_Evidence_Pack.md")


def claim_filename(claim_id: str) -> str:
    return f"{slug(claim_id)}.md"


def claim_sort_key(claim: dict[str, str]) -> tuple[int, int, str]:
    chapter = claim.get("chapter", "")
    chapter_rank = CHAPTER_ORDER.index(chapter) if chapter in CHAPTER_ORDER else len(CHAPTER_ORDER)
    return (chapter_rank, RISK_ORDER.get(claim.get("risk_level", ""), 4), claim.get("claim_id", ""))


def read_tickets() -> dict[str, list[Ticket]]:
    data = core.read_json(TICKETS_JSON_PATH)
    if not data:
        return {}
    by_claim: dict[str, list[Ticket]] = defaultdict(list)
    for t in data:
        by_claim[t["claim_id"]].append(Ticket(t["number"], t["priority"], t.get("action", "")))
    return dict(by_claim)


def read_audit_hits() -> dict[str, list[AuditHit]]:
    data = core.read_json(AUDIT_JSON_PATH)
    if not data:
        return {}
    hits: dict[str, list[AuditHit]] = defaultdict(list)
    for h in data:
        hits[h["claim_id"]].append(AuditHit(
            severity=h["severity"],
            rule=h["rule"],
            part=h["part"],
            paragraph=str(h["paragraph"]),
            section=h["section"],
            claim_id=h["claim_id"],
            ticket=h.get("ticket", ""),
            fix=h["fix"],
            context=h["context"],
        ))
    return dict(hits)


def build_downstream(claims: list[dict[str, str]]) -> dict[str, list[str]]:
    downstream: dict[str, list[str]] = defaultdict(list)
    for claim in claims:
        claim_id = claim.get("claim_id", "")
        for dependency in core.split_values(claim.get("depends_on", "")):
            downstream[dependency].append(claim_id)
    return {claim_id: sorted(values) for claim_id, values in downstream.items()}


def load_data() -> EvidenceData:
    claims_raw, _ = core.read_csv(MATRIX_PATH)
    claims = sorted(claims_raw, key=claim_sort_key)
    entities_raw, _ = core.read_csv(ENTITIES_PATH)
    terms_raw, _ = core.read_csv(TERMS_PATH)
    sources_raw, _ = core.read_csv(SOURCES_PATH)
    catalog_raw, _ = core.read_csv(CATALOG_PATH)

    return EvidenceData(
        claims=claims,
        entities=core.row_map(entities_raw, "entity_id"),
        terms=core.row_map(terms_raw, "term_id"),
        sources=core.row_map(sources_raw, "source_id"),
        catalog=core.row_map(catalog_raw, "file"),
        tickets=read_tickets(),
        audit_hits=read_audit_hits(),
        downstream=build_downstream(claims),
    )


def claim_ids(claims: list[dict[str, str]]) -> list[str]:
    return [claim.get("claim_id", "") for claim in claims if claim.get("claim_id")]


def collect_claim_field(claims: list[dict[str, str]], field: str) -> list[str]:
    values: list[str] = []
    for claim in claims:
        values.extend(core.split_values(claim.get(field, "")))
    return core.unique(values)


def collect_reports(claims: list[dict[str, str]], data: EvidenceData) -> list[str]:
    reports = collect_claim_field(claims, "report_files")
    for source_id in collect_claim_field(claims, "source_ids"):
        reports.extend(core.split_values(data.sources.get(source_id, {}).get("report_files", "")))
    return core.unique(reports)


def ticket_label(claim_id: str, data: EvidenceData) -> str:
    tickets = data.tickets.get(claim_id, [])
    if not tickets:
        return "None"
    return "; ".join(f"{ticket.number} [{ticket.priority}]" for ticket in tickets)


def audit_label(claim_id: str, data: EvidenceData) -> str:
    hits = data.audit_hits.get(claim_id, [])
    if not hits:
        return "None"
    severities = ", ".join(sorted({hit.severity for hit in hits}, key=lambda item: RISK_ORDER.get(item, 4)))
    return f"{len(hits)} hit(s): {severities}"


def ticket_action(claim_id: str, data: EvidenceData) -> str:
    return "; ".join(ticket.action for ticket in data.tickets.get(claim_id, []) if ticket.action) or "None"


def risk_counts(claims: list[dict[str, str]]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for claim in claims:
        counts[claim.get("risk_level", "")] += 1
    parts = []
    for risk in ["critical", "high", "medium", "low"]:
        if counts.get(risk):
            parts.append(f"{risk}: {counts[risk]}")
    return "; ".join(parts) or "none"


def source_rows(claims: list[dict[str, str]], data: EvidenceData) -> list[list[str]]:
    rows: list[list[str]] = []
    ids = collect_claim_field(claims, "source_ids")
    for source_id in ids:
        source = data.sources.get(source_id, {})
        using_claims = [c.get("claim_id", "") for c in claims if source_id in core.split_values(c.get("source_ids", ""))]
        rows.append([
            source_id,
            source.get("title", "UNKNOWN"),
            f"{source.get('source_type', '')} / {source.get('status', '')}",
            "; ".join(using_claims),
            source.get("report_files", ""),
            source.get("cautions", ""),
        ])
    return rows


def report_rows(claims: list[dict[str, str]], data: EvidenceData) -> list[list[str]]:
    rows: list[list[str]] = []
    for report in collect_reports(claims, data):
        catalog = data.catalog.get(report, {})
        rows.append([
            report,
            catalog.get("chapters", ""),
            catalog.get("topics", ""),
            catalog.get("keywords", ""),
            catalog.get("cautions", ""),
            catalog.get("notes", ""),
        ])
    return rows


def entity_rows(claims: list[dict[str, str]], data: EvidenceData) -> list[list[str]]:
    rows: list[list[str]] = []
    for entity_id in collect_claim_field(claims, "entity_ids"):
        entity = data.entities.get(entity_id, {})
        rows.append([
            entity_id,
            entity.get("canonical_label", "UNKNOWN"),
            entity.get("type", ""),
            entity.get("status", ""),
            entity.get("cautions", ""),
        ])
    return rows


def term_rows(claims: list[dict[str, str]], data: EvidenceData) -> list[list[str]]:
    rows: list[list[str]] = []
    for term_id in collect_claim_field(claims, "term_ids"):
        term = data.terms.get(term_id, {})
        rows.append([
            term_id,
            term.get("canonical_label", "UNKNOWN"),
            term.get("romanization", ""),
            term.get("status", ""),
            term.get("cautions", ""),
        ])
    return rows


def dependency_rows(claims: list[dict[str, str]], data: EvidenceData) -> list[list[str]]:
    rows: list[list[str]] = []
    for claim in claims:
        claim_id = claim.get("claim_id", "")
        rows.append([
            claim_id,
            claim.get("depends_on", "") or "None",
            "; ".join(data.downstream.get(claim_id, [])) or "None",
        ])
    return rows


def ticket_rows(claims: list[dict[str, str]], data: EvidenceData) -> list[list[str]]:
    rows: list[list[str]] = []
    for claim in claims:
        claim_id = claim.get("claim_id", "")
        for ticket in data.tickets.get(claim_id, []):
            rows.append([ticket.number, ticket.priority, claim_id, ticket.action])
    rows.sort(key=lambda row: (PRIORITY_ORDER.get(row[1], 4), row[0]))
    return rows


def audit_rows(claims: list[dict[str, str]], data: EvidenceData) -> list[list[str]]:
    rows: list[list[str]] = []
    for claim in claims:
        for hit in data.audit_hits.get(claim.get("claim_id", ""), []):
            rows.append([hit.severity, hit.rule, hit.part, hit.paragraph, hit.section, hit.claim_id, hit.fix, hit.context])
    rows.sort(key=lambda row: (RISK_ORDER.get(row[0], 4), row[5], int(row[3]) if row[3].isdigit() else 0))
    return rows


def claim_rows(claims: list[dict[str, str]], data: EvidenceData) -> list[list[str]]:
    rows: list[list[str]] = []
    for claim in claims:
        claim_id = claim.get("claim_id", "")
        rows.append([
            claim_id,
            claim.get("claim_type", ""),
            f"{claim.get('strength', '')} / {claim.get('status', '')} / {claim.get('risk_level', '')}",
            claim.get("claim", ""),
            claim.get("integration_target", ""),
            ticket_label(claim_id, data),
            audit_label(claim_id, data),
        ])
    return rows


def render_chapter_pack(chapter: str, claims: list[dict[str, str]], data: EvidenceData) -> str:
    chapter_claim_ids = claim_ids(claims)
    lines = [
        f"# {CHAPTER_LABELS.get(chapter, chapter)} Evidence Pack", "",
        "Generated from current backend state.", "",
        "Use this before drafting or revising. It is metadata, not prose: open the report files for quotations and exact citations.", "",
        "## Snapshot", "",
        f"- Claims: {len(claims)}",
        f"- Sources: {len(collect_claim_field(claims, 'source_ids'))}",
        f"- Reports: {len(collect_reports(claims, data))}",
        f"- Open tickets: {sum(len(data.tickets.get(claim_id, [])) for claim_id in chapter_claim_ids)}",
        f"- Manuscript audit hits: {sum(len(data.audit_hits.get(claim_id, [])) for claim_id in chapter_claim_ids)}",
        f"- Risk mix: {risk_counts(claims)}", "",
        "## Load-Bearing Claims", "",
    ]
    lines.extend(core.markdown_table(["Claim ID", "Type", "Strength / Status / Risk", "Claim", "Target", "Ticket", "Audit Hits"], claim_rows(claims, data)))

    lines.extend(["", "## Evidence Ledger", ""])
    lines.extend(core.markdown_table(["Source ID", "Title", "Type / Status", "Claims", "Report Files", "Cautions"], source_rows(claims, data)))

    lines.extend(["", "## Report Pull List", ""])
    lines.extend(core.markdown_table(["Report", "Chapters", "Topics", "Keywords", "Cautions", "Notes"], report_rows(claims, data)))

    lines.extend(["", "## Authority Entities", ""])
    lines.extend(core.markdown_table(["ID", "Canonical", "Type", "Status", "Cautions"], entity_rows(claims, data)))

    lines.extend(["", "## Authority Terms", ""])
    lines.extend(core.markdown_table(["ID", "Canonical", "Romanization", "Status", "Cautions"], term_rows(claims, data)))

    lines.extend(["", "## Dependencies", ""])
    lines.extend(core.markdown_table(["Claim", "Depends On", "Downstream"], dependency_rows(claims, data)))

    lines.extend(["", "## Open Tickets", ""])
    lines.extend(core.markdown_table(["Ticket", "Priority", "Claim", "Action"], ticket_rows(claims, data)))

    lines.extend(["", "## Manuscript Risk Hits", ""])
    lines.extend(core.markdown_table(["Severity", "Rule", "Part", "Paragraph", "Section", "Claim", "Recommended Fix", "Context"], audit_rows(claims, data)))

    lines.extend([
        "", "## Drafting Gate", "",
        "- Treat correction-risk claims as blockers until the live manuscript audit clears them.",
        "- If a claim has a ticket, keep the prose provisional or resolve the ticket first.",
        "- Use this pack with the matching dossier; the pack proves the chain, the dossier gives the wider context.", "",
        "## Working Commands", "",
        "```powershell",
        "python tools/research_metadata.py refresh",
        "python tools/research_metadata.py validate",
        "python tools/research_truth_control.py validate",
        "python tools/research_truth_control.py tickets",
        "python tools/research_truth_control.py impact",
        "python tools/build_chapter_dossiers.py",
        "python tools/manuscript_risk_audit.py",
        "python tools/build_evidence_packs.py",
        "```", "",
    ])
    return "\n".join(lines).rstrip() + "\n"


def render_claim_pack(claim: dict[str, str], data: EvidenceData) -> str:
    claim_id = claim.get("claim_id", "")
    claims = [claim]
    upstream_ids = core.split_values(claim.get("depends_on", ""))
    downstream_ids = data.downstream.get(claim_id, [])
    claim_by_id = {item.get("claim_id", ""): item for item in data.claims if item.get("claim_id")}

    upstream_claims = [claim_by_id[dep] for dep in upstream_ids if dep in claim_by_id]
    downstream_claims = [claim_by_id[dep] for dep in downstream_ids if dep in claim_by_id]

    lines = [
        f"# {claim_id} Evidence Pack", "",
        "Generated from current backend state.", "",
        "## Claim", "",
    ]
    lines.extend(core.markdown_table(
        ["Chapter", "Type", "Strength", "Status", "Risk", "Integration Target", "Ticket Action"],
        [[claim.get("chapter", ""), claim.get("claim_type", ""), claim.get("strength", ""), claim.get("status", ""), claim.get("risk_level", ""), claim.get("integration_target", ""), claim.get("ticket_action", "")]],
    ))

    lines.extend(["", "## Claim Text", "", claim.get("claim", "") or "None.", ""])

    lines.extend(["## Evidence Sources", ""])
    lines.extend(core.markdown_table(["Source ID", "Title", "Type / Status", "Claims", "Report Files", "Cautions"], source_rows(claims, data)))

    lines.extend(["", "## Report Pull List", ""])
    lines.extend(core.markdown_table(["Report", "Chapters", "Topics", "Keywords", "Cautions", "Notes"], report_rows(claims, data)))

    lines.extend(["", "## Authority Entities", ""])
    lines.extend(core.markdown_table(["ID", "Canonical", "Type", "Status", "Cautions"], entity_rows(claims, data)))

    lines.extend(["", "## Authority Terms", ""])
    lines.extend(core.markdown_table(["ID", "Canonical", "Romanization", "Status", "Cautions"], term_rows(claims, data)))

    lines.extend(["", "## Dependencies And Impact", ""])
    lines.extend(core.markdown_table(
        ["Direction", "Claim ID", "Chapter", "Strength / Status / Risk", "Claim"],
        [
            ["Upstream", c.get("claim_id", ""), c.get("chapter", ""), f"{c.get('strength', '')} / {c.get('status', '')} / {c.get('risk_level', '')}", c.get("claim", "")]
            for c in upstream_claims
        ] + [
            ["Downstream", c.get("claim_id", ""), c.get("chapter", ""), f"{c.get('strength', '')} / {c.get('status', '')} / {c.get('risk_level', '')}", c.get("claim", "")]
            for c in downstream_claims
        ],
    ))

    lines.extend(["", "## Open Tickets", ""])
    lines.extend(core.markdown_table(["Ticket", "Priority", "Claim", "Action"], ticket_rows(claims, data)))

    lines.extend(["", "## Manuscript Risk Hits", ""])
    lines.extend(core.markdown_table(["Severity", "Rule", "Part", "Paragraph", "Section", "Claim", "Recommended Fix", "Context"], audit_rows(claims, data)))

    lines.extend([
        "", "## Evidence Handling", "",
        f"- Ticket status: {ticket_label(claim_id, data)}",
        f"- Audit status: {audit_label(claim_id, data)}",
        f"- Immediate action: {ticket_action(claim_id, data)}",
        "- Do not let fluent prose outrun this evidence chain.", "",
    ])
    return "\n".join(lines).rstrip() + "\n"


def selected_claims(data: EvidenceData, chapters: list[str], claim_ids_filter: list[str]) -> list[dict[str, str]]:
    if claim_ids_filter:
        wanted = set(claim_ids_filter)
        return [claim for claim in data.claims if claim.get("claim_id") in wanted]
    if chapters:
        wanted_chapters = set(chapters)
        return [claim for claim in data.claims if claim.get("chapter") in wanted_chapters]
    return data.claims


def selected_chapters(claims: list[dict[str, str]], chapters: list[str]) -> list[str]:
    if chapters:
        return chapters
    present = {claim.get("chapter", "") for claim in claims if claim.get("chapter")}
    ordered = [chapter for chapter in CHAPTER_ORDER if chapter in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def write_readme(chapter_files: list[str], claim_files: list[str]) -> None:
    lines = [
        "# Evidence Packs", "",
        "Generated from current backend state.", "",
        "Evidence packs are generated from `argument_matrix.csv`, `authority/*.csv`, `research_catalog.csv`, `RESEARCH_TICKETS.json`, and `MANUSCRIPT_RISK_AUDIT.json`.", "",
        "Use them as proof-chain packets before drafting. Do not hand-edit generated packs; edit the matrix, authority files, catalog, tickets source, or audit source, then regenerate.", "",
        "## Chapter Packs", "",
    ]
    lines.extend(f"- `chapters/{file_name}`" for file_name in chapter_files)
    lines.extend(["", "## Claim Packs", ""])
    lines.extend(f"- `claims/{file_name}`" for file_name in claim_files)
    lines.extend([
        "", "## Regenerate", "", "```powershell",
        "python tools/research_metadata.py refresh",
        "python tools/research_metadata.py validate",
        "python tools/research_truth_control.py validate",
        "python tools/manuscript_risk_audit.py",
        "python tools/build_evidence_packs.py",
        "```", "",
    ])
    EVIDENCE_DIR.mkdir(exist_ok=True)
    (EVIDENCE_DIR / "README.md").write_text("\n".join(lines), encoding="utf-8")


def build(chapters: list[str], claim_ids_filter: list[str]) -> int:
    data = load_data()
    claims = selected_claims(data, chapters, claim_ids_filter)
    if not claims:
        print("ERROR: no claims matched the requested evidence-pack scope.")
        return 1

    full_generation = not chapters and not claim_ids_filter
    if full_generation:
        core.safe_clear_generated_dir(CHAPTER_DIR, ROOT)
        core.safe_clear_generated_dir(CLAIM_DIR, ROOT)
    else:
        CHAPTER_DIR.mkdir(parents=True, exist_ok=True)
        CLAIM_DIR.mkdir(parents=True, exist_ok=True)

    by_chapter: dict[str, list[dict[str, str]]] = defaultdict(list)
    for claim in claims:
        by_chapter[claim.get("chapter", "")].append(claim)

    chapter_files: list[str] = []
    for chapter in selected_chapters(claims, chapters):
        chapter_claims = by_chapter.get(chapter, [])
        if not chapter_claims:
            continue
        file_name = chapter_filename(chapter)
        (CHAPTER_DIR / file_name).write_text(render_chapter_pack(chapter, chapter_claims, data), encoding="utf-8")
        chapter_files.append(file_name)

    claim_files: list[str] = []
    for claim in claims:
        file_name = claim_filename(claim.get("claim_id", ""))
        (CLAIM_DIR / file_name).write_text(render_claim_pack(claim, data), encoding="utf-8")
        claim_files.append(file_name)

    write_readme(chapter_files, claim_files)
    print(f"Wrote {len(chapter_files)} chapter packs and {len(claim_files)} claim packs to evidence_packs/")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build generated evidence packs from the research backend.")
    parser.add_argument("--chapter", action="append", default=[], help="Generate packs only for this chapter key, e.g. Ch6.")
    parser.add_argument("--claim", action="append", default=[], help="Generate a pack only for this claim ID.")
    args = parser.parse_args(argv)
    return build(args.chapter, args.claim)


if __name__ == "__main__":
    raise SystemExit(main())
