"""Telemetry-health diagnostics for the research dashboard."""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

QUEUE_NAMES = (
    "stale_merge",
    "missing_query_log",
    "slow_query",
    "false_zero",
    "degraded_fidelity",
    "hbom_lag",
    "simulation_unpromoted",
    "session_drought",
)

RAW_TELEMETRY_PREFIXES = ("query_log", "card_events", "decision_events", "backtest_results")


def text_value(value: Any) -> str:
    return "" if value is None else str(value).strip()


def parse_dt(value: Any) -> dt.datetime | None:
    text = text_value(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def iso_from_mtime(path: Path) -> str:
    return dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc).isoformat()


def rel_path(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path)
    except OSError:
        return ""


def health_warning(
    category: str,
    severity: str,
    path: str,
    message: str,
    *,
    queue: str = "",
    evidence: dict[str, Any] | None = None,
    session_id: str = "",
) -> dict[str, Any]:
    """Return a dashboard warning; queue/evidence are added by mutation."""
    from tools import build_dashboard

    warning = build_dashboard.dashboard_warning(category, path, message)
    warning["severity"] = severity
    if queue:
        warning["queue"] = queue
    if evidence:
        warning["evidence"] = evidence
    if session_id:
        warning["session_id"] = session_id
    return warning


def queue_item(
    queue: str,
    category: str,
    severity: str,
    path: str,
    message: str,
    *,
    evidence: dict[str, Any] | None = None,
    session_id: str = "",
) -> dict[str, Any]:
    return health_warning(
        category,
        severity,
        path,
        message,
        queue=queue,
        evidence=evidence or {},
        session_id=session_id,
    )


def raw_prefix(path: Path) -> tuple[str, str] | None:
    stem = path.stem
    for prefix in RAW_TELEMETRY_PREFIXES:
        marker = f"{prefix}_"
        if stem.startswith(marker):
            return prefix, stem[len(marker):]
    return None


def iter_raw_ndjson(sessions_dir: Path | None) -> list[tuple[Path, str, str]]:
    if sessions_dir is None or not sessions_dir.exists():
        return []
    files: list[tuple[Path, str, str]] = []
    for path in sorted(sessions_dir.glob("*.ndjson")):
        info = raw_prefix(path)
        if not info:
            continue
        kind, session_id = info
        files.append((path, kind, session_id))
    return files


def read_ndjson(path: Path) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    skipped = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows, 1
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows, skipped


def severity_for_elapsed(ms: int) -> str | None:
    if ms >= 6000:
        return "hard"
    if ms >= 3000:
        return "soft"
    return None


def is_false_zero(row: dict[str, Any]) -> bool:
    query_plan = row.get("response_query_plan")
    if not isinstance(query_plan, dict):
        return False
    if text_value(query_plan.get("route_reason")) == "malformed_query_short_circuit":
        return False
    warnings = [text_value(item).lower() for item in row.get("wrapper_warnings", []) if text_value(item)]
    if any(item.startswith("query error:") for item in warnings):
        return False
    metadata = row.get("response_server_metadata")
    corpus_shape = metadata.get("corpus_shape") if isinstance(metadata, dict) else {}
    if not isinstance(corpus_shape, dict):
        corpus_shape = {}
    metadata_count = int(corpus_shape.get("metadata_row_count") or corpus_shape.get("metadata") or 0)
    return (
        bool(query_plan.get("fts_used"))
        and metadata_count > 0
        and int(row.get("returned_count") or 0) == 0
        and int(row.get("total_matches") or 0) == 0
    )


