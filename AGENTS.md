# Research Dashboard Template Instructions

This is a reusable research-harness template. Replace this file for each new book or research project.

## Working Principles

- Preserve raw research reports in `research_reports/reports/` before or while harvesting them into cards.
- Treat `cards/`, generated indexes, diagnostics, and dashboards as the production layer.
- Treat raw reports, source notes, and project memory as provenance; do not discard them just because their contents have been harvested.
- Keep project-specific people, terms, source corpora, and prose guardrails out of the template. Put them in the project repository that is created from this template.
- Avoid editing generated files by hand when a build script can regenerate them from canonical inputs.

## Template Setup Checklist

1. Rename the project in `authority/authority.yaml`.
2. Replace sample authority CSV rows.
3. Replace sample cards and argument arcs.
4. Add project-specific prompts only after the generic smoke tests pass.
5. Run `python tools/build_cards.py --validate-only` before drafting or dashboard work.