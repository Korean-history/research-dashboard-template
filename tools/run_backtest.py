"""Run Research Alpha recall backtests against seeds."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lib import mcp_wrapper
from tools.lib.telemetry import append_ndjson, ensure_session_id, session_path, utc_now

SEEDS_PATH = ROOT / "backtest_seeds.yaml"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "backtests"
LIVE_MCP_TOOLS = {"journals-library": "journals_search"}


def as_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [as_text(item) for item in value if as_text(item)]
    text = as_text(value)
    return [text] if text else []


def chapter_relevance(seed: dict[str, Any], chapter: str) -> str:
    primary = set(as_list(seed.get("primary_chapters")))
    secondary = set(as_list(seed.get("secondary_chapters")))
    if not primary and isinstance(seed.get("manuscript_propagation"), list):
        primary = {as_text(item.get("chapter")) for item in seed["manuscript_propagation"] if isinstance(item, dict)}
    if chapter in primary:
        return "primary"
    if chapter in secondary:
        return "secondary"
    return "untagged"


def fixture_path(seed_id: str, mcp: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in f"{seed_id}_{mcp}")
    return FIXTURE_DIR / f"{safe}.json"


def load_fixture(seed_id: str, mcp: str) -> dict[str, Any] | None:
    path = fixture_path(seed_id, mcp)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _score_rank(index: int) -> tuple[str, int]:
    if index <= 3:
        return "pass", index
    if index <= 10:
        return "partial", index
    return "fail", index


def score_response(response: dict[str, Any], expected: list[str]) -> tuple[str, int | None]:
    results = response.get("results", []) if isinstance(response.get("results"), list) else []
    expected_set = {item for item in expected if item}
    for index, hit in enumerate(results[:50], 1):
        if not isinstance(hit, dict):
            text = as_text(hit)
            if any(expected_item and expected_item in text for expected_item in expected_set):
                return _score_rank(index)
            continue
        for key in ("record_key", "reference_id", "id"):
            if as_text(hit.get(key)) in expected_set:
                return _score_rank(index)
        text_blob = " ".join(as_text(hit.get(key)) for key in ("title", "snippet"))
        if any(expected_item and expected_item in text_blob for expected_item in expected_set):
            return _score_rank(index)
    return "fail", None


def run_anchor(session_id: str, seed: dict[str, Any], anchor: dict[str, Any], chapter: str) -> dict[str, Any]:
    seed_id = as_text(seed.get("seed_id"))
    mcp = as_text(anchor.get("mcp"))
    query = as_text(anchor.get("query"))
    relevance = chapter_relevance(seed, chapter)
    expected = as_list(seed.get("expected_top_hits"))
    mode = "live" if mcp in LIVE_MCP_TOOLS else "fixture"
    warnings: list[str] = []
    response: dict[str, Any] | None = None

    if mode == "live":
        try:
            response = mcp_wrapper.call(session_id, mcp, LIVE_MCP_TOOLS[mcp], ROOT, query=query, route_hint=anchor.get("route_hint", "auto"), limit=50)
        except Exception as exc:
            warnings.append(f"live_call_error: {exc}")
    else:
        response = load_fixture(seed_id, mcp)
        if response is None:
            status = "error"
            return {
                "timestamp": utc_now(),
                "session_id": session_id,
                "event": "backtest_result",
                "seed_id": seed_id,
                "mcp": mcp,
                "query": query,
                "chapter_relevance": relevance,
                "mode": mode,
                "result_status": status,
                "expected_rank": None,
                "warnings": [f"missing_fixture: {fixture_path(seed_id, mcp).as_posix()}"],
            }
    if response is None:
        status, rank = "error", None
    else:
        status, rank = score_response(response, expected)
    return {
        "timestamp": utc_now(),
        "session_id": session_id,
        "event": "backtest_result",
        "seed_id": seed_id,
        "mcp": mcp,
        "query": query,
        "chapter_relevance": relevance,
        "mode": mode,
        "result_status": status,
        "expected_rank": rank,
        "expected_top_hits": expected,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run recall backtests before a Harvester session.")
    parser.add_argument("--chapter", required=True)
    parser.add_argument("--session-id")
    args = parser.parse_args(argv)
    session_id = ensure_session_id(args.session_id)

    raw = yaml.safe_load(SEEDS_PATH.read_text(encoding="utf-8")) or {}
    seeds = raw.get("seeds", []) if isinstance(raw, dict) else []
    results: list[dict[str, Any]] = []
    hard_block = False
    for seed in seeds:
        if not isinstance(seed, dict):
            continue
        for anchor in seed.get("search_anchors", []) or []:
            if not isinstance(anchor, dict):
                continue
            result = run_anchor(session_id, seed, anchor, args.chapter)
            results.append(result)
            append_ndjson(session_path("backtest_results", session_id, ROOT), result)
            if result["chapter_relevance"] == "primary" and result["result_status"] in {"fail", "error"}:
                hard_block = True
                print(f"BLOCK: {result['seed_id']} / {result['mcp']} / {result['result_status']}", file=sys.stderr)
            elif result["result_status"] in {"fail", "error"}:
                print(f"WARN: {result['seed_id']} / {result['mcp']} / {result['result_status']}")

    passes = sum(1 for item in results if item["result_status"] == "pass")
    partials = sum(1 for item in results if item["result_status"] == "partial")
    failures = sum(1 for item in results if item["result_status"] in {"fail", "error"})
    print(f"Backtest session {session_id}: {passes} pass, {partials} partial, {failures} fail/error")
    return 1 if hard_block else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
