"""Shared card promotion-gate evaluation."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

PROMOTION_GATE_REQUIRED_FIELDS = ["provenance_report", "source_query", "arc_rationale", "strength"]
PROMOTION_GATE_PROMOTED_STATUSES = {"report_verified", "source_verified", "print_ready"}


def as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [as_text(item) for item in value if as_text(item)]
    text = as_text(value)
    return [text] if text else []


def source_ids_for(metadata: dict[str, Any]) -> list[str]:
    values: list[str] = []
    source_id = as_text(metadata.get("source_id"))
    if source_id:
        values.append(source_id)
    for item in as_list(metadata.get("source_ids")):
        if item not in values:
            values.append(item)
    return values


@dataclass(frozen=True)
class PromotionGateState:
    ready: bool
    promoted: bool
    missing: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate(metadata: dict[str, Any], card_type: str = "source_snippet", card_status: str = "") -> PromotionGateState:
    if card_type != "source_snippet":
        return PromotionGateState(True, False, [], [])

    missing = [field for field in PROMOTION_GATE_REQUIRED_FIELDS if not as_text(metadata.get(field))]
    if not source_ids_for(metadata):
        missing.append("source_id/source_ids")
    if "warning_flags" not in metadata:
        missing.append("warning_flags")

    warnings: list[str] = []
    strength = as_text(metadata.get("strength")) or "unknown"
    warning_flags = as_list(metadata.get("warning_flags"))
    if strength in {"weak", "unknown"} and not warning_flags:
        warnings.append("thin_evidence_requires_warning_flags")
    if as_text(metadata.get("citation_status")) == "unverified" and not warning_flags:
        warnings.append("unverified_citation_requires_warning_flags")

    promoted = as_text(metadata.get("citation_status")) in PROMOTION_GATE_PROMOTED_STATUSES or card_status in {"review", "stable"}
    return PromotionGateState(not missing and not warnings, promoted, missing, warnings)


def validate_inbox_block(owner: str, metadata: dict[str, Any]) -> list[str]:
    state = evaluate(metadata, "source_snippet")
    errors: list[str] = []
    if state.missing:
        errors.append(f"{owner}: promotion gate missing field(s): {', '.join(state.missing)}")
    if "thin_evidence_requires_warning_flags" in state.warnings:
        errors.append(f"{owner}: weak/unknown strength requires warning_flags explaining thin evidence.")
    if as_text(metadata.get("citation_status")) in PROMOTION_GATE_PROMOTED_STATUSES and state.missing:
        errors.append(
            f"{owner}: citation_status {metadata.get('citation_status')} cannot be promoted without complete report-to-card provenance gates."
        )
    return errors


def validate_for_chain_lock(owner: str, metadata: dict[str, Any], card_type: str = "source_snippet", card_status: str = "") -> list[str]:
    state = evaluate(metadata, card_type, card_status)
    if state.ready:
        return []
    issues = state.missing + state.warnings
    return [f"{owner}: source card fails promotion gate: {', '.join(issues)}"]
