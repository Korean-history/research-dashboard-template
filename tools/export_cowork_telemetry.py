"""Append exported Cowork MCP-call logs into a Research Alpha telemetry session."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lib import mcp_wrapper
from tools.lib.telemetry import append_decision_event, ensure_session_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import Cowork MCP call logs into an existing Harvester telemetry session.")
    parser.add_argument("--session-id", required=True, help="Existing Harvester session UUID; no auto-generation.")
    parser.add_argument("--cowork-log", required=True)
    args = parser.parse_args(argv)

    try:
        session_id = ensure_session_id(args.session_id)
        path = Path(args.cowork_log)
        count = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            if isinstance(raw, dict):
                mcp_wrapper.log_external_call(session_id, raw, ROOT)
                count += 1
        append_decision_event(session_id, "external_export_session_complete", root=ROOT, cowork_log=str(path), imported_calls=count)
        print(f"OK: imported {count} Cowork calls into session {session_id}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
