from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_cmd(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def test_template_cards_and_dashboard_build(tmp_path: Path) -> None:
    workspace = tmp_path / "template_workspace"
    ignore = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache")
    shutil.copytree(ROOT, workspace, ignore=ignore)

    validate = run_cmd("tools/build_cards.py", "--validate-only", cwd=workspace)
    assert validate.returncode == 0, validate.stdout

    cards = run_cmd("tools/build_cards.py", cwd=workspace)
    assert cards.returncode == 0, cards.stdout
    assert (workspace / "CARDS_INDEX.json").exists()
    assert (workspace / "CARDS_DIAGNOSTICS.json").exists()

    dashboard = run_cmd("tools/build_dashboard.py", "--now-utc", "2026-05-01T00:00:00+00:00", cwd=workspace)
    assert dashboard.returncode == 0, dashboard.stdout
    html = (workspace / "RESEARCH_DASHBOARD.html").read_text(encoding="utf-8")
    assert "Research Dashboard" in html
    assert "argument_readiness" in html


def test_template_prompts_validate() -> None:
    result = run_cmd("tools/validate_prompts.py")
    assert result.returncode == 0, result.stdout
