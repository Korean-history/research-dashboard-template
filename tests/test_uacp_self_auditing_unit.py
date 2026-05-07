"""Direct unit coverage for UACP self-auditing helper modules.

These tests intentionally avoid importing tools.build_cards so Dropbox file
hydration problems in the build entrypoint do not mask pure-library regressions.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import verify_drafted_prose
from tools.lib.card_dates import overlaps, parse_date_or_range, strictly_after
from tools.lib.card_diagnostics import build_self_audit
from tools.lib.card_id_resolver import CardIdResolver
from tools.lib import card_id_resolver


@dataclass
class DummyCard:
    card_id: str
    card_type: str = "source_snippet"
    status: str = "stable"
    metadata: dict = field(default_factory=dict)
    resolved_outgoing_links_by_relation: dict = field(default_factory=dict)
    unresolved_outgoing_links: list = field(default_factory=list)


def test_date_parser_accepts_common_imprecise_literals():
    year_range = parse_date_or_range("1932-1934")
    dotted_range = parse_date_or_range("1932..1934")
    slash_month = parse_date_or_range("1932/04")
    spring = parse_date_or_range("spring 1932")
    fiscal = parse_date_or_range("fiscal year 1944")

    assert year_range.start == dt.date(1932, 1, 1)
    assert year_range.end == dt.date(1934, 12, 31)
    assert dotted_range.start == year_range.start
    assert dotted_range.end == year_range.end
    assert slash_month.precision == "month"
    assert slash_month.start == dt.date(1932, 4, 1)
    assert spring.start == dt.date(1932, 3, 1)
    assert spring.end == dt.date(1932, 5, 31)
    assert fiscal.start == dt.date(1944, 4, 1)
    assert fiscal.end == dt.date(1945, 3, 31)


def test_date_interval_algebra_stays_on_interval_bounds():
    assert overlaps(parse_date_or_range("1932-1934"), parse_date_or_range("1933-02"))
    assert strictly_after(parse_date_or_range("1935"), parse_date_or_range("1932..1934"))


def test_resolver_returns_dict_and_keeps_resolution_private(tmp_path: Path):
    resolver = CardIdResolver(
        tmp_path,
        {"snip:legacy": {"canonical": "snippet:canonical", "status": "deprecated_alias"}},
        {"snippet:canonical"},
    )

    resolved = resolver.resolve("snip:legacy")

    assert resolved["canonical"] == "snippet:canonical"
    assert resolved["alias"] == "snip:legacy"
    assert isinstance(resolved, dict)
    assert "Resolution" not in card_id_resolver.__all__
    assert not hasattr(card_id_resolver, "Resolution")


def test_self_audit_summarizes_unresolved_edges_and_unique_targets():
    resolver = SimpleNamespace(incoming_aliases=lambda _card_id: [])
    cards = [
        DummyCard(
            "snippet:a",
            unresolved_outgoing_links=[
                {"relation": "cites", "raw": "snippet:missing"},
                {"relation": "related", "raw": "snippet:missing"},
                {"relation": "cites", "raw": "claim:missing"},
            ],
        )
    ]

    payload, _tickets = build_self_audit(cards, resolver, generated_at_utc="2026-05-01T00:00:00+00:00")
    summary = payload["summary"]

    assert summary["unresolved_link_count"] == 3
    assert summary["unresolved_link_target_count"] == 2
    assert summary["unresolved_link_duplicate_count"] == 1


def test_verify_drafted_prose_has_clear_rapidfuzz_disabled_message(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(verify_drafted_prose, "fuzz", None)

    with pytest.raises(RuntimeError, match="install rapidfuzz>=3"):
        verify_drafted_prose.require_rapidfuzz()
