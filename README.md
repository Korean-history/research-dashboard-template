# Research Dashboard Template

Reusable research-harness template for book-scale or long-form scholarly projects.

This repository is the project-neutral version of the research dashboard harness. It keeps the reusable machinery and a tiny sample corpus, but it intentionally excludes any project-specific manuscript, archival evidence base, cards, reports, people, terms, or interpretive memory.

## What Is Included

- `tools/`: card validation, dashboard build, evidence/retrieval helpers, prompt validation, and prose verification utilities.
- `authority/`: schema files plus starter authority CSV/YAML files.
- `cards/`: a tiny sample claim/source/synthesis corpus for smoke testing.
- `docs/prompts/`: generic Harvester, Weaver, and polish prompts.
- `tests/`: portable unit and smoke tests.
- `research_reports/reports/`: sample location for preserving raw report bodies before or while harvesting them into cards.

## Start A New Project

1. Clone or copy this template into a new project repository.
2. Replace `AGENTS.md` with project-specific working memory and guardrails.
3. Replace the sample authority rows in `authority/*.csv`.
4. Replace the sample cards in `cards/`.
5. Put raw research reports under `research_reports/reports/` and keep them there as provenance.
6. Run the validation/build sequence below.

## Validation

```bash
python -m pip install -r requirements.txt
python -m py_compile tools/build_cards.py tools/build_dashboard.py tools/verify_drafted_prose.py
python tools/validate_prompts.py
python tools/build_cards.py --validate-only
python tools/build_cards.py
python tools/build_dashboard.py --now-utc 2026-05-01T00:00:00+00:00
python -m pytest -q
```

Open `RESEARCH_DASHBOARD.html` after the dashboard build.

## Preservation Rule

The cards and generated indexes are the production layer. Raw reports are the archival/provenance layer. Future Harvester or research runs should preserve their raw `.md` report bodies under `research_reports/reports/` before or while harvesting into cards.