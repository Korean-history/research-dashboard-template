"""Raw DOCX paragraph and note extraction helpers."""
from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
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


@dataclass
class ExtractedParagraph:
    paragraph: int
    text: str
    part: str = "Main"
    section: str = "front matter"
    note_texts: list[str] = field(default_factory=list)


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
        chapter_titles = {
            "1": "Ch1",
            "2": "Ch2 / deleted context",
            "3": "Ch3",
            "4": "Ch4",
            "5": "Ch5",
            "6": "Ch6",
            "7": "Ch7",
        }
        if lower in chapter_titles:
            return chapter_titles[lower]
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


def note_id_is_real(note_id: str | None) -> bool:
    if note_id is None:
        return False
    try:
        return int(note_id) > 0
    except ValueError:
        return True


def paragraph_note_refs(paragraph: ET.Element) -> tuple[list[str], list[str]]:
    footnotes: list[str] = []
    endnotes: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{{{NS['w']}}}footnoteReference":
            note_id = node.attrib.get(f"{{{NS['w']}}}id")
            if note_id:
                footnotes.append(note_id)
        elif node.tag == f"{{{NS['w']}}}endnoteReference":
            note_id = node.attrib.get(f"{{{NS['w']}}}id")
            if note_id:
                endnotes.append(note_id)
    return footnotes, endnotes


def note_text_map(xml: bytes, part: str) -> dict[str, str]:
    root = ET.fromstring(xml)
    tag = f"{{{NS['w']}}}footnote" if part == "Footnotes" else f"{{{NS['w']}}}endnote"
    notes: dict[str, str] = {}
    for note in root.findall(f".//{tag}", NS):
        note_id = note.attrib.get(f"{{{NS['w']}}}id")
        if not note_id_is_real(note_id):
            continue
        texts = [paragraph_text(paragraph) for paragraph in note.findall(".//w:p", NS)]
        notes[note_id or ""] = " ".join(text for text in texts if text).strip()
    return notes


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
        footnotes, endnotes = paragraph_note_refs(paragraph)
        for note_id in footnotes:
            footnote_map.setdefault(note_id, set()).add(section)
        for note_id in endnotes:
            endnote_map.setdefault(note_id, set()).add(section)
        locations.append(Location(part="Main", paragraph=para_num, section=section, text=text))
    return locations


def xml_paragraphs_notes(xml: bytes, part: str, note_map: dict[str, set[str]]) -> list[Location]:
    notes = note_text_map(xml, part)
    locations: list[Location] = []
    for para_num, (note_id, text) in enumerate(notes.items(), 1):
        sections = note_map.get(note_id, set())
        if len(sections) > 1:
            section = "multi-section"
        elif len(sections) == 1:
            section = next(iter(sections))
        else:
            section = "front matter"
        if text:
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


def read_docx_paragraphs(path: Path) -> list[ExtractedParagraph]:
    paragraphs: list[ExtractedParagraph] = []
    with zipfile.ZipFile(path) as docx:
        names = set(docx.namelist())
        footnotes = note_text_map(docx.read("word/footnotes.xml"), "Footnotes") if "word/footnotes.xml" in names else {}
        endnotes = note_text_map(docx.read("word/endnotes.xml"), "Endnotes") if "word/endnotes.xml" in names else {}
        if "word/document.xml" not in names:
            return []
        root = ET.fromstring(docx.read("word/document.xml"))
        section = "front matter"
        para_num = 0
        for paragraph in root.findall(".//w:p", NS):
            text = paragraph_text(paragraph)
            if not text:
                continue
            para_num += 1
            section = infer_section(text, section, is_word_heading(paragraph))
            fn_ids, en_ids = paragraph_note_refs(paragraph)
            note_texts = [footnotes[note_id] for note_id in fn_ids if note_id in footnotes]
            note_texts.extend(endnotes[note_id] for note_id in en_ids if note_id in endnotes)
            paragraphs.append(ExtractedParagraph(para_num, text, "Main", section, note_texts))
    return paragraphs
