"""Argument-chain role normalization for Research Alpha."""
from __future__ import annotations

from typing import Any

VALID_ARGUMENT_ROLES = {"climactic", "supporting", "synthesis", "contextual"}

LEGACY_ROLE_ALIASES = {
    "climax": "climactic",
    "proof": "supporting",
    "setup": "contextual",
    "background": "contextual",
    "bridge": "contextual",
    "transition": "contextual",
    "friction": "supporting",
    "quote": "supporting",
}


def as_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_argument_role(value: Any, default: str = "supporting") -> tuple[str, str | None]:
    """Return (normalized_role, warning)."""
    raw = as_text(value)
    if not raw:
        return default, None
    if raw in VALID_ARGUMENT_ROLES:
        return raw, None
    if raw in LEGACY_ROLE_ALIASES:
        normalized = LEGACY_ROLE_ALIASES[raw]
        return normalized, f"legacy argument_role {raw!r} normalized to {normalized!r}"
    return raw, f"invalid argument_role {raw!r}; expected one of {sorted(VALID_ARGUMENT_ROLES)}"


def legacy_role_axes(item: dict[str, Any]) -> tuple[str, str, list[str]]:
    legacy_role = as_text(item.get("role"))
    fallback_policy = "paraphrase"
    warnings: list[str] = []
    if legacy_role == "quote":
        fallback_policy = "quote_directly"
    elif legacy_role == "background":
        fallback_policy = "background_context"
    role, warning = normalize_argument_role(item.get("argument_role") or legacy_role or "supporting")
    if warning:
        warnings.append(warning)
    return role, as_text(item.get("prose_policy")) or fallback_policy, warnings
