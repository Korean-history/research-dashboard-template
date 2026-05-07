"""Merge per-session telemetry files into one timestamp-sorted NDJSON file."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def read_lines(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        item["_source_file"] = path.name
        rows.append(item)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge query/card/decision/backtest session telemetry.")
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args(argv)

    session_dir = ROOT / "telemetry" / "sessions"
    kinds = ["query_log", "card_events", "decision_events", "backtest_results"]
    rows: list[dict[str, Any]] = []
    for kind in kinds:
        rows.extend(read_lines(session_dir / f"{kind}_{args.session_id}.ndjson"))
    rows.sort(key=lambda item: (str(item.get("timestamp", "")), str(item.get("event", "")), str(item.get("_source_file", ""))))
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for item in rows:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    target = session_dir / f"merged_{args.session_id}.ndjson"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(json.dumps(item, ensure_ascii=False, default=str) for item in output) + ("\n" if output else ""), encoding="utf-8")
    print(f"Wrote {target} with {len(output)} rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
