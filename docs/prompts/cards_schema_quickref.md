# Cards Schema Quick Reference

Generated from `authority/cards_schema.yaml`. Do not edit the generated block by hand; run `python tools/build_cards_quickref.py`.

<!-- BEGIN GENERATED FROM authority/cards_schema.yaml -->

## Inbox Authoring Rules

- The per-type lists below are split for Harvester inbox authoring. Persisted cards contain every schema-required field, but inbox batches omit ingestor-managed fields.
- Ingestor-managed/defaulted fields: `id`, `created`, `updated`, and `status`. Do not include them in normal inbox cards; `tools/ingest_cards.py` injects IDs and dates and defaults absent `status` to `draft`.
- Every inbox card still supplies the operator-required core fields: `title`, `card_type`, `chapter_relevance`, `arc_ids`, `tags`, and `linked_cards`, plus the type-specific fields listed for that card type.
- The YAML examples are inbox blocks, not persisted card files.

## Cross-Card Reference Fields

Use only real persisted card IDs in cross-card reference fields. Leave the field empty or omit it until the target card exists; do not use descriptive placeholders, same-batch future IDs, or `MISSING_CARD` in card frontmatter.

`linked_cards` is the general relation map and is checked during ingest. The type-specific reference fields below are also real references and are checked by the card build validator:

- `linked_cards`: mapping; values are existing card IDs under cites/related/contradicts/supersedes
- `claim.depends_on`: claim IDs or existing card IDs
- `synthesis.inputs`: existing source, idea, claim, or synthesis card IDs
- `synthesis.output_claims`: claim IDs or existing card IDs
- `bridge.upstream_claims`: claim IDs or existing card IDs
- `bridge.downstream_claims`: claim IDs or existing card IDs
- `counterargument.refuting_synthesis`: existing synthesis card IDs
- `counterargument.refuting_snippets`: existing source_snippet card IDs
- `question.candidate_evidence`: existing card IDs only; put URLs, file paths, EndNote refs, and retrieval instructions in notes
- `timeline.causal_predecessors`: existing timeline/card IDs
- `timeline.evidence_cards`: existing source_snippet card IDs
- `moc.parent_moc`: existing MOC/card ID
- `moc.child_cards`: existing card IDs
- `scaffold.sub_arguments`: claim IDs or existing card IDs
- `scaffold.feeding_synthesis`: existing synthesis/card IDs

For research gaps, create a `question` or `scaffold` card. Reserve `MISSING_CARD` for `argument_chains.yaml` chain items only.
For `question` cards, `candidate_evidence` is a card-reference field. Put retrieval pointers, URLs, local file paths, EndNote record numbers, and search instructions in `notes:` unless they are already persisted card IDs.

## Card Types

### `source_snippet`

- Filename prefix: `snippet.`
- ID prefix: `snippet:`
- Required in inbox/operator-supplied: `card_type, title, chapter_relevance, arc_ids, tags, linked_cards, source_id, original_lang, extraction_date, extraction_verified, original_snippet, translation_or_summary`
- Required in persisted card/ingestor-managed: `id, created, updated, status`
- Required in persisted card/complete schema: `id, card_type, title, created, updated, status, chapter_relevance, arc_ids, tags, linked_cards, source_id, original_lang, extraction_date, extraction_verified, original_snippet, translation_or_summary`
- Optional fields: `template_instance, card_source, notes, logseq_file, page_or_line, source_locator, report_files, claim_ids, entity_ids, term_ids, provenance_report, source_query, source_ids, arc_rationale, strength, warning_flags, evidence_role, evidence_type, citation_status, risk_level, friction_notes`

```yaml
card_type: source_snippet
title: <title>
chapter_relevance:
- Ch6
arc_ids:
- arc:example
tags:
- ch:6
linked_cards: {}
source_id: <source_id>
original_lang: <original_lang>
extraction_date: '2026-04-26'
extraction_verified: true
original_snippet: <original_snippet>
translation_or_summary: <translation_or_summary>
```

### `idea`

- Filename prefix: `idea.`
- ID prefix: `idea:`
- Required in inbox/operator-supplied: `card_type, title, chapter_relevance, arc_ids, tags, linked_cards, register`
- Required in persisted card/ingestor-managed: `id, created, updated, status`
- Required in persisted card/complete schema: `id, card_type, title, created, updated, status, chapter_relevance, arc_ids, tags, linked_cards, register`
- Optional fields: `template_instance, card_source, notes, term_id, canonical_form, term_variants, romanization, parent_idea, sub_ideas, cautions`

