"""Research Alpha Harvester activation gate.

This runner establishes the session contract. It does not proxy live agent MCP
calls; it validates the memo, runs recall backtests, and writes telemetry.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lib.memo_gate import validate_memo
from tools.lib.telemetry import append_decision_event, ensure_session_id, flush_fallback, session_path


def check_writeable(session_id: str) -> None:
    append_decision_event(session_id, "writeability_probe", root=ROOT)


def run_backtest(chapter: str, session_id: str) -> int:
    result = subprocess.run(
        [sys.executable, "tools/run_backtest.py", "--chapter", chapter, "--session-id", session_id],
        cwd=ROOT,
        text=True,
    )
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Activate a Research Alpha Harvester session.")
    parser.add_argument("--memo", required=True)
    parser.add_argument("--chapter", required=True)
    parser.add_argument("--session-id")
    parser.add_argument("--force-stale-memo", action="store_true")
    parser.add_argument("--flush-buffer", choices=["primary", "fallback", "leave"], default="primary")
    parser.add_argument("--skip-backtest", action="store_true", help="Development escape hatch; records a warning event.")
    args = parser.parse_args(argv)

    session_id = ensure_session_id(args.session_id)
    print(f"[HARVESTER SESSION ID: {session_id} - USE FOR MANUAL EXPORT]")
    try:
        check_writeable(session_id)
        memo = validate_memo(Path(args.memo), args.chapter, args.force_stale_memo)
        append_decision_event(
            session_id,
            "harvester_session_started",
            root=ROOT,
            memo_path=str(Path(args.memo)),
            memo_sha256=memo.sha256,
            memo_text_snapshot=memo.text,
            memo_metadata=memo.metadata,
            warnings=memo.warnings,
        )
        if args.skip_backtest:
            append_decision_event(session_id, "backtest_skipped", root=ROOT)
        else:
            rc = run_backtest(args.chapter, session_id)
            if rc != 0:
                append_decision_event(session_id, "harvester_session_blocked_by_backtest", root=ROOT, returncode=rc)
                return rc
        for kind in ("query_log", "card_events", "decision_events", "backtest_results"):
            flush_fallback(session_path(kind, session_id, ROOT), args.flush_buffer)
        print("OK: Harvester activation gates passed.")
        return 0
    except Exception as exc:
        append_decision_event(session_id, "harvester_session_failed", root=ROOT, error=str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
