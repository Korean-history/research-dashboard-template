"""Build a standalone zero-server research dashboard."""
from __future__ import annotations

import argparse
import os
import json
import re
import subprocess
import sys
import datetime as dt
from collections import defaultdict
from pathlib import Path
from typing import Any

CODE_ROOT = Path(__file__).resolve().parents[1]
ROOT = CODE_ROOT
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if os.environ.get("PYTEST_CURRENT_TEST"):
    for _ambient_forbidden in ("sqlite3.dbapi2", "mcp", "anyio.abc", "httpx", "aiohttp"):
        sys.modules.pop(_ambient_forbidden, None)

from tools.lib import core, report_paths
from tools.lib.chain_role_aliases import normalize_argument_role

TEMPLATE_PATH = ROOT / "tools" / "dashboard_template.html"
OUTPUT_PATH = ROOT / "RESEARCH_DASHBOARD.html"
RETRIEVAL_INDEX_PATH = ROOT / "RETRIEVAL_INDEX.json"
SOURCE_SNIPPETS_PATH = ROOT / "source_snippets.yaml"
CARDS_INDEX_PATH = ROOT / "CARDS_INDEX.json"
CARDS_DIAGNOSTICS_PATH = ROOT / "CARDS_DIAGNOSTICS.json"
ARGUMENT_CHAINS_PATH = ROOT / "argument_chains.yaml"
MATRIX_PATH = ROOT / "argument_matrix.csv"
SOURCES_PATH = ROOT / "authority" / "sources.csv"
ENTITIES_PATH = ROOT / "authority" / "entities.csv"
TERMS_PATH = ROOT / "authority" / "terms.csv"
TAGS_PATH = ROOT / "authority" / "tags.yaml"
CARDS_SCHEMA_PATH = ROOT / "authority" / "cards_schema.yaml"
TICKETS_PATH = ROOT / "RESEARCH_TICKETS.json"
AUDIT_PATH = ROOT / "MANUSCRIPT_RISK_AUDIT.json"
IMPACT_PATH = ROOT / "CLAIM_DEPENDENCY_REPORT.json"
INBOX_PATH = ROOT / "inbox.md"
VERIFICATION_REPORTS_DIR = ROOT / "verification_reports"
WEAVER_SCAFFOLDS_DIR = ROOT / "weaver_scaffolds"
POLISH_PACKETS_DIR = ROOT / "polish_packets"
FINAL_PACKETS_DIR = ROOT / "final_packets"
PROMPTS_DIR = ROOT / "docs" / "prompts"
TELEMETRY_DIR = ROOT / "telemetry" / "sessions"

DATA_PLACEHOLDER = "<!-- DASHBOARD_DATA -->"


def configure_root(root: str | Path | None = None) -> Path:
    global ROOT, OUTPUT_PATH, RETRIEVAL_INDEX_PATH, SOURCE_SNIPPETS_PATH, CARDS_INDEX_PATH
    global CARDS_DIAGNOSTICS_PATH, ARGUMENT_CHAINS_PATH, MATRIX_PATH, SOURCES_PATH
    global ENTITIES_PATH, TERMS_PATH, TAGS_PATH, CARDS_SCHEMA_PATH, TICKETS_PATH
    global AUDIT_PATH, IMPACT_PATH, INBOX_PATH, VERIFICATION_REPORTS_DIR
    global WEAVER_SCAFFOLDS_DIR, POLISH_PACKETS_DIR, FINAL_PACKETS_DIR
    global PROMPTS_DIR, TELEMETRY_DIR

    if root is not None:
        ROOT = Path(root)
    OUTPUT_PATH = ROOT / "RESEARCH_DASHBOARD.html"
    RETRIEVAL_INDEX_PATH = ROOT / "RETRIEVAL_INDEX.json"
    SOURCE_SNIPPETS_PATH = ROOT / "source_snippets.yaml"
    CARDS_INDEX_PATH = ROOT / "CARDS_INDEX.json"
    CARDS_DIAGNOSTICS_PATH = ROOT / "CARDS_DIAGNOSTICS.json"
    ARGUMENT_CHAINS_PATH = ROOT / "argument_chains.yaml"
    MATRIX_PATH = ROOT / "argument_matrix.csv"
    SOURCES_PATH = ROOT / "authority" / "sources.csv"
    ENTITIES_PATH = ROOT / "authority" / "entities.csv"
    TERMS_PATH = ROOT / "authority" / "terms.csv"
    TAGS_PATH = ROOT / "authority" / "tags.yaml"
    CARDS_SCHEMA_PATH = ROOT / "authority" / "cards_schema.yaml"
    TICKETS_PATH = ROOT / "RESEARCH_TICKETS.json"
    AUDIT_PATH = ROOT / "MANUSCRIPT_RISK_AUDIT.json"
    IMPACT_PATH = ROOT / "CLAIM_DEPENDENCY_REPORT.json"
    INBOX_PATH = ROOT / "inbox.md"
    VERIFICATION_REPORTS_DIR = ROOT / "verification_reports"
    WEAVER_SCAFFOLDS_DIR = ROOT / "weaver_scaffolds"
    POLISH_PACKETS_DIR = ROOT / "polish_packets"
    FINAL_PACKETS_DIR = ROOT / "final_packets"
    PROMPTS_DIR = ROOT / "docs" / "prompts"
    TELEMETRY_DIR = ROOT / "telemetry" / "sessions"
    return ROOT

HBOM_LITE_SUFFIX = "_HBOM_LITE.json"
HBOM_ACCOUNT_SUFFIX = "_ACCOUNT.md"
RESEARCH_PORTFOLIO_SCHEMA_VERSION = 1
CONSUMED_HBOM_SCHEMA_VERSION = 1
RESEARCH_PORTFOLIO_HIGH_WARNING_THRESHOLD = 5
RESEARCH_PORTFOLIO_EXPOSURE_CONCENTRATION_THRESHOLD = 0.70
RESEARCH_PORTFOLIO_READY_MAX_WARNINGS = 0
RESEARCH_PORTFOLIO_READY_MIN_HIGH_CONVICTION_CLAIMS = 3
RESEARCH_PORTFOLIO_READY_MAX_AGE_DAYS = 90
RESEARCH_PORTFOLIO_NEAR_MAX_WARNINGS = 2
RESEARCH_PORTFOLIO_NEAR_MIN_HIGH_OR_MEDIUM_CLAIMS = 2
RESEARCH_PORTFOLIO_NEAR_MAX_AGE_DAYS = 180

RESEARCH_PORTFOLIO_REQUIRED_TOP_LEVEL = {
    "schema_version",
    "generated_at_utc",
    "generator",
    "run",
    "queries",
    "evidence",
    "claims",
    "next_actions",
    "warnings",
    "report_status",
    "promotion_metadata",
    "future_compatibility",
}
SUPPORT_TYPES = (
    "primary_supported",
    "secondary_supported",
    "mixed_support",
    "logseq_derived",
    "speculative",
    "unresolved",
)
SUPPORT_TYPE_BUCKET = "unknown_support_type"
KNOWN_ZONES = ("canonical", "derivative", "interpretive")
KNOWN_MODES = ("fresh_report", "comparison_report", "close_reading_report", "audit_report", "backfill")
KNOWN_PROMOTION_STATUSES = ("exploratory", "supported", "manuscript_ready_candidate")
KNOWN_TELEMETRY_FIDELITIES = (
    "complete",
    "degraded_journals",
    "degraded_uacp",
    "partial_missing",
    "not_available",
)
WELL_KNOWN_CORPORA = {
    "uacp",
    "journals",
    "endnote",
    "calibre",
    "logseq",
    "manuscript",
    "cards",
    "manuscript_memory",
    "other",
}
CONVICTION_TIERS = ("top", "high", "medium", "low", "unknown")
LIQUIDITY_TIERS = ("ready", "near", "not_ready")
STALENESS_TIERS = ("fresh", "aging", "stale")
SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}
STALENESS_RANK = {"stale": 3, "aging": 2, "fresh": 1}
CONVICTION_TIER_RANK = {
    "top": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "unknown": 0,
}


def load_json(path: Path, fallback: Any) -> Any:
    data = core.read_json(path)
    return fallback if data is None else data


def load_yaml(path: Path, fallback: Any) -> Any:
    data = core.read_yaml(path)
    return fallback if data is None else data


def load_csv(path: Path) -> list[dict[str, str]]:
    rows, errors = core.read_csv(path)
    if errors:
        print(f"WARN: {path.name}: {'; '.join(errors)}")
    return rows


def closed_count(keys: tuple[str, ...], *, extra: tuple[str, ...] = ()) -> dict[str, int]:
    return {key: 0 for key in (*keys, *extra)}


def sorted_int_counts(counts: dict[str, int]) -> dict[str, int]:
    return {key: int(counts[key]) for key in sorted(counts)}


def deep_sort(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): deep_sort(value[key]) for key in sorted(value, key=lambda item: str(item))}
    if isinstance(value, list):
        return [deep_sort(item) for item in value]
    return value


def normalize_now_utc(now_utc: str | None = None) -> tuple[dt.datetime, str]:
    if now_utc is None:
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        return now, now.isoformat()
    raw = str(now_utc).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid now_utc timestamp: {now_utc!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"Invalid now_utc timestamp (timezone required): {now_utc!r}")
    now = parsed.astimezone(dt.timezone.utc).replace(microsecond=0)
    return now, now.isoformat()


def parse_iso_datetime(value: Any) -> dt.datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.timezone.utc).replace(microsecond=0)


def dashboard_warning(category: str, path: str, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "category": category,
        "severity": "soft",
        "path": path,
        "message": message,
    }
    payload.update({key: deep_sort(value) for key, value in extra.items() if value not in (None, "", [], {})})
    return payload


