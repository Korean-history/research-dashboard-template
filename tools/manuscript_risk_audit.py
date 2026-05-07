"""Scan the live manuscript DOCX for authority and correction-risk issues."""
from __future__ import annotations

import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lib import core
from tools.lib.docx_extractor import read_docx_locations

DEFAULT_MANUSCRIPT = ROOT / "manuscript.docx"
AUTHORITY_DIR = ROOT / "authority"
AUDIT_RULES_PATH = AUTHORITY_DIR / "audit_rules.yaml"
AUDIT_PATH = ROOT / "MANUSCRIPT_RISK_AUDIT.md"
AUDIT_JSON_PATH = ROOT / "MANUSCRIPT_RISK_AUDIT.json"
TICKETS_JSON_PATH = ROOT / "RESEARCH_TICKETS.json"

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
CHAPTER_WORDS = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
}


@dataclass(frozen=True)
class Location:
    part: str
    paragraph: int
    section: str
    text: str


@dataclass(frozen=True)
class RiskRule:
    rule_id: str
    severity: str
    claim_id: str
    title: str
    recommendation: str
    patterns: tuple[re.Pattern[str], ...]
    skip_patterns: tuple[re.Pattern[str], ...] = ()


@dataclass(frozen=True)
class Finding:
    rule: RiskRule
    location: Location
    context: str


def rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE | re.DOTALL)


def load_risk_rules() -> list[RiskRule]:
    data = core.read_yaml(AUDIT_RULES_PATH)
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise RuntimeError(f"Invalid audit rule file: {AUDIT_RULES_PATH}")

    rules: list[RiskRule] = []
    for item in data["rules"]:
        if not isinstance(item, dict):
            raise RuntimeError(f"Invalid audit rule entry in {AUDIT_RULES_PATH}")
        patterns = tuple(rx(pattern) for pattern in item.get("patterns", []))
        if not patterns:
            raise RuntimeError(f"Audit rule has no patterns: {item.get('rule_id')}")
        rules.append(RiskRule(
            rule_id=str(item["rule_id"]),
            severity=str(item["severity"]),
            claim_id=str(item["claim_id"]),
            title=str(item["title"]),
            recommendation=str(item["recommendation"]),
            patterns=patterns,
            skip_patterns=tuple(rx(pattern) for pattern in item.get("skip_if_patterns", [])),
        ))
    return rules


RISK_RULES = load_risk_rules()


def read_tickets() -> dict[str, str]:
    data = core.read_json(TICKETS_JSON_PATH)
    if not data:
        return {}
    return {t["claim_id"]: f"{t['number']} [{t['priority']}]" for t in data}


def chapter_marker(text: str) -> str | None:
    match = re.match(r"^chapter\s+(one|two|three|four|five|six|seven|\d+)\b", text)
    if not match:
        return None
    value = CHAPTER_WORDS.get(match.group(1), match.group(1))
    if value == "2":
        return "Ch2 / deleted context"
    return f"Ch{value}"


def infer_section(text: str, current: str, is_heading: bool) -> str:
    compact = re.sub(r"\s+", " ", text.strip())
    lower = compact.lower()
    marker = chapter_marker(lower)

    if is_heading:
        if "introduction" in lower:
            return "Introduction"
        if "epilogue" in lower:
            return "Epilogue"
        if marker:
            return marker
        match = re.search(r"chapter\s*(\d+)", lower)
        if match:
            return f"Ch{match.group(1)}"
        if lower == "1" or "fascism and pan-asianism" in lower:
            return "Ch1"
        if lower == "2" or "mindo" in lower:
            return "Ch2 / deleted context"
        if lower == "3" or "naisen ittai" in lower:
            return "Ch3"
        if lower == "4" or "fascist visuality" in lower:
            return "Ch4"
        if lower == "5" or "constant dripping" in lower:
            return "Ch5"
        if lower == "6" or "ideological conversion" in lower:
            return "Ch6"
        if lower == "7" or "empire of hunger" in lower:
            return "Ch7"

    if lower == "introduction" or lower.startswith("introduction:"):
        return "Introduction"
    if marker:
        return marker
    if lower in {"1", "2", "3", "4", "5", "6", "7"}:
        return "Ch2 / deleted context" if lower == "2" else f"Ch{lower}"
    if lower.startswith("epilogue"):
        return "Epilogue"

    return current


