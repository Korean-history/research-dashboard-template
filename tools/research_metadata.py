"""Research metadata catalog, index validator, and report-link extractor."""
from __future__ import annotations

import argparse
import difflib
import fnmatch
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lib import core, report_paths

INDEX_PATH = ROOT / "RESEARCH_REPORT_INDEX.md"
CATALOG_PATH = ROOT / "research_catalog.csv"
AUTHORITY_DIR = ROOT / "authority"
CATALOG_RULES_PATH = AUTHORITY_DIR / "catalog_rules.yaml"
SOURCE_LINK_PATTERNS_PATH = AUTHORITY_DIR / "source_link_patterns.yaml"
CITATION_PATTERNS_PATH = AUTHORITY_DIR / "citation_patterns.yaml"
SOURCES_PATH = AUTHORITY_DIR / "sources.csv"
TERMS_PATH = AUTHORITY_DIR / "terms.csv"

INDEXES_DIR = ROOT / "indexes"
REPORT_SOURCE_LINKS_PATH = INDEXES_DIR / "report_source_links.csv"
REPORT_CITATION_GRAPH_PATH = INDEXES_DIR / "report_citation_graph.csv"
REPORT_CITATION_INDEX_PATH = INDEXES_DIR / "report_citation_index.json"
UNMATCHED_VOCAB_PATH = INDEXES_DIR / "unmatched_vocabulary_report.md"
UNRESOLVED_CITATIONS_PATH = INDEXES_DIR / "unresolved_citations.md"

CONTROL_FILES = {
    "AGENTS.md",
    "CLAUDE.md",
    "CLAIM_DEPENDENCY_REPORT.md",
    "CODEX_NOTES.md",
    "MANUSCRIPT_RISK_AUDIT.md",
    "README.md",
    "RESEARCH_REPORT_INDEX.md",
    "RESEARCH_TICKETS.md",
    "TASKS.md",
}

CATALOG_FIELDS = [
    "file",
    "family",
    "task",
    "title",
    "chapters",
    "topics",
    "keywords",
    "coverage",
    "status",
    "source_corpus",
    "cluster_id",
    "integration_target",
    "cautions",
    "notes",
]

SOURCE_LINK_FIELDS = [
    "report_file",
    "logseq_file",
    "corpus_id",
    "mention_count",
    "first_mention_line",
    "context_snippet",
    "extraction_confidence",
]

CITATION_FIELDS = [
    "from_report",
    "to_report",
    "mention_count",
    "first_mention_line",
    "context_snippet",
    "citation_intent",
    "extraction_confidence",
]

ALLOWED_COVERAGE = {"complete", "partial", "reference", "context_only", "planned"}
COVERAGE_ORDER = {"complete": 0, "partial": 1, "reference": 2, "context_only": 3, "planned": 4}
CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}

FAMILY_ORDER = {
    "early-task": 10,
    "batch": 20,
    "marathon": 30,
    "m2": 40,
    "r3": 50,
    "r4": 60,
    "r5": 70,
    "ugaki": 80,
    "synthesis": 90,
    "assessment": 100,
    "queue": 110,
    "progress-log": 120,
    "completion-marker": 130,
    "other": 999,
}

SYNTHESIS_FILES = {
    "Analytical_Arch_NI_HI_Relationship.md",
    "Book_Assessment_Strengths_and_Improvements.md",
    "Book_Falling_Short_of_Own_Goals.md",
    "Book_Map_and_Chapter_Plan.md",
    "Chapter_Arc_Development_Evaluation.md",
    "ChatGPT Deep Research Synthesis.md",
    "Food Situation Reports - Rumor Content Analysis (Complete).md",
    "Manuscript_Revision_Recommendations.md",
    "R3_R4_Synthesis_Part1_Partial.md",
    "Shadow_Economy_Police_Archive_Analysis.md",
    "Straw_Bag_Economy_Back_Propagation.md",
}

REPORT_SIDE_CAR_SUFFIXES = ("_ACCOUNT.md", "_CLAIMS.yaml", "_HBOM_LITE.json")


@dataclass(frozen=True)
class IndexMetadata:
    refs: set[str]
    patterns: set[str]
    topics_by_file: dict[str, set[str]]
    keywords_by_file: dict[str, set[str]]
    chapters_by_file: dict[str, set[str]]


@dataclass
class Mention:
    value: str
    line: int
    start: int
    end: int
    context: str
    confidence: str


def rel(path: Path) -> str:
    return core.nfc(path.relative_to(ROOT).as_posix())


def report_logical_name(path: Path) -> str:
    relative = rel(path)
    if relative.startswith(f"{report_paths.REPORT_REPOSITORY}/"):
        return core.nfc(path.name)
    return relative


def report_exists(report_ref: str) -> bool:
    return report_paths.resolve(ROOT, report_ref) is not None


def resolve_report_path(report_ref: str) -> Path | None:
    return report_paths.resolve(ROOT, report_ref)


def is_report_sidecar(name: str) -> bool:
    return any(name.endswith(suffix) for suffix in REPORT_SIDE_CAR_SUFFIXES)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def read_report_body(path: Path) -> str:
    escaped = str(path.resolve()).replace("'", "''")
    command = f"[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); Get-Content -Encoding UTF8 -Raw -LiteralPath '{escaped}'"
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        print(f"WARN: timed out reading report body: {rel(path)}")
        return ""
    if result.returncode != 0:
        print(f"WARN: failed to read report body: {rel(path)}")
        return ""
    return result.stdout