```yaml
card_type: idea
title: <title>
chapter_relevance:
- Ch6
arc_ids:
- arc:example
tags:
- ch:6
linked_cards: {}
register:
- example
```

### `claim`

- Filename prefix: `claim.`
- ID prefix: `claim:`
- Required in inbox/operator-supplied: `card_type, title, chapter_relevance, arc_ids, tags, linked_cards, claim_text, claim_type, strength, risk_level`
- Required in persisted card/ingestor-managed: `id, created, updated, status`
- Required in persisted card/complete schema: `id, card_type, title, created, updated, status, chapter_relevance, arc_ids, tags, linked_cards, claim_text, claim_type, strength, risk_level`
- Optional fields: `template_instance, card_source, notes, depends_on, integration_target, ticket_action, matrix_row_id`

```yaml
card_type: claim
title: <title>
chapter_relevance:
- Ch6
arc_ids:
- arc:example
tags:
- ch:6
linked_cards: {}
claim_text: <claim_text>
claim_type: <claim_type>
strength: <strength>
risk_level: <risk_level>
```

### `entity`

- Filename prefix: `entity.`
- ID prefix: `entity:`
- Required in inbox/operator-supplied: `card_type, title, chapter_relevance, arc_ids, tags, linked_cards, entity_id, entity_subtype, canonical_label, role_in_book`
- Required in persisted card/ingestor-managed: `id, created, updated, status`
- Required in persisted card/complete schema: `id, card_type, title, created, updated, status, chapter_relevance, arc_ids, tags, linked_cards, entity_id, entity_subtype, canonical_label, role_in_book`
- Optional fields: `template_instance, card_source, notes, romanization_primary, romanization_variants, korean_reading, dates, chapters_appears, correction_history, cautions`

```yaml
card_type: entity
title: <title>
chapter_relevance:
- Ch6
arc_ids:
- arc:example
tags:
- ch:6
linked_cards: {}
entity_id: <entity_id>
entity_subtype: <entity_subtype>
canonical_label: <canonical_label>
role_in_book: <role_in_book>
```

### `question`

- Filename prefix: `question.`
- ID prefix: `question:`
- Required in inbox/operator-supplied: `card_type, title, chapter_relevance, arc_ids, tags, linked_cards, question_text, question_status, opened_date, priority`
- Required in persisted card/ingestor-managed: `id, created, updated, status`
- Required in persisted card/complete schema: `id, card_type, title, created, updated, status, chapter_relevance, arc_ids, tags, linked_cards, question_text, question_status, opened_date, priority`
- Optional fields: `template_instance, card_source, notes, closed_date, candidate_evidence, blocking`

```yaml
card_type: question
title: <title>
chapter_relevance:
- Ch6
arc_ids:
- arc:example
tags:
- ch:6
linked_cards: {}
question_text: <question_text>
question_status: <question_status>
opened_date: '2026-04-26'
priority: <priority>
```

### `synthesis`

- Filename prefix: `synthesis.`
- ID prefix: `synthesis:`
- Required in inbox/operator-supplied: `card_type, title, chapter_relevance, arc_ids, tags, linked_cards, synthesis_text, inputs, register, strength`
- Required in persisted card/ingestor-managed: `id, created, updated, status`
- Required in persisted card/complete schema: `id, card_type, title, created, updated, status, chapter_relevance, arc_ids, tags, linked_cards, synthesis_text, inputs, register, strength`
- Optional fields: `template_instance, card_source, notes, output_claims, derived_from_reports`

```yaml
card_type: synthesis
title: <title>
chapter_relevance:
- Ch6
arc_ids:
- arc:example
tags:
- ch:6
linked_cards: {}
synthesis_text: <synthesis_text>
inputs: []
register:
- example
strength: <strength>
```

### `bridge`

- Filename prefix: `bridge.`
- ID prefix: `bridge:`
- Required in inbox/operator-supplied: `card_type, title, chapter_relevance, arc_ids, tags, linked_cards, from_chapter, to_chapter, bridge_text, register`
- Required in persisted card/ingestor-managed: `id, created, updated, status`
- Required in persisted card/complete schema: `id, card_type, title, created, updated, status, chapter_relevance, arc_ids, tags, linked_cards, from_chapter, to_chapter, bridge_text, register`
- Optional fields: `template_instance, card_source, notes, upstream_claims, downstream_claims, informed_by_reports`

```yaml
card_type: bridge
title: <title>
chapter_relevance:
- Ch6
arc_ids:
- arc:example
tags:
- ch:6
linked_cards: {}
from_chapter: <from_chapter>
to_chapter: <to_chapter>
bridge_text: <bridge_text>
register:
- example
```