def raw_line_diagnostics(
    sessions_dir: Path | None,
    now_dt: dt.datetime,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], dict[str, int], list[dict[str, Any]], dict[str, dict[str, int]]]:
    queues: dict[str, list[dict[str, Any]]] = {name: [] for name in QUEUE_NAMES}
    global_warnings: list[dict[str, Any]] = []
    fidelity_histogram: dict[str, int] = defaultdict(int)
    harvester_events: list[dict[str, Any]] = []
    raw_counts_by_session: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for path, kind, session_id in iter_raw_ndjson(sessions_dir):
        rows, skipped = read_ndjson(path)
        raw_counts_by_session[session_id][kind] += len(rows)
        if skipped:
            global_warnings.append(health_warning(
                "malformed_ndjson",
                "soft",
                rel_path(path),
                f"Skipped malformed NDJSON lines in {path.name}.",
                queue="global",
                evidence={"skipped_count": skipped},
                session_id=session_id,
            ))
        for row in rows:
            fidelity = text_value(row.get("telemetry_fidelity")) or "unknown"
            fidelity_histogram[fidelity] += 1
            if kind == "query_log":
                query_plan = row.get("response_query_plan")
                if isinstance(query_plan, dict):
                    elapsed_ms = int(query_plan.get("elapsed_ms") or 0)
                    severity = severity_for_elapsed(elapsed_ms)
                    if severity:
                        queues["slow_query"].append(queue_item(
                            "slow_query",
                            "slow_query",
                            severity,
                            rel_path(path),
                            f"Query elapsed_ms={elapsed_ms}.",
                            evidence={"elapsed_ms": elapsed_ms, "tool": row.get("tool", ""), "mcp": row.get("mcp", "")},
                            session_id=session_id,
                        ))
                    if is_false_zero(row):
                        queues["false_zero"].append(queue_item(
                            "false_zero",
                            "false_zero",
                            "soft",
                            rel_path(path),
                            "FTS query returned zero rows despite indexed metadata.",
                            evidence={
                                "query_terms": query_plan.get("query_terms", []),
                                "route_reason": query_plan.get("route_reason", ""),
                            },
                            session_id=session_id,
                        ))
                if fidelity in {"not_available"}:
                    severity = "hard"
                elif fidelity in {"partial_missing"} or fidelity.startswith("degraded"):
                    severity = "soft"
                else:
                    severity = ""
                if severity:
                    queues["degraded_fidelity"].append(queue_item(
                        "degraded_fidelity",
                        "degraded_fidelity",
                        severity,
                        rel_path(path),
                        f"Telemetry fidelity is {fidelity}.",
                        evidence={"telemetry_fidelity": fidelity},
                        session_id=session_id,
                    ))
            if kind == "decision_events" and row.get("event") == "harvester_session_started":
                event_dt = parse_dt(row.get("timestamp"))
                harvester_events.append({
                    "session_id": text_value(row.get("session_id") or session_id),
                    "timestamp": row.get("timestamp", ""),
                    "event_dt": event_dt,
                    "path": rel_path(path),
                    "row": row,
                })

    return queues, global_warnings, dict(fidelity_histogram), harvester_events, raw_counts_by_session