def ensure_indexes_dir() -> None:
    INDEXES_DIR.mkdir(parents=True, exist_ok=True)


def title_from_filename(path: Path) -> str:
    title = path.stem.replace("_", " ").replace("-", " ")
    return core.nfc(re.sub(r"\s+", " ", title).strip())


def as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def split_multi(value: str | None) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[;,|]", str(value))
    return [part.strip() for part in parts if part.strip()]


def load_yaml_config(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    data = core.read_yaml(path)
    return data if isinstance(data, dict) else fallback


def load_catalog_rules() -> tuple[dict[str, tuple[str, str]], list[tuple[re.Pattern[str], str, str]]]:
    data = core.read_yaml(CATALOG_RULES_PATH)
    if not isinstance(data, dict):
        raise RuntimeError(f"{CATALOG_RULES_PATH.relative_to(ROOT).as_posix()} must be a YAML mapping.")

    exact_rules: dict[str, tuple[str, str]] = {}
    for item in data.get("exact_metadata", []):
        if not isinstance(item, dict):
            raise RuntimeError("catalog_rules.yaml exact_metadata entries must be mappings.")
        file_name = str(item.get("file", "")).strip()
        topic = str(item.get("topic", "")).strip()
        keywords = str(item.get("keywords", "")).strip()
        if not file_name or not topic:
            raise RuntimeError("catalog_rules.yaml exact_metadata entries require file and topic.")
        exact_rules[file_name] = (topic, keywords)

    pattern_rules: list[tuple[re.Pattern[str], str, str]] = []
    for item in data.get("pattern_metadata", []):
        if not isinstance(item, dict):
            raise RuntimeError("catalog_rules.yaml pattern_metadata entries must be mappings.")
        pattern = str(item.get("pattern", "")).strip()
        topic = str(item.get("topic", "")).strip()
        keywords = str(item.get("keywords", "")).strip()
        if not pattern or not topic:
            raise RuntimeError("catalog_rules.yaml pattern_metadata entries require pattern and topic.")
        pattern_rules.append((re.compile(pattern), topic, keywords))

    return exact_rules, pattern_rules


FALLBACK_EXACT_METADATA, FALLBACK_PATTERN_METADATA = load_catalog_rules()


def git_tracked_files() -> set[str]:
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotePath=false", "ls-files", "*.md"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except Exception:
        return set()
    return {core.nfc(line.strip().replace("\\", "/")) for line in result.stdout.splitlines() if line.strip()}


def parse_index() -> IndexMetadata:
    text = read_text(INDEX_PATH)
    all_refs = {core.nfc(m.replace("\\", "/")) for m in re.findall(r"`([^`]+\.md)`", text)}
    refs = report_paths.alias_set([ref for ref in all_refs if "*" not in ref and "?" not in ref])
    patterns = all_refs - refs
    topics_by_file: dict[str, set[str]] = {}
    keywords_by_file: dict[str, set[str]] = {}
    chapters_by_file: dict[str, set[str]] = {}
    in_topic_lookup = False

    for line in text.splitlines():
        if line.startswith("## "):
            in_topic_lookup = line.startswith("## 3. ")

        line_refs = report_paths.alias_set([
            core.nfc(m.replace("\\", "/"))
            for m in re.findall(r"`([^`]+\.md)`", line)
            if "*" not in m and "?" not in m
        ])
        if not line_refs:
            continue

        chapter_labels = set(re.findall(r"Ch\. ?\d+(?:-\d+)?", line))
        if re.search(r"\bIntroduction\b", line):
            chapter_labels.add("Introduction")
        if re.search(r"\bEpilogue\b", line):
            chapter_labels.add("Epilogue")
        if re.search(r"Deleted|deleted|Former Ch\. 2", line):
            chapter_labels.add("deleted Ch. 2 context")

        topic, keywords = "", ""
        if in_topic_lookup and line.lstrip().startswith("|"):
            parts = [part.strip() for part in line.strip().strip("|").split("|")]
            is_topic_row = (
                len(parts) >= 3
                and parts[0] not in {"Topic", "-------"}
                and not parts[0].startswith("-")
                and "検索語" not in parts[1]
            )
            if is_topic_row:
                topic = parts[0]
                keywords = parts[1]

        for ref in line_refs:
            if chapter_labels:
                chapters_by_file.setdefault(ref, set()).update(chapter_labels)
            if topic and "Research_" not in topic and not topic.startswith("`"):
                topics_by_file.setdefault(ref, set()).add(topic)
            if keywords and not keywords.startswith("-"):
                for item in core.split_values(keywords):
                    keywords_by_file.setdefault(ref, set()).add(item)

    return IndexMetadata(refs, patterns, topics_by_file, keywords_by_file, chapters_by_file)


def read_report_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    try:
        with path.open("rb") as f:
            prefix = f.read(8)
    except OSError:
        return {}, ""
    if not prefix.startswith(b"---"):
        return {}, ""
    text = read_text(path)
    if not re.match(r"\A---\s*\r?\n", text):
        return {}, text
    metadata, body, errors = core.read_markdown_card(path)
    if errors:
        return {}, text
    return metadata, body


def family_for(name: str) -> str:
    if name.startswith("Research_Task"):
        return "early-task"
    if name.startswith("Research_Batch"):
        return "batch"
    if name.startswith("Research_Marathon"):
        return "marathon"
    if name.startswith("Research_M2"):
        return "m2"
    if name.startswith("Research_R3"):
        return "r3"
    if name.startswith("Research_R4"):
        return "r4"
    if name.startswith("Research_R5"):
        return "r5"
    if name.startswith("Ugaki_") or name.startswith("Research_Ugaki"):
        return "ugaki"
    if name.startswith("Research_Round") or name.startswith("Round_"):
        return "queue"
    if name.startswith("research-") or name in {"SESSION_HANDOFF.md", "research-plan.md", "research-plan-v4.md"}:
        return "progress-log"
    if name in {"RESEARCH_COMPLETE.md", "MARATHON2_COMPLETE.md"}:
        return "completion-marker"
    if name in SYNTHESIS_FILES:
        return "synthesis"
    if name.startswith("Book_") or name.startswith("Chapter_") or name.startswith("Manuscript_"):
        return "assessment"
    return "other"


def task_for(name: str) -> str:
    patterns = [
        r"Research_R([345])_Task(\d+)",
        r"Research_M2_Task(\d+)",
        r"Research_Marathon_Task(\d+)",
        r"Research_Batch(\d+)_Task(\d+)",
        r"Research_Task(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, name)
        if match:
            return ".".join(match.groups())
    return ""


def is_indexed(file_name: str, index_meta: IndexMetadata) -> bool:
    if file_name in index_meta.refs:
        return True
    return any(fnmatch.fnmatch(file_name, pattern) for pattern in index_meta.patterns)


def candidate_files(index_meta: IndexMetadata) -> list[Path]:
    candidates: set[Path] = set()

    search_dirs = [
        ROOT,
        ROOT / report_paths.REPORT_REPOSITORY,
        ROOT / report_paths.REPORT_REPOSITORY / "reports",
    ]
    for directory in search_dirs:
        if not directory.exists():
            continue
        for path in directory.glob("*.md"):
            name = core.nfc(path.name)
            if name in CONTROL_FILES or is_report_sidecar(name):
                continue
            if (
                name.startswith("Research_")
                or name.startswith("research-")
                or name.startswith("Round_")
                or name.startswith("Ugaki_")
                or name in index_meta.refs
                or name in SYNTHESIS_FILES
                or name in {"RESEARCH_COMPLETE.md", "MARATHON2_COMPLETE.md", "SESSION_HANDOFF.md"}
            ):
                candidates.add(path)

    for ref in index_meta.refs:
        path = resolve_report_path(ref)
        if path and core.nfc(path.name) not in CONTROL_FILES:
            candidates.add(path)

    return sorted(candidates, key=lambda p: (FAMILY_ORDER.get(family_for(p.name), 999), report_logical_name(p).lower()))


def derive_cautions(file_name: str, topics: set[str], title: str) -> str:
    cautions: list[str] = []
    if "Deleted Mindo / Ontological Drift" in topics or file_name in {
        "Research_R3_Task03_Hyon_Yongsop_1940.md",
        "Research_R3_Task04_Yi_Yonggun.md",
        "Research_R3_Task05_In_Jeongsik.md",
    }:
        cautions.append("Deleted Ch. 2 context only; salvage portable concepts.")
    if file_name == "Research_R3_Task05_In_Jeongsik.md":
        cautions.append("Do not romanize 印 as Yi.")
    if file_name == "Research_Ugaki_Diary_Deep_Read.md":
        cautions.append("Negative findings are unreliable; use Ugaki extraction file for corrected 心田/民度 data.")
    if "Nishiwaki" in title or "Nishiwaki" in file_name:
        cautions.append("Verify Nishiwaki kanji before manuscript use.")
    if "Task90" in file_name or "Synthesis_Audit" in file_name:
        cautions.append("Gap audit, not primary extraction.")
    return " | ".join(cautions)


def merge_pipe_values(existing: str, auto: str) -> str:
    values: list[str] = []
    for raw in [existing, auto]:
        for value in [part.strip() for part in raw.split("|") if part.strip()]:
            if value not in values:
                values.append(value)
    return " | ".join(values)


def fallback_metadata(file_name: str) -> tuple[set[str], set[str]]:
    topics: set[str] = set()
    keywords: set[str] = set()

    exact = FALLBACK_EXACT_METADATA.get(file_name)
    if exact:
        topic, keyword_string = exact
        topics.add(topic)
        keywords.update(core.split_values(keyword_string))

    for pattern, topic, keyword_string in FALLBACK_PATTERN_METADATA:
        if pattern.search(file_name):
            topics.add(topic)
            keywords.update(core.split_values(keyword_string))

    return topics, keywords


def read_catalog() -> list[dict[str, str]]:
    rows, _ = core.read_csv(CATALOG_PATH)
    return rows


def coverage_from_existing(file_name: str, existing: dict[str, str], index_meta: IndexMetadata, file_exists: bool) -> str:
    coverage = as_text(existing.get("coverage"))
    if coverage in ALLOWED_COVERAGE:
        return coverage
    indexed = as_text(existing.get("indexed")).lower()
    if indexed == "yes":
        return "complete"
    if indexed == "no":
        return "reference" if file_exists else "planned"
    return "complete" if is_indexed(file_name, index_meta) else ("reference" if file_exists else "planned")


def list_to_catalog_value(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(as_text(item) for item in value if as_text(item))
    return as_text(value)


def build_rows() -> list[dict[str, str]]:
    index_meta = parse_index()
    tracked = git_tracked_files()
    tracked_aliases = report_paths.alias_set(tracked)
    existing_by_file = {core.nfc(row.get("file", "")): row for row in read_catalog() if row.get("file")}
    existing_by_name = {Path(key).name: row for key, row in existing_by_file.items()}
    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    for path in candidate_files(index_meta):
        file_name = report_logical_name(path)
        if file_name in seen:
            continue
        seen.add(file_name)
        existing = existing_by_file.get(file_name) or existing_by_name.get(Path(file_name).name, {})
        frontmatter: dict[str, Any] = {}
        title = as_text(frontmatter.get("title")) or existing.get("title") or title_from_filename(path)
        family = as_text(frontmatter.get("family")) or family_for(path.name)
        task = as_text(frontmatter.get("task")) or task_for(path.name)
        fallback_topics, fallback_keywords = fallback_metadata(path.name)

        topics = set(index_meta.topics_by_file.get(file_name, set()))
        topics.update(fallback_topics)
        topics.update(split_multi(list_to_catalog_value(frontmatter.get("topics"))))

        keywords = set(index_meta.keywords_by_file.get(file_name, set()))
        keywords.update(fallback_keywords)
        keywords.update(split_multi(list_to_catalog_value(frontmatter.get("keywords"))))

        chapters = index_meta.chapters_by_file.get(file_name, set())
        chapters.update(split_multi(list_to_catalog_value(frontmatter.get("chapters"))))
        auto_integration_target = "; ".join(sorted(chapters))
        auto_cautions = derive_cautions(path.name, topics, title)

        existing_notes = existing.get("notes", "")
        if file_name in tracked_aliases and existing_notes == "untracked at generation time":
            notes = ""
        else:
            notes = existing_notes or ("" if file_name in tracked_aliases else "untracked at generation time")

        row = {
            "file": file_name,
            "family": family,
            "task": task,
            "title": title,
            "chapters": "; ".join(sorted(chapters)),
            "topics": "; ".join(sorted(topics)),
            "keywords": "; ".join(sorted(keywords)),
            "coverage": coverage_from_existing(file_name, existing, index_meta, file_exists=True),
            "status": as_text(frontmatter.get("status")) or existing.get("status") or "auto-seeded",
            "source_corpus": existing.get("source_corpus", ""),
            "cluster_id": existing.get("cluster_id", as_text(frontmatter.get("cluster") or frontmatter.get("cluster_id"))),
            "integration_target": existing.get("integration_target") or auto_integration_target,
            "cautions": merge_pipe_values(existing.get("cautions", ""), auto_cautions),
            "notes": notes,
        }
        rows.append(row)

    for file_name, existing in sorted(existing_by_file.items()):
        if file_name in seen:
            continue
        if report_exists(file_name):
            continue
        row = {field: existing.get(field, "") for field in CATALOG_FIELDS}
        row["file"] = file_name
        row["coverage"] = coverage_from_existing(file_name, existing, index_meta, file_exists=False)
        rows.append(row)

    return rows


def requires_index(row: dict[str, str]) -> bool:
    family_logged = {"early-task", "batch", "marathon"}
    non_report_controls = {"progress-log", "queue", "completion-marker", "assessment"}
    return row.get("family") not in family_logged | non_report_controls


def normalize_report_name(value: str) -> str:
    return core.nfc(value.strip().strip("`").replace("\\", "/"))


def is_repo_markdown_reference(value: str, catalog_files: set[str]) -> bool:
    normalized = normalize_report_name(value)
    name = Path(normalized).name
    if normalized in catalog_files or name in catalog_files:
        return True
    if resolve_report_path(normalized) or resolve_report_path(name):
        return True
    if name in CONTROL_FILES or name in SYNTHESIS_FILES:
        return True
    return name.startswith(("Research_", "research-", "Round_", "Ugaki_", "Book_", "Chapter_", "Manuscript_", "CARDS_", "RESEARCH_"))


def line_number(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def context_snippet(text: str, start: int, end: int, width: int) -> str:
    left = max(0, start - width)
    right = min(len(text), end + width)
    snippet = re.sub(r"\s+", " ", text[left:right]).strip()
    return snippet


def iter_pattern_mentions(text: str, patterns: list[dict[str, Any]], width: int) -> list[Mention]:
    mentions: list[Mention] = []
    for item in patterns:
        pattern = as_text(item.get("regex"))
        confidence = as_text(item.get("confidence")) or "low"
        if not pattern:
            continue
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            print(f"WARN: skipping invalid regex {pattern!r}: {exc}")
            continue
        for match in compiled.finditer(text):
            value = match.group(1) if match.groups() else match.group(0)
            mentions.append(Mention(value, line_number(text, match.start()), match.start(), match.end(), context_snippet(text, match.start(), match.end(), width), confidence))
    return mentions


def source_title_candidates(row: dict[str, str]) -> list[str]:
    candidates = [row.get("source_id", "").replace("source:", "").replace("_", " "), row.get("title", "")]
    return [re.sub(r"\s+", " ", item).strip().lower() for item in candidates if item.strip()]


def resolve_corpus_id(logseq_file: str, report_file: str, source_rows: list[dict[str, str]], valid_sources: set[str]) -> str:
    if logseq_file in valid_sources:
        return logseq_file
    basename = Path(logseq_file.replace("\\", "/")).name
    stem = Path(basename).stem.lower()
    matches: list[str] = []
    for row in source_rows:
        source_id = row.get("source_id", "")
        if not source_id:
            continue
        for candidate in source_title_candidates(row):
            if candidate and (candidate in stem or stem in candidate):
                matches.append(source_id)
                break
    matches = core.unique(matches)
    if len(matches) == 1:
        return matches[0]

    report_matches = []
    for row in source_rows:
        report_files = report_paths.alias_set(core.split_values(row.get("report_files", "")))
        if report_paths.aliases(report_file) & report_files and row.get("source_id"):
            report_matches.append(row["source_id"])
    report_matches = core.unique(report_matches)
    if not matches and len(report_matches) == 1:
        return report_matches[0]
    for source_id in matches:
        if source_id in report_matches:
            return source_id
    return ""


def better_confidence(current: str, candidate: str) -> str:
    if not current:
        return candidate
    return current if CONFIDENCE_ORDER.get(current, 99) <= CONFIDENCE_ORDER.get(candidate, 99) else candidate


def extract_source_links_rows() -> list[dict[str, str]]:
    ensure_indexes_dir()
    rows = read_catalog()
    catalog_files = report_paths.alias_set([row.get("file", "") for row in rows if row.get("file")])
    source_rows, source_errors = core.read_csv(SOURCES_PATH)
    if source_errors:
        print(f"WARN: {SOURCES_PATH.name}: {'; '.join(source_errors)}")
    valid_sources = {row.get("source_id", "") for row in source_rows if row.get("source_id")}
    config = load_yaml_config(SOURCE_LINK_PATTERNS_PATH, {})
    aggregate: dict[tuple[str, str, str], dict[str, str]] = {}

    for row in rows:
        report_file = row.get("file", "")
        path = resolve_report_path(report_file)
        if not report_file or not path:
            continue
        text = read_report_body(path)
        if not text:
            continue
        mentions: list[tuple[str, str, str]] = []
        for mention in iter_pattern_mentions(text, config.get("logseq_paths", []), 80):
            value = mention.value.strip()
            if not value.endswith(".md") or is_repo_markdown_reference(value, catalog_files):
                continue
            mentions.append((value, "", mention.confidence))
            key = (report_file, value, "")
            corpus_id = resolve_corpus_id(value, report_file, source_rows, valid_sources)
            if corpus_id:
                key = (report_file, value, corpus_id)
            add_source_link_mention(aggregate, key, mention)

        for mention in iter_pattern_mentions(text, config.get("filename_only", []), 80):
            value = mention.value.strip()
            if not value.endswith(".md") or is_repo_markdown_reference(value, catalog_files):
                continue
            corpus_id = resolve_corpus_id(value, report_file, source_rows, valid_sources)
            add_source_link_mention(aggregate, (report_file, value, corpus_id), mention)

        for mention in iter_pattern_mentions(text, config.get("corpus_id_mentions", []), 80):
            source_id = mention.value if mention.value.startswith("source:") else f"source:{mention.value}"
            if source_id not in valid_sources:
                continue
            add_source_link_mention(aggregate, (report_file, "", source_id), mention)

    return sorted(aggregate.values(), key=lambda item: (item["report_file"], item["logseq_file"], item["corpus_id"]))


def add_source_link_mention(aggregate: dict[tuple[str, str, str], dict[str, str]], key: tuple[str, str, str], mention: Mention) -> None:
    report_file, logseq_file, corpus_id = key
    if key not in aggregate:
        aggregate[key] = {
            "report_file": report_file,
            "logseq_file": logseq_file,
            "corpus_id": corpus_id,
            "mention_count": "0",
            "first_mention_line": str(mention.line),
            "context_snippet": mention.context,
            "extraction_confidence": mention.confidence,
        }
    row = aggregate[key]
    row["mention_count"] = str(int(row["mention_count"]) + 1)
    if mention.line < int(row["first_mention_line"]):
        row["first_mention_line"] = str(mention.line)
        row["context_snippet"] = mention.context
    row["extraction_confidence"] = better_confidence(row["extraction_confidence"], mention.confidence)


def write_source_links() -> int:
    rows = extract_source_links_rows()
    core.write_csv(REPORT_SOURCE_LINKS_PATH, rows, SOURCE_LINK_FIELDS)
    print(f"Wrote {rel(REPORT_SOURCE_LINKS_PATH)} with {len(rows)} rows.")
    return 0


def task_lookup_keys(row: dict[str, str]) -> list[str]:
    family = row.get("family", "")
    task = row.get("task", "")
    keys: list[str] = []
    if family in {"r3", "r4", "r5"} and "." in task:
        round_num, task_num = task.split(".", 1)
        keys.extend([f"R{int(round_num)}.{int(task_num)}", f"R{int(round_num)}.{task_num.zfill(2)}"])
    elif family == "m2" and task:
        keys.extend([f"M2.{int(float(task))}", f"M2.{str(task).zfill(2)}"])
    elif family == "marathon" and task:
        keys.append(f"Marathon.{int(float(task))}")
    elif family == "batch" and "." in task:
        batch_num, task_num = task.split(".", 1)
        keys.extend([f"Batch{int(batch_num)}.{int(task_num)}", f"Batch {int(batch_num)}.{int(task_num)}"])
    elif family == "early-task" and task:
        keys.append(f"Task.{int(float(task))}")

    file_name = row.get("file", "")
    patterns = [
        (r"Research_R([345])_Task0?(\d+)", lambda m: [f"R{m.group(1)}.{int(m.group(2))}", f"R{m.group(1)}.{m.group(2).zfill(2)}"]),
        (r"Research_M2_Task0?(\d+)", lambda m: [f"M2.{int(m.group(1))}", f"M2.{m.group(1).zfill(2)}"]),
        (r"Research_Marathon_Task0?(\d+)", lambda m: [f"Marathon.{int(m.group(1))}"]),
        (r"Research_Batch(\d+)_Task0?(\d+)", lambda m: [f"Batch{int(m.group(1))}.{int(m.group(2))}", f"Batch {int(m.group(1))}.{int(m.group(2))}"]),
    ]
    for pattern, builder in patterns:
        match = re.search(pattern, file_name)
        if match:
            keys.extend(builder(match))
    return core.unique(keys)


def build_task_lookup(rows: list[dict[str, str]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for row in rows:
        file_name = row.get("file", "")
        for key in task_lookup_keys(row):
            lookup[key] = file_name
    return lookup


def infer_intent(text: str, start: int, end: int, config: dict[str, Any]) -> str:
    window = text[max(0, start - 30):min(len(text), end + 30)].lower()
    for intent, words in (config.get("intent_keywords") or {}).items():
        for word in words or []:
            if str(word).lower() in window:
                return str(intent)
    return "unclear"


def add_citation_mention(
    aggregate: dict[tuple[str, str], dict[str, str]],
    from_report: str,
    to_report: str,
    mention: Mention,
    intent: str,
) -> None:
    key = (from_report, to_report)
    if key not in aggregate:
        aggregate[key] = {
            "from_report": from_report,
            "to_report": to_report,
            "mention_count": "0",
            "first_mention_line": str(mention.line),
            "context_snippet": mention.context,
            "citation_intent": intent,
            "extraction_confidence": mention.confidence,
        }
    row = aggregate[key]
    row["mention_count"] = str(int(row["mention_count"]) + 1)
    if mention.line < int(row["first_mention_line"]):
        row["first_mention_line"] = str(mention.line)
        row["context_snippet"] = mention.context
        row["citation_intent"] = intent
    row["extraction_confidence"] = better_confidence(row["extraction_confidence"], mention.confidence)
    if row["citation_intent"] == "unclear" and intent != "unclear":
        row["citation_intent"] = intent


def unresolved_row(from_report: str, raw: str, line: int, context: str, confidence: str) -> dict[str, str]:
    return {
        "from_report": from_report,
        "raw_reference": raw,
        "first_mention_line": str(line),
        "context_snippet": context,
        "extraction_confidence": confidence,
    }


def extract_citation_rows() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    ensure_indexes_dir()
    rows = read_catalog()
    catalog_files = report_paths.alias_set([row.get("file", "") for row in rows if row.get("file")])
    task_lookup = build_task_lookup(rows)
    config = load_yaml_config(CITATION_PATTERNS_PATH, {})
    aggregate: dict[tuple[str, str], dict[str, str]] = {}
    unresolved: list[dict[str, str]] = []

    for row in rows:
        from_report = row.get("file", "")
        path = resolve_report_path(from_report)
        if not from_report or not path:
            continue
        text = read_report_body(path)
        if not text:
            continue

        for to_report in sorted(catalog_files, key=len, reverse=True):
            if not to_report or to_report == from_report:
                continue
            for match in re.finditer(re.escape(to_report), text):
                mention = Mention(to_report, line_number(text, match.start()), match.start(), match.end(), context_snippet(text, match.start(), match.end(), 120), "high")
                intent = infer_intent(text, match.start(), match.end(), config)
                add_citation_mention(aggregate, from_report, to_report, mention, intent)

        for mention in iter_pattern_mentions(text, config.get("filename_match", []), 120):
            raw = normalize_report_name(mention.value)
            name = Path(raw).name
            if raw in catalog_files or name in catalog_files:
                continue
            unresolved.append(unresolved_row(from_report, raw, mention.line, mention.context, mention.confidence))

        for item in config.get("round_task_match", []):
            pattern = as_text(item.get("regex"))
            confidence = as_text(item.get("confidence")) or "medium"
            if not pattern:
                continue
            compiled = re.compile(pattern, re.IGNORECASE)
            for match in compiled.finditer(text):
                key = citation_task_key(match)
                mention = Mention(match.group(0), line_number(text, match.start()), match.start(), match.end(), context_snippet(text, match.start(), match.end(), 120), confidence)
                to_report = task_lookup.get(key, "")
                if to_report and to_report != from_report:
                    add_citation_mention(aggregate, from_report, to_report, mention, infer_intent(text, match.start(), match.end(), config))
                elif not to_report:
                    unresolved.append(unresolved_row(from_report, match.group(0), mention.line, mention.context, confidence))

        loose_targets = {str(k).lower(): str(v) for k, v in (config.get("loose_phrase_targets") or {}).items()}
        for mention in iter_pattern_mentions(text, config.get("loose_phrases", []), 120):
            raw = mention.value.lower()
            to_report = loose_targets.get(raw, "")
            if to_report in catalog_files and to_report != from_report:
                add_citation_mention(aggregate, from_report, to_report, mention, infer_intent(text, mention.start, mention.end, config))
            else:
                unresolved.append(unresolved_row(from_report, mention.value, mention.line, mention.context, mention.confidence))

    citation_rows = sorted(aggregate.values(), key=lambda item: (item["from_report"], item["to_report"]))
    unique_unresolved: dict[tuple[str, str, str], dict[str, str]] = {}
    for item in unresolved:
        key = (item["from_report"], item["raw_reference"], item["first_mention_line"])
        unique_unresolved.setdefault(key, item)
    return citation_rows, sorted(unique_unresolved.values(), key=lambda item: (item["from_report"], item["raw_reference"], item["first_mention_line"]))


def citation_task_key(match: re.Match[str]) -> str:
    groups = match.groups()
    text = match.group(0).lower()
    if text.startswith("r") and len(groups) >= 2:
        return f"{groups[0].upper()}.{int(groups[1])}"
    if "marathon" in text and groups:
        return f"Marathon.{int(groups[0])}"
    if text.startswith("m") and len(groups) >= 2:
        return f"M{int(groups[0])}.{int(groups[1])}"
    if "batch" in text and len(groups) >= 2:
        return f"Batch{int(groups[0])}.{int(groups[1])}"
    return match.group(0)


def write_unresolved_citations(rows: list[dict[str, str]]) -> None:
    lines = [
        "# AUTO-GENERATED LOG - DO NOT EDIT",
        "",
        "# Unresolved Report Citations",
        "",
        "Generated by `python tools/research_metadata.py extract-citations`.",
        "",
    ]
    if not rows:
        lines.append("No unresolved citations.")
    else:
        table_rows = [
            [row["from_report"], row["raw_reference"], row["first_mention_line"], row["extraction_confidence"], row["context_snippet"]]
            for row in rows
        ]
        lines.extend(core.markdown_table(["From", "Raw Reference", "Line", "Confidence", "Context"], table_rows))
    lines.append("")
    UNRESOLVED_CITATIONS_PATH.write_text("\n".join(lines), encoding="utf-8")


def write_citation_index(citation_rows: list[dict[str, str]]) -> None:
    catalog_rows = read_catalog()
    nodes = []
    for row in catalog_rows:
        file_name = row.get("file", "")
        if not file_name:
            continue
        nodes.append({
            "id": file_name,
            "family": row.get("family", ""),
            "task": row.get("task", ""),
            "chapters": core.split_values(row.get("chapters", "")),
            "coverage": row.get("coverage", ""),
            "cluster_id": split_multi(row.get("cluster_id", "")),
        })
    edges = [
        {
            "from": row["from_report"],
            "to": row["to_report"],
            "intent": row["citation_intent"],
            "confidence": row["extraction_confidence"],
            "mention_count": int(row["mention_count"]),
            "first_mention_line": int(row["first_mention_line"]),
            "context_snippet": row["context_snippet"],
        }
        for row in citation_rows
    ]
    core.write_json(REPORT_CITATION_INDEX_PATH, {"schema_version": 1, "nodes": nodes, "edges": edges})


def write_citations() -> int:
    citation_rows, unresolved = extract_citation_rows()
    core.write_csv(REPORT_CITATION_GRAPH_PATH, citation_rows, CITATION_FIELDS)
    write_citation_index(citation_rows)
    write_unresolved_citations(unresolved)
    print(f"Wrote {rel(REPORT_CITATION_GRAPH_PATH)} with {len(citation_rows)} edges.")
    print(f"Wrote {rel(REPORT_CITATION_INDEX_PATH)} with {len(citation_rows)} edges and {len(read_catalog())} nodes.")
    print(f"Wrote {rel(UNRESOLVED_CITATIONS_PATH)} with {len(unresolved)} unresolved references.")
    return 0


def controlled_vocabulary() -> dict[str, str]:
    rows, errors = core.read_csv(TERMS_PATH)
    if errors:
        print(f"WARN: {TERMS_PATH.name}: {'; '.join(errors)}")
    values: dict[str, str] = {}
    for row in rows:
        term_id = row.get("term_id", "")
        candidates = [term_id, row.get("canonical_label", ""), row.get("romanization", "")]
        candidates.extend(split_multi(row.get("variants", "")))
        for candidate in candidates:
            text = candidate.strip()
            if text:
                values[text.lower()] = term_id
                values[text] = term_id
    return values


def vocabulary_terms_for_row(row: dict[str, str]) -> list[str]:
    return core.unique(core.split_values(row.get("topics", "")) + core.split_values(row.get("keywords", "")))


def write_unmatched_vocabulary() -> int:
    ensure_indexes_dir()
    vocab = controlled_vocabulary()
    suggestion_pool = sorted({key for key in vocab if key and not key.startswith("term:")}, key=str.lower)
    unmatched: list[dict[str, str]] = []
    for row in read_catalog():
        for term in vocabulary_terms_for_row(row):
            if term in vocab or term.lower() in vocab:
                continue
            suggestion = ""
            close = difflib.get_close_matches(term.lower(), [item.lower() for item in suggestion_pool], n=1, cutoff=0.68)
            if close:
                matched = next((item for item in suggestion_pool if item.lower() == close[0]), close[0])
                suggestion = f"{matched} ({vocab.get(matched) or vocab.get(matched.lower(), '')})".strip()
            unmatched.append({
                "report_file": row.get("file", ""),
                "field_value": term,
                "suggestion": suggestion,
            })

    lines = [
        "# AUTO-GENERATED LOG - DO NOT EDIT",
        "",
        "# Unmatched Vocabulary Report",
        "",
        "Generated by `python tools/research_metadata.py match-vocabulary`.",
        "",
    ]
    if not unmatched:
        lines.append("No unmatched vocabulary.")
    else:
        table_rows = [[item["report_file"], item["field_value"], item["suggestion"]] for item in unmatched]
        lines.extend(core.markdown_table(["Report", "Unmatched Term", "Did You Mean"], table_rows))
    lines.append("")
    UNMATCHED_VOCAB_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {rel(UNMATCHED_VOCAB_PATH)} with {len(unmatched)} unmatched values.")
    return 0


def validate_source_links() -> list[str]:
    rows, errors = core.read_csv(REPORT_SOURCE_LINKS_PATH)
    if errors:
        return [f"Missing source-link index: {REPORT_SOURCE_LINKS_PATH.relative_to(ROOT).as_posix()}"]
    source_rows, _ = core.read_csv(SOURCES_PATH)
    valid_sources = {row.get("source_id", "") for row in source_rows if row.get("source_id")}
    warnings: list[str] = []
    for row in rows:
        corpus_id = row.get("corpus_id", "")
        if corpus_id and corpus_id not in valid_sources:
            warnings.append(f"Source link references unknown source_id {corpus_id}: {row.get('report_file')}")
    return warnings


def validate() -> int:
    index_meta = parse_index()
    rows = read_catalog()
    if not rows:
        print("ERROR: research_catalog.csv is missing or empty. Run: python tools/research_metadata.py refresh")
        return 1

    errors: list[str] = []
    warnings: list[str] = []

    row_fields = set(rows[0].keys()) if rows else set()
    missing_columns = [field for field in CATALOG_FIELDS if field not in row_fields]
    if missing_columns:
        errors.append("research_catalog.csv missing columns: " + ", ".join(missing_columns))
    if "indexed" in row_fields:
        errors.append("research_catalog.csv still has deprecated indexed column; run python tools/research_metadata.py refresh")

    row_files = [core.nfc(row.get("file", "")) for row in rows]
    row_file_set = set(row_files)
    duplicates = sorted({f for f in row_files if row_files.count(f) > 1 and f})
    if duplicates:
        errors.append("Duplicate catalog rows: " + ", ".join(duplicates))

    actual_candidates = {report_logical_name(path) for path in candidate_files(index_meta)}
    missing_catalog = sorted(actual_candidates - row_file_set)
    if missing_catalog:
        errors.append("Report candidates missing from catalog: " + ", ".join(missing_catalog[:20]))
        if len(missing_catalog) > 20:
            errors.append(f"...and {len(missing_catalog) - 20} more missing catalog entries.")

    missing_files = sorted(f for f in row_file_set if f and not report_exists(f) and next((row.get("coverage") for row in rows if row.get("file") == f), "") != "planned")
    if missing_files:
        errors.append("Catalog entries whose files do not exist: " + ", ".join(missing_files))

    missing_index_refs = sorted(ref for ref in index_meta.refs if not report_exists(ref))
    if missing_index_refs:
        errors.append("Index references missing files: " + ", ".join(missing_index_refs))

    unmatched_patterns = sorted(
        pattern for pattern in index_meta.patterns
        if not any(fnmatch.fnmatch(file_name, pattern) for file_name in actual_candidates)
    )
    if unmatched_patterns:
        warnings.append("Index patterns match no report files: " + ", ".join(unmatched_patterns))

    for row in rows:
        coverage = row.get("coverage", "")
        if coverage not in ALLOWED_COVERAGE:
            errors.append(f"Invalid coverage for {row.get('file')}: {coverage}")
        if requires_index(row) and coverage not in {"complete", "partial", "context_only"}:
            warnings.append(f"Not indexed: {row.get('file')} ({coverage})")
        if not row.get("title"):
            warnings.append(f"Missing title metadata: {row.get('file')}")
        if not row.get("topics") and row.get("family") in {"r3", "r4", "r5", "marathon"}:
            warnings.append(f"No topic keyword mapping yet: {row.get('file')}")

    active_section = []
    in_starting_points = False
    for line in read_text(INDEX_PATH).splitlines():
        if line.startswith("## 2. "):
            in_starting_points = True
        elif in_starting_points and line.startswith("### Deleted"):
            break
        if in_starting_points:
            active_section.append(line)
    for line in active_section:
        if line.startswith("| Ch. 2"):
            errors.append("Former Ch. 2 appears in active chapter starting points.")

    warnings.extend(validate_source_links())

    print(f"Catalog rows: {len(rows)}")
    print(f"Index concrete references: {len(index_meta.refs)}")
    print(f"Index coverage patterns: {len(index_meta.patterns)}")
    print(f"Report candidates: {len(actual_candidates)}")

    if warnings:
        print("\nWARNINGS")
        for warning in warnings[:80]:
            print(f"- {warning}")
        if len(warnings) > 80:
            print(f"- ...and {len(warnings) - 80} more warnings")

    if errors:
        print("\nERRORS")
        for error in errors:
            print(f"- {error}")
        return 1

    print("\nOK: catalog and research index are structurally consistent.")
    return 0


def refresh(full: bool = False) -> int:
    rows = build_rows()
    core.write_csv(CATALOG_PATH, rows, CATALOG_FIELDS)
    print(f"Wrote {CATALOG_PATH.name} with {len(rows)} rows.")
    if full:
        write_source_links()
        write_citations()
        write_unmatched_vocabulary()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and validate research metadata catalog.")
    sub = parser.add_subparsers(dest="command", required=True)
    refresh_parser = sub.add_parser("refresh", help="Regenerate research_catalog.csv from current reports and index.")
    refresh_parser.add_argument("--full", action="store_true", help="Also rebuild source links, citation graph, and vocabulary report.")
    sub.add_parser("validate", help="Validate research_catalog.csv against reports and index.")
    sub.add_parser("extract-source-links", help="Extract report-to-source links into indexes/report_source_links.csv.")
    sub.add_parser("extract-citations", help="Extract inter-report citations into indexes/report_citation_graph.csv and JSON.")
    sub.add_parser("match-vocabulary", help="Report catalog topics/keywords not matched to authority/terms.csv.")
    args = parser.parse_args(argv)

    if args.command == "refresh":
        return refresh(full=args.full)
    if args.command == "validate":
        return validate()
    if args.command == "extract-source-links":
        return write_source_links()
    if args.command == "extract-citations":
        return write_citations()
    if args.command == "match-vocabulary":
        return write_unmatched_vocabulary()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