def is_word_heading(paragraph: ET.Element) -> bool:
    p_pr = paragraph.find("w:pPr", NS)
    if p_pr is None:
        return False
    p_style = p_pr.find("w:pStyle", NS)
    if p_style is None:
        return False
    style_value = p_style.attrib.get(f"{{{NS['w']}}}val", "")
    return "Heading" in style_value


def paragraph_text(paragraph: ET.Element) -> str:
    pieces: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{{{NS['w']}}}t" and node.text:
            pieces.append(node.text)
        elif node.tag == f"{{{NS['w']}}}tab":
            pieces.append("\t")
        elif node.tag == f"{{{NS['w']}}}br":
            pieces.append(" ")
    return "".join(pieces).strip()


def xml_paragraphs_main(xml: bytes, footnote_map: dict[str, set[str]], endnote_map: dict[str, set[str]]) -> list[Location]:
    root = ET.fromstring(xml)
    locations: list[Location] = []
    section = "front matter"
    para_num = 0
    for paragraph in root.findall(".//w:p", NS):
        text = paragraph_text(paragraph)
        if not text:
            continue

        para_num += 1
        section = infer_section(text, section, is_word_heading(paragraph))

        for node in paragraph.iter():
            if node.tag == f"{{{NS['w']}}}footnoteReference":
                fn_id = node.attrib.get(f"{{{NS['w']}}}id")
                if fn_id:
                    footnote_map.setdefault(fn_id, set()).add(section)
            elif node.tag == f"{{{NS['w']}}}endnoteReference":
                en_id = node.attrib.get(f"{{{NS['w']}}}id")
                if en_id:
                    endnote_map.setdefault(en_id, set()).add(section)

        locations.append(Location(part="Main", paragraph=para_num, section=section, text=text))
    return locations


def note_id_is_real(note_id: str | None) -> bool:
    if note_id is None:
        return False
    try:
        return int(note_id) > 0
    except ValueError:
        return True


def xml_paragraphs_notes(xml: bytes, part: str, note_map: dict[str, set[str]]) -> list[Location]:
    root = ET.fromstring(xml)
    locations: list[Location] = []
    para_num = 0
    tag = f"{{{NS['w']}}}footnote" if part == "Footnotes" else f"{{{NS['w']}}}endnote"

    for note in root.findall(f".//{tag}", NS):
        note_id = note.attrib.get(f"{{{NS['w']}}}id")
        if not note_id_is_real(note_id):
            continue

        sections = note_map.get(note_id or "", set())
        if len(sections) > 1:
            section = "multi-section"
        elif len(sections) == 1:
            section = next(iter(sections))
        else:
            section = "front matter"

        for paragraph in note.findall(".//w:p", NS):
            text = paragraph_text(paragraph)
            if not text:
                continue
            para_num += 1
            locations.append(Location(part=part, paragraph=para_num, section=section, text=text))
    return locations


def read_docx_locations(path: Path) -> list[Location]:
    locations: list[Location] = []
    footnote_map: dict[str, set[str]] = {}
    endnote_map: dict[str, set[str]] = {}

    with zipfile.ZipFile(path) as docx:
        names = set(docx.namelist())

        if "word/document.xml" in names:
            locations.extend(xml_paragraphs_main(docx.read("word/document.xml"), footnote_map, endnote_map))

        if "word/footnotes.xml" in names:
            locations.extend(xml_paragraphs_notes(docx.read("word/footnotes.xml"), "Footnotes", footnote_map))
        if "word/endnotes.xml" in names:
            locations.extend(xml_paragraphs_notes(docx.read("word/endnotes.xml"), "Endnotes", endnote_map))

        for xml_path in sorted(name for name in names if re.match(r"word/(?:header|footer)\d+\.xml", name)):
            root = ET.fromstring(docx.read(xml_path))
            part_name = xml_path.removeprefix("word/").removesuffix(".xml")
            para_num = 0
            for paragraph in root.findall(".//w:p", NS):
                text = paragraph_text(paragraph)
                if text:
                    para_num += 1
                    locations.append(Location(part=part_name, paragraph=para_num, section="front matter", text=text))
    return locations


