"""Run the research backend pipeline in dependency order."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

COMMANDS = [
    ["tools/research_metadata.py", "refresh"],
    ["tools/research_metadata.py", "extract-source-links"],
    ["tools/research_metadata.py", "extract-citations"],
    ["tools/research_metadata.py", "match-vocabulary"],
    ["tools/research_metadata.py", "validate"],
    ["tools/research_truth_control.py", "validate"],
    ["tools/build_authority_quickref.py"],
    ["tools/validate_prompts.py"],
    ["tools/build_cards.py"],
    ["tools/research_truth_control.py", "tickets"],
    ["tools/research_truth_control.py", "impact"],
    ["tools/build_chapter_dossiers.py"],
    ["tools/manuscript_risk_audit.py"],
    ["tools/build_evidence_packs.py"],
    ["tools/research_retrieval.py", "build"],
    ["tools/build_dashboard.py"],
]


def main() -> int:
    for index, command in enumerate(COMMANDS, 1):
        rendered = " ".join(["python", *command])
        print(f"\n[{index}/{len(COMMANDS)}] {rendered}", flush=True)
        try:
            subprocess.run([sys.executable, *command], cwd=ROOT, check=True)
        except subprocess.CalledProcessError as exc:
            print(f"\nFAILED: {rendered}", flush=True)
            return exc.returncode
    print("\nOK: backend pipeline completed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
