"""Shared core utilities for the research backend."""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import yaml


def nfc(text: str | None) -> str:
    """Normalize string to NFC to fix Git/macOS/Windows path drift."""
    if not text:
        return ""
    return unicodedata.normalize("NFC", str(text))


def read_csv(path: Path, required_fields: list[str] | None = None) -> tuple[list[dict[str, str]], list[str]]:
    """Read CSV safely with utf-8-sig to prevent BOM metadata wipeouts."""
    errors: list[str] = []
    if not path.exists():
        return [], [f"Missing file: {path.name}"]

    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if required_fields:
            fieldnames = reader.fieldnames or []
            missing = [field for field in required_fields if field not in fieldnames]
            if missing:
                errors.append(f"{path.name} missing columns: {', '.join(missing)}")
        rows = list(reader)
    return rows, errors


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    """Write CSV safely without BOM for standard tooling."""
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def split_values(value: str | None) -> list[str]:
    """Split semicolon-delimited values and strip whitespace."""
    if not value:
        return []
    return [part.strip() for part in str(value).split(";") if part.strip()]


def row_map(rows: list[dict[str, str]], key: str) -> dict[str, dict[str, str]]:
    return {row.get(key, ""): row for row in rows if row.get(key)}


def unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            out.append(value)
            seen.add(value)
    return out


def md_escape(value: Any) -> str:
    """Escape Markdown table cells."""
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    """Generate a Markdown table safely."""
    if not rows:
        return ["None."]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(md_escape(v) for v in row) + " |")
    return lines


def read_json(path: Path) -> list | dict | None:
    """Read machine-readable JSON sidecar."""
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def read_yaml(path: Path) -> list | dict | None:
    """Read YAML config with PyYAML."""
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text)


def read_markdown_card(path: Path) -> tuple[dict[str, Any], str, list[str]]:
    """Read a Markdown card with YAML frontmatter and return metadata, body, errors.

    This helper is intentionally read-only. Build scripts may compile card
    metadata into JSON/CSV views, but must never write back to the source card.
    """
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n)?(.*)\Z", text, re.DOTALL)
    if not match:
        return {}, text, [f"{path.as_posix()} missing YAML frontmatter."]

    frontmatter_text, body = match.groups()
    try:
        data = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError as exc:
        return {}, body, [f"{path.as_posix()} has invalid YAML frontmatter: {exc}"]
    if not isinstance(data, dict):
        errors.append(f"{path.as_posix()} frontmatter must be a YAML mapping.")
        data = {}
    return data, body, errors


def write_json(path: Path, data: list | dict) -> None:
    """Write formatted JSON sidecar."""
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def safe_clear_generated_dir(directory: Path, root: Path) -> None:
    """Safely clear a generated directory, guarding against path drift."""
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)
        (directory / ".generated").touch()
        return

    try:
        directory.resolve().relative_to(root.resolve())
    except ValueError:
        print(f"Safety abort: {directory} is not relative to {root}")
        return

    sentinel_path = directory / ".generated"
    if not sentinel_path.exists():
        has_files = any(directory.iterdir())
        if has_files:
            print(f"Safety abort: {directory} missing .generated sentinel file.")
            return
        sentinel_path.touch()

    for path in directory.glob("*.md"):
        if path.name != "README.md":
            path.unlink()
    for path in directory.glob("*.json"):
        path.unlink()
