"""Python-call wrapper for MCP-like searches used by Research Alpha tooling.

This is deliberately not a live agent proxy. It records telemetry for Python
callers such as backtests and post-session Cowork exports.
"""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Callable

from tools.lib import telemetry

JOURNALS_REQUIRED_RESPONSE_FIELDS = ("elapsed_ms", "server_metadata", "query_plan", "warnings")
JOURNALS_REQUIRED_QUERY_PLAN_FIELDS = (
    "branch",
    "execution_route",
    "query_route",
    "query_terms",
    "fts_match",
    "fts_used",
    "fts_tables",
    "query_executed",
    "query_params",
    "elapsed_ms",
    "requested_route_hint",
    "route_reason",
)


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _callable_from_tool(obj: Any) -> Callable[..., Any]:
    if callable(obj):
        return obj
    for attr in ("fn", "func", "_fn"):
        candidate = getattr(obj, attr, None)
        if callable(candidate):
            return candidate
    raise TypeError(f"object is not callable: {obj!r}")


def _journals_search_callable() -> Callable[..., Any]:
    from tools import journals_mcp_server

    return _callable_from_tool(journals_mcp_server.journals_search)


def _top_hit_ids(response: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for hit in as_list(response.get("results"))[:10]:
        if not isinstance(hit, dict):
            continue
        value = hit.get("record_key") or hit.get("reference_id") or hit.get("id") or hit.get("title")
        if value is not None:
            ids.append(str(value))
    return ids


def _server_metadata(response: dict[str, Any]) -> dict[str, Any]:
    meta = response.get("server_metadata")
    if isinstance(meta, dict):
        return meta
    out: dict[str, Any] = {}
    for key in ("db_path", "corpus_shape", "query_route", "score_scope", "score_note"):
        if key in response:
            out[key] = response[key]
    return out


def _legacy_reconstruct_query_plan(response: dict[str, Any]) -> dict[str, Any]:
    return {
        "query_route": response.get("query_route"),
        "query_terms": response.get("query_terms"),
        "fts_match": response.get("fts_match"),
        "query_executed": response.get("query_executed"),
        "query_params": response.get("query_params"),
    }


def _response_query_plan(response: dict[str, Any]) -> dict[str, Any]:
    raw_query_plan = response.get("query_plan")
    if isinstance(raw_query_plan, dict):
        return raw_query_plan
    return _legacy_reconstruct_query_plan(response)


def _missing_or_null(response: dict[str, Any], field: str) -> bool:
    return field not in response or response.get(field) is None


def _journals_query_plan_degraded(response: dict[str, Any]) -> bool:
    raw_query_plan = response.get("query_plan")
    if not isinstance(raw_query_plan, dict):
        return True
    for field in JOURNALS_REQUIRED_QUERY_PLAN_FIELDS:
        if field not in raw_query_plan or raw_query_plan.get(field) is None:
            return True
    return False


def _telemetry_fidelity(mcp: str, response: dict[str, Any]) -> str:
    if mcp == "journals-library":
        if any(_missing_or_null(response, field) for field in JOURNALS_REQUIRED_RESPONSE_FIELDS):
            return "degraded_journals"
        if _journals_query_plan_degraded(response):
            return "degraded_journals"
    return str(response.get("telemetry_fidelity") or "complete")


def detect_wrapper_warnings(response: dict[str, Any], telemetry_fidelity: str | None = None) -> list[str]:
    warnings: list[str] = []
    results = as_list(response.get("results"))
    raw_total = response.get("total_matches", response.get("total_matches_exact"))
    try:
        total = int(raw_total)
    except (TypeError, ValueError):
        total = 0
    if total > 0 and not results:
        warnings.append("zero_rendered_results_with_nonzero_total")
    if (telemetry_fidelity or response.get("telemetry_fidelity")) == "degraded_journals":
        warnings.append("degraded_journals")
    return warnings


def telemetry_line(
    *,
    session_id: str,
    mcp: str,
    tool: str,
    request: dict[str, Any],
    response: dict[str, Any],
    event_source: str = "python_wrapper",
) -> dict[str, Any]:
    results = as_list(response.get("results"))
    query_plan = _response_query_plan(response)
    fidelity = _telemetry_fidelity(mcp, response)
    line = telemetry.base_event(
        session_id,
        "mcp_call",
        event_source=event_source,
        mcp=mcp,
        tool=tool,
        request=request,
        response_query_plan=query_plan,
        response_server_metadata=_server_metadata(response),
        top_hit_ids=_top_hit_ids(response),
        total_matches=response.get("total_matches"),
        returned_count=len(results),
        warnings=as_list(response.get("warnings")),
        telemetry_fidelity=fidelity,
        wrapper_warnings=detect_wrapper_warnings(response, fidelity),
    )
    return line


def call(session_id: str, mcp: str, tool: str, root: Path | None = None, **kwargs: Any) -> dict[str, Any]:
    if mcp != "journals-library" or tool != "journals_search":
        raise NotImplementedError(f"live Python wrapper only supports journals-library/journals_search, got {mcp}/{tool}")
    func = _journals_search_callable()
    signature = inspect.signature(func)
    accepted = {key: value for key, value in kwargs.items() if key in signature.parameters}
    response = func(**accepted)
    if not isinstance(response, dict):
        response = {"results": response}
    telemetry.append_query_log_line(
        session_id,
        telemetry_line(session_id=session_id, mcp=mcp, tool=tool, request=accepted, response=response),
        root,
    )
    return response


def log_external_call(session_id: str, raw: dict[str, Any], root: Path | None = None) -> None:
    mcp = str(raw.get("mcp") or raw.get("server") or raw.get("name") or "external")
    tool = str(raw.get("tool") or raw.get("method") or "unknown")
    request = raw.get("request") if isinstance(raw.get("request"), dict) else {"query": raw.get("query", "")}
    response = raw.get("response") if isinstance(raw.get("response"), dict) else raw
    telemetry.append_external_call(
        session_id,
        telemetry_line(
            session_id=session_id,
            mcp=mcp,
            tool=tool,
            request=request,
            response=response,
            event_source="external_export",
        ),
        root,
    )
