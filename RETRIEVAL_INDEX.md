# Retrieval Index

Generated from `argument_arcs.yaml`, `source_snippets.yaml`, `authority/tags.yaml`, and the authority/matrix backend.

Use this as the entry point for research assembly. It is designed to keep Claude from brute-force searching every report from scratch.

## Summary

- Argument arcs: 1
- Controlled tags: 4
- Source snippets: 1

## Argument Arcs

| Arc ID | Title | Chapters | Claims | Snippets | Missing Claims | Needs Print | High/Critical | Tags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| arc:demo_argument | Demo Argument Arc | Ch1 | 1 | 1 | 0 | 1 | 0 | method; evidence; synthesis |

## Controlled Tags

| Category | Tag ID | Label | Aliases |
| --- | --- | --- | --- |
| chapter_scope | ch:1 | Chapter 1 | Ch1; Chapter One |
| workflow | method | Method | methodology; workflow |
| workflow | evidence | Evidence | source; primary source |
| workflow | synthesis | Synthesis | argument synthesis |

## Generated Arc Packs

- `retrieval_packs/arcs/arc_demo_argument.md` and `retrieval_packs/arcs/arc_demo_argument.json`

## Workflow

```powershell
python tools/research_retrieval.py validate
python tools/research_retrieval.py build
python tools/build.py
```
