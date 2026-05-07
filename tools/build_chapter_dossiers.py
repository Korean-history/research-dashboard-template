"""Generate chapter dossiers from the authority files and argument matrix."""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lib import core

DOSSIER_DIR = ROOT / "dossiers"
AUTHORITY_DIR = ROOT / "authority"
MATRIX_PATH = ROOT / "argument_matrix.csv"
ENTITIES_PATH = AUTHORITY_DIR / "entities.csv"
TERMS_PATH = AUTHORITY_DIR / "terms.csv"
SOURCES_PATH = AUTHORITY_DIR / "sources.csv"
AUTHORITY_MANIFEST_PATH = AUTHORITY_DIR / "authority.yaml"

CHAPTER_FILES = {
    "Introduction": "Intro_Dossier.md",
    "Ch1": "Ch1_Ugaki_Dossier.md",
    "Ch3": "Ch3_Naisen_Hakko_Dossier.md",
    "Ch4": "Ch4_Visuality_Dossier.md",
    "Ch5": "Ch5_Sincerity_Dossier.md",
    "Ch6": "Ch6_Rensei_Yamato_Juku_Dossier.md",
    "Ch7": "Ch7_Empire_of_Hunger_Dossier.md",
    "Epilogue": "Epilogue_Dossier.md",
    "Deleted Ch2 context": "Deleted_Ch2_Context_Dossier.md",
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
TICKET_STRENGTHS = {"weak", "unknown"}
TICKET_STATUSES = {"needs_verification", "draft", "correction_risk"}
TICKET_RISKS = {"high", "critical"}


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


def manifest_excerpt() -> list[str]:
    if not AUTHORITY_MANIFEST_PATH.exists():
        return ["- Missing `authority/authority.yaml`."]
    return [
        "- the project owner's interpretive decision remains sovereign.",
        "- Fluency is never evidence; claims need sources, reports, authority IDs, and risk status.",
        "- Governing image: colonial mobilization as crucible.",
        "- Do not flatten the book into simple victimhood, simple collaboration, or simple resilience.",
        "- Track coercion and opportunity, sincerity performance and institutional access, discipline and improvisation, residue and resilience.",
        "- Preserve the archive's friction.",
    ]


def collect_claim_ids(claims: list[dict[str, str]]) -> set[str]:
    return {claim.get("claim_id", "") for claim in claims if claim.get("claim_id")}


def dossier_for_chapter(chapter: str, claims: list[dict[str, str]], all_claims: list[dict[str, str]], entities: dict[str, dict[str, str]], terms: dict[str, dict[str, str]], sources: dict[str, dict[str, str]]) -> str:
    claim_ids = collect_claim_ids(claims)
    dependent_claims = [
        claim for claim in all_claims
        if any(dep in claim_ids for dep in core.split_values(claim.get("depends_on", ""))) and claim.get("claim_id") not in claim_ids
    ]

    entity_ids = sorted({item for claim in claims for item in core.split_values(claim.get("entity_ids", ""))})
    term_ids = sorted({item for claim in claims for item in core.split_values(claim.get("term_ids", ""))})
    source_ids = sorted({item for claim in claims for item in core.split_values(claim.get("source_ids", ""))})
    report_files = sorted({item for claim in claims for item in core.split_values(claim.get("report_files", ""))})

    lines = [
        f"# {CHAPTER_LABELS.get(chapter, chapter)} Dossier", "",
        "Generated from current backend state.", "",
        "Edit `argument_matrix.csv`, `authority/*.csv`, or `authority/authority.yaml`, then regenerate this dossier.", "",
        "## Constitutional Guardrails", "",
        *manifest_excerpt(), "",
        "## Active Claims", "",
    ]

    claim_rows = [
        [
            claim.get("claim_id", ""),
            claim.get("claim_type", ""),
            f"{claim.get('strength', '')} / {claim.get('status', '')} / {claim.get('risk_level', '')}",
            claim.get("claim", ""),
            claim.get("integration_target", ""),
            claim.get("depends_on", "") or "None",
        ]
        for claim in claims
    ]
    lines.extend(core.markdown_table(["Claim ID", "Type", "Strength / Status / Risk", "Claim", "Target", "Depends On"], claim_rows))

    lines.extend(["", "## Open Tickets", ""])
    ticket_rows = [
        [priority(claim), claim.get("claim_id", ""), claim.get("ticket_action", "") or "Add ticket_action in argument_matrix.csv."]
        for claim in sorted([item for item in claims if ticket_needed(item)], key=lambda item: ({"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}[priority(item)], item.get("claim_id", "")))
    ]
    lines.extend(core.markdown_table(["Priority", "Claim ID", "Action"], ticket_rows))

    lines.extend(["", "## Authority Entities", ""])
    entity_rows = [
        [
            entity_id,
            entities.get(entity_id, {}).get("canonical_label", "UNKNOWN"),
            entities.get(entity_id, {}).get("type", ""),
            entities.get(entity_id, {}).get("status", ""),
            entities.get(entity_id, {}).get("cautions", ""),
        ]
        for entity_id in entity_ids
    ]
    lines.extend(core.markdown_table(["ID", "Canonical", "Type", "Status", "Cautions"], entity_rows))

    lines.extend(["", "## Authority Terms", ""])
    term_rows = [
        [
            term_id,
            terms.get(term_id, {}).get("canonical_label", "UNKNOWN"),
            terms.get(term_id, {}).get("romanization", ""),
            terms.get(term_id, {}).get("status", ""),
            terms.get(term_id, {}).get("cautions", ""),
        ]
        for term_id in term_ids
    ]
    lines.extend(core.markdown_table(["ID", "Canonical", "Romanization", "Status", "Cautions"], term_rows))

    lines.extend(["", "## Source Clusters", ""])
    source_rows = [
        [
            source_id,
            sources.get(source_id, {}).get("title", "UNKNOWN"),
            sources.get(source_id, {}).get("source_type", ""),
            sources.get(source_id, {}).get("status", ""),
            sources.get(source_id, {}).get("cautions", ""),
        ]
        for source_id in source_ids
    ]
    lines.extend(core.markdown_table(["ID", "Title", "Type", "Status", "Cautions"], source_rows))

    lines.extend(["", "## Report Files", ""])
    if report_files:
        lines.extend(f"- `{report}`" for report in report_files)
    else:
        lines.append("None.")

    lines.extend(["", "## Dependency Notes", ""])
    dependency_rows = [[claim.get("claim_id", ""), claim.get("depends_on", "")] for claim in claims if claim.get("depends_on")]
    lines.extend(core.markdown_table(["Claim", "Depends On"], dependency_rows))

    lines.extend(["", "## Downstream Claims", ""])
    downstream_rows = [[claim.get("claim_id", ""), claim.get("chapter", ""), claim.get("risk_level", ""), claim.get("claim", "")] for claim in dependent_claims]
    lines.extend(core.markdown_table(["Claim ID", "Chapter", "Risk", "Claim"], downstream_rows))

    lines.extend([
        "", "## Working Commands", "", "```powershell",
        "python tools/research_truth_control.py validate",
        "python tools/research_truth_control.py tickets",
        "python tools/research_truth_control.py impact",
        "python tools/build_chapter_dossiers.py",
        "python tools/manuscript_risk_audit.py",
        "python tools/build_evidence_packs.py",
        "```", "",
    ])
    return "\n".join(lines)


def write_readme(chapter_counts: dict[str, int]) -> None:
    lines = [
        "# Chapter Dossiers", "",
        "Generated context windows for drafting and revision. Do not hand-edit these unless you are deliberately making a temporary note; durable changes belong in `argument_matrix.csv`, `authority/*.csv`, or `authority/authority.yaml`.", "",
        "Regenerate with:", "", "```powershell",
        "python tools/build_chapter_dossiers.py",
        "```", "", "## Files", "",
    ]
    for chapter in CHAPTER_ORDER:
        if chapter in chapter_counts:
            count = chapter_counts[chapter]
            noun = "claim" if count == 1 else "claims"
            lines.append(f"- `{CHAPTER_FILES[chapter]}` - {CHAPTER_LABELS[chapter]} ({count} {noun})")
    lines.append("")
    (DOSSIER_DIR / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    claims, _ = core.read_csv(MATRIX_PATH)
    en_rows, _ = core.read_csv(ENTITIES_PATH)
    te_rows, _ = core.read_csv(TERMS_PATH)
    so_rows, _ = core.read_csv(SOURCES_PATH)

    entities = core.row_map(en_rows, "entity_id")
    terms = core.row_map(te_rows, "term_id")
    sources = core.row_map(so_rows, "source_id")

    by_chapter: dict[str, list[dict[str, str]]] = defaultdict(list)
    for claim in claims:
        by_chapter[claim.get("chapter", "")].append(claim)

    DOSSIER_DIR.mkdir(exist_ok=True)
    chapter_counts: dict[str, int] = {}
    for chapter in CHAPTER_ORDER:
        chapter_claims = by_chapter.get(chapter, [])
        if not chapter_claims:
            continue
        chapter_counts[chapter] = len(chapter_claims)
        output = dossier_for_chapter(chapter, chapter_claims, claims, entities, terms, sources)
        (DOSSIER_DIR / CHAPTER_FILES[chapter]).write_text(output, encoding="utf-8")

    write_readme(chapter_counts)
    print(f"Wrote {len(chapter_counts)} dossiers to {DOSSIER_DIR.relative_to(ROOT).as_posix()}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
