"""Per-session NDJSON telemetry with Dropbox/Windows-friendly appends."""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

LINE_LIMIT_BYTES = 256 * 1024
STRING_KEEP_BYTES = 64 * 1024
APPEND_RETRY_DELAYS = [0.1, 0.5, 2.0]
REQUEST_FALLBACK_BYTES = 8 * 1024


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def ensure_session_id(session_id: str | None = None) -> str:
    if session_id:
        uuid.UUID(session_id)
        return session_id
    return str(uuid.uuid4())


def session_dir(root: Path | None = None) -> Path:
    base = root or Path.cwd()
    return base / "telemetry" / "sessions"


def session_path(kind: str, session_id: str, root: Path | None = None) -> Path:
    return session_dir(root) / f"{kind}_{ensure_session_id(session_id)}.ndjson"


def fallback_dir() -> Path:
    return Path(tempfile.gettempdir()) / "research_alpha_telemetry"


def fallback_path(path: Path) -> Path:
    return fallback_dir() / path.name


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _truncate_string(value: str) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= LINE_LIMIT_BYTES:
        return value
    head = encoded[:STRING_KEEP_BYTES].decode("utf-8", errors="ignore")
    tail = encoded[-STRING_KEEP_BYTES:].decode("utf-8", errors="ignore")
    return f"{head}\n[TRUNCATED: {len(encoded) - (2 * STRING_KEEP_BYTES)} BYTES]\n{tail}"


def truncate_value(value: Any, field_path: str = "") -> Any:
    if isinstance(value, str):
        return _truncate_string(value)
    if isinstance(value, dict):
        truncated = {key: truncate_value(item, f"{field_path}.{key}" if field_path else str(key)) for key, item in value.items()}
        size = _json_size(truncated)
        if size > LINE_LIMIT_BYTES:
            return {"truncated": True, "original_size_bytes": size, "field_path": field_path}
        return truncated
    if isinstance(value, list):
        truncated = [truncate_value(item, f"{field_path}[]") for item in value]
        size = _json_size(truncated)
        if size > LINE_LIMIT_BYTES:
            return {"truncated": True, "original_size_bytes": size, "field_path": field_path}
        return truncated
    return value


def prepare_line(payload: dict[str, Any]) -> dict[str, Any]:
    line = {key: truncate_value(value, key) for key, value in payload.items()}
    if _json_size(line) <= LINE_LIMIT_BYTES:
        return line
    request = line.get("request", {})
    if _json_size(request) > REQUEST_FALLBACK_BYTES:
        request = {
            "truncated": True,
            "original_size_bytes": _json_size(request),
            "field_path": "request",
        }
    return {
        "timestamp": line.get("timestamp", utc_now()),
        "session_id": line.get("session_id", ""),
        "event": line.get("event", "telemetry_line_truncated"),
        "mcp": line.get("mcp", ""),
        "tool": line.get("tool", ""),
        "request": request,
        "truncated": True,
        "original_size_bytes": _json_size(line),
    }


def append_ndjson(path: Path, payload: dict[str, Any], retries: int | None = None, backoff: float | None = None) -> Path:
    line = json.dumps(prepare_line(payload), ensure_ascii=False, default=str) + "\n"
    targets = [path, fallback_path(path)]
    last_error: Exception | None = None
    delays = APPEND_RETRY_DELAYS if retries is None else [backoff or 0.1 for _ in range(retries)]
    for target in targets:
        for attempt, delay in enumerate(delays):
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(line)
                return target
            except PermissionError as exc:
                last_error = exc
                if attempt < len(delays) - 1:
                    time.sleep(delay)
            except OSError as exc:
                last_error = exc
                break
    if last_error:
        raise last_error
    raise RuntimeError(f"could not append telemetry to {path}")


def base_event(session_id: str, event: str, **fields: Any) -> dict[str, Any]:
    payload = {"timestamp": utc_now(), "session_id": ensure_session_id(session_id), "event": event}
    payload.update(fields)
    return payload


def append_query_log_line(session_id: str, payload: dict[str, Any], root: Path | None = None) -> Path:
    return append_ndjson(session_path("query_log", session_id, root), payload)


def append_card_event(session_id: str, event: str, root: Path | None = None, **fields: Any) -> Path:
    return append_ndjson(session_path("card_events", session_id, root), base_event(session_id, event, **fields))


def append_decision_event(session_id: str, event: str, root: Path | None = None, **fields: Any) -> Path:
    return append_ndjson(session_path("decision_events", session_id, root), base_event(session_id, event, **fields))


def append_external_call(session_id: str, payload: dict[str, Any], root: Path | None = None) -> Path:
    event = base_event(session_id, payload.get("event", "external_mcp_call"), event_source="external_export")
    event.update(payload)
    event["session_id"] = ensure_session_id(session_id)
    event.setdefault("timestamp", utc_now())
    event.setdefault("event_source", "external_export")
    return append_query_log_line(session_id, event, root)


def flush_fallback(path: Path, decision: str = "primary") -> list[Path]:
    """Move buffered fallback lines to primary path, keep them, or delete after merge."""
    fb = fallback_path(path)
    if not fb.exists() or decision == "leave":
        return []
    if decision not in {"primary", "fallback"}:
        raise ValueError("--flush-buffer must be primary, fallback, or leave")
    if decision == "fallback":
        return [fb]
    path.parent.mkdir(parents=True, exist_ok=True)
    with fb.open("r", encoding="utf-8") as src, path.open("a", encoding="utf-8", newline="\n") as dst:
        dst.write(src.read())
    try:
        os.remove(fb)
    except OSError:
        pass
    return [path]