def context_for(text: str, match: re.Match[str], width: int = 220) -> str:
    start = max(match.start() - width // 2, 0)
    end = min(match.end() + width // 2, len(text))
    snippet = re.sub(r"\s+", " ", text[start:end]).strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet += "..."
    return snippet


def should_skip(rule: RiskRule, text: str) -> bool:
    return any(pattern.search(text) for pattern in rule.skip_patterns)


def scan_locations(locations: list[Location]) -> list[Finding]:
    findings: list[Finding] = []
    for location in locations:
        for rule in RISK_RULES:
            if should_skip(rule, location.text):
                continue
            for pattern in rule.patterns:
                match = pattern.search(location.text)
                if match:
                    findings.append(Finding(rule=rule, location=location, context=context_for(location.text, match)))
                    break
    findings.sort(key=lambda item: (SEVERITY_ORDER[item.rule.severity], item.rule.rule_id, item.location.part, item.location.paragraph))
    return findings


def write_audit(path: Path = DEFAULT_MANUSCRIPT) -> int:
    if not path.exists():
        print(f"ERROR: manuscript not found: {path}")
        return 1

    locations = read_docx_locations(path)
    findings = scan_locations(locations)
    ticket_map = read_tickets()

    counts = {severity: 0 for severity in SEVERITY_ORDER}
    for finding in findings:
        counts[finding.rule.severity] += 1

    audit_json = []
    rows = []
    for finding in findings:
        audit_json.append({
            "severity": finding.rule.severity,
            "rule": finding.rule.rule_id,
            "part": finding.location.part,
            "paragraph": finding.location.paragraph,
            "section": finding.location.section,
            "claim_id": finding.rule.claim_id,
            "ticket": ticket_map.get(finding.rule.claim_id, "No open ticket"),
            "fix": finding.rule.recommendation,
            "context": finding.context,
        })
        rows.append([
            finding.rule.severity.upper(),
            finding.rule.rule_id,
            finding.location.part,
            str(finding.location.paragraph),
            finding.location.section,
            finding.rule.claim_id,
            ticket_map.get(finding.rule.claim_id, "No open ticket"),
            finding.rule.recommendation,
            finding.context,
        ])

    lines = [
        "# Manuscript Risk Audit", "",
        "Generated from current backend state.", "",
        f"Manuscript: `{path.name}`", "",
        "This audit scans the live DOCX for known authority and correction-risk patterns. It does not modify the manuscript.", "",
        "## Summary", "",
        f"- Paragraph-like units scanned: {len(locations)}",
        f"- Findings: {len(findings)}",
        f"- Critical: {counts['critical']}",
        f"- High: {counts['high']}",
        f"- Medium: {counts['medium']}",
        f"- Low: {counts['low']}", "",
        "## Findings By Severity", "",
    ]
    lines.extend(core.markdown_table(["Severity", "Rule", "Part", "Paragraph", "Section", "Claim", "Ticket", "Recommended Fix", "Context"], rows))

    lines.extend(["", "## Rule Coverage", ""])
    coverage_rows = []
    for rule in RISK_RULES:
        hit_count = sum(1 for finding in findings if finding.rule.rule_id == rule.rule_id)
        coverage_rows.append([rule.severity.upper(), rule.rule_id, rule.claim_id, str(hit_count), rule.recommendation])
    lines.extend(core.markdown_table(["Severity", "Rule", "Claim", "Hits", "Recommendation"], coverage_rows))

    lines.extend(["", "## Workflow", "", "```powershell", "python tools/manuscript_risk_audit.py", "python tools/research_truth_control.py validate", "python tools/research_truth_control.py tickets", "```", ""])

    while lines and lines[-1] == "":
        lines.pop()
    AUDIT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    core.write_json(AUDIT_JSON_PATH, audit_json)
    print(f"Wrote {AUDIT_PATH.name} and JSON sidecar with {len(findings)} findings.")
    return 0


def main() -> int:
    return write_audit()


if __name__ == "__main__":
    raise SystemExit(main())
