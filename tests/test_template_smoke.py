from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_cmd(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def test_template_cards_and_dashboard_build() -> None:
    validate = run_cmd("tools/build_cards.py", "--validate-only")
    assert validate.returncode == 0, validate.stdout

    cards = run_cmd("tools/build_cards.py")
    assert cards.returncode == 0, cards.stdout
    assert (ROOT / "CARDS_INDEX.json").exists()
    assert (ROOT / "CARDS_DIAGNOSTICS.json").exists()

    dashboard = run_cmd("tools/build_dashboard.py", "--now-utc", "2026-05-01T00:00:00+00:00")
    assert dashboard.returncode == 0, dashboard.stdout
    html = (ROOT / "RESEARCH_DASHBOARD.html").read_text(encoding="utf-8")
    assert "Research Dashboard" in html
    assert "argument_readiness" in html


def test_template_prompts_validate() -> None:
    result = run_cmd("tools/validate_prompts.py")
    assert result.returncode == 0, result.stdout