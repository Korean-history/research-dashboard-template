"""Utilities for resolving research-report references.

The research harness stores report references in several historical forms:
bare filenames at the workspace root, paths under ``research_reports/``, and
occasionally absolute Windows paths copied from Logseq-style reports. Keep this
module deliberately small so validation can normalize those forms without
rewriting the underlying catalog.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

REPORT_REPOSITORY = "research_reports"


def _clean(value: object) -> str:
    return "" if value is None else str(value).strip().strip("`")


def _strip_line_suffix(value: str) -> str:
    # Markdown references sometimes carry :line suffixes. Preserve Windows drive
    # letters while trimming only a final numeric locator.
    head, sep, tail = value.rpartition(":")
    if sep and tail.isdigit() and not (len(head) == 1 and head.isalpha()):
        return head
    return value


def _candidate_paths(root: Path, report_ref: str) -> list[Path]:
    ref = _strip_line_suffix(_clean(report_ref))
    if not ref:
        return []
    ref_path = Path(ref)
    candidates = [ref_path] if ref_path.is_absolute() else []
    candidates.extend([
        root / ref,
        root / REPORT_REPOSITORY / ref,
        root / REPORT_REPOSITORY / "reports" / ref,
        root / REPORT_REPOSITORY / "tmp" / ref,
    ])
    return candidates


def resolve(root: Path, report_ref: str) -> Path | None:
    """Return the first existing path matching a report reference."""
    for path in _candidate_paths(root, report_ref):
        try:
            if path.exists():
                return path
        except OSError:
            continue
    return None


def aliases(report_ref: str) -> set[str]:
    """Return normalized aliases for comparing report references."""
    ref = _strip_line_suffix(_clean(report_ref))
    if not ref:
        return set()
    path = Path(ref)
    parts = [part for part in path.parts if part not in {".", ""}]
    out = {ref.replace("\\", "/"), path.name}
    if len(parts) >= 2 and parts[0] == REPORT_REPOSITORY:
        out.add("/".join(parts[1:]))
    if len(parts) >= 3 and parts[0] == REPORT_REPOSITORY and parts[1] in {"reports", "tmp"}:
        out.add("/".join(parts[2:]))
    return {item for item in out if item}


def alias_set(report_refs: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for ref in report_refs:
        out.update(aliases(ref))
    return out