def telemetry_sessions_by_id(telemetry_state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sessions = telemetry_state.get("sessions", []) if isinstance(telemetry_state, dict) else []
    return {
        text_value(session.get("session_id")): session
        for session in sessions
        if isinstance(session, dict) and text_value(session.get("session_id"))
    }


def state_session_diagnostics(
    telemetry_state: dict[str, Any],
    queues: dict[str, list[dict[str, Any]]],
    now_dt: dt.datetime,
) -> None:
    sessions = telemetry_state.get("sessions", []) if isinstance(telemetry_state, dict) else []
    for session in sessions if isinstance(sessions, list) else []:
        if not isinstance(session, dict):
            continue
        session_id = text_value(session.get("session_id"))
        source_counts = session.get("source_counts") if isinstance(session.get("source_counts"), dict) else {}
        if session.get("stale_merge"):
            source_total = sum(int(count or 0) for kind, count in source_counts.items() if kind != "merged")
            merged_count = int(source_counts.get("merged") or 0)
            queues["stale_merge"].append(queue_item(
                "stale_merge",
                "stale_merge",
                "soft",
                text_value(session.get("path")),
                "Merged telemetry lags behind raw source telemetry.",
                evidence={"lag_events": max(0, source_total - merged_count), "source_total": source_total, "merged": merged_count},
                session_id=session_id,
            ))
        if int(source_counts.get("query_log") or 0) == 0 and any(int(source_counts.get(kind) or 0) for kind in ("decision_events", "card_events", "backtest_results", "merged")):
            queues["missing_query_log"].append(queue_item(
                "missing_query_log",
                "missing_query_log",
                "soft",
                text_value(session.get("path")),
                "Session has telemetry artifacts but no query_log rows.",
                evidence={"source_counts": dict(source_counts)},
                session_id=session_id,
            ))

    latest_dates = [parse_dt(session.get("mtime_iso")) for session in sessions if isinstance(session, dict)]
    latest_dates = [item for item in latest_dates if item is not None]
    if not latest_dates:
        queues["session_drought"].append(queue_item(
            "session_drought",
            "session_drought",
            "hard",
            "telemetry/sessions",
            "No telemetry sessions are visible.",
            evidence={"session_count": 0},
        ))
        return
    latest = max(latest_dates)
    age_days = (now_dt - latest).days
    if age_days >= 14:
        queues["session_drought"].append(queue_item(
            "session_drought",
            "session_drought",
            "soft",
            "telemetry/sessions",
            f"Most recent telemetry session is {age_days} days old.",
            evidence={"age_days": age_days, "latest_session_utc": latest.isoformat()},
        ))


def account_session_ids(research_accounts: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for account in research_accounts:
        if not isinstance(account, dict):
            continue
        telemetry = account.get("telemetry")
        if isinstance(telemetry, dict) and text_value(telemetry.get("session_id")):
            ids.add(text_value(telemetry.get("session_id")))
    return ids


def hbom_lag_diagnostics(
    harvester_events: list[dict[str, Any]],
    matched_session_ids: set[str],
    state_sessions: dict[str, dict[str, Any]],
    raw_counts_by_session: dict[str, dict[str, int]],
    queues: dict[str, list[dict[str, Any]]],
    now_dt: dt.datetime,
) -> None:
    for event in harvester_events:
        session_id = text_value(event.get("session_id"))
        if not session_id or session_id in matched_session_ids:
            continue
        state_session = state_sessions.get(session_id, {})
        state_counts = state_session.get("source_counts") if isinstance(state_session.get("source_counts"), dict) else {}
        raw_counts = raw_counts_by_session.get(session_id, {})
        if state_counts and int(state_counts.get("query_log") or 0) == 0:
            continue
        if raw_counts and int(raw_counts.get("query_log") or 0) == 0 and state_counts:
            continue
        event_dt = event.get("event_dt")
        if not isinstance(event_dt, dt.datetime):
            continue
        age_days = (now_dt - event_dt).days
        if age_days < 7:
            continue
        severity = "hard" if age_days >= 14 else "soft"
        queues["hbom_lag"].append(queue_item(
            "hbom_lag",
            "hbom_lag",
            severity,
            text_value(event.get("path")),
            f"Harvester session has not been promoted into a research account after {age_days} days.",
            evidence={"age_days": age_days, "started_at": event.get("timestamp", "")},
            session_id=session_id,
        ))


def classify_simulation(path: Path, data: dict[str, Any]) -> str:
    name = path.name.lower()
    if "comparison" in name:
        return "comparison"
    if "backtest" in name:
        return "backtest"
    if "simulation" in name:
        return "simulation"
    if "telemetry" in name:
        return "telemetry"
    if "probe_date" in data:
        return "comparison"
    return "classified"


def index_simulation_telemetry(simulation_dir: Path | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    global_warnings: list[dict[str, Any]] = []
    if simulation_dir is None or not simulation_dir.exists():
        if simulation_dir is not None:
            global_warnings.append(health_warning(
                "simulation_dir_missing",
                "soft",
                rel_path(simulation_dir),
                "Simulation telemetry directory is missing.",
                queue="global",
            ))
        return {"file_count": 0, "files": [], "last_modified_iso": None}, global_warnings

    files: list[dict[str, Any]] = []
    for path in sorted(simulation_dir.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            global_warnings.append(health_warning(
                "malformed_json",
                "soft",
                rel_path(path),
                f"Could not parse simulation telemetry JSON: {path.name}.",
                queue="global",
            ))
            continue
        if not isinstance(data, dict):
            data = {}
        files.append({
            "name": path.name,
            "path": rel_path(path),
            "kind": classify_simulation(path, data),
            "last_modified_iso": iso_from_mtime(path),
            "probe_date": data.get("probe_date", ""),
        })
    last_modified = max((item["last_modified_iso"] for item in files), default=None)
    return {"file_count": len(files), "files": files, "last_modified_iso": last_modified}, global_warnings


def simulation_unpromoted_diagnostics(
    simulation_index: dict[str, Any],
    research_accounts: list[dict[str, Any]],
    queues: dict[str, list[dict[str, Any]]],
) -> None:
    referenced = set()
    for account in research_accounts:
        if not isinstance(account, dict):
            continue
        telemetry = account.get("telemetry")
        if isinstance(telemetry, dict):
            referenced.add(text_value(telemetry.get("ndjson_path")))
            referenced.add(text_value(telemetry.get("simulation_path")))
    referenced.discard("")
    for item in simulation_index.get("files", []):
        path = text_value(item.get("path"))
        if path and path not in referenced:
            queues["simulation_unpromoted"].append(queue_item(
                "simulation_unpromoted",
                "simulation_unpromoted",
                "soft",
                path,
                "Simulation telemetry exists without a matching promoted research account.",
                evidence={"kind": item.get("kind", ""), "name": item.get("name", "")},
            ))


def warning_sort_key(warning: dict[str, Any]) -> tuple[int, str, str, str, str]:
    severity_rank = {"hard": 0, "soft": 1}.get(text_value(warning.get("severity")), 2)
    return (
        severity_rank,
        text_value(warning.get("category")),
        text_value(warning.get("session_id")),
        text_value(warning.get("path")),
        text_value(warning.get("message")),
    )


def build_counts(queues: dict[str, list[dict[str, Any]]], warnings: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    queue_counts = {name: len(queues.get(name, [])) for name in QUEUE_NAMES}
    queue_counts["total"] = sum(queue_counts.values())
    severity_counts = Counter(text_value(warning.get("severity")) or "soft" for warning in warnings)
    warning_counts = {
        "hard": int(severity_counts.get("hard", 0)),
        "soft": int(severity_counts.get("soft", 0)),
    }
    warning_counts["total"] = warning_counts["hard"] + warning_counts["soft"]
    return queue_counts, warning_counts


def build_telemetry_health(
    *,
    telemetry_state: dict[str, Any],
    research_accounts: list[dict[str, Any]],
    research_portfolio: dict[str, Any],
    sessions_dir: Path | None = None,
    simulation_dir: Path | None = None,
    now_utc: str,
) -> dict[str, Any]:
    telemetry = deepcopy(telemetry_state if isinstance(telemetry_state, dict) else {})
    accounts = deepcopy(research_accounts if isinstance(research_accounts, list) else [])
    _portfolio = deepcopy(research_portfolio if isinstance(research_portfolio, dict) else {})
    now_dt = parse_dt(now_utc) or dt.datetime.now(dt.timezone.utc)
    normalized_now = now_dt.isoformat()

    queues, global_warnings, fidelity_histogram, harvester_events, raw_counts = raw_line_diagnostics(sessions_dir, now_dt)
    state_session_diagnostics(telemetry, queues, now_dt)
    state_sessions = telemetry_sessions_by_id(telemetry)
    hbom_lag_diagnostics(harvester_events, account_session_ids(accounts), state_sessions, raw_counts, queues, now_dt)

    simulation_index, simulation_warnings = index_simulation_telemetry(simulation_dir)
    global_warnings.extend(simulation_warnings)
    simulation_unpromoted_diagnostics(simulation_index, accounts, queues)

    if not fidelity_histogram:
        fidelity_histogram = {"unknown": 0}
    queue_items = [item for name in QUEUE_NAMES for item in queues.get(name, [])]
    warnings = sorted(queue_items + global_warnings, key=warning_sort_key)
    queue_counts, warning_counts = build_counts(queues, warnings)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": normalized_now,
        "session_count": len(telemetry.get("sessions", []) or []),
        "queues": {name: sorted(queues.get(name, []), key=warning_sort_key) for name in QUEUE_NAMES},
        "queue_counts": queue_counts,
        "global_warnings": sorted(global_warnings, key=warning_sort_key),
        "warnings": warnings,
        "warning_counts": warning_counts,
        "top_warnings": warnings[:10],
        "fidelity_histogram": dict(sorted(fidelity_histogram.items())),
        "simulation_index": simulation_index,
    }
