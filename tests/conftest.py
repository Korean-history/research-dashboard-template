"""Shared pytest helpers for acceptance fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run tests marked live that require external services or databases.",
    )
    parser.addoption(
        "--run-uacp-contract",
        action="store_true",
        default=False,
        help="Collect the parked UACP MCP contract even when UACP_Master is absent.",
    )


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool:
    path = Path(str(collection_path))
    if path.name != "test_uacp_mcp_acceptance.py":
        return False
    if config.getoption("--run-uacp-contract"):
        return False
    return not (PROJECT_ROOT / "UACP_Master").exists()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="live external-service tests require --run-live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture(autouse=True)
def expose_ingest_cards_to_temp_repo(request: pytest.FixtureRequest) -> None:
    """Expose the real ingestor inside isolated ingest-test workspaces.

    The ingest acceptance tests intentionally build a miniature repo under
    tmp_path and invoke ``python tools/ingest_cards.py`` from that cwd. This
    wrapper keeps the test workspace isolated while still exercising the real
    implementation from the project checkout.
    """
    node_path = Path(str(getattr(request.node, "path", "")))
    if node_path.name != "test_ingest_cards_acceptance.py":
        return
    if "repo_root" not in request.fixturenames:
        return

    workspace = Path(request.getfixturevalue("repo_root"))
    if not (workspace / "authority" / "cards_schema.yaml").exists():
        return

    project_root = Path(__file__).resolve().parent.parent
    wrapper_dir = workspace / "tools"
    wrapper_dir.mkdir(exist_ok=True)
    (wrapper_dir / "__init__.py").write_text("", encoding="utf-8")
    wrapper = wrapper_dir / "ingest_cards.py"
    real_script = (project_root / "tools" / "ingest_cards.py").as_posix()
    wrapper.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import importlib.util",
                "import sys",
                "from pathlib import Path",
                f"_REAL = Path({real_script!r})",
                "spec = importlib.util.spec_from_file_location('_real_ingest_cards', _REAL)",
                "module = importlib.util.module_from_spec(spec)",
                "assert spec and spec.loader",
                "sys.modules[spec.name] = module",
                "spec.loader.exec_module(module)",
                "main = module.main",
                "if __name__ == '__main__':",
                "    raise SystemExit(main(sys.argv[1:]))",
                "",
            ]
        ),
        encoding="utf-8",
    )
