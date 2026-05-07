"""CLI for bounded Shinjitai/Kyujitai query expansion."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.orthography import expand_orthographic_variants

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass


def markdown_rows(query: str, max_variants: int) -> str:
    lines = [
        "| Label | Query | Replacements |",
        "|---|---|---|",
    ]
    for variant in expand_orthographic_variants(query, max_variants=max_variants):
        replacements = ", ".join(variant.replacements) if variant.replacements else "none"
        safe_query = variant.query.replace("|", "\\|")
        lines.append(f"| `{variant.label}` | `{safe_query}` | {replacements} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Propose labeled Shinjitai/Kyujitai companion queries for UACP searches."
    )
    parser.add_argument("query", help="Search query to expand.")
    parser.add_argument("--max-variants", type=int, default=12, help="Maximum variants to emit.")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    args = parser.parse_args(argv)

    if args.format == "json":
        payload = [
            {
                "label": variant.label,
                "query": variant.query,
                "replacements": list(variant.replacements),
            }
            for variant in expand_orthographic_variants(args.query, max_variants=args.max_variants)
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(markdown_rows(args.query, args.max_variants))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