def repo_relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def safe_relative_display(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def normalize_repo_path(raw_path: Any) -> str:
    return str(raw_path or "").strip().replace("\\", "/")


def resolve_path_for_presence(root: Path, display_path: str) -> tuple[Path, bool]:
    path = Path(display_path)
    if path.is_absolute():
        return path, is_relative_to(path, root)
    resolved = (root / display_path).resolve()
    return resolved, is_relative_to(resolved, root)


def should_prune_dashboard_dir(root: Path, path: Path) -> bool:
    name = path.name
    if name in {".git", "node_modules", "__pycache__", "backups"}:
        return True
    if name.startswith(".tmp-"):
        return True
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    if len(parts) >= 2 and parts[0] == "cards" and parts[1] == ".staging":
        return True
    if "tests" in parts and name == "fixtures":
        return True
    return False


def iter_hbom_lite_sidecars(root: Path) -> list[Path]:
    root = root.resolve()
    if not root.exists():
        return []
    matched: list[Path] = []
    for dirpath_raw, dirnames, filenames in os.walk(root):
        dirpath = Path(dirpath_raw)
        dirnames[:] = sorted(
            name for name in dirnames
            if not should_prune_dashboard_dir(root, dirpath / name)
        )
        for filename in sorted(filenames):
            if not filename.endswith(HBOM_LITE_SUFFIX):
                continue
            if ".tmp." in filename or filename.startswith(".tmp-"):
                continue
            matched.append(dirpath / filename)
    return sorted(matched, key=lambda item: repo_relative(root, item))


def read_sidecar_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None, "sidecar_read_error"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None, "malformed_json"
    if not isinstance(data, dict):
        return None, "type_violation"
    return data, None


def normalize_corpus(value: Any) -> str:
    corpus = str(value or "").strip()
    return corpus if corpus in WELL_KNOWN_CORPORA else "other"


def normalize_closed_enum(
    *,
    value: Any,
    known: tuple[str, ...],
    field: str,
    warning_path: str,
    warnings: list[dict[str, Any]],
    fallback: str,
) -> str:
    token = str(value or "").strip()
    if token in known:
        return token
    if token:
        warnings.append(dashboard_warning(
            "unrecognized_enum_value",
            warning_path,
            f"Unrecognized {field}: {token}",
            field=field,
            value=token,
        ))
    return fallback


def list_or_type_warning(value: Any, field: str, warning_path: str, warnings: list[dict[str, Any]]) -> list[Any] | None:
    if isinstance(value, list):
        return value
    warnings.append(dashboard_warning("type_violation", warning_path, f"{field} must be a list.", field=field))
    return None


def mapping_or_type_warning(value: Any, field: str, warning_path: str, warnings: list[dict[str, Any]]) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    warnings.append(dashboard_warning("type_violation", warning_path, f"{field} must be a mapping.", field=field))
    return None


def action_text(value: Any) -> str:
    if isinstance(value, dict):
        return core.nfc(str(value.get("action_text") or value.get("next_action") or value.get("text") or "").strip())
    return core.nfc(str(value or "").strip())


def normalized_action_list(values: list[Any]) -> list[Any]:
    normalized: list[Any] = []
    for item in values:
        if isinstance(item, dict):
            normalized.append(deep_sort(item))
        else:
            normalized.append(core.nfc(str(item)))
    return normalized


def is_simulation_sidecar(root: Path, path: Path, data: dict[str, Any]) -> bool:
    if data.get("simulation") is True:
        return True
    try:
        parts = path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        parts = path.parts
    return any(part == "codex_simulations" for part in parts)


def truncate_nfc(value: Any, limit: int = 120) -> str:
    text = core.nfc(str(value or ""))
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def conviction_tier_for_claim(claim: dict[str, Any]) -> str:
    support_type = str(claim.get("support_type") or "")
    base = {
        "primary_supported": "high",
        "mixed_support": "high",
        "secondary_supported": "medium",
        "logseq_derived": "medium",
        "speculative": "low",
        "unresolved": "low",
    }.get(support_type, "unknown")
    if base == "high" and claim.get("promotion_status") == "manuscript_ready_candidate":
        return "top"
    return base


def dominant_conviction_tier(counts: dict[str, int]) -> str | None:
    if not sum(counts.values()):
        return None
    return max(counts, key=lambda tier: (counts[tier], CONVICTION_TIER_RANK.get(tier, -1)))


def source_class_for_evidence(evidence: dict[str, Any]) -> str:
    evidence_type = str(evidence.get("evidence_type") or "").strip()
    if evidence_type in {"primary", "secondary"}:
        return evidence_type
    zone = str(evidence.get("zone") or "").strip()
    if zone == "canonical":
        return "primary"
    if zone == "derivative":
        return "secondary"
    if zone == "interpretive":
        return "interpretive"
    return "unknown"


def compute_account_summary(account: dict[str, Any]) -> dict[str, Any]:
    support_counts = closed_count(SUPPORT_TYPES, extra=(SUPPORT_TYPE_BUCKET,))
    zone_counts: dict[str, int] = defaultdict(int)
    warning_counts: dict[str, int] = defaultdict(int)
    conviction_counts = closed_count(CONVICTION_TIERS)
    evidence_corpus_counts: dict[str, int] = defaultdict(int)
    query_corpus_counts: dict[str, int] = defaultdict(int)

    for claim in account["claims"]:
        support_type = str(claim.get("support_type") or SUPPORT_TYPE_BUCKET)
        support_counts[support_type] = support_counts.get(support_type, 0) + 1
        conviction_counts[conviction_tier_for_claim(claim)] += 1
    for evidence in account["evidence"]:
        zone_counts[str(evidence.get("zone") or "unknown_zone")] += 1
        evidence_corpus_counts[str(evidence.get("corpus") or "other")] += 1
    for query in account["queries"]:
        query_corpus_counts[str(query.get("corpus") or "other")] += 1
    for warning in account["warnings"]:
        warning_counts[str(warning.get("category") or "other")] += 1

    next_action_count = len([item for item in account["next_actions"] if action_text(item)])
    next_action_count += sum(1 for claim in account["claims"] if action_text(claim.get("next_action")))

    return {
        "query_count": len(account["queries"]),
        "evidence_count": len(account["evidence"]),
        "claim_count": len(account["claims"]),
        "warning_count": len(account["warnings"]),
        "next_action_count": next_action_count,
        "support_type_distribution": sorted_int_counts(support_counts),
        "zone_distribution": sorted_int_counts(zone_counts),
        "warning_category_distribution": sorted_int_counts(warning_counts),
        "evidence_corpus_distribution": sorted_int_counts(evidence_corpus_counts),
        "query_corpus_distribution": sorted_int_counts(query_corpus_counts),
        "conviction_distribution": {
            "claims_by_conviction": sorted_int_counts(conviction_counts),
            "dominant_conviction_tier": dominant_conviction_tier(conviction_counts),
        },
    }


def compute_staleness(days: int) -> str:
    if days <= 30:
        return "fresh"
    if days <= 90:
        return "aging"
    return "stale"


def compute_liquidity(account: dict[str, Any]) -> str:
    summary = account["summary"]
    if summary["claim_count"] == 0:
        return "not_ready"
    conviction_counts = summary["conviction_distribution"]["claims_by_conviction"]
    high_claims = int(conviction_counts.get("high", 0)) + int(conviction_counts.get("top", 0))
    high_or_medium_claims = high_claims + int(conviction_counts.get("medium", 0))
    has_low_support = any(
        str(claim.get("support_type") or "") in {"speculative", "unresolved"}
        for claim in account["claims"]
    )
    warning_count = int(summary["warning_count"])
    age_days = int(account["time_in_position_days"])
    if (
        warning_count == RESEARCH_PORTFOLIO_READY_MAX_WARNINGS
        and high_claims >= RESEARCH_PORTFOLIO_READY_MIN_HIGH_CONVICTION_CLAIMS
        and not has_low_support
        and age_days < RESEARCH_PORTFOLIO_READY_MAX_AGE_DAYS
    ):
        return "ready"
    if (
        warning_count <= RESEARCH_PORTFOLIO_NEAR_MAX_WARNINGS
        and high_or_medium_claims >= RESEARCH_PORTFOLIO_NEAR_MIN_HIGH_OR_MEDIUM_CLAIMS
        and age_days < RESEARCH_PORTFOLIO_NEAR_MAX_AGE_DAYS
    ):
        return "near"
    return "not_ready"


def normalize_account(
    root: Path,
    path: Path,
    data: dict[str, Any],
    now_dt: dt.datetime,
    warnings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    warning_path = repo_relative(root, path)
    missing = sorted(RESEARCH_PORTFOLIO_REQUIRED_TOP_LEVEL - set(data))
    if missing:
        warnings.append(dashboard_warning(
            "missing_required_field",
            warning_path,
            "HBOM Lite sidecar is missing required top-level fields.",
            missing_fields=missing,
        ))
        return None
    if data.get("schema_version") != CONSUMED_HBOM_SCHEMA_VERSION:
        warnings.append(dashboard_warning(
            "unsupported_schema_version",
            warning_path,
            f"Unsupported HBOM Lite schema_version: {data.get('schema_version')!r}.",
            schema_version=data.get("schema_version"),
        ))
        return None

    run = mapping_or_type_warning(data.get("run"), "run", warning_path, warnings)
    queries_raw = list_or_type_warning(data.get("queries"), "queries", warning_path, warnings)
    evidence_raw = list_or_type_warning(data.get("evidence"), "evidence", warning_path, warnings)
    claims_raw = list_or_type_warning(data.get("claims"), "claims", warning_path, warnings)
    warnings_raw = list_or_type_warning(data.get("warnings"), "warnings", warning_path, warnings)
    next_actions_raw = list_or_type_warning(data.get("next_actions"), "next_actions", warning_path, warnings)
    if None in (run, queries_raw, evidence_raw, claims_raw, warnings_raw, next_actions_raw):
        return None

    filename_stem = path.name[:-len(HBOM_LITE_SUFFIX)]
    report_path_raw = normalize_repo_path(run.get("report_path") if run else "")
    if not report_path_raw:
        report_path_raw = f"{filename_stem}.md"
        warnings.append(dashboard_warning(
            "report_path_fallback_to_filename",
            warning_path,
            "run.report_path is absent; derived report path from sidecar filename.",
        ))
    report_fs_path, report_inside = resolve_path_for_presence(root, report_path_raw)
    report_path = repo_relative(root, report_fs_path) if report_inside else report_path_raw
    if not report_inside:
        warnings.append(dashboard_warning(
            "external_path_in_account",
            warning_path,
            "run.report_path resolves outside the repository.",
            report_path=report_path_raw,
        ))

    session_label = core.nfc(str(run.get("session_label") or "")).strip()
    position_id = session_label or (Path(report_path).stem if report_inside and report_path else filename_stem)
    report_stem = position_id or filename_stem
    md_path = warning_path[:-len(HBOM_LITE_SUFFIX)] + HBOM_ACCOUNT_SUFFIX
    telemetry_raw = run.get("telemetry") if isinstance(run, dict) else {}
    if telemetry_raw is None:
        telemetry_raw = {}
    if not isinstance(telemetry_raw, dict):
        warnings.append(dashboard_warning("type_violation", warning_path, "run.telemetry must be a mapping.", field="run.telemetry"))
        return None

    fidelity = normalize_closed_enum(
        value=telemetry_raw.get("fidelity", "not_available"),
        known=KNOWN_TELEMETRY_FIDELITIES,
        field="telemetry.fidelity",
        warning_path=warning_path,
        warnings=warnings,
        fallback="unknown_telemetry_fidelity",
    )
    mode = normalize_closed_enum(
        value=run.get("mode", ""),
        known=KNOWN_MODES,
        field="run.mode",
        warning_path=warning_path,
        warnings=warnings,
        fallback="unknown_mode",
    )

    corpus_layers: list[str] = []
    for item in run.get("corpus_layers_queried") or []:
        normalized = normalize_corpus(item)
        if normalized not in corpus_layers:
            corpus_layers.append(normalized)

    queries: list[dict[str, Any]] = []
    for item in queries_raw or []:
        if not isinstance(item, dict):
            continue
        query = deep_sort(item)
        query["corpus"] = normalize_corpus(query.get("corpus"))
        queries.append(query)

    evidence: list[dict[str, Any]] = []
    for item in evidence_raw or []:
        if not isinstance(item, dict):
            continue
        record = deep_sort(item)
        record["corpus"] = normalize_corpus(record.get("corpus"))
        record["zone"] = normalize_closed_enum(
            value=record.get("zone", ""),
            known=KNOWN_ZONES,
            field="evidence.zone",
            warning_path=warning_path,
            warnings=warnings,
            fallback="unknown_zone",
        )
        evidence.append(record)

    claims: list[dict[str, Any]] = []
    for item in claims_raw or []:
        if not isinstance(item, dict):
            continue
        claim = deep_sort(item)
        claim["support_type"] = normalize_closed_enum(
            value=claim.get("support_type", ""),
            known=SUPPORT_TYPES,
            field="claim.support_type",
            warning_path=warning_path,
            warnings=warnings,
            fallback=SUPPORT_TYPE_BUCKET,
        )
        raw_promotion = str(claim.get("promotion_status") or data.get("report_status") or "exploratory")
        claim["promotion_status"] = normalize_closed_enum(
            value=raw_promotion,
            known=KNOWN_PROMOTION_STATUSES,
            field="claim.promotion_status",
            warning_path=warning_path,
            warnings=warnings,
            fallback="unknown_promotion_status",
        )
        linked = claim.get("linked_evidence")
        if not isinstance(linked, list):
            linked = []
        claim["linked_evidence"] = [str(ref) for ref in linked if str(ref)]
        claim["conviction_tier"] = conviction_tier_for_claim(claim)
        claims.append(claim)

    account_warnings = [deep_sort(item) for item in (warnings_raw or []) if isinstance(item, dict)]
    next_actions = normalized_action_list(next_actions_raw or [])
    future_compatibility = data.get("future_compatibility") if isinstance(data.get("future_compatibility"), dict) else {}
    future_extra = {
        key: data[key]
        for key in data.keys()
        if key not in RESEARCH_PORTFOLIO_REQUIRED_TOP_LEVEL
    }
    simulation = is_simulation_sidecar(root, path, data)

    generated_dt = parse_iso_datetime(data.get("generated_at_utc"))
    if generated_dt is None:
        generated_dt = now_dt
        generated_at_utc = now_dt.isoformat()
        warnings.append(dashboard_warning(
            "invalid_generated_at_utc",
            warning_path,
            "generated_at_utc is missing, naive, or invalid; using dashboard now_utc for derived age.",
        ))
    else:
        generated_at_utc = generated_dt.isoformat()
    time_days = max(0, int((now_dt - generated_dt).total_seconds() // 86400))

    json_path = warning_path
    md_fs_path, _md_inside = resolve_path_for_presence(root, md_path)
    account = {
        "account_id": warning_path,
        "position_id": position_id,
        "report_stem": report_stem,
        "json_path": json_path,
        "md_path": md_path,
        "report_path": report_path,
        "schema_version": data.get("schema_version"),
        "generated_at_utc": generated_at_utc,
        "topic": core.nfc(str(run.get("topic") or report_stem)),
        "date": core.nfc(str(run.get("date") or "")),
        "operator": core.nfc(str(run.get("operator") or "")),
        "agent": core.nfc(str(run.get("agent") or "")),
        "session_label": session_label,
        "mode": mode,
        "simulation": simulation,
        "report_status": core.nfc(str(data.get("report_status") or "")),
        "telemetry": {
            "session_id": telemetry_raw.get("session_id"),
            "ndjson_path": telemetry_raw.get("ndjson_path"),
            "fidelity": fidelity,
        },
        "corpus_layers_queried": corpus_layers,
        "queries": queries,
        "evidence": evidence,
        "claims": claims,
        "warnings": account_warnings,
        "next_actions": next_actions,
        "files_present": {
            "report": report_fs_path.exists(),
            "json": path.exists(),
            "md": md_fs_path.exists(),
        },
        "future_compatibility": deep_sort(future_compatibility),
        "future_compatibility_extra": deep_sort(future_extra),
        "time_in_position_days": time_days,
        "staleness_tier": compute_staleness(time_days),
        "sidecar_st_mtime_ns": path.stat().st_mtime_ns,
        "is_latest_for_stem": True,
    }
    account["summary"] = compute_account_summary(account)
    account["liquidity_tier"] = compute_liquidity(account)
    return account


def mark_latest_accounts(accounts: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for account in accounts:
        grouped[str(account.get("position_id") or account.get("account_id"))].append(account)

    for position_id, group in grouped.items():
        group.sort(key=lambda item: (
            parse_iso_datetime(item.get("generated_at_utc")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
            int(item.get("sidecar_st_mtime_ns") or 0),
            str(item.get("account_id") or ""),
        ))
        for item in group:
            item["is_latest_for_stem"] = False
        group[-1]["is_latest_for_stem"] = True
        if len(group) <= 1:
            continue
        warnings.append(dashboard_warning(
            "multiple_accounts_for_report",
            str(group[-1].get("json_path") or ""),
            "Multiple HBOM Lite accounts share one position_id.",
            position_id=position_id,
            account_ids=[item["account_id"] for item in group],
        ))
        timestamp_counts: dict[str, int] = defaultdict(int)
        for item in group:
            timestamp_counts[str(item.get("generated_at_utc") or "")] += 1
        duplicates = sorted(timestamp for timestamp, count in timestamp_counts.items() if count > 1)
        if duplicates:
            warnings.append(dashboard_warning(
                "duplicate_account_timestamp",
                str(group[-1].get("json_path") or ""),
                "Multiple accounts for one position share generated_at_utc.",
                position_id=position_id,
                generated_at_utc=duplicates,
            ))
        report_paths = sorted({str(item.get("report_path") or "") for item in group if item.get("report_path")})
        if len(report_paths) > 1:
            warnings.append(dashboard_warning(
                "position_path_drift",
                str(group[-1].get("json_path") or ""),
                "Accounts sharing one position_id have divergent report_path values.",
                position_id=position_id,
                alias_paths=report_paths,
            ))
        canonical_report_path = str(group[-1].get("report_path") or "")
        for item in group:
            item["position_canonical_report_path"] = canonical_report_path


def account_sort_ts(account: dict[str, Any]) -> float:
    parsed = parse_iso_datetime(account.get("generated_at_utc"))
    return parsed.timestamp() if parsed else 0.0


def has_no_followup(account: dict[str, Any]) -> bool:
    if any(action_text(item) for item in account.get("next_actions", [])):
        return False
    return not any(action_text(claim.get("next_action")) for claim in account.get("claims", []))


def account_has_meaningful_zero(account: dict[str, Any]) -> bool:
    for query in account.get("queries", []):
        if query.get("meaningful_zero") is True and query.get("false_zero_risk_note") not in (None, ""):
            return True
    return False


def meaningful_zero_risk_note(account: dict[str, Any]) -> str:
    for query in account.get("queries", []):
        if query.get("meaningful_zero") is True:
            note = core.nfc(str(query.get("false_zero_risk_note") or "")).strip()
            if note:
                return note
    return ""


def add_drawdown_entry(
    entries: dict[tuple[str, str | None, str], dict[str, Any]],
    account: dict[str, Any],
    rule_name: str,
    severity: str,
    *,
    claim: dict[str, Any] | None = None,
    suggested_action: str = "Review this position.",
) -> None:
    claim_id = str(claim.get("claim_id") or "") if claim else None
    key = (str(account["account_id"]), claim_id, rule_name)
    entries[key] = {
        "account_id": account["account_id"],
        "position_id": account["position_id"],
        "account_topic": account["topic"],
        "claim_id": claim_id,
        "claim_text_truncated": truncate_nfc(claim.get("text")) if claim else "",
        "rule_name": rule_name,
        "severity": severity,
        "staleness_tier": account["staleness_tier"],
        "generated_at_utc": account["generated_at_utc"],
        "sidecar_st_mtime_ns": account["sidecar_st_mtime_ns"],
        "suggested_action": suggested_action,
    }


def build_drawdown_queue(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: dict[tuple[str, str | None, str], dict[str, Any]] = {}
    for account in accounts:
        if not account.get("is_latest_for_stem"):
            continue
        if account["telemetry"].get("fidelity") == "not_available":
            add_drawdown_entry(entries, account, "telemetry_unavailable", "low", suggested_action="Check telemetry provenance.")
        if any(warning.get("category") == "telemetry_stale_merge" for warning in account.get("warnings", [])):
            add_drawdown_entry(entries, account, "telemetry_stale_merge", "low", suggested_action="Refresh telemetry merge.")
        if len(account.get("corpus_layers_queried") or []) == 1:
            add_drawdown_entry(entries, account, "single_corpus_account", "low", suggested_action="Cross-check another corpus.")
        if int(account["summary"]["warning_count"]) >= RESEARCH_PORTFOLIO_HIGH_WARNING_THRESHOLD:
            add_drawdown_entry(entries, account, "account_high_warning_count", "medium", suggested_action="Clear accumulated warnings.")
        evidence_by_ref = {str(item.get("ref_id")): item for item in account.get("evidence", []) if item.get("ref_id") is not None}
        missing_link_claims: set[str] = set()
        for warning in account.get("warnings", []):
            if warning.get("category") != "claim_evidence_missing_link":
                continue
            linked_claims = warning.get("linked_claims") if isinstance(warning.get("linked_claims"), list) else []
            missing_link_claims.update(str(item) for item in linked_claims if str(item))

        for claim in account.get("claims", []):
            linked = claim.get("linked_evidence") if isinstance(claim.get("linked_evidence"), list) else []
            claim_id = str(claim.get("claim_id") or "")
            resolved = [evidence_by_ref[ref] for ref in linked if ref in evidence_by_ref]
            if not linked:
                add_drawdown_entry(entries, account, "claim_no_linked_evidence", "medium", claim=claim, suggested_action="Link supporting evidence.")
            if claim_id in missing_link_claims or any(ref not in evidence_by_ref for ref in linked):
                add_drawdown_entry(entries, account, "claim_dangling_evidence_link", "medium", claim=claim, suggested_action="Repair evidence references.")
            if claim.get("support_type") == "speculative":
                add_drawdown_entry(entries, account, "claim_speculative", "low", claim=claim, suggested_action="Add support or mark as open.")
            if claim.get("support_type") == "unresolved":
                add_drawdown_entry(entries, account, "claim_unresolved", "low", claim=claim, suggested_action="Resolve or close the claim.")
            if linked and len(resolved) == len(linked) and all(item.get("zone") == "interpretive" for item in resolved):
                add_drawdown_entry(entries, account, "claim_interpretive_only_support", "low", claim=claim, suggested_action="Attach primary or secondary evidence.")

    return sorted(
        entries.values(),
        key=lambda item: (
            -SEVERITY_RANK.get(str(item.get("severity")), 0),
            -STALENESS_RANK.get(str(item.get("staleness_tier")), 0),
            -account_sort_ts(item),
            -int(item.get("sidecar_st_mtime_ns") or 0),
            str(item.get("account_id") or ""),
            str(item.get("rule_name") or ""),
            str(item.get("claim_id") or ""),
        ),
    )


def add_hedge_entry(
    entries: dict[tuple[str, str | None, str], dict[str, Any]],
    account: dict[str, Any],
    claim: dict[str, Any] | None,
    rule_name: str,
    hedge_action: str,
    linked_evidence_count: int,
) -> None:
    claim_id = str(claim.get("claim_id") or "") if claim else None
    key = (str(account["account_id"]), claim_id, rule_name)
    entries[key] = {
        "account_id": account["account_id"],
        "position_id": account["position_id"],
        "account_topic": account["topic"],
        "claim_id": claim_id,
        "claim_text_truncated": truncate_nfc(claim.get("text")) if claim else "",
        "rule_name": rule_name,
        "hedge_action": hedge_action,
        "linked_evidence_count": linked_evidence_count,
        "generated_at_utc": account["generated_at_utc"],
        "sidecar_st_mtime_ns": account["sidecar_st_mtime_ns"],
    }


def build_hedge_needed_queue(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: dict[tuple[str, str | None, str], dict[str, Any]] = {}
    for account in accounts:
        if not account.get("is_latest_for_stem"):
            continue
        if account_has_meaningful_zero(account) and has_no_followup(account):
            note = meaningful_zero_risk_note(account)
            action = f"Re-run query with variant terms: {note}" if note else "Re-run query with variant terms; check false-zero risk note"
            add_hedge_entry(
                entries,
                account,
                None,
                "account_meaningful_zero_with_no_followup",
                action,
                0,
            )
        evidence_by_ref = {str(item.get("ref_id")): item for item in account.get("evidence", []) if item.get("ref_id") is not None}
        for claim in account.get("claims", []):
            linked = claim.get("linked_evidence") if isinstance(claim.get("linked_evidence"), list) else []
            resolved = [evidence_by_ref[ref] for ref in linked if ref in evidence_by_ref]
            if not resolved:
                continue
            corpora = {str(item.get("corpus") or "other") for item in resolved}
            zones = {str(item.get("zone") or "unknown_zone") for item in resolved}
            source_classes = {source_class_for_evidence(item) for item in resolved}
            if len(resolved) == 1:
                add_hedge_entry(entries, account, claim, "claim_thin_evidence", "Add a second linked evidence record", len(resolved))
            if len(resolved) >= 2 and len(corpora) == 1 and "other" not in corpora:
                add_hedge_entry(entries, account, claim, "single_corpus_evidence", "Cross-check via a different corpus layer", len(resolved))
            if len(resolved) >= 2 and len(zones) == 1:
                add_hedge_entry(entries, account, claim, "single_zone_evidence", "Cross-check via a different evidence zone", len(resolved))
            if source_classes == {"primary"} and not any(item.get("zone") == "derivative" for item in resolved):
                add_hedge_entry(entries, account, claim, "primary_only_no_secondary", "Add secondary-source historiographic context", len(resolved))
            if source_classes == {"secondary"} and not any(item.get("zone") == "canonical" for item in resolved):
                add_hedge_entry(entries, account, claim, "secondary_only_no_primary", "Cross-check primary source for the underlying claim", len(resolved))
            if corpora == {"logseq"}:
                add_hedge_entry(entries, account, claim, "logseq_only_no_independent_check", "Verify via UACP, journals, or EndNote", len(resolved))

    return sorted(
        entries.values(),
        key=lambda item: (
            -account_sort_ts(item),
            -int(item.get("sidecar_st_mtime_ns") or 0),
            str(item.get("account_id") or ""),
            str(item.get("claim_id") or ""),
            str(item.get("rule_name") or ""),
        ),
    )


def build_next_action_queues(accounts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    claim_entries: list[dict[str, Any]] = []
    run_entries: list[dict[str, Any]] = []
    for account in accounts:
        if not account.get("is_latest_for_stem"):
            continue
        for claim in account.get("claims", []):
            next_action = action_text(claim.get("next_action"))
            if not next_action:
                continue
            claim_entries.append({
                "account_id": account["account_id"],
                "position_id": account["position_id"],
                "account_topic": account["topic"],
                "claim_id": str(claim.get("claim_id") or ""),
                "claim_text_truncated": truncate_nfc(claim.get("text")),
                "next_action": next_action,
                "support_type": claim.get("support_type") or "",
                "linked_evidence_count": len(claim.get("linked_evidence") or []),
                "generated_at_utc": account["generated_at_utc"],
                "sidecar_st_mtime_ns": account["sidecar_st_mtime_ns"],
            })
        for index, item in enumerate(account.get("next_actions", [])):
            text = action_text(item)
            if not text:
                continue
            run_entries.append({
                "account_id": account["account_id"],
                "position_id": account["position_id"],
                "account_topic": account["topic"],
                "action_text": text,
                "index": index,
                "generated_at_utc": account["generated_at_utc"],
                "sidecar_st_mtime_ns": account["sidecar_st_mtime_ns"],
            })
    claim_entries.sort(
        key=lambda item: (
            -account_sort_ts(item),
            -int(item.get("sidecar_st_mtime_ns") or 0),
            str(item.get("claim_id") or ""),
            str(item.get("account_id") or ""),
        )
    )
    run_entries.sort(
        key=lambda item: (
            -account_sort_ts(item),
            -int(item.get("sidecar_st_mtime_ns") or 0),
            int(item.get("index") or 0),
            str(item.get("account_id") or ""),
        )
    )
    return claim_entries, run_entries


def build_research_portfolio_block(
    accounts: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    now_iso: str,
) -> dict[str, Any]:
    totals = {
        "total_accounts": len(accounts),
        "total_production_accounts": sum(1 for account in accounts if not account.get("simulation")),
        "total_simulation_accounts": sum(1 for account in accounts if account.get("simulation")),
        "total_claims": sum(int(account["summary"]["claim_count"]) for account in accounts),
        "total_evidence": sum(int(account["summary"]["evidence_count"]) for account in accounts),
        "total_queries": sum(int(account["summary"]["query_count"]) for account in accounts),
        "total_warnings_in_accounts": sum(int(account["summary"]["warning_count"]) for account in accounts),
        "total_next_actions": sum(int(account["summary"]["next_action_count"]) for account in accounts),
        "total_dashboard_warnings": len(warnings),
    }

    accounts_querying_corpus: dict[str, int] = defaultdict(int)
    queries_per_corpus: dict[str, int] = defaultdict(int)
    evidence_per_corpus: dict[str, int] = defaultdict(int)
    zone_counts: dict[str, int] = defaultdict(int)
    support_counts = closed_count(SUPPORT_TYPES, extra=(SUPPORT_TYPE_BUCKET,))
    fidelity_counts: dict[str, int] = defaultdict(int)
    warning_counts: dict[str, int] = defaultdict(int)
    conviction_claim_counts = closed_count(CONVICTION_TIERS)
    dominant_counts = closed_count(CONVICTION_TIERS)
    liquidity_counts = closed_count(LIQUIDITY_TIERS)
    staleness_counts = closed_count(STALENESS_TIERS)

    for account in accounts:
        for corpus in sorted(set(account.get("corpus_layers_queried") or [])):
            accounts_querying_corpus[corpus] += 1
        for query in account.get("queries", []):
            queries_per_corpus[str(query.get("corpus") or "other")] += 1
        zones_seen: set[str] = set()
        for evidence in account.get("evidence", []):
            corpus = str(evidence.get("corpus") or "other")
            zone = str(evidence.get("zone") or "unknown_zone")
            evidence_per_corpus[corpus] += 1
            zone_counts[zone] += 1
            zones_seen.add(zone)
        for claim in account.get("claims", []):
            support_type = str(claim.get("support_type") or SUPPORT_TYPE_BUCKET)
            support_counts[support_type] = support_counts.get(support_type, 0) + 1
            conviction_claim_counts[conviction_tier_for_claim(claim)] += 1
        fidelity_counts[str(account.get("telemetry", {}).get("fidelity") or "not_available")] += 1
        for warning in account.get("warnings", []):
            warning_counts[str(warning.get("category") or "other")] += 1
        dominant = account["summary"]["conviction_distribution"].get("dominant_conviction_tier")
        if dominant:
            dominant_counts[str(dominant)] = dominant_counts.get(str(dominant), 0) + 1
        liquidity_counts[str(account.get("liquidity_tier") or "not_ready")] += 1
        staleness_counts[str(account.get("staleness_tier") or "fresh")] += 1

    for warning in warnings:
        warning_counts[str(warning.get("category") or "other")] += 1

    canonical_count = int(zone_counts.get("canonical", 0))
    total_evidence = int(totals["total_evidence"])
    latest_accounts = [account for account in accounts if account.get("is_latest_for_stem")]
    next_action_queue, run_level_next_actions = build_next_action_queues(accounts)
    thresholds = {
        "high_warning_threshold": RESEARCH_PORTFOLIO_HIGH_WARNING_THRESHOLD,
        "exposure_concentration_threshold": RESEARCH_PORTFOLIO_EXPOSURE_CONCENTRATION_THRESHOLD,
        "ready_max_warnings": RESEARCH_PORTFOLIO_READY_MAX_WARNINGS,
        "ready_min_high_conviction_claims": RESEARCH_PORTFOLIO_READY_MIN_HIGH_CONVICTION_CLAIMS,
        "ready_max_age_days": RESEARCH_PORTFOLIO_READY_MAX_AGE_DAYS,
        "near_max_warnings": RESEARCH_PORTFOLIO_NEAR_MAX_WARNINGS,
        "near_min_high_or_medium_claims": RESEARCH_PORTFOLIO_NEAR_MIN_HIGH_OR_MEDIUM_CLAIMS,
        "near_max_age_days": RESEARCH_PORTFOLIO_NEAR_MAX_AGE_DAYS,
    }
    ready_accounts = sorted(
        [account for account in latest_accounts if account.get("liquidity_tier") == "ready"],
        key=lambda item: (-account_sort_ts(item), -int(item.get("sidecar_st_mtime_ns") or 0), str(item.get("account_id") or "")),
    )
    near_accounts = sorted(
        [account for account in latest_accounts if account.get("liquidity_tier") == "near"],
        key=lambda item: (-account_sort_ts(item), -int(item.get("sidecar_st_mtime_ns") or 0), str(item.get("account_id") or "")),
    )

    config = dict(thresholds)
    config["thresholds"] = thresholds
    config["weights"] = {}

    return {
        "schema_version": RESEARCH_PORTFOLIO_SCHEMA_VERSION,
        "consumed_hbom_schema_version": CONSUMED_HBOM_SCHEMA_VERSION,
        "now_utc": now_iso,
        "totals": totals,
        "corpus_exposure": {
            "accounts_querying_corpus": sorted_int_counts(accounts_querying_corpus),
            "queries_per_corpus": sorted_int_counts(queries_per_corpus),
            "evidence_per_corpus": sorted_int_counts(evidence_per_corpus),
            "single_corpus_accounts": [account["account_id"] for account in accounts if len(account.get("corpus_layers_queried") or []) == 1],
            "no_uacp_accounts": [account["account_id"] for account in accounts if "uacp" not in (account.get("corpus_layers_queried") or [])],
            "no_journals_accounts": [account["account_id"] for account in accounts if "journals" not in (account.get("corpus_layers_queried") or [])],
            "no_logseq_accounts": [account["account_id"] for account in accounts if "logseq" not in (account.get("corpus_layers_queried") or [])],
        },
        "zone_balance": {
            "canonical_evidence_count": canonical_count,
            "derivative_evidence_count": int(zone_counts.get("derivative", 0)),
            "interpretive_evidence_count": int(zone_counts.get("interpretive", 0)),
            "mixed_zone_accounts": sum(1 for account in accounts if len({item.get("zone") for item in account.get("evidence", [])}) > 1),
            "interpretive_only_accounts": sum(
                1 for account in accounts
                if account.get("evidence") and all(item.get("zone") == "interpretive" for item in account.get("evidence", []))
            ),
            "canonical_share_pct": round((canonical_count / total_evidence * 100) if total_evidence else 0.0, 1),
        },
        "support_type_distribution": {
            "claims_by_support_type": sorted_int_counts(support_counts),
        },
        "telemetry_distribution": {
            "accounts_by_fidelity": sorted_int_counts(fidelity_counts),
        },
        "warning_category_distribution": {
            "warnings_by_category": sorted_int_counts(warning_counts),
        },
        "drawdown_queue": build_drawdown_queue(accounts),
        "next_action_queue": next_action_queue,
        "run_level_next_actions": run_level_next_actions,
        "conviction_distribution": {
            "claims_by_conviction": sorted_int_counts(conviction_claim_counts),
            "accounts_by_dominant_conviction": sorted_int_counts(dominant_counts),
        },
        "hedge_needed_queue": build_hedge_needed_queue(accounts),
        "liquidity_distribution": {
            "accounts_by_liquidity": sorted_int_counts(liquidity_counts),
        },
        "liquidity_cue": {
            "ready": [str(account.get("position_id") or account.get("account_id")) for account in ready_accounts],
            "near": [str(account.get("position_id") or account.get("account_id")) for account in near_accounts],
        },
        "staleness_distribution": {
            "accounts_by_staleness": sorted_int_counts(staleness_counts),
        },
        "config": config,
        "simulation_summary": {
            "production_count": totals["total_production_accounts"],
            "simulation_count": totals["total_simulation_accounts"],
            "production_accounts": totals["total_production_accounts"],
            "simulation_accounts": totals["total_simulation_accounts"],
            "has_production_accounts": totals["total_production_accounts"] > 0,
            "has_simulation_accounts": totals["total_simulation_accounts"] > 0,
        },
    }


def build_research_portfolio_state(root: Path = ROOT, *, now_utc: str | None = None) -> dict[str, Any]:
    root = Path(root).resolve()
    now_dt, now_iso = normalize_now_utc(now_utc)
    portfolio_warnings: list[dict[str, Any]] = []
    accounts: list[dict[str, Any]] = []

    for path in iter_hbom_lite_sidecars(root):
        warning_path = repo_relative(root, path)
        data, read_error = read_sidecar_json(path)
        if read_error:
            portfolio_warnings.append(dashboard_warning(read_error, warning_path, f"Could not read HBOM Lite sidecar: {read_error}."))
            continue
        assert data is not None
        account = normalize_account(root, path, data, now_dt, portfolio_warnings)
        if account is not None:
            accounts.append(account)

    accounts.sort(key=lambda item: str(item.get("account_id") or ""))
    mark_latest_accounts(accounts, portfolio_warnings)
    portfolio = build_research_portfolio_block(accounts, portfolio_warnings, now_iso)
    return {
        "research_accounts": accounts,
        "research_portfolio": portfolio,
        "research_portfolio_warnings": portfolio_warnings,
    }


def by_key(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {str(row.get(key, "")): row for row in rows if row.get(key)}


def group_by_key(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = row.get(key)
        if value:
            grouped[str(value)].append(row)
    return dict(grouped)


def iso_from_mtime(path: Path) -> str:
    return dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).replace(microsecond=0).isoformat()


def safe_read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return core.nfc(path.read_text(encoding="utf-8"))


def schema_card_types() -> list[str]:
    schema = load_yaml(CARDS_SCHEMA_PATH, {})
    raw = schema.get("card_types", {}) if isinstance(schema, dict) else {}
    if isinstance(raw, dict):
        return list(raw)
    return list(raw or [])


def card_id_minute(card_id: str, filename: str) -> str:
    match = re.search(r"(\d{12})_\d{2}", card_id or filename)
    return match.group(1) if match else ""


def iso_from_card_minute(minute: str) -> str:
    if not re.fullmatch(r"\d{12}", minute or ""):
        return ""
    return f"{minute[0:4]}-{minute[4:6]}-{minute[6:8]}T{minute[8:10]}:{minute[10:12]}:00"


def effective_card_created(card: dict[str, Any]) -> str:
    created = str(card.get("created") or "").strip()
    minute = str(card.get("minute") or "").strip()
    minute_iso = iso_from_card_minute(minute)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", created):
        created_day = created.replace("-", "")
        if minute_iso and minute.startswith(created_day):
            return minute_iso
        return f"{created}T00:00:00"
    return created or minute_iso or str(card.get("mtime_iso") or "")


def card_order_key(card: dict[str, Any]) -> tuple[str, str, str]:
    return (
        effective_card_created(card),
        str(card.get("mtime_iso") or ""),
        str(card.get("id") or ""),
    )


def walk_cards_state() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cards_dir = ROOT / "cards"
    card_types = schema_card_types()
    cards: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)

    for card_type in card_types:
        directory = cards_dir / card_type
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            metadata, _, errors = core.read_markdown_card(path)
            if errors:
                continue
            card_id = str(metadata.get("id", "") or path.stem)
            declared_type = str(metadata.get("card_type", "") or card_type)
            created = str(metadata.get("created", "") or "")
            counts[declared_type] += 1
            cards.append({
                "id": card_id,
                "card_type": declared_type,
                "created": created,
                "path": safe_relative_display(ROOT, path),
                "mtime_iso": iso_from_mtime(path),
                "minute": card_id_minute(card_id, path.name),
            })

    cards_sorted = sorted(cards, key=card_order_key, reverse=True)
    state = {
        "cards_total": len(cards),
        "cards_by_type": dict(sorted(counts.items())),
        "cards_recent_ids": [item["id"] for item in cards_sorted[:5]],
    }
    return state, cards


def inbox_state() -> dict[str, Any]:
    if not INBOX_PATH.exists():
        return {
            "inbox_present": False,
            "inbox_size_bytes": 0,
            "inbox_mtime_iso": "",
            "inbox_content": "",
        }
    return {
        "inbox_present": True,
        "inbox_size_bytes": INBOX_PATH.stat().st_size,
        "inbox_mtime_iso": iso_from_mtime(INBOX_PATH),
        "inbox_content": safe_read_text(INBOX_PATH),
    }


def last_ingest_state(cards: list[dict[str, Any]]) -> dict[str, Any]:
    if not cards:
        return {"last_ingest_iso": "", "last_ingest_count": 0}
    newest = max(cards, key=card_order_key)
    minute = newest.get("minute") or ""
    if minute:
        count = sum(1 for item in cards if item.get("minute") == minute)
    else:
        count = 1
    return {
        "last_ingest_iso": iso_from_card_minute(minute) or effective_card_created(newest) or newest.get("mtime_iso", ""),
        "last_ingest_count": count,
    }


def parse_verdict(text: str) -> str:
    match = re.search(r"Verdict:\*\*\s*(PASS|REVIEW|FAIL)|Verdict:\s*(PASS|REVIEW|FAIL)", text, re.IGNORECASE)
    if not match:
        return ""
    return (match.group(1) or match.group(2) or "").upper()


def parse_draft_basename(text: str, path: Path) -> str:
    heading = re.search(r"^#\s+Verification Report\s+-\s+(.+?)\s+against", text, re.MULTILINE)
    if heading:
        return heading.group(1).strip()
    footer = re.search(r"DOCX/MD:\s*`([^`]+)`", text)
    if footer:
        return Path(footer.group(1)).name
    return path.stem


def weaver_safe_filename(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    text = re.sub(r"_+", "_", text).strip("._")
    return text[:80] or "argument_chain"


def file_artifact_meta(root: Path, path: Path, *, present_label: str = "ready") -> dict[str, Any]:
    exists = path.exists()
    payload: dict[str, Any] = {
        "present": exists,
        "status": present_label if exists else "missing",
        "path": safe_relative_display(root, path),
        "mtime_iso": iso_from_mtime(path) if exists else "",
        "size_bytes": path.stat().st_size if exists else 0,
    }
    return payload


def weaver_artifact_stage(artifacts: dict[str, dict[str, Any]]) -> tuple[str, str]:
    packet_ready = artifacts["packet"]["present"]
    scaffold_ready = artifacts["scaffold"]["present"]
    report_ready = artifacts["verification_report"]["present"]
    verdict = str(artifacts["verification_report"].get("verdict") or "").upper()

    if report_ready and verdict == "PASS":
        return "verified_pass", "Verified: PASS"
    if report_ready:
        return "verification_review", f"Verified: {verdict or 'REVIEW'}"
    if scaffold_ready:
        return "needs_verification", "Scaffold exists; verify next"
    if packet_ready or artifacts["verifier_chain"]["present"]:
        return "packet_ready", "Packet ready"
    return "not_prepared", "Not prepared"


def weaver_artifact_next_action(stage: str) -> str:
    if stage == "verified_pass":
        return "Ready for Human lock or Claude polish packet."
    if stage == "verification_review":
        return "Open the verification report, resolve review items, then rerun the verifier."
    if stage == "needs_verification":
        return "Run the verifier command for this scaffold."
    if stage == "packet_ready":
        return "Fill or regenerate the scaffold, then run verification."
    return "Run the Weaver prep command for the selected chain."


CLAUDE_POLISH_OUTPUT_PLACEHOLDER = "[Claude polished prose goes here."


def polish_output_is_shell(path: Path) -> bool:
    if not path.exists():
        return False
    return CLAUDE_POLISH_OUTPUT_PLACEHOLDER in safe_read_text(path)


def parse_polish_verdict(text: str) -> str:
    match = re.search(r"\*\*Verification verdict:\*\*\s*(PASS|REVIEW|FAIL|UNKNOWN)", text, re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).upper()


def parse_marker_drift_status(text: str) -> str:
    match = re.search(r"\*\*Marker drift status:\*\*\s*(PASS|REVIEW|FAIL|UNKNOWN)", text, re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).upper()


def Human_final_safe_filename(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "").strip())
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:80] or "Human_final"


def polish_artifact_stage(artifacts: dict[str, dict[str, Any]]) -> tuple[str, str]:
    packet_ready = artifacts["packet"]["present"]
    output_ready = artifacts["output"]["present"]
    output_shell = bool(artifacts["output"].get("is_shell"))

    if output_ready and not output_shell:
        return "output_ready", "Claude output ready"
    if output_ready and output_shell:
        return "output_shell", "Output shell ready"
    if packet_ready:
        return "packet_ready", "Polish packet ready"
    return "not_prepared", "Polish packet missing"


def polish_artifact_next_action(stage: str) -> str:
    if stage == "output_ready":
        return "Open the Claude output and prepare Human final review."
    if stage == "output_shell":
        return "Send the polish packet to Claude, then replace the output shell with Claude's response."
    if stage == "packet_ready":
        return "Open the polish packet and send it to Claude."
    return "Run the Claude polish packet command after Human locks the scaffold."


def build_polish_artifacts_state(
    argument_chains: dict[str, Any],
    *,
    root: Path = ROOT,
    out_dir: Path | None = None,
    final_dir: Path | None = None,
) -> dict[str, Any]:
    out_dir = out_dir or (root / "polish_packets")
    final_dir = final_dir or (root / "final_packets")
    by_chain_id: dict[str, Any] = {}
    summary_counts: dict[str, int] = defaultdict(int)
    chains = argument_chains.get("chains", []) if isinstance(argument_chains, dict) else []

    for chain in chains:
        if not isinstance(chain, dict):
            continue
        chain_id = str(chain.get("chain_id") or chain.get("id") or "").strip()
        if not chain_id:
            continue
        stem = weaver_safe_filename(chain_id)
        packet = out_dir / f"claude_polish_packet_{stem}.md"
        output = out_dir / f"claude_polished_output_{stem}.md"
        final_stem = Human_final_safe_filename(f"claude_polished_output_{stem}")
        final_packet = final_dir / f"Human_final_packet_{final_stem}.md"
        final_report = final_dir / f"Human_final_verification_{final_stem}.md"
        artifacts = {
            "packet": file_artifact_meta(root, packet),
            "output": file_artifact_meta(root, output),
            "final_packet": file_artifact_meta(root, final_packet),
            "final_verification_report": file_artifact_meta(root, final_report),
        }
        if packet.exists():
            artifacts["packet"]["verification_verdict"] = parse_polish_verdict(safe_read_text(packet))
        else:
            artifacts["packet"]["verification_verdict"] = ""
        if output.exists():
            artifacts["output"]["is_shell"] = polish_output_is_shell(output)
            artifacts["output"]["status"] = "shell" if artifacts["output"]["is_shell"] else "ready"
        else:
            artifacts["output"]["is_shell"] = False
        if final_packet.exists():
            final_text = safe_read_text(final_packet)
            artifacts["final_packet"]["verification_verdict"] = parse_polish_verdict(final_text)
            artifacts["final_packet"]["marker_drift_status"] = parse_marker_drift_status(final_text)
        else:
            artifacts["final_packet"]["verification_verdict"] = ""
            artifacts["final_packet"]["marker_drift_status"] = ""
        if final_report.exists():
            artifacts["final_verification_report"]["verdict"] = parse_verdict(safe_read_text(final_report)) or "REVIEW"
        else:
            artifacts["final_verification_report"]["verdict"] = ""
        stage, label = polish_artifact_stage(artifacts)
        summary_counts[stage] += 1
        by_chain_id[chain_id] = {
            "chain_id": chain_id,
            "stem": stem,
            "final_stem": final_stem,
            "stage": stage,
            "label": label,
            "next_action": polish_artifact_next_action(stage),
            "artifacts": artifacts,
        }

    return {
        "schema_version": 1,
        "out_dir": safe_relative_display(root, out_dir),
        "summary": dict(sorted(summary_counts.items())),
        "by_chain_id": by_chain_id,
    }


def build_weaver_artifacts_state(
    argument_chains: dict[str, Any],
    *,
    root: Path = ROOT,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    out_dir = out_dir or (root / "weaver_scaffolds")
    by_chain_id: dict[str, Any] = {}
    summary_counts: dict[str, int] = defaultdict(int)
    chains = argument_chains.get("chains", []) if isinstance(argument_chains, dict) else []

    for chain in chains:
        if not isinstance(chain, dict):
            continue
        chain_id = str(chain.get("chain_id") or chain.get("id") or "").strip()
        if not chain_id:
            continue
        stem = weaver_safe_filename(chain_id)
        packet = out_dir / f"weaver_packet_{stem}.md"
        verifier_chain = out_dir / f"weaver_chain_{stem}.yaml"
        scaffold = out_dir / f"weaver_scaffold_{stem}.md"
        verification_report = out_dir / f"weaver_verification_{stem}.md"
        artifacts = {
            "packet": file_artifact_meta(root, packet),
            "verifier_chain": file_artifact_meta(root, verifier_chain),
            "scaffold": file_artifact_meta(root, scaffold),
            "verification_report": file_artifact_meta(root, verification_report),
        }
        if verification_report.exists():
            report_text = safe_read_text(verification_report)
            artifacts["verification_report"]["verdict"] = parse_verdict(report_text) or "REVIEW"
            artifacts["verification_report"]["draft_basename"] = parse_draft_basename(report_text, verification_report)
        else:
            artifacts["verification_report"]["verdict"] = ""
            artifacts["verification_report"]["draft_basename"] = ""
        stage, label = weaver_artifact_stage(artifacts)
        summary_counts[stage] += 1
        by_chain_id[chain_id] = {
            "chain_id": chain_id,
            "stem": stem,
            "stage": stage,
            "label": label,
            "next_action": weaver_artifact_next_action(stage),
            "artifacts": artifacts,
        }

    return {
        "schema_version": 1,
        "out_dir": safe_relative_display(root, out_dir),
        "summary": dict(sorted(summary_counts.items())),
        "by_chain_id": by_chain_id,
    }


def verification_reports_state() -> dict[str, Any]:
    reports = sorted(
        VERIFICATION_REPORTS_DIR.glob("verification_*.md") if VERIFICATION_REPORTS_DIR.exists() else [],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not reports:
        return {"last_verification": None, "older_verification_reports": []}

    def report_meta(path: Path, include_content: bool = False) -> dict[str, Any]:
        content = safe_read_text(path)
        payload: dict[str, Any] = {
            "path": safe_relative_display(ROOT, path),
            "absolute_path": str(path),
            "draft_basename": parse_draft_basename(content, path),
            "verdict": parse_verdict(content) or "REVIEW",
            "mtime_iso": iso_from_mtime(path),
        }
        if include_content:
            payload["content"] = content
        return payload

    return {
        "last_verification": report_meta(reports[0], include_content=True),
        "older_verification_reports": [report_meta(path) for path in reports[1:6]],
    }


def build_cards_status_state(validate_build: bool = False) -> dict[str, str]:
    if not validate_build:
        return {
            "build_cards_status": "drift",
            "build_cards_message": "D4 deferred per CLAUDE.md. (Validation skipped, run with --validate-build)",
        }

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    try:
        result = subprocess.run(
            [sys.executable, "tools/build_cards.py", "--validate-only"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"build_cards_status": "fail", "build_cards_message": "build_cards.py --validate-only timed out after 60s."}
    except OSError as exc:
        return {"build_cards_status": "fail", "build_cards_message": str(exc)}

    combined = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    tail = core.nfc(combined.strip()[-600:])
    if result.returncode == 0:
        return {"build_cards_status": "clean", "build_cards_message": tail or "build_cards.py --validate-only passed."}
    if result.returncode == 1:
        return {"build_cards_status": "drift", "build_cards_message": "D4 deferred per CLAUDE.md. " + tail}
    return {"build_cards_status": "fail", "build_cards_message": tail or f"build_cards.py exited {result.returncode}."}


def prompt_contents() -> dict[str, str]:
    return {
        "harvester": safe_read_text(PROMPTS_DIR / "harvester_prompt.md"),
        "weaver": safe_read_text(PROMPTS_DIR / "weaver_prompt.md"),
        "polish": safe_read_text(PROMPTS_DIR / "claude_polish_prompt.md"),
        "quickref": safe_read_text(PROMPTS_DIR / "cards_schema_quickref.md"),
    }


def report_texts_state(snippets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    reports: set[str] = set()
    for snippet in snippets:
        reports.update(str(report).strip() for report in (snippet.get("report_files") or []) if str(report).strip())

    payload: dict[str, dict[str, Any]] = {}
    root = ROOT.resolve()
    for report in sorted(reports):
        normalized = report.replace("\\", "/").strip()
        if not normalized:
            continue
        path = report_paths.resolve(ROOT, normalized) or (ROOT / normalized).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            payload[normalized] = {
                "path": normalized,
                "absolute_path": "",
                "exists": False,
                "content": "Report path is outside the project workspace.",
            }
            continue
        payload[normalized] = {
            "path": normalized,
            "absolute_path": str(path),
            "exists": path.exists(),
            "content": safe_read_text(path) if path.exists() else "Report file not found.",
        }
    return payload


def build_harness_state(validate_build: bool = False) -> dict[str, Any]:
    card_state, cards = walk_cards_state()
    state: dict[str, Any] = {}
    state.update(card_state)
    state.update(inbox_state())
    state.update(last_ingest_state(cards))
    state.update(verification_reports_state())
    state.update(build_cards_status_state(validate_build))
    state["prompts"] = prompt_contents()
    return state


def telemetry_state() -> dict[str, Any]:
    telemetry_kinds = ("query_log", "card_events", "decision_events", "backtest_results", "merged")
    sessions: list[dict[str, Any]] = []
    if TELEMETRY_DIR.exists():
        by_session: dict[str, dict[str, Path]] = defaultdict(dict)
        for path in TELEMETRY_DIR.glob("*.ndjson"):
            for kind in telemetry_kinds:
                prefix = f"{kind}_"
                if path.stem.startswith(prefix):
                    session_id = path.stem[len(prefix):]
                    if session_id:
                        by_session[session_id][kind] = path
                    break

        def telemetry_line_count(path: Path | None) -> int:
            if path is None or not path.exists():
                return 0
            return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())

        def session_mtime(item: tuple[str, dict[str, Path]]) -> float:
            return max((path.stat().st_mtime for path in item[1].values()), default=0.0)

        for session_id, paths in sorted(by_session.items(), key=session_mtime, reverse=True)[:5]:
            query_path = paths.get("query_log")
            merged_path = paths.get("merged")
            call_source = query_path or merged_path
            latest_meta: dict[str, Any] = {}
            calls: list[dict[str, Any]] = []
            db_paths: set[str] = set()
            if call_source and call_source.exists():
                for line in call_source.read_text(encoding="utf-8").splitlines()[-60:]:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if item.get("event") != "mcp_call" and call_source == merged_path:
                        continue
                    meta = item.get("response_server_metadata")
                    if isinstance(meta, dict) and meta:
                        latest_meta = meta
                        db_path = str(meta.get("db_path", "") or "")
                        if db_path:
                            db_paths.add(db_path)
                        calls.append({
                            "timestamp": item.get("timestamp", ""),
                            "mcp": item.get("mcp", ""),
                            "tool": item.get("tool", ""),
                            "db_path": db_path,
                            "corpus_shape": meta.get("corpus_shape", {}),
                            "wrapper_warnings": item.get("wrapper_warnings", []),
                        })
            corpus_shape = latest_meta.get("corpus_shape", {}) if isinstance(latest_meta, dict) else {}
            metadata_count = int(
                corpus_shape.get("metadata_row_count", 0)
                or corpus_shape.get("metadata", 0)
                or corpus_shape.get("records", 0)
                or 0
            ) if isinstance(corpus_shape, dict) else 0
            full_text_count = int(
                corpus_shape.get("full_text_row_count", 0)
                or corpus_shape.get("full_text", 0)
                or 0
            ) if isinstance(corpus_shape, dict) else 0
            source_counts = {kind: telemetry_line_count(paths.get(kind)) for kind in telemetry_kinds if paths.get(kind)}
            source_line_count = sum(count for kind, count in source_counts.items() if kind != "merged")
            merged_count = int(source_counts.get("merged") or 0)
            merged_mtime = paths["merged"].stat().st_mtime if "merged" in paths else 0.0
            latest_source_mtime = max(
                (path.stat().st_mtime for kind, path in paths.items() if kind != "merged"),
                default=0.0,
            )
            stale_merge = bool(merged_path and (source_line_count > merged_count or latest_source_mtime > merged_mtime))
            if stale_merge:
                chip = "amber"
            elif not query_path and source_counts:
                chip = "amber"
            elif metadata_count and full_text_count == 0:
                chip = "red"
            elif metadata_count and full_text_count < (metadata_count / 2):
                chip = "amber"
            else:
                chip = "green"
            if calls:
                db_summary = (
                    f"DB consistent across {len(calls)} calls"
                    if len(db_paths) <= 1
                    else f"session straddled {len(db_paths)} databases"
                )
            else:
                visible_counts = ", ".join(f"{kind} {count}" for kind, count in source_counts.items())
                db_summary = f"no query calls; {visible_counts}" if visible_counts else "no telemetry rows"
            if stale_merge:
                db_summary = f"{db_summary}; stale merge"
            display_path = query_path or merged_path or next(iter(paths.values()))
            sessions.append({
                "session_id": session_id,
                "path": safe_relative_display(ROOT, display_path),
                "mtime_iso": iso_from_mtime(display_path),
                "server_metadata": latest_meta,
                "active_db_chip": chip,
                "db_summary": db_summary,
                "db_paths": sorted(db_paths),
                "source_counts": source_counts,
                "stale_merge": stale_merge,
                "source_files": {kind: safe_relative_display(ROOT, path) for kind, path in sorted(paths.items())},
                "calls": calls[-20:],
            })
    latest_session = sessions[0] if sessions else {}
    aggregate_source_counts: dict[str, int] = {}
    for session in sessions:
        for kind, count in (session.get("source_counts") or {}).items():
            aggregate_source_counts[kind] = aggregate_source_counts.get(kind, 0) + int(count or 0)
    return {
        "sessions": sessions,
        "session_count": len(sessions),
        "latest_session_id": latest_session.get("session_id"),
        "active_db_chip": latest_session.get("active_db_chip"),
        "stale_merge_count": sum(1 for session in sessions if session.get("stale_merge")),
        "missing_query_log_count": sum(
            1
            for session in sessions
            if (session.get("source_counts") or {}) and "query_log" not in (session.get("source_counts") or {})
        ),
        "source_counts": aggregate_source_counts,
    }


def normalize_snippet(snippet: dict[str, Any]) -> dict[str, Any]:
    list_fields = {"arc_ids", "claim_ids", "source_ids", "report_files", "entity_ids", "term_ids", "tags", "chapters", "warning_flags"}
    normalized: dict[str, Any] = {}
    for key, value in snippet.items():
        if key in list_fields:
            normalized[key] = value if isinstance(value, list) else core.split_values(str(value))
        elif value is None:
            normalized[key] = ""
        else:
            normalized[key] = value
    return normalized


def merge_snippets(legacy_snippets: list[dict[str, Any]], card_snippets: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    snippets: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicates: list[str] = []

    for snippet in legacy_snippets:
        normalized = normalize_snippet(snippet)
        snippet_id = str(normalized.get("snippet_id", "")).strip()
        if snippet_id:
            seen.add(snippet_id)
        snippets.append(normalized)

    for snippet in card_snippets:
        normalized = normalize_snippet(snippet)
        snippet_id = str(normalized.get("snippet_id", "")).strip()
        if snippet_id and snippet_id in seen:
            duplicates.append(snippet_id)
            continue
        if snippet_id:
            seen.add(snippet_id)
        snippets.append(normalized)

    return snippets, duplicates


def normalize_chain(chain: dict[str, Any]) -> dict[str, Any]:
    chain_id = str(chain.get("id") or chain.get("chain_id") or "").strip()
    if not chain_id:
        return {}
    raw_items = chain.get("items", chain.get("cards", []))
    items: list[dict[str, Any]] = []
    if isinstance(raw_items, list):
        for index, item in enumerate(raw_items, 1):
            if not isinstance(item, dict):
                continue
            snippet_id = str(item.get("snippet_id") or item.get("card_id") or "").strip()
            missing_evidence = str(item.get("missing_evidence_needed", "") or "")
            if not snippet_id and missing_evidence:
                snippet_id = "MISSING_CARD"
            if not snippet_id:
                continue
            legacy_role = str(item.get("role", "") or "")
            legacy_map = {
                "quote": ("supporting", "quote_directly"),
                "background": ("contextual", "background_context"),
                "friction": ("supporting", "paraphrase"),
                "transition": ("contextual", "paraphrase"),
                "bridge": ("contextual", "paraphrase"),
                "climax": ("climactic", "paraphrase"),
                "setup": ("contextual", "paraphrase"),
                "proof": ("supporting", "paraphrase"),
            }
            fallback_role, fallback_policy = legacy_map.get(legacy_role, ("supporting", "paraphrase"))
            argument_role, _role_warning = normalize_argument_role(item.get("argument_role") or fallback_role)
            cited_card_ids = item.get("cited_card_ids")
            if not isinstance(cited_card_ids, list):
                cited_card_ids = [snippet_id] if snippet_id != "MISSING_CARD" else []
            items.append({
                "id": str(item.get("id") or f"{chain_id}:item:{index}"),
                "snippet_id": snippet_id,
                "card_id": snippet_id,
                "cited_card_ids": cited_card_ids,
                "argument_role": argument_role,
                "prose_policy": str(item.get("prose_policy") or fallback_policy),
                "instruction": str(item.get("instruction", "") or ""),
                "caveat": str(item.get("caveat", "") or ""),
                "override_rationale": str(item.get("override_rationale", "") or ""),
                "missing_evidence_needed": missing_evidence,
            })
    return {
        "id": chain_id,
        "chain_id": chain_id,
        "title": str(chain.get("title", "Untitled chain") or "Untitled chain"),
        "chapter": str(chain.get("chapter", "") or ""),
        "source_agent": str(chain.get("source_agent", "") or ""),
        "target": str(chain.get("target", "") or ""),
        "movement": str(chain.get("movement", "") or ""),
        "notes": str(chain.get("notes", "") or ""),
        "status": str(chain.get("status", "simulation") or "simulation"),
        "items": items,
    }


def load_argument_chains() -> dict[str, Any]:
    raw = load_yaml(ARGUMENT_CHAINS_PATH, {"schema_version": 1, "chains": []})
    if not isinstance(raw, dict):
        return {"schema_version": 1, "chains": []}
    chains = [
        normalized for chain in raw.get("chains", [])
        if isinstance(chain, dict)
        for normalized in [normalize_chain(chain)]
        if normalized
    ]
    return {
        "schema_version": raw.get("schema_version", 1),
        "purpose": raw.get("purpose", ""),
        "chains": chains,
    }


def load_tags() -> dict[str, Any]:
    raw = load_yaml(TAGS_PATH, {})
    tags: dict[str, dict[str, Any]] = {}
    categories: list[dict[str, Any]] = []
    for category in raw.get("tag_categories", []) if isinstance(raw, dict) else []:
        category_id = str(category.get("category_id", ""))
        categories.append({"category_id": category_id, "tags": [tag.get("tag_id", "") for tag in category.get("tags", [])]})
        for tag in category.get("tags", []):
            tag_id = str(tag.get("tag_id", ""))
            if tag_id:
                tags[tag_id] = {
                    "tag_id": tag_id,
                    "category_id": category_id,
                    "label": tag.get("label", ""),
                    "aliases": tag.get("aliases", []),
                }
    return {"tags": tags, "categories": categories}


def argument_readiness_widget(cards_diagnostics: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(cards_diagnostics, dict):
        cards_diagnostics = {}
    claims = cards_diagnostics.get("claims") if isinstance(cards_diagnostics.get("claims"), dict) else {}
    breakdown = {"blocked": 0, "review": 0, "usable": 0, "strong": 0}
    rows: list[dict[str, Any]] = []
    for claim_id, diag in sorted(claims.items()):
        if not isinstance(diag, dict):
            continue
        readiness = str(diag.get("argument_readiness") or "review")
        if readiness not in breakdown:
            readiness = "review"
        breakdown[readiness] += 1
        vector = diag.get("evidence_vector") if isinstance(diag.get("evidence_vector"), dict) else {}
        rows.append({
            "claim_id": claim_id,
            "argument_readiness": readiness,
            "primary_quote_count": int(vector.get("primary_quote_count") or 0),
            "source_verified_count": int(vector.get("source_verified_count") or 0),
            "active_conflict_count": int(vector.get("active_conflict_count") or 0),
            "chronology_warning_count": int(vector.get("chronology_warning_count") or 0),
            "rationale": diag.get("argument_readiness_rationale", ""),
        })
    summary = cards_diagnostics.get("summary") if isinstance(cards_diagnostics.get("summary"), dict) else {}
    if isinstance(summary.get("argument_readiness_breakdown"), dict):
        for key in breakdown:
            breakdown[key] = int(summary["argument_readiness_breakdown"].get(key) or breakdown[key])
    return {"breakdown": breakdown, "counts": breakdown, "claims": rows}


def build_state(validate_build: bool = False, *, now_utc: str | None = None) -> dict[str, Any]:
    from tools.lib.card_graph import build_card_graph
    from tools.lib.telemetry_health import build_telemetry_health

    retrieval = load_json(RETRIEVAL_INDEX_PATH, {"arcs": [], "snippets": [], "tags": {}})
    snippet_yaml = load_yaml(SOURCE_SNIPPETS_PATH, {"snippets": []})
    yaml_snippets = snippet_yaml.get("snippets", []) if isinstance(snippet_yaml, dict) else []
    cards_index = load_json(CARDS_INDEX_PATH, {"summary": {"cards": 0}, "source_snippets": []})
    cards_diagnostics = load_json(CARDS_DIAGNOSTICS_PATH, {"schema_version": 1, "summary": {}, "cards": {}, "claims": {}})
    card_snippets = cards_index.get("source_snippets", []) if isinstance(cards_index, dict) else []
    snippets, duplicate_card_snippets = merge_snippets(yaml_snippets, card_snippets)
    cards_summary = cards_index.get("summary", {}) if isinstance(cards_index, dict) else {}

    claims = load_csv(MATRIX_PATH)
    sources = load_csv(SOURCES_PATH)
    entities = load_csv(ENTITIES_PATH)
    terms = load_csv(TERMS_PATH)
    tickets = load_json(TICKETS_PATH, [])
    audit_hits = load_json(AUDIT_PATH, [])
    impact = load_json(IMPACT_PATH, {})
    argument_chains = load_argument_chains()
    weaver_artifacts = build_weaver_artifacts_state(argument_chains, root=ROOT, out_dir=WEAVER_SCAFFOLDS_DIR)
    polish_artifacts = build_polish_artifacts_state(
        argument_chains,
        root=ROOT,
        out_dir=POLISH_PACKETS_DIR,
        final_dir=FINAL_PACKETS_DIR,
    )
    _, normalized_now_utc = normalize_now_utc(now_utc)
    card_graph = build_card_graph(cards_index, now_utc=normalized_now_utc, argument_chains=argument_chains)

    portfolio_state = build_research_portfolio_state(ROOT, now_utc=now_utc)
    telemetry = telemetry_state()
    telemetry_health = build_telemetry_health(
        telemetry_state=telemetry,
        research_accounts=portfolio_state.get("research_accounts", []),
        research_portfolio=portfolio_state.get("research_portfolio", {}),
        sessions_dir=TELEMETRY_DIR,
        simulation_dir=ROOT / "codex_simulations",
        now_utc=normalized_now_utc,
    )

    state = {
        "schema_version": 1,
        "title": "Research Dashboard",
        "source_files": {
            "retrieval": RETRIEVAL_INDEX_PATH.name,
            "snippets": SOURCE_SNIPPETS_PATH.name,
            "cards": CARDS_INDEX_PATH.name,
            "argument_chains": ARGUMENT_CHAINS_PATH.name,
            "claims": MATRIX_PATH.name,
            "tickets": TICKETS_PATH.name,
            "audit": AUDIT_PATH.name,
            "impact": IMPACT_PATH.name,
        },
        "summary": {
            "arcs": len(retrieval.get("arcs", [])),
            "snippets": len(snippets),
            "legacy_snippets": len(yaml_snippets),
            "card_snippets": len(card_snippets),
            "cards": cards_summary.get("cards", 0),
            "duplicate_card_snippets": len(duplicate_card_snippets),
            "claims": len(claims),
            "tickets": len(tickets),
            "audit_hits": len(audit_hits),
            "argument_chains": len(argument_chains.get("chains", [])),
            "card_graph_nodes": card_graph["node_count"],
            "card_graph_edges": card_graph["edge_count"],
            "telemetry_health_warnings": telemetry_health["warning_counts"]["total"],
            "weaver_verified_pass": weaver_artifacts["summary"].get("verified_pass", 0),
            "polish_outputs_ready": polish_artifacts["summary"].get("output_ready", 0),
        },
        "arcs": retrieval.get("arcs", []),
        "snippets": snippets,
        "report_texts": report_texts_state(snippets),
        "card_snippet_duplicates": duplicate_card_snippets,
        "claims": claims,
        "claims_by_id": by_key(claims, "claim_id"),
        "sources": sources,
        "sources_by_id": by_key(sources, "source_id"),
        "entities_by_id": by_key(entities, "entity_id"),
        "terms_by_id": by_key(terms, "term_id"),
        "tickets": tickets,
        "tickets_by_claim": group_by_key(tickets, "claim_id"),
        "audit_hits": audit_hits,
        "audit_by_claim": group_by_key(audit_hits, "claim_id"),
        "impact": impact,
        "tags": load_tags(),
        "argument_chains": argument_chains,
        "weaver_artifacts": weaver_artifacts,
        "polish_artifacts": polish_artifacts,
        "harness": build_harness_state(validate_build),
        "telemetry": telemetry,
        "telemetry_health": telemetry_health,
        "card_graph": card_graph,
        "cards_diagnostics": cards_diagnostics,
        "argument_readiness": argument_readiness_widget(cards_diagnostics),
    }
    state.update(portfolio_state)
    return state


def inject_data(template: str, state: dict[str, Any]) -> str:
    payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True)
    payload = payload.replace("</", "<\\/")
    block = f'<script id="dashboard-data" type="application/json">\n{payload}\n</script>'
    if DATA_PLACEHOLDER not in template:
        raise RuntimeError(f"Dashboard template missing placeholder: {DATA_PLACEHOLDER}")
    return template.replace(DATA_PLACEHOLDER, block)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a standalone zero-server research dashboard.")
    parser.add_argument("--root", help="Workspace root to render; defaults to the live repo.")
    parser.add_argument("--validate-build", action="store_true", help="Run build_cards.py --validate-only validation.")
    parser.add_argument("--now-utc", help="Use a fixed UTC ISO timestamp for deterministic dashboard builds.")
    args = parser.parse_args([] if argv is None else argv)
    configure_root(args.root)

    if not TEMPLATE_PATH.exists():
        print(f"ERROR: missing template: {TEMPLATE_PATH}")
        return 1
    try:
        state = build_state(validate_build=args.validate_build, now_utc=args.now_utc)
    except (TypeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2
    html = inject_data(TEMPLATE_PATH.read_text(encoding="utf-8"), state)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH.name} with {state['summary']['arcs']} arcs and {state['summary']['snippets']} snippets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