### `counterargument`

- Filename prefix: `counterargument.`
- ID prefix: `counterargument:`
- Required in inbox/operator-supplied: `card_type, title, chapter_relevance, arc_ids, tags, linked_cards, position_text, position_status`
- Required in persisted card/ingestor-managed: `id, created, updated, status`
- Required in persisted card/complete schema: `id, card_type, title, created, updated, status, chapter_relevance, arc_ids, tags, linked_cards, position_text, position_status`
- Optional fields: `template_instance, card_source, notes, position_holders, historiographical_tradition, refuting_synthesis, refuting_snippets, informed_by_reports`

```yaml
card_type: counterargument
title: <title>
chapter_relevance:
- Ch6
arc_ids:
- arc:example
tags:
- ch:6
linked_cards: {}
position_text: <position_text>
position_status: <position_status>
```

### `timeline`

- Filename prefix: `timeline.`
- ID prefix: `timeline:`
- Required in inbox/operator-supplied: `card_type, title, chapter_relevance, arc_ids, tags, linked_cards, event_label, date, date_precision, sequence_id, sequence_order`
- Required in persisted card/ingestor-managed: `id, created, updated, status`
- Required in persisted card/complete schema: `id, card_type, title, created, updated, status, chapter_relevance, arc_ids, tags, linked_cards, event_label, date, date_precision, sequence_id, sequence_order`
- Optional fields: `template_instance, card_source, notes, date_end, causal_predecessors, evidence_cards`

```yaml
card_type: timeline
title: <title>
chapter_relevance:
- Ch6
arc_ids:
- arc:example
tags:
- ch:6
linked_cards: {}
event_label: <event_label>
date: <date>
date_precision: <date_precision>
sequence_id: <sequence_id>
sequence_order: <sequence_order>
```

### `place`

- Filename prefix: `place.`
- ID prefix: `place:`
- Required in inbox/operator-supplied: `card_type, title, chapter_relevance, arc_ids, tags, linked_cards, place_id, place_label, spatial_argument, register`
- Required in persisted card/ingestor-managed: `id, created, updated, status`
- Required in persisted card/complete schema: `id, card_type, title, created, updated, status, chapter_relevance, arc_ids, tags, linked_cards, place_id, place_label, spatial_argument, register`
- Optional fields: `template_instance, card_source, notes, geographic_location, occupants, associated_events`

```yaml
card_type: place
title: <title>
chapter_relevance:
- Ch6
arc_ids:
- arc:example
tags:
- ch:6
linked_cards: {}
place_id: <place_id>
place_label: <place_label>
spatial_argument: <spatial_argument>
register:
- example
```

### `moc`

- Filename prefix: `moc.`
- ID prefix: `moc:`
- Required in inbox/operator-supplied: `card_type, title, chapter_relevance, arc_ids, tags, linked_cards, moc_scope, moc_level, child_cards`
- Required in persisted card/ingestor-managed: `id, created, updated, status`
- Required in persisted card/complete schema: `id, card_type, title, created, updated, status, chapter_relevance, arc_ids, tags, linked_cards, moc_scope, moc_level, child_cards`
- Optional fields: `template_instance, card_source, notes, parent_moc, max_children_warning`

```yaml
card_type: moc
title: <title>
chapter_relevance:
- Ch6
arc_ids:
- arc:example
tags:
- ch:6
linked_cards: {}
moc_scope: <moc_scope>
moc_level: <moc_level>
child_cards: []
```

### `scaffold`

- Filename prefix: `scaffold.`
- ID prefix: `scaffold:`
- Required in inbox/operator-supplied: `card_type, title, chapter_relevance, arc_ids, tags, linked_cards, scaffold_text, structural_pattern, output_chapter_section`
- Required in persisted card/ingestor-managed: `id, created, updated, status`
- Required in persisted card/complete schema: `id, card_type, title, created, updated, status, chapter_relevance, arc_ids, tags, linked_cards, scaffold_text, structural_pattern, output_chapter_section`
- Optional fields: `template_instance, card_source, notes, sub_arguments, feeding_synthesis, informed_by_reports`

```yaml
card_type: scaffold
title: <title>
chapter_relevance:
- Ch6
arc_ids:
- arc:example
tags:
- ch:6
linked_cards: {}
scaffold_text: <scaffold_text>
structural_pattern: <structural_pattern>
output_chapter_section: <output_chapter_section>
```

## Hybrid Templates

The schema declares template stubs for composite historical arguments. Use them as structure, not as card-frontmatter fields.

<!-- END GENERATED -->
