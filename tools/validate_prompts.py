"""Validate static Harvester/Weaver prompt templates."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = ROOT / "docs" / "prompts"
SCHEMA_PATH = ROOT / "authority" / "cards_schema.yaml"


PROMPTS = {
    "harvester_prompt.md": ["Identity", "workflow", "Anchor", "Discourse", "Cross-MCP", "Card schema", "Friction", "MISSING_EVIDENCE", "50k Triage", "Token economics"],
    "weaver_prompt.md": ["Identity", "Workflow", "Prose policy", "Argument-role", "Friction preservation", "No fabrication", "MISSING_EVIDENCE", "Style", "Output format"],
    "claude_polish_prompt.md": ["Identity", "Inputs", "Chain Authority", "Polish Rules", "Output Format"],
    "cards_schema_quickref.md": ["Cards Schema Quick Reference", "BEGIN GENERATED FROM authority/cards_schema.yaml", "END GENERATED"],
}
REQUIRED_PATHS = [
    "CLAUDE.md",
    "MCP_Search_Optimization_Tests.md",
    "CARDS_DESIGN_SPEC_v1.md",
    "authority/cards_schema.yaml",
    "argument_arcs.yaml",
    "research_catalog.csv",
]


def load_schema_fields() -> set[str]:
    schema = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8")) or {}
    fields = {"id", "card_type"}
    raw = schema.get("card_types", {})
    if isinstance(raw, dict):
        for item in raw.values():
            fields.update(item.get("required_core", []) or [])
            fields.update(item.get("required_type_specific", []) or [])
            fields.update(item.get("optional", []) or [])
    else:
        fields.update((schema.get("core_fields", {}).get("required") or {}).keys())
        fields.update((schema.get("core_fields", {}).get("optional") or {}).keys())
        for item in (schema.get("type_fields", {}) or {}).values():
            fields.update((item.get("required") or {}).keys())
            fields.update((item.get("optional") or {}).keys())
    return fields


def balanced_code_fences(content: str) -> bool:
    return content.count("```") % 2 == 0


def validate_schema_examples(content: str, valid_fields: set[str]) -> list[str]:
    errors: list[str] = []
    for block in re.findall(r"```yaml\n(.*?)\n```", content, re.DOTALL):
        try:
            parsed = yaml.safe_load(block)
        except yaml.YAMLError as exc:
            errors.append(f"YAML example does not parse: {exc}")
            continue
        if isinstance(parsed, dict):
            for key in parsed:
                if key not in valid_fields and key != "card_types":
                    errors.append(f"YAML example references unknown field: {key}")
    return errors


def main() -> int:
    errors: list[str] = []
    valid_fields = load_schema_fields()
    for filename, required in PROMPTS.items():
        path = PROMPTS_DIR / filename
        if not path.exists():
            errors.append(f"Missing prompt template: {path}")
            continue
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            errors.append(f"{filename} has UTF-8 BOM.")
        content = raw.decode("utf-8")
        if len(content) <= 500:
            errors.append(f"{filename} is too short.")
        if not balanced_code_fences(content):
            errors.append(f"{filename} has unbalanced code fences.")
        lower = content.lower()
        for phrase in required:
            if phrase.lower() not in lower:
                errors.append(f"{filename} missing required phrase/header: {phrase}")
        errors.extend(f"{filename}: {error}" for error in validate_schema_examples(content, valid_fields))

    harvester = (PROMPTS_DIR / "harvester_prompt.md").read_text(encoding="utf-8") if (PROMPTS_DIR / "harvester_prompt.md").exists() else ""
    weaver = (PROMPTS_DIR / "weaver_prompt.md").read_text(encoding="utf-8") if (PROMPTS_DIR / "weaver_prompt.md").exists() else ""
    for phrase in ["do not smooth", "evidence_type: friction", "negative_finding", "counterargument", "project_specific_guardrail", "flatten"]:
        if phrase.lower() not in harvester.lower():
            errors.append(f"harvester_prompt.md missing friction phrase: {phrase}")
    for phrase in ["every load-bearing claim", "card_id", "do not invent", "missing_evidence"]:
        if phrase.lower() not in weaver.lower():
            errors.append(f"weaver_prompt.md missing no-fabrication phrase: {phrase}")
    for phrase in ["genuinely", "honestly", "straightforward", "em-dash", "not X but Y", "quiet"]:
        if phrase.lower() not in weaver.lower():
            errors.append(f"weaver_prompt.md missing style phrase: {phrase}")
    for path_text in REQUIRED_PATHS:
        if (path_text in harvester or path_text in weaver) and not (ROOT / path_text).exists():
            errors.append(f"Referenced path does not exist: {path_text}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("OK: prompt templates validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
