"""Validate and lock a dashboard-exported argument chain."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lib import core
from tools.lib.chain_role_aliases import VALID_ARGUMENT_ROLES, normalize_argument_role
from tools.lib.card_id_resolver import load_resolver
from tools.lib.promotion_gates import validate_for_chain_lock
from tools.lib.telemetry import append_decision_event, ensure_session_id

ARGUMENT_CHAINS_PATH = ROOT / "argument_chains.yaml"
CARDS_DIAGNOSTICS_PATH = ROOT / "CARDS_DIAGNOSTICS.json"
VALID_PROSE_POLICIES = {"quote_directly", "paraphrase", "background_context", "footnote_only", "do_not_use_directly"}
DIRECT_QUOTE_RE = re.compile(r"\b(?:direct quote|verbatim|quote directly)\b", re.IGNORECASE)


def as_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [as_text(item) for item in value if as_text(item)]
    text = as_text(value)
    return [text] if text else []


def configure_root(root: str | Path | None = None) -> Path:
    global ROOT, ARGUMENT_CHAINS_PATH, CARDS_DIAGNOSTICS_PATH
    if root is not None:
        ROOT = Path(root)
    ARGUMENT_CHAINS_PATH = ROOT / "argument_chains.yaml"
    CARDS_DIAGNOSTICS_PATH = ROOT / "CARDS_DIAGNOSTICS.json"
    return ROOT


def resolve_card_id(raw_id: str, *, root: str | Path | None = None) -> str | None:
    resolver = load_resolver(Path(root) if root is not None else ROOT)
    return resolver.resolve(raw_id).get("canonical")


def decode_payload(payload_b64: str) -> dict[str, Any]:
    try:
        raw = base64.b64decode(payload_b64.encode("ascii"), validate=True)
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"--payload-b64 is not valid base64-encoded JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("--payload-b64 must decode to a JSON object")
    return data


def load_payload_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("--chain-yaml must point to a JSON/YAML object")
    if "chains" in data and isinstance(data["chains"], list) and data["chains"]:
        first = data["chains"][0]
        if isinstance(first, dict):
            return first
    return data


def read_cards() -> dict[str, dict[str, Any]]:
    cards: dict[str, dict[str, Any]] = {}
    cards_dir = ROOT / "cards"
    if not cards_dir.exists():
        return cards
    for path in cards_dir.glob("**/*.md"):
        metadata, body, errors = core.read_markdown_card(path)
        if errors:
            continue
        card_id = as_text(metadata.get("id"))
        if card_id:
            cards[card_id] = {"metadata": metadata, "body": body, "path": path.relative_to(ROOT).as_posix()}
    return cards


def load_diagnostics() -> dict[str, Any]:
    data = core.read_json(CARDS_DIAGNOSTICS_PATH)
    return data if isinstance(data, dict) else {}


def load_chains() -> dict[str, Any]:
    raw = core.read_yaml(ARGUMENT_CHAINS_PATH)
    if not isinstance(raw, dict):
        return {"schema_version": 2, "purpose": "Durable argument-chain simulations and approved writing briefs built from source snippet cards.", "chains": []}
    raw.setdefault("schema_version", 2)
    raw.setdefault("chains", [])
    return raw


def item_card_ids(item: dict[str, Any]) -> list[str]:
    values = as_list(item.get("cited_card_ids"))
    for key in ("card_id", "snippet_id"):
        value = as_text(item.get(key))
        if value and value not in values:
            values.append(value)
    return values


def validate_item(
    item: dict[str, Any],
    index: int,
    cards: dict[str, dict[str, Any]],
    cli_rationale: str,
    resolver: Any,
    diagnostics: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    item_id = as_text(item.get("id")) or f"item:{index}"
    role, role_warning = normalize_argument_role(item.get("argument_role") or item.get("role") or "supporting")
    if role_warning:
        (warnings if role in VALID_ARGUMENT_ROLES else errors).append(f"{item_id}: {role_warning}")

    instruction = as_text(item.get("instruction"))
    inferred_match = DIRECT_QUOTE_RE.search(instruction)
    prose_policy = as_text(item.get("prose_policy")) or ("quote_directly" if inferred_match else "paraphrase")
    if inferred_match and not as_text(item.get("prose_policy")):
        warnings.append(
            f"{item_id}: prose_policy inferred from instruction substring {inferred_match.group(0)!r}; "
            "set prose_policy: quote_directly explicitly"
        )
    if prose_policy not in VALID_PROSE_POLICIES:
        errors.append(f"{item_id}: invalid prose_policy: {prose_policy}")

    raw_cited_card_ids = item_card_ids(item)
    if not raw_cited_card_ids and not as_text(item.get("missing_evidence_needed")):
        errors.append(f"{item_id}: missing cited_card_ids/card_id")

    cited_card_ids: list[str] = []
    cited_card_id_aliases: dict[str, str] = {}
    item_rationale = as_text(item.get("override_rationale"))
    effective_rationale = item_rationale or as_text(cli_rationale)
    override = bool(effective_rationale)
    if override and len(effective_rationale.strip()) < 50:
        errors.append(f"{item_id}: override_rationale must be at least 50 substantive characters")

    for raw_card_id in raw_cited_card_ids:
        if raw_card_id == "MISSING_CARD":
            errors.append(f"{item_id}: MISSING_CARD cannot be locked into an argument chain")
            continue
        resolution = resolver.resolve(raw_card_id) if resolver else {"canonical": raw_card_id, "status": "canonical"}
        card_id = as_text(resolution.get("canonical"))
        if not card_id:
            if raw_card_id.startswith("snip:"):
                errors.append(f"{item_id}: legacy source_snippets.yaml id {raw_card_id} cannot be locked; cite a cards/** card id")
            else:
                errors.append(f"{item_id}: unknown card id: {raw_card_id}")
            continue
        if raw_card_id != card_id:
            cited_card_id_aliases[raw_card_id] = card_id
        if card_id not in cited_card_ids:
            cited_card_ids.append(card_id)
        card = cards.get(card_id)
        if not card:
            errors.append(f"{item_id}: unknown card id: {card_id}")
            continue
        metadata = card["metadata"]
        # The self-auditing build output becomes the conflict/chronology gate.
        # When absent (older alpha-activation path), preserve the original
        # promotion-gate behavior.
        if not diagnostics:
            errors.extend(validate_for_chain_lock(item_id, metadata, as_text(metadata.get("card_type")), as_text(metadata.get("status"))))
        card_diag = (diagnostics.get("cards") or {}).get(card_id, {})
        conflict_degree = ((card_diag.get("conflict_profile") or {}).get("active_conflict_degree") or "none")
        if conflict_degree in {"refuted", "superseded"}:
            errors.append(f"{item_id}: card {card_id} is {conflict_degree} and requires override_rationale")
        if prose_policy == "quote_directly":
            evidence_type = as_text(metadata.get("evidence_type"))
            if evidence_type and evidence_type not in {"primary_quote", "primary_excerpt"}:
                errors.append(f"{item_id}: quote_directly requires primary quote evidence; {card_id} is {evidence_type}")
            if not as_text(metadata.get("original_snippet")):
                errors.append(f"{item_id}: quote_directly requires original_snippet on {card_id}")
        if DIRECT_QUOTE_RE.search(as_text(item.get("instruction"))) and prose_policy != "quote_directly":
            warnings.append(f"{item_id}: instruction contains direct-quote language but prose_policy is {prose_policy}")
        if as_text(metadata.get("risk_level")) in {"high", "critical"} and not as_text(item.get("caveat")):
            msg = f"{item_id}: high-risk card {card_id} requires a caveat or override_rationale"
            if override:
                warnings.append(msg)
            else:
                errors.append(msg)

    normalized = dict(item)
    normalized["id"] = item_id
    normalized["argument_role"] = role
    normalized["prose_policy"] = prose_policy
    normalized["cited_card_ids"] = cited_card_ids
    if cited_card_id_aliases:
        normalized["cited_card_id_aliases"] = cited_card_id_aliases
    if effective_rationale:
        normalized["override_rationale"] = effective_rationale
    normalized.pop("snippet_id", None)
    normalized.pop("role", None)
    return normalized, errors, warnings


def validate_chain(payload: dict[str, Any], rationale: str = "") -> tuple[dict[str, Any], list[str], list[str]]:
    cards = read_cards()
    resolver = load_resolver(ROOT, set(cards))
    diagnostics = load_diagnostics()
    errors: list[str] = []
    warnings: list[str] = []
    chain_id = as_text(payload.get("chain_id") or payload.get("id"))
    if not chain_id:
        errors.append("chain payload missing chain_id")
        chain_id = "missing-chain-id"
    raw_items = payload.get("items", payload.get("cards", []))
    if not isinstance(raw_items, list) or not raw_items:
        errors.append(f"{chain_id}: chain requires at least one item")
        raw_items = []
    if rationale and len(rationale.strip()) < 50:
        errors.append("--rationale must be at least 50 substantive characters when overriding validation")

    normalized_items: list[dict[str, Any]] = []
    overridable_errors: list[str] = []
    for index, item in enumerate(raw_items, 1):
        if not isinstance(item, dict):
            errors.append(f"{chain_id} item #{index}: item must be a mapping")
            continue
        normalized, item_errors, item_warnings = validate_item(item, index, cards, rationale, resolver, diagnostics)
        effective_override = bool(as_text(normalized.get("override_rationale")))
        if effective_override:
            hard = [err for err in item_errors if "unknown card id" in err or "legacy source_snippets" in err or "MISSING_CARD" in err]
            overridable = [err for err in item_errors if err not in hard]
            errors.extend(hard)
            overridable_errors.extend(overridable)
            item_warnings.extend(overridable)
        else:
            errors.extend(item_errors)
        warnings.extend(item_warnings)
        normalized_items.append(normalized)

    chain = {
        "chain_id": chain_id,
        "title": as_text(payload.get("title")) or "Untitled chain",
        "chapter": as_text(payload.get("chapter")),
        "source_agent": as_text(payload.get("source_agent")) or "dashboard",
        "status": "approved",
        "locked_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "items": normalized_items,
    }
    for field in ("target", "movement", "notes"):
        if field in payload:
            chain[field] = payload[field]
    return chain, errors, warnings


def write_chain(chain: dict[str, Any]) -> None:
    raw = load_chains()
    chains = raw.get("chains", [])
    if not isinstance(chains, list):
        chains = []
    chain_id = chain["chain_id"]
    replaced = False
    for index, existing in enumerate(chains):
        if isinstance(existing, dict) and as_text(existing.get("chain_id") or existing.get("id")) == chain_id:
            chains[index] = chain
            replaced = True
            break
    if not replaced:
        chains.append(chain)
    raw["chains"] = chains
    ARGUMENT_CHAINS_PATH.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and lock an argument-chain payload exported by the dashboard.")
    parser.add_argument("--root", help="Workspace root to validate against.")
    parser.add_argument("--payload-b64", help="Base64-encoded JSON chain payload.")
    parser.add_argument("--chain-yaml", help="Path to staged chain JSON/YAML file exported by the dashboard.")
    parser.add_argument("--lock", help="Optional chain ID assertion for file-based lock commands.")
    parser.add_argument("--session-id", help="Telemetry session id.")
    parser.add_argument("--rationale", default="", help="Override rationale, at least 50 characters.")
    parser.add_argument("--dry-run", action="store_true", help="Validate only; do not update argument_chains.yaml.")
    parser.add_argument("--emit-locked-yaml", help="Write normalized locked chain YAML to this path even in dry-run mode.")
    args = parser.parse_args(argv)
    configure_root(args.root)

    session_id = ensure_session_id(args.session_id)
    try:
        if bool(args.payload_b64) == bool(args.chain_yaml):
            raise ValueError("provide exactly one of --payload-b64 or --chain-yaml")
        payload = decode_payload(args.payload_b64) if args.payload_b64 else load_payload_file(Path(args.chain_yaml))
        if args.lock and as_text(payload.get("chain_id") or payload.get("id")) not in {"", args.lock}:
            raise ValueError(f"--lock {args.lock} conflicts with payload chain_id {payload.get('chain_id') or payload.get('id')}")
        chain, errors, warnings = validate_chain(payload, args.rationale)
        for warning in warnings:
            print(f"WARN: {warning}")
        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            append_decision_event(session_id, "chain_lock_failed", chain_id=chain.get("chain_id"), errors=errors, warnings=warnings, root=ROOT)
            return 1
        if args.emit_locked_yaml:
            Path(args.emit_locked_yaml).write_text(yaml.safe_dump(chain, sort_keys=False, allow_unicode=True), encoding="utf-8")
        if not args.dry_run:
            write_chain(chain)
        append_decision_event(
            session_id,
            "chain_locked" if not args.dry_run else "chain_validated",
            chain_id=chain.get("chain_id"),
            warnings=warnings,
            prose_policy_inferred_warnings=[warning for warning in warnings if "prose_policy inferred" in warning],
            override=bool(args.rationale),
            root=ROOT,
        )
        print(f"OK: {'validated' if args.dry_run else 'locked'} chain {chain['chain_id']}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
