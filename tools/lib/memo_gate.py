"""Investment-memo frontmatter gate for Harvester sessions."""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

VALID_DECISIONS = {"draft", "hold", "search_more", "kill", "split_to_article"}
LEGACY_DECISIONS = {
    "search-more": "search_more",
    "search more": "search_more",
    "split-to-article": "split_to_article",
    "split to article": "split_to_article",
}
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
FRESH_DAYS = 14


@dataclass(frozen=True)
class MemoGateResult:
    metadata: dict[str, Any]
    text: str
    sha256: str
    warnings: list[str]


def read_text_with_retry(path: Path, retries: int = 5, backoff: float = 0.15) -> str:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError as exc:
            last_error = exc
            time.sleep(backoff * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError(f"could not read memo: {path}")


def read_bytes_with_retry(path: Path, retries: int = 5, backoff: float = 0.15) -> bytes:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            return path.read_bytes()
        except PermissionError as exc:
            last_error = exc
            time.sleep(backoff * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError(f"could not read memo: {path}")


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError("memo missing required YAML frontmatter; see RESEARCH_ALPHA_ACTIVATION_DESIGN_SPEC §3.1 for the required schema")
    data = yaml.safe_load(match.group(1)) or {}
    if not isinstance(data, dict):
        raise ValueError("memo YAML frontmatter must be a mapping")
    return data, text[match.end():]


def parse_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    return dt.date.fromisoformat(text)


def validate_memo(path: Path, chapter: str, force_stale_memo: bool = False, today: dt.date | None = None) -> MemoGateResult:
    raw_bytes = read_bytes_with_retry(path)
    text = raw_bytes.decode("utf-8")
    metadata, _body = parse_frontmatter(text)
    warnings: list[str] = []

    for field in ["bet_id", "primary_chapter", "decision", "authored"]:
        if not metadata.get(field):
            raise ValueError(f"memo frontmatter missing required field: {field}")

    primary_chapter = str(metadata.get("primary_chapter")).strip()
    if primary_chapter != chapter:
        raise ValueError(f"--chapter {chapter} conflicts with memo primary_chapter {primary_chapter}; reconcile before run")

    decision = str(metadata.get("decision")).strip()
    if decision in LEGACY_DECISIONS:
        normalized = LEGACY_DECISIONS[decision]
        warnings.append(f"legacy decision spelling {decision!r} normalized to {normalized!r}")
        decision = normalized
        metadata["decision"] = normalized
    if decision not in VALID_DECISIONS:
        raise ValueError(f"memo decision must be one of {sorted(VALID_DECISIONS)}")
    if decision != "draft":
        raise ValueError(f"memo decision is {decision}; Harvester activation requires decision: draft")

    signal = parse_date(metadata.get("last_decision_change")) or parse_date(metadata.get("authored"))
    today = today or dt.date.today()
    if signal is None or (today - signal).days > FRESH_DAYS:
        if not force_stale_memo:
            raise ValueError(
                "memo freshness cannot be verified (last_decision_change absent and authored > 14 days OR both absent); "
                "re-affirm decision in frontmatter or pass --force-stale-memo to bypass"
            )
        warnings.append("stale memo freshness bypassed by --force-stale-memo")

    digest = hashlib.sha256(raw_bytes).hexdigest()
    return MemoGateResult(metadata=metadata, text=text, sha256=digest, warnings=warnings)
