"""Verify drafted prose against a card-chain brief."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

RAPIDFUZZ_DISABLED_MESSAGE = (
    "Verifier disabled: install rapidfuzz>=3 to enable short-quote partial_ratio gating."
)
try:
    from rapidfuzz import fuzz
except ModuleNotFoundError:
    fuzz = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lib import core
from tools.lib.card_id_resolver import load_resolver
from tools.lib.docx_extractor import read_docx_paragraphs
from tools.lib.semantic_judge import load_judge
from tools.lib.telemetry import append_decision_event


CARD_CITATION_RE = re.compile(r"\(card:([^)]+)\)")
CHAIN_ITEM_RE = re.compile(r"\(chain_item:([^)]+)\)")
MISSING_EVIDENCE_RE = re.compile(r"\[MISSING_EVIDENCE:\s*([^\]]+)\]", re.IGNORECASE | re.DOTALL)
FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]]+)\]:\s*(.*)", re.DOTALL)
FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]]+)\]")
CLAIM_TRIGGER_RE = re.compile(r"\b(?:18|19|20)\d{2}(?:-\d{2}(?:-\d{2})?)?\b|昭和\d+年|[一二三四五六七八九十]+年")
QUOTE_PATTERNS = [
    re.compile(r'(?<![\w])"(.+?)"(?![\w])', re.DOTALL),
    re.compile(r"“(.+?)”", re.DOTALL),
    re.compile(r"「(.+?)」", re.DOTALL),
    re.compile(r"『(.+?)』", re.DOTALL),
    re.compile(r"«(.+?)»", re.DOTALL),
]
SEARCH_FIELDS = {
    "original_snippet",
    "translation_or_summary",
    "claim_text",
    "synthesis_text",
    "bridge_text",
    "position_text",
    "spatial_argument",
    "entity_name",
    "canonical_label",
}
SOURCE_SNIPPETS_PATH = ROOT / "source_snippets.yaml"


def require_rapidfuzz() -> None:
    if fuzz is None:
        raise RuntimeError(RAPIDFUZZ_DISABLED_MESSAGE)


@dataclass
class Paragraph:
    index: int
    text: str
    note_texts: list[str] = field(default_factory=list)

    @property
    def citation_text(self) -> str:
        if not self.note_texts:
            return self.text
        return self.text + "\n" + "\n".join(self.note_texts)


@dataclass(frozen=True)
class ChainItem:
    item_id: str
    card_id: str
    metadata: dict[str, Any]
    caveat: str = ""


@dataclass
class CardRef:
    card_id: str
    path: Path
    metadata: dict[str, Any]
    body: str
    fields: dict[str, str]


@dataclass
class QuoteHit:
    paragraph: int
    quote: str
    before: str
    after: str
    card_id: str = ""
    field: str = ""
    score: float = 0.0
    raw_score: float = 0.0
    note: str = ""


@dataclass
class CitationHit:
    paragraph: int
    card_id: str
    status: str
    score: float | None
    note: str = ""


def nfc(text: Any) -> str:
    return unicodedata.normalize("NFC", "" if text is None else str(text))


def norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", nfc(text)).strip()


def contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", text))


def read_markdown(path: Path) -> list[Paragraph]:
    text = nfc(path.read_text(encoding="utf-8"))
    raw_blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    footnotes: dict[str, str] = {}
    body_blocks: list[str] = []
    for block in raw_blocks:
        match = FOOTNOTE_DEF_RE.match(block)
        if match:
            footnotes[match.group(1)] = match.group(2).strip()
        else:
            body_blocks.append(block)

    paragraphs: list[Paragraph] = []
    for idx, block in enumerate(body_blocks, 1):
        refs = FOOTNOTE_REF_RE.findall(block)
        note_texts = [footnotes[ref] for ref in refs if ref in footnotes]
        paragraphs.append(Paragraph(idx, block, note_texts))
    return paragraphs


def read_draft(path: Path) -> list[Paragraph]:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return read_markdown(path)
    if suffix == ".docx":
        return [
            Paragraph(item.paragraph, nfc(item.text), [nfc(note) for note in item.note_texts])
            for item in read_docx_paragraphs(path)
        ]
    raise ValueError(f"Unsupported draft format: {path.suffix}")


def load_memo_snapshot(session_id: str, root: Path | None = None) -> dict[str, Any] | None:
    if not session_id:
        return None
    base = root or Path.cwd()
    path = base / "telemetry" / "sessions" / f"decision_events_{session_id}.ndjson"
    if not path.exists():
        return None
    snapshot: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") == "harvester_session_started":
            snapshot = {
                "memo_metadata": event.get("memo_metadata", {}),
                "memo_text_snapshot": event.get("memo_text_snapshot", ""),
                "memo_sha256": event.get("memo_sha256", ""),
                "memo_path": event.get("memo_path", ""),
                "timestamp": event.get("timestamp", ""),
            }
    return snapshot


def load_chain(path: Path, *, root: Path | None = None) -> tuple[str, list[ChainItem], list[dict[str, Any]]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("Chain brief must be a YAML mapping.")
    resolver = load_resolver(root or Path.cwd())
    chain_id = as_text(data.get("chain_id") or path.stem)
    raw_items = data.get("cards")
    if raw_items is None:
        raw_items = data.get("items", [])
    items: list[ChainItem] = []
    missing: list[dict[str, Any]] = []
    for raw in raw_items or []:
        if not isinstance(raw, dict):
            continue
        item_id = as_text(raw.get("id")) or f"item:{len(items) + 1}"
        card_ids = raw.get("cited_card_ids")
        if isinstance(card_ids, list):
            candidate_ids = [as_text(value) for value in card_ids if as_text(value)]
        else:
            candidate_ids = [as_text(raw.get("card_id") or raw.get("snippet_id"))]
        resolved_ids: list[str] = []
        for card_id in [card_id for card_id in candidate_ids if card_id]:
            resolved = resolver.resolve(card_id)
            resolved_ids.append(as_text(resolved.get("canonical")) or card_id)
        candidate_ids = resolved_ids
        if not candidate_ids:
            continue
        if "MISSING_CARD" in candidate_ids:
            missing.append(raw)
            continue
        items.append(ChainItem(item_id, candidate_ids[0], raw, as_text(raw.get("caveat"))))
    return chain_id, items, missing


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return str(value).strip()


def read_cards(cards_dir: Path) -> dict[str, CardRef]:
    cards: dict[str, CardRef] = {}
    if cards_dir.exists():
        for path in cards_dir.glob("**/*.md"):
            metadata, body, errors = core.read_markdown_card(path)
            if errors:
                continue
            card_id = as_text(metadata.get("id"))
            if not card_id:
                continue
            fields: dict[str, str] = {}
            for key, value in metadata.items():
                if isinstance(value, str) and (key in SEARCH_FIELDS or "\n" in value):
                    fields[key] = norm_space(value)
            if body.strip():
                fields["body"] = norm_space(body)
            cards[card_id] = CardRef(card_id, path, metadata, body, fields)
    source_snippets_path = cards_dir.parent / "source_snippets.yaml"
    if not source_snippets_path.exists():
        source_snippets_path = SOURCE_SNIPPETS_PATH
    raw_snippets = core.read_yaml(source_snippets_path)
    if isinstance(raw_snippets, dict):
        for snippet in raw_snippets.get("snippets", []) or []:
            if not isinstance(snippet, dict):
                continue
            card_id = as_text(snippet.get("snippet_id") or snippet.get("id"))
            if not card_id or card_id in cards:
                continue
            metadata = dict(snippet)
            metadata["id"] = card_id
            metadata.setdefault("card_type", "source_snippet")
            fields: dict[str, str] = {}
            for key, value in metadata.items():
                if isinstance(value, str) and (key in SEARCH_FIELDS or "\n" in value):
                    fields[key] = norm_space(value)
            body = as_text(snippet.get("notes"))
            if body:
                fields["body"] = norm_space(body)
            cards[card_id] = CardRef(card_id, source_snippets_path, metadata, body, fields)
    resolver = load_resolver(cards_dir.parent, set(cards))
    for alias, entry in sorted(resolver.aliases.items()):
        canonical = as_text(entry.get("canonical"))
        if canonical in cards and alias not in cards:
            cards[alias] = cards[canonical]
    return cards


def extract_quotations(paragraphs: list[Paragraph]) -> list[QuoteHit]:
    hits: list[QuoteHit] = []
    for paragraph in paragraphs:
        for pattern in QUOTE_PATTERNS:
            for match in pattern.finditer(paragraph.text):
                quote = norm_space(match.group(1))
                if not quote:
                    continue
                before = paragraph.text[max(0, match.start() - 80) : match.start()]
                after = paragraph.text[match.end() : match.end() + 80]
                hits.append(QuoteHit(paragraph.index, quote, norm_space(before), norm_space(after)))
    deduped: list[QuoteHit] = []
    seen: set[tuple[int, str]] = set()
    for hit in sorted(hits, key=lambda item: (item.paragraph, item.quote)):
        key = (hit.paragraph, hit.quote)
        if key not in seen:
            deduped.append(hit)
            seen.add(key)
    return deduped


def extract_citations(paragraphs: list[Paragraph]) -> list[CitationHit]:
    hits: list[CitationHit] = []
    for paragraph in paragraphs:
        for match in CARD_CITATION_RE.finditer(paragraph.citation_text):
            card_id = norm_space(match.group(1))
            hits.append(CitationHit(paragraph.index, card_id, "pending", None))
    return hits


def paragraph_chain_item_ids(paragraph: Paragraph) -> list[str]:
    seen: list[str] = []
    for match in CHAIN_ITEM_RE.finditer(paragraph.citation_text):
        item_id = norm_space(match.group(1))
        if item_id and item_id not in seen:
            seen.append(item_id)
    return seen


def extract_missing_evidence(paragraphs: list[Paragraph]) -> list[tuple[int, str]]:
    items: list[tuple[int, str]] = []
    for paragraph in paragraphs:
        for match in MISSING_EVIDENCE_RE.finditer(paragraph.text):
            items.append((paragraph.index, norm_space(match.group(1))))
    return items


def score_context(hit: QuoteHit, field_text: str) -> float:
    require_rapidfuzz()
    context = norm_space(f"{hit.before} {hit.quote} {hit.after}")
    if not context:
        return 0.0
    return fuzz.partial_token_set_ratio(context, field_text)


def best_quote_match(hit: QuoteHit, cards: list[CardRef]) -> QuoteHit:
    require_rapidfuzz()
    best = QuoteHit(hit.paragraph, hit.quote, hit.before, hit.after)
    for card in cards:
        for field, field_text in card.fields.items():
            if not field_text:
                continue
            raw = fuzz.partial_ratio(hit.quote, field_text)
            score = float(raw)
            note = ""
            short = len(hit.quote.strip()) < 15 or len(hit.quote.split()) < 3
            if short and score > 70:
                strict_ok = contains_cjk(hit.quote) and hit.quote in field_text
                if not strict_ok:
                    score = 65.0
                    note = "short-quote weak context"
            if score > best.score:
                best.card_id = card.card_id
                best.field = field
                best.score = score
                best.raw_score = float(raw)
                best.note = note
    return best


def score_citation_context(paragraph: Paragraph, card: CardRef) -> float:
    require_rapidfuzz()
    context = CARD_CITATION_RE.sub("", paragraph.text)
    context = FOOTNOTE_REF_RE.sub("", context)
    context = norm_space(context)
    if not context:
        return 100.0
    best = 0.0
    for field_text in card.fields.values():
        if field_text:
            best = max(best, float(fuzz.partial_token_set_ratio(context, field_text)))
    return best


def classify_quotes(quotes: list[QuoteHit], chain_cards: list[CardRef]) -> tuple[list[QuoteHit], list[QuoteHit], list[QuoteHit]]:
    anchored: list[QuoteHit] = []
    suspicious: list[QuoteHit] = []
    unanchored: list[QuoteHit] = []
    for quote in quotes:
        matched = best_quote_match(quote, chain_cards)
        if matched.score >= 95:
            anchored.append(matched)
        elif matched.score >= 60:
            suspicious.append(matched)
        else:
            unanchored.append(matched)
    return anchored, suspicious, unanchored


def classify_citations(citations: list[CitationHit], paragraphs: list[Paragraph], card_index: dict[str, CardRef]) -> list[CitationHit]:
    paragraphs_by_index = {paragraph.index: paragraph for paragraph in paragraphs}
    out: list[CitationHit] = []
    for citation in citations:
        card = card_index.get(citation.card_id)
        if not card:
            out.append(CitationHit(citation.paragraph, citation.card_id, "broken", None, "Card file not found."))
            continue
        score = score_citation_context(paragraphs_by_index[citation.paragraph], card)
        if score >= 70:
            out.append(CitationHit(citation.paragraph, citation.card_id, "anchored", score))
        else:
            out.append(CitationHit(citation.paragraph, citation.card_id, "low lexical overlap", score, "low lexical overlap; review support manually"))
    return out


def potentially_unanchored(paragraphs: list[Paragraph]) -> list[tuple[int, str, str]]:
    flagged: list[tuple[int, str, str]] = []
    for paragraph in paragraphs:
        text = paragraph.text.strip()
        if not text or text.startswith("#") or FOOTNOTE_DEF_RE.match(text):
            continue
        if CARD_CITATION_RE.search(paragraph.citation_text):
            continue
        if MISSING_EVIDENCE_RE.search(text):
            continue
        if CLAIM_TRIGGER_RE.search(text):
            flagged.append((paragraph.index, norm_space(text), "date or era trigger"))
    return flagged


def caveat_survival_rows(paragraphs: list[Paragraph], chain_items: list[ChainItem], judge_name: str | None) -> list[list[Any]]:
    by_id = {item.item_id: item for item in chain_items if item.caveat}
    if not by_id:
        return []
    judge = load_judge(judge_name)
    rows: list[list[Any]] = []
    seen_items: set[str] = set()
    any_marker_seen = False
    for paragraph in paragraphs:
        item_ids = paragraph_chain_item_ids(paragraph)
        if item_ids:
            any_marker_seen = True
        for item_id in item_ids:
            item = by_id.get(item_id)
            if not item:
                continue
            seen_items.add(item_id)
            judgment = judge.judge("Does the paragraph preserve the chain caveat?", evidence=paragraph.text, claim=item.caveat)
            rows.append([paragraph.index, item_id, item.caveat, judgment.status, judgment.reason])
    section_text = "\n".join(paragraph.text for paragraph in paragraphs)
    for item_id, item in by_id.items():
        if item_id not in seen_items:
            judgment = judge.judge(
                "Does the whole section preserve the chain caveat after human marker deletion?",
                evidence=section_text,
                claim=item.caveat,
            )
            reason = judgment.reason
            if not any_marker_seen:
                reason = f"whole-section fallback after missing marker: {reason}"
            rows.append(["section", item_id, item.caveat, judgment.status, reason])
    return rows


def memo_consistency_rows(snapshot: dict[str, Any] | None, paragraphs: list[Paragraph], judge_name: str | None) -> list[list[Any]]:
    if not snapshot:
        return [["memo_snapshot", "manual_review_required", "No harvester_session_started memo snapshot found."]]
    text = as_text(snapshot.get("memo_text_snapshot"))
    claim_match = re.search(r"##\s*1\.\s*Claim\s*(.*?)(?:\n##\s*\d+\.|\Z)", text, re.DOTALL | re.IGNORECASE)
    if not claim_match:
        return [["claim_consistency", "manual_review_required", "Memo snapshot has no §1 Claim section."]]
    claim = norm_space(claim_match.group(1))
    prose = norm_space("\n".join(paragraph.text for paragraph in paragraphs))
    judgment = load_judge(judge_name).judge("Does the prose preserve the memo central claim?", evidence=prose, claim=claim)
    return [["claim_consistency", judgment.status, judgment.reason]]


def missing_evidence_linkage_rows(snapshot: dict[str, Any] | None, paragraphs: list[Paragraph]) -> list[list[Any]]:
    if not snapshot:
        return [["not_applicable", "No memo snapshot."]]
    text = as_text(snapshot.get("memo_text_snapshot"))
    section_match = re.search(r"##\s*7\.\s*Missing Evidence\s*(.*?)(?:\n##\s*\d+\.|\Z)", text, re.DOTALL | re.IGNORECASE)
    if not section_match:
        return [["not_applicable", "Memo snapshot has no §7 Missing Evidence section."]]
    entries = [norm_space(item) for item in re.findall(r"(?:^|\n)\s*(?:[-*]|\d+[.)])\s+(.+)", section_match.group(1))]
    prose = norm_space("\n".join(paragraph.text for paragraph in paragraphs)).lower()
    rows: list[list[Any]] = []
    for entry in entries:
        tokens = [token.lower() for token in re.findall(r"[\w'-]+", entry) if len(token) > 3]
        matched = bool(tokens and any(token in prose for token in tokens[:6]))
        rows.append([entry, "fulfilled" if matched else "failed"])
    return rows or [["not_applicable", "Missing Evidence section has no list entries."]]


def overrides_invoked_rows(chain_id: str, chain_items: list[ChainItem]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for item in chain_items:
        rationale = as_text(item.metadata.get("override_rationale"))
        if rationale:
            rows.append([chain_id, item.item_id, rationale, item.caveat or ""])
    return rows


def markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    return core.markdown_table(headers, rows) if rows else ["None."]


def quote_rows(items: list[QuoteHit]) -> list[list[Any]]:
    return [[item.paragraph, item.quote, item.card_id or "(no close match)", item.field, int(round(item.score)), item.note] for item in items]


def write_report(
    report_path: Path,
    draft: Path,
    chain_id: str,
    chain_items: list[ChainItem],
    paragraphs: list[Paragraph],
    quotes: tuple[list[QuoteHit], list[QuoteHit], list[QuoteHit]],
    citations: list[CitationHit],
    unanchored_paragraphs: list[tuple[int, str, str]],
    missing_evidence: list[tuple[int, str]],
    missing_chain_items: list[dict[str, Any]],
    memo_rows: list[list[Any]],
    missing_linkage_rows: list[list[Any]],
    caveat_rows: list[list[Any]],
    override_rows: list[list[Any]],
    session_id: str,
    verdict: str,
) -> None:
    anchored, suspicious, unanchored = quotes
    lines = [
        f"# Verification Report - {draft.name} against chain {chain_id}",
        "",
        f"**Generated:** {dt.datetime.now().replace(microsecond=0).isoformat()}",
        f"**Cards in chain:** {len(chain_items)}",
        f"**Paragraphs analyzed:** {len(paragraphs)}",
        f"**Quoted passages:** {len(anchored) + len(suspicious) + len(unanchored)}",
        f"**Inline citations:** {len(citations)}",
        f"**MISSING_EVIDENCE markers:** {len(missing_evidence) + len(missing_chain_items)}",
        f"**Session ID:** {session_id or 'none'}",
        "",
        "## Summary",
        "",
        f"- Anchored quotations: {len(anchored)}",
        f"- Suspicious paraphrases: {len(suspicious)}",
        f"- Unanchored quotations: {len(unanchored)}",
        f"- Anchored citations: {sum(1 for item in citations if item.status == 'anchored')}",
        f"- Low lexical overlap citations: {sum(1 for item in citations if item.status == 'low lexical overlap')}",
        f"- Broken citations: {sum(1 for item in citations if item.status == 'broken')}",
        f"- Potentially unanchored claim paragraphs: {len(unanchored_paragraphs)}",
        f"- MISSING_EVIDENCE flags: {len(missing_evidence) + len(missing_chain_items)}",
        "",
        f"**Verdict:** {verdict}",
        "",
        "## §1 Anchored Quotations",
        "",
    ]
    lines.extend(markdown_table(["Para", "Quotation", "Card", "Field", "Score", "Note"], quote_rows(anchored)))
    lines.extend(["", "## §2 Suspicious Paraphrases", ""])
    lines.extend(markdown_table(["Para", "Quotation", "Closest card", "Field", "Score", "Note"], quote_rows(suspicious)))
    lines.extend(["", "## §3 Unanchored Quotations", ""])
    lines.extend(markdown_table(["Para", "Quotation", "Best card", "Field", "Score", "Note"], quote_rows(unanchored)))
    lines.extend(["", "## §4 Citation Verification", ""])
    lines.extend(markdown_table(
        ["Para", "Citation", "Status", "Score", "Notes"],
        [[item.paragraph, f"(card:{item.card_id})", item.status, "" if item.score is None else int(round(item.score)), item.note] for item in citations],
    ))
    lines.extend(["", "## §5 Potentially Unanchored Claim Paragraphs", ""])
    lines.extend(markdown_table(["Para", "Paragraph", "Trigger"], [[idx, text, trigger] for idx, text, trigger in unanchored_paragraphs]))
    lines.extend(["", "## §6 MISSING_EVIDENCE Items", ""])
    missing_rows = [[idx, desc] for idx, desc in missing_evidence]
    for item in missing_chain_items:
        missing_rows.append(["chain", f"MISSING_CARD: {as_text(item.get('missing_evidence_needed'))}"])
    lines.extend(markdown_table(["Para", "Description"], missing_rows))
    lines.extend(["", "## §7 Memo Consistency", ""])
    lines.extend(markdown_table(["Check", "Status", "Reason"], memo_rows))
    lines.extend(["", "## §8 Missing Evidence Linkage", ""])
    lines.extend(markdown_table(["Memo item", "Status"], missing_linkage_rows))
    lines.extend(["", "## §9 Caveat Survival", ""])
    lines.extend(markdown_table(["Para", "Chain item", "Caveat", "Status", "Reason"], caveat_rows))
    lines.extend(["", "## §10 Overrides Invoked", ""])
    lines.extend(markdown_table(["Chain", "Chain item", "Override rationale", "Caveat"], override_rows) if override_rows else ["(none)"])
    lines.extend([
        "",
        "## Verification Footer",
        "",
        "Tool: `tools/verify_drafted_prose.py` v2",
        f"DOCX/MD: `{draft}`",
        f"Chain: `{chain_id}`",
        "Cards directory: `cards/`",
        "",
    ])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


def determine_verdict(unanchored_quotes: list[QuoteHit], citations: list[CitationHit], suspicious: list[QuoteHit], unanchored_paragraphs: list[tuple[int, str, str]], unresolved_chain: list[str], caveat_rows: list[list[Any]]) -> str:
    if unanchored_quotes or any(item.status == "broken" for item in citations) or unresolved_chain:
        return "FAIL"
    if suspicious or any(item.status == "low lexical overlap" for item in citations) or unanchored_paragraphs or any(row[3] != "pass" for row in caveat_rows):
        return "REVIEW"
    return "PASS"


def run(draft_path: Path, brief_path: Path, report_path: Path | None, session_id: str = "", semantic_judge: str | None = None) -> int:
    try:
        require_rapidfuzz()
        paragraphs = read_draft(draft_path)
        chain_id, chain_items, missing_chain_items = load_chain(brief_path, root=Path.cwd())
        card_index = read_cards(Path.cwd() / "cards")
        unresolved_chain = [item.card_id for item in chain_items if item.card_id not in card_index]
        chain_cards = [card_index[item.card_id] for item in chain_items if item.card_id in card_index]

        quotations = extract_quotations(paragraphs)
        quote_groups = classify_quotes(quotations, chain_cards)
        citations = classify_citations(extract_citations(paragraphs), paragraphs, card_index)
        missing_evidence = extract_missing_evidence(paragraphs)
        unanchored_paras = potentially_unanchored(paragraphs)
        memo_snapshot = load_memo_snapshot(session_id, Path.cwd()) if session_id else None
        memo_rows = memo_consistency_rows(memo_snapshot, paragraphs, semantic_judge) if session_id else []
        missing_linkage_rows = missing_evidence_linkage_rows(memo_snapshot, paragraphs) if session_id else []
        caveat_rows = caveat_survival_rows(paragraphs, chain_items, semantic_judge)
        override_rows = overrides_invoked_rows(chain_id, chain_items)
        verdict = determine_verdict(quote_groups[2], citations, quote_groups[1], unanchored_paras, unresolved_chain, caveat_rows)

        if report_path is None:
            timestamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
            report_path = Path.cwd() / "verification_reports" / f"verification_{draft_path.stem}_{timestamp}.md"
        write_report(
            report_path,
            draft_path,
            chain_id,
            chain_items,
            paragraphs,
            quote_groups,
            citations,
            unanchored_paras,
            missing_evidence,
            missing_chain_items,
            memo_rows,
            missing_linkage_rows,
            caveat_rows,
            override_rows,
            session_id,
            verdict,
        )
        if session_id:
            append_decision_event(session_id, "draft_prose_verified", root=Path.cwd(), chain_id=chain_id, report_path=str(report_path), verdict=verdict)
        print(f"{verdict}: wrote {report_path}")
        return {"PASS": 0, "REVIEW": 1, "FAIL": 2}[verdict]
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify drafted prose against a chain brief.")
    parser.add_argument("draft")
    parser.add_argument("--brief")
    parser.add_argument("--chain", help="Alias for --brief.")
    parser.add_argument("--report")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--semantic-judge", choices=["deterministic", "manual", "llm"], default="manual")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    brief = args.chain or args.brief
    if not brief:
        parser.error("--brief or --chain is required")
    return run(Path(args.draft), Path(brief), Path(args.report) if args.report else None, args.session_id, args.semantic_judge)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
