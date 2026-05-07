"""Generate a chapter-scoped card review queue digest."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lib import core

CARDS_INDEX_PATH = ROOT / "CARDS_INDEX.json"
DEFAULT_OUTPUT_DIR = ROOT / "cards" / "review_queues"

TYPE_ORDER = {
    "scaffold": 0,
    "claim": 1,
    "synthesis": 2,
    "bridge": 3,
    "counterargument": 4,
    "idea": 5,
    "timeline": 6,
    "place": 7,
    "moc": 8,
    "entity": 9,
    "question": 10,
    "source_snippet": 11,
}

PRIMARY_FIELDS = [
    "scaffold_text",
    "claim_text",
    "synthesis_text",
    "bridge_text",
    "position_text",
    "question_text",
    "event_label",
    "spatial_argument",
    "role_in_book",
]

REPORT_FIELDS = ["provenance_report", "derived_from_reports", "informed_by_reports", "report_files"]


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return core.split_values(text) if text else []


def md_block(label: str, value: str) -> list[str]:
    if not value.strip():
        return []
    return [f"**{label}**", "", "```text", value.strip(), "```", ""]


def card_chapters(card: dict[str, Any]) -> list[str]:
    chapters = as_list(card.get("chapters"))
    metadata = card.get("metadata", {}) if isinstance(card.get("metadata"), dict) else {}
    chapters.extend(as_list(metadata.get("chapter_relevance")))
    return sorted(set(chapters))


def relevant_cards(cards: list[dict[str, Any]], chapter: str) -> list[dict[str, Any]]:
    return [
        card for card in cards
        if chapter in card_chapters(card)
    ]


def sort_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        cards,
        key=lambda card: (
            TYPE_ORDER.get(str(card.get("card_type", "")), 99),
            str(card.get("title", "")).lower(),
            str(card.get("card_id", "")),
        ),
    )


def metadata(card: dict[str, Any]) -> dict[str, Any]:
    value = card.get("metadata", {})
    return value if isinstance(value, dict) else {}


def link_summary(card: dict[str, Any]) -> tuple[list[str], list[str]]:
    linked = card.get("linked_cards", {})
    if not isinstance(linked, dict):
        return [], []
    return as_list(linked.get("outgoing")), as_list(linked.get("incoming"))


def report_refs(card: dict[str, Any]) -> list[str]:
    meta = metadata(card)
    refs: list[str] = []
    for field in REPORT_FIELDS:
        refs.extend(as_list(meta.get(field)))
    return sorted(set(refs))


def primary_text(card: dict[str, Any]) -> tuple[str, str]:
    meta = metadata(card)
    for field in PRIMARY_FIELDS:
        value = str(meta.get(field, "") or "").strip()
        if value:
            return field, value
    return "", ""


def render_source_snippet(card: dict[str, Any]) -> list[str]:
    meta = metadata(card)
    lines: list[str] = []
    lines.extend(md_block("Original Snippet", str(meta.get("original_snippet", "") or "")))
    lines.extend(md_block("Translation / Summary", str(meta.get("translation_or_summary", "") or "")))
    return lines


def render_card(card: dict[str, Any], index: int) -> list[str]:
    meta = metadata(card)
    outgoing, incoming = link_summary(card)
    field, text = primary_text(card)
    lines = [
        f"## {index}. {card.get('title', 'Untitled')}",
        "",
        f"- Card: `{card.get('card_id', '')}`",
        f"- Type: `{card.get('card_type', '')}`",
        f"- Status: `{card.get('status', '')}`",
        f"- Path: `{card.get('path', '')}`",
        f"- Chapters: {', '.join(card_chapters(card)) or 'None'}",
        f"- Arcs: {', '.join(as_list(card.get('arc_ids'))) or 'None'}",
        f"- Tags: {', '.join(as_list(card.get('tags'))) or 'None'}",
        f"- Reports: {', '.join(report_refs(card)) or 'None'}",
        f"- Outgoing links: {', '.join(f'`{item}`' for item in outgoing) or 'None'}",
        f"- Incoming links: {', '.join(f'`{item}`' for item in incoming) or 'None'}",
        "",
        "Review notes:",
        "",
        "- [ ] Accept",
        "- [ ] Revise",
        "- [ ] Needs source check",
        "",
    ]
    if field and text:
        lines.extend(md_block(field, text))
    if card.get("card_type") == "source_snippet":
        lines.extend(render_source_snippet(card))
    body = str(card.get("body", "") or "").strip()
    if body:
        lines.extend(md_block("Body", body))
    if not field and card.get("card_type") != "source_snippet" and not body:
        compact_meta = {
            key: value for key, value in meta.items()
            if key not in {"id", "card_type", "title", "created", "updated", "status", "linked_cards"}
            and value not in {"", [], None}
        }
        if compact_meta:
            lines.extend(md_block("Metadata", "\n".join(f"{key}: {value}" for key, value in compact_meta.items())))
    lines.append("---")
    lines.append("")
    return lines


def dependency_overview(cards: list[dict[str, Any]]) -> list[str]:
    if not cards:
        return ["No dependency overview available.", ""]
    ids = {str(card.get("card_id", "")) for card in cards}
    lines = ["## Dependency Overview", ""]
    for card in sort_cards(cards):
        outgoing, incoming = link_summary(card)
        local_out = [item for item in outgoing if item in ids]
        local_in = [item for item in incoming if item in ids]
        lines.append(f"- `{card.get('card_id', '')}` ({card.get('card_type', '')})")
        lines.append(f"  - cites/links to: {', '.join(f'`{item}`' for item in local_out) or 'None in this chapter queue'}")
        lines.append(f"  - cited/linked by: {', '.join(f'`{item}`' for item in local_in) or 'None in this chapter queue'}")
    lines.append("")
    return lines


def build_queue(chapter: str) -> str:
    data = core.read_json(CARDS_INDEX_PATH)
    if not isinstance(data, dict):
        raise RuntimeError("CARDS_INDEX.json is missing or invalid. Run `python tools/build_cards.py` first.")
    cards = data.get("cards", [])
    if not isinstance(cards, list):
        cards = []
    selected = sort_cards(relevant_cards(cards, chapter))
    lines = [
        f"# Card Review Queue — {chapter}",
        "",
        "Generated by `python tools/cards_review_queue.py`. This digest is safe to annotate during review; regenerating it will overwrite local notes in the same output file.",
        "",
        f"Cards in queue: {len(selected)}",
        "",
    ]
    lines.extend(dependency_overview(selected))
    if not selected:
        lines.extend(["No cards found for this chapter.", ""])
    else:
        for index, card in enumerate(selected, 1):
            lines.extend(render_card(card, index))
    return "\n".join(lines)


def output_path_for(chapter: str) -> Path:
    safe = chapter.replace(" ", "_").replace(".", "").replace("/", "_")
    return DEFAULT_OUTPUT_DIR / f"{safe}_review_queue.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a chapter card review queue.")
    parser.add_argument("--chapter", required=True, help="Chapter label, e.g. Ch1, Ch5, Epilogue.")
    parser.add_argument("--output", help="Optional markdown output path.")
    parser.add_argument("--stdout", action="store_true", help="Print the queue instead of writing a file.")
    args = parser.parse_args(argv)

    queue = build_queue(args.chapter)
    if args.stdout:
        print(queue)
        return 0

    output = Path(args.output) if args.output else output_path_for(args.chapter)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(queue, encoding="utf-8")
    print(f"Wrote {output.relative_to(ROOT).as_posix()}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
