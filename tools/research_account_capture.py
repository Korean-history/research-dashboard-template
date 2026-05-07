"""Generate HBOM Lite and ACCOUNT sidecars for research reports."""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import os
import sys
import tempfile
import traceback
import unicodedata
from pathlib import Path
from typing import Any

import yaml

GENERATOR_VERSION = "1.0.0"
SCHEMA_VERSION = 1

ROOT_KEYS = {"topic", "date", "mode", "session_label", "evidence", "claims", "next_actions", "manual_warnings"}
EVIDENCE_KEYS = {
    "ref_id",
    "zone",
    "corpus",
    "record_id",
    "record_key",
    "title",
    "authors",
    "year",
    "journal",
    "date",
    "source_institution",
    "surfaced_by_query",
    "notes",
    "evidence_role",
    "evidence_type",
}
CLAIM_KEYS = {"claim_id", "text", "support_type", "linked_evidence", "risk_note", "next_action", "promotion_status"}
MANUAL_WARNING_KEYS = {"category", "message", "linked_claims"}

MODES = {"fresh_report", "comparison_report", "close_reading_report", "audit_report", "backfill"}
SUPPORT_TYPES = {
    "primary_supported",
    "secondary_supported",
    "logseq_derived",
    "mixed_support",
    "speculative",
    "unresolved",
}
PROMOTION_STATUSES = {"exploratory", "supported", "manuscript_ready_candidate"}
ZONES = {"canonical", "derivative", "interpretive"}
KNOWN_CORPORA = {"uacp", "journals", "endnote", "calibre", "logseq", "manuscript", "cards", "manuscript_memory", "other"}
DERIVATIVE_CORPORA = {"journals", "endnote", "calibre", "logseq"}
INTERPRETIVE_CORPORA = {"manuscript", "cards", "manuscript_memory", "other"}


class HardError(Exception):
    """Operator or schema error that exits 2."""


def nfc(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [nfc(item) for item in value]
    if isinstance(value, dict):
        return {key: nfc(item) for key, item in value.items()}
    return value


def warn(category: str, message: str, linked_claims: list[str] | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"severity": "soft", "category": category, "message": message}
    if linked_claims:
        item["linked_claims"] = linked_claims
    return item


def assign_warning_ids(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, item in enumerate(warnings, start=1):
        entry = dict(item)
        entry.setdefault("severity", "soft")
        entry.setdefault("category", "other")
        entry.setdefault("message", "")
        entry["warning_id"] = f"w_{index:02d}"
        out.append(entry)
    return out


def read_yaml(path: Path) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise HardError(f"Malformed claims YAML: {exc}") from exc
    except OSError as exc:
        raise HardError(f"Could not read claims YAML {path}: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise HardError("Claims YAML root must be a mapping.")
    return nfc(loaded)


def suggestion(key: str, permitted: set[str]) -> str:
    matches = difflib.get_close_matches(key, sorted(permitted), n=1, cutoff=0.45)
    if matches:
        return f" Did you mean '{matches[0]}'?"
    return ""


def check_keys(mapping: dict[str, Any], permitted: set[str], context: str) -> None:
    for key in mapping:
        if key not in permitted:
            raise HardError(
                f"Unknown key '{key}' in Claims YAML at {context}.{suggestion(str(key), permitted)} "
                f"Permitted keys: {', '.join(sorted(permitted))}"
            )


def validate_claims_yaml(payload: dict[str, Any]) -> None:
    check_keys(payload, ROOT_KEYS, "root")
    for list_name, permitted in (("evidence", EVIDENCE_KEYS), ("claims", CLAIM_KEYS), ("manual_warnings", MANUAL_WARNING_KEYS)):
        value = payload.get(list_name, [])
        if value is None:
            continue
        if not isinstance(value, list):
            raise HardError(f"Claims YAML field '{list_name}' must be a list.")
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise HardError(f"Claims YAML {list_name}[{index}] must be a mapping.")
            check_keys(item, permitted, f"{list_name}[{index}]")


def require_text(item: dict[str, Any], key: str, context: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or value == "":
        raise HardError(f"Missing required field {context}.{key}.")
    return value


def validate_claim_record(item: dict[str, Any], index: int) -> None:
    context = f"claims[{index}]"
    require_text(item, "claim_id", context)
    require_text(item, "text", context)
    support_type = require_text(item, "support_type", context)
    if support_type not in SUPPORT_TYPES:
        raise HardError(f"Unknown support_type '{support_type}' in {context}.")
    promotion = item.get("promotion_status")
    if promotion is not None and promotion not in PROMOTION_STATUSES:
        raise HardError(f"Unknown promotion_status '{promotion}' in {context}.")


def validate_evidence_record(item: dict[str, Any], index: int) -> None:
    context = f"evidence[{index}]"
    require_text(item, "ref_id", context)
    require_text(item, "corpus", context)
    require_text(item, "title", context)
    zone = item.get("zone")
    if zone is not None and zone not in ZONES:
        raise HardError(f"Unknown zone '{zone}' in {context}.")


def default_zone(corpus: str) -> str:
    if corpus == "uacp":
        return "canonical"
    if corpus in DERIVATIVE_CORPORA:
        return "derivative"
    if corpus in INTERPRETIVE_CORPORA:
        return "interpretive"
    return "interpretive"


def normalize_corpus(raw_mcp: str, raw_tool: str) -> str:
    haystacks = [raw_mcp.lower(), raw_tool.lower(), f"{raw_mcp} {raw_tool}".lower()]
    mapping = [
        (("journals-library", "journals_search", "journals"), "journals"),
        (("uacp-library", "uacp_search", "uacp"), "uacp"),
        (("endnote-library", "endnote_search", "endnote"), "endnote"),
        (("calibre-library", "neolibrarian_search", "neolibrarian", "calibre"), "calibre"),
        (("logseq-files", "logseq_roam", "logseq_search", "logseq"), "logseq"),
    ]
    for needles, corpus in mapping:
        for needle in sorted(needles, key=len, reverse=True):
            if any(needle in haystack for haystack in haystacks):
                return corpus
    return "other"


def find_repo_root(start: Path) -> tuple[Path, dict[str, Any] | None]:
    current = start.resolve() if start.exists() else start.resolve().parent
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists():
            return candidate, None
    fallback = Path.cwd().resolve()
    return fallback, warn("other", f"Could not locate repo root from {start}; fell back to {fallback}.")


def relative_or_absolute(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def line_count(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for _line in handle:
            count += 1
    return count


def resolve_telemetry(args: argparse.Namespace, repo_root: Path, warnings: list[dict[str, Any]]) -> tuple[Path | None, str | None, str | None]:
    if args.telemetry_session and args.telemetry_path:
        raise HardError("--telemetry-session and --telemetry-path are mutually exclusive.")
    if args.telemetry_path:
        path = Path(args.telemetry_path)
        if not path.exists():
            warnings.append(warn("telemetry_unavailable", f"Telemetry path not found: {path}."))
            return None, None, None
        resolved = path.resolve()
        try:
            ndjson_path = resolved.relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            ndjson_path = str(resolved)
            warnings.append(warn("other", f"Telemetry path is outside the repo and is not portable: {resolved}."))
        return resolved, None, ndjson_path
    if not args.telemetry_session:
        warnings.append(warn("telemetry_unavailable", "No telemetry session supplied."))
        return None, None, None

    telemetry_root = Path(args.telemetry_root).resolve() if args.telemetry_root else repo_root
    sessions = telemetry_root / "telemetry" / "sessions"
    merged = sessions / f"merged_{args.telemetry_session}.ndjson"
    query_log = sessions / f"query_log_{args.telemetry_session}.ndjson"
    chosen: Path | None = None
    if merged.exists() and query_log.exists():
        merged_count = line_count(merged)
        query_count = line_count(query_log)
        if query_log.stat().st_mtime_ns > merged.stat().st_mtime_ns or query_count > merged_count:
            chosen = query_log
            warnings.append(
                warn(
                    "telemetry_stale_merge",
                    f"query_log is newer or longer than merged telemetry; reading query_log ({query_count} lines vs {merged_count}).",
                )
            )
        else:
            chosen = merged
    elif merged.exists():
        chosen = merged
    elif query_log.exists():
        chosen = query_log
    else:
        warnings.append(warn("telemetry_unavailable", f"No telemetry file found for session {args.telemetry_session}."))
        return None, args.telemetry_session, None

    assert chosen is not None
    return chosen.resolve(), args.telemetry_session, relative_or_absolute(chosen, repo_root)


def is_relevant_line(line: dict[str, Any], source_path: Path, is_telemetry_path: bool) -> bool:
    if line.get("event") != "mcp_call":
        return False
    if line.get("event_source") not in {"python_wrapper", "external_export"}:
        return False
    if is_telemetry_path:
        return True
    if source_path.name.startswith("query_log_"):
        return True
    return str(line.get("_source_file", "")).startswith("query_log_")


def query_from_line(line: dict[str, Any]) -> dict[str, Any]:
    raw_mcp = str(line.get("mcp", ""))
    raw_tool = str(line.get("tool", ""))
    request = line.get("request") if isinstance(line.get("request"), dict) else {}
    plan = line.get("response_query_plan") if isinstance(line.get("response_query_plan"), dict) else {}
    warnings = line.get("warnings") if isinstance(line.get("warnings"), list) else []
    wrapper_warnings = line.get("wrapper_warnings") if isinstance(line.get("wrapper_warnings"), list) else []
    top_hit_ids = line.get("top_hit_ids") if isinstance(line.get("top_hit_ids"), list) else []
    route = plan.get("execution_route") or plan.get("branch") or plan.get("query_route") or raw_tool
    item: dict[str, Any] = {
        "query": str(request.get("query", "")),
        "corpus": normalize_corpus(raw_mcp, raw_tool),
        "raw_mcp": raw_mcp,
        "raw_tool": raw_tool,
        "route": str(route or ""),
        "warning_count": len(warnings),
        "wrapper_warning_count": len(wrapper_warnings),
        "total_matches": line.get("total_matches"),
        "returned_count": line.get("returned_count"),
        "meaningful_zero": False,
        "false_zero_risk_note": None,
        "telemetry_fidelity": str(line.get("telemetry_fidelity") or "complete"),
        "top_hit_ids": [str(value) for value in top_hit_ids],
    }
    if "elapsed_ms" in plan:
        item["elapsed_ms"] = plan.get("elapsed_ms")
    if isinstance(line.get("response_server_metadata"), dict):
        item["server_metadata"] = line["response_server_metadata"]
    return nfc(item)


def read_queries(path: Path | None, is_telemetry_path: bool, warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if path is None:
        return []
    queries: list[dict[str, Any]] = []
    malformed = 0
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(line, dict) or not is_relevant_line(line, path, is_telemetry_path):
                continue
            queries.append(query_from_line(line))
    if malformed:
        warnings.append(warn("other", f"Skipped {malformed} malformed NDJSON line(s)."))
    return queries


def process_evidence(items: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        validate_evidence_record(raw, index)
        item = dict(raw)
        corpus = str(item["corpus"])
        if "zone" not in item or item.get("zone") is None:
            item["zone"] = default_zone(corpus)
        notes = item.get("notes")
        if isinstance(notes, str) and len(notes) > 1000:
            truncated = len(notes) - 1000
            item["notes"] = f"{notes[:1000]} [truncated: {truncated} characters]"
            warnings.append(warn("other", f"Evidence {item['ref_id']} notes exceeded 1000 characters and were truncated."))
        if corpus not in KNOWN_CORPORA:
            warnings.append(warn("unrecognized_corpus_type", f"Evidence {item['ref_id']} uses unrecognized corpus '{corpus}'."))
        zone = str(item.get("zone"))
        if zone == "canonical" and corpus == "logseq":
            warnings.append(
                warn(
                    "expected_anomaly_logseq_canonical",
                    f"Evidence {item['ref_id']} is logseq + canonical; preserving operator override.",
                )
            )
        elif zone == "canonical" and corpus != "uacp":
            warnings.append(warn("other", f"Evidence {item['ref_id']} has zone/corpus mismatch: canonical + {corpus}."))
        elif zone == "derivative" and corpus not in DERIVATIVE_CORPORA:
            warnings.append(warn("other", f"Evidence {item['ref_id']} has zone/corpus mismatch: derivative + {corpus}."))
        elif zone == "interpretive" and corpus not in INTERPRETIVE_CORPORA:
            warnings.append(warn("other", f"Evidence {item['ref_id']} has zone/corpus mismatch: interpretive + {corpus}."))
        out.append(nfc(item))
    return out


def process_claims(items: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if len(items) > 25:
        warnings.append(warn("other", "Claim count exceeds soft cap of 25; consider splitting the report into multiple accounts."))
    for index, raw in enumerate(items):
        validate_claim_record(raw, index)
        item = dict(raw)
        item.setdefault("linked_evidence", [])
        item.setdefault("risk_note", None)
        item.setdefault("next_action", None)
        promotion = item.get("promotion_status", "exploratory")
        if promotion in {"supported", "manuscript_ready_candidate"}:
            item["promotion_status"] = "exploratory"
            warnings.append(warn("claim_promotion_overreach", f"Claim {item['claim_id']} requested {promotion}; downgraded to exploratory.", [str(item["claim_id"])]))
        else:
            item["promotion_status"] = promotion
        if not isinstance(item["linked_evidence"], list):
            raise HardError(f"claims[{index}].linked_evidence must be a list.")
        out.append(nfc(item))
    return out


def process_manual_warnings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item.get("category"), str) or not isinstance(item.get("message"), str):
            raise HardError(f"manual_warnings[{index}] requires category and message.")
        out.append(warn(str(item["category"]), str(item["message"]), item.get("linked_claims") if isinstance(item.get("linked_claims"), list) else None))
    return out


def telemetry_fidelity(queries: list[dict[str, Any]]) -> str:
    if not queries:
        return "not_available"
    values = [str(item.get("telemetry_fidelity") or "not_available") for item in queries]
    if all(value == "complete" for value in values):
        return "complete"
    if any(value == "not_available" for value in values) and any(value != "not_available" for value in values):
        return "partial_missing"
    if any(value == "degraded_uacp" for value in values):
        return "degraded_uacp"
    if any(value == "degraded_journals" for value in values):
        return "degraded_journals"
    return values[0]


def render_account(doc: dict[str, Any]) -> str:
    run = doc["run"]
    title = run.get("session_label") or run.get("topic") or "Untitled"
    lines: list[str] = [
        "<!--",
        "DO NOT EDIT THIS FILE BY HAND.",
        "This Markdown is auto-rendered from the companion _HBOM_LITE.json.",
        "Running tools/research_account_capture.py --force regenerates this file from the JSON and overwrites manual edits.",
        "The JSON is the source of truth.",
        "-->",
        "",
        f"# Research Account - {title}",
        "",
        f"**Report:** [{run['report_path']}]({run['report_path']})",
        f"**Topic:** {run.get('topic', '')}",
        f"**Date:** {run.get('date', '')} · **Mode:** {run.get('mode', '')} · **Operator:** {run.get('operator', '')} · **Agent:** {run.get('agent', '')}",
        f"**Telemetry:** session `{run['telemetry'].get('session_id')}` · fidelity `{run['telemetry'].get('fidelity')}` · log `{run['telemetry'].get('ndjson_path')}`",
        f"**Corpus layers queried:** {' · '.join(run.get('corpus_layers_queried', []))}",
        f"**Report status:** {doc.get('report_status', 'exploratory')}",
        "",
        "---",
        "",
        "## Queries",
        "",
    ]
    if doc["queries"]:
        lines.extend(["| Query | Corpus | Route | Total | Returned | Warnings | Notes |", "|---|---|---|---:|---:|---:|---|"])
        for query in doc["queries"]:
            notes = query.get("false_zero_risk_note") or "-"
            warnings = int(query.get("warning_count") or 0) + int(query.get("wrapper_warning_count") or 0)
            lines.append(
                f"| `{query.get('query', '')}` | {query.get('corpus', '')} | {query.get('route', '')} | "
                f"{query.get('total_matches', '-')} | {query.get('returned_count', '-')} | {warnings} | {notes} |"
            )
    else:
        lines.append("No telemetry queries captured.")
    lines.extend(["", "## Evidence", ""])
    for zone in ("canonical", "derivative", "interpretive"):
        zone_items = sorted([item for item in doc["evidence"] if item.get("zone") == zone], key=lambda item: str(item.get("ref_id", "")))
        lines.append(f"### {zone.title()} zone")
        if zone_items:
            for item in zone_items:
                detail = item.get("date") or item.get("year") or item.get("journal") or item.get("source_institution") or item.get("corpus")
                notes = item.get("notes") or "-"
                surfaced = item.get("surfaced_by_query") or "-"
                lines.append(f"- **{item.get('title', '')}** ({item.get('corpus', '')} · {detail}) - {notes}. Surfaced by `{surfaced}`. Ref `{item.get('ref_id')}`.")
        else:
            lines.append("- None captured.")
        lines.append("")
    lines.extend(["## Claims", ""])
    if doc["claims"]:
        for claim in sorted(doc["claims"], key=lambda item: str(item.get("claim_id", ""))):
            linked = ", ".join(str(value) for value in claim.get("linked_evidence", [])) or "-"
            lines.extend(
                [
                    f"### {claim.get('claim_id')} - {claim.get('support_type')}",
                    claim.get("text", ""),
                    f"Evidence: {linked}.",
                    f"Risk: {claim.get('risk_note') or 'none noted'}.",
                    f"Next: {claim.get('next_action') or '-'}.",
                    f"Promotion: {claim.get('promotion_status')}.",
                    "",
                ]
            )
    else:
        lines.extend(["No claims captured.", ""])
    lines.extend(["## Warnings", ""])
    if doc["warnings"]:
        for item in sorted(doc["warnings"], key=lambda entry: str(entry.get("warning_id", ""))):
            lines.append(f"- `{item.get('warning_id')}` **{item.get('category')}**: {item.get('message')}")
    else:
        lines.append("- None.")
    lines.extend(["", "## Next actions", ""])
    if doc["next_actions"]:
        for item in doc["next_actions"]:
            lines.append(f"- {item}")
    else:
        lines.append("- None captured.")
    return "\n".join(lines) + "\n"


def atomic_write(path: Path, text: str) -> None:
    tmp_name: str | None = None
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent, delete=False, prefix=".tmp-", suffix=".tmp") as handle:
            tmp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        if tmp_name:
            try:
                os.remove(tmp_name)
            except OSError:
                pass
        raise


def validate_output(doc: dict[str, Any]) -> None:
    required = ["schema_version", "generated_at_utc", "generator", "run", "queries", "evidence", "claims", "warnings", "report_status", "future_compatibility"]
    for key in required:
        if key not in doc:
            raise HardError(f"Output schema missing {key}.")
    if doc["schema_version"] != SCHEMA_VERSION:
        raise HardError("Output schema_version must be 1.")
    if not isinstance(doc["queries"], list):
        raise HardError("Output queries must be a list.")
    if doc["report_status"] not in PROMOTION_STATUSES:
        raise HardError("Output report_status enum violation.")
    for warning in doc["warnings"]:
        if warning.get("severity") != "soft":
            raise HardError("Output warnings must be soft at v1.")


def build_doc(args: argparse.Namespace) -> tuple[dict[str, Any], Path, Path]:
    report = Path(args.report_path).resolve()
    if not report.exists() or not report.is_file():
        raise HardError(f"Report path not found: {report}")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else report.parent
    json_path = out_dir / f"{report.stem}_HBOM_LITE.json"
    md_path = out_dir / f"{report.stem}_ACCOUNT.md"
    if not args.force and not args.check and (json_path.exists() or md_path.exists()):
        raise HardError("Sidecar already exists; pass --force to overwrite.")

    warnings: list[dict[str, Any]] = []
    repo_root, root_warning = find_repo_root(report)
    if root_warning and not args.telemetry_root:
        warnings.append(root_warning)

    claims_payload: dict[str, Any] = {}
    if args.claims_yaml:
        claims_payload = read_yaml(Path(args.claims_yaml))
        validate_claims_yaml(claims_payload)

    telemetry_path, session_id, ndjson_sidecar_path = resolve_telemetry(args, repo_root, warnings)
    queries = read_queries(telemetry_path, bool(args.telemetry_path), warnings)

    evidence_raw = claims_payload.get("evidence") or []
    claims_raw = claims_payload.get("claims") or []
    manual_warnings_raw = claims_payload.get("manual_warnings") or []
    if args.warnings_yaml:
        warnings_payload = read_yaml(Path(args.warnings_yaml))
        extra = warnings_payload.get("manual_warnings", warnings_payload.get("warnings", []))
        if isinstance(extra, list):
            manual_warnings_raw = [*manual_warnings_raw, *extra]
        else:
            raise HardError("--warnings-yaml must contain a warnings or manual_warnings list.")
    evidence = process_evidence(evidence_raw, warnings)
    claims = process_claims(claims_raw, warnings)
    warnings.extend(process_manual_warnings(manual_warnings_raw))

    evidence_ids = {str(item.get("ref_id")) for item in evidence}
    for claim in claims:
        for ref_id in claim.get("linked_evidence", []):
            if str(ref_id) not in evidence_ids:
                warnings.append(warn("claim_evidence_missing_link", f"Claim {claim['claim_id']} references missing evidence {ref_id}.", [str(claim["claim_id"])]))

    top_report_status = "exploratory"
    topic = args.topic or claims_payload.get("topic") or report.stem
    date_value = args.date or claims_payload.get("date") or str(args.now_utc or dt.datetime.now(dt.timezone.utc).date())[:10]
    mode = args.mode or claims_payload.get("mode") or "fresh_report"
    if mode not in MODES:
        raise HardError(f"Unknown mode '{mode}'.")
    session_label = args.session_label or claims_payload.get("session_label") or report.stem
    corpus_layers = sorted({str(item.get("corpus")) for item in queries + evidence if item.get("corpus")})
    doc = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": args.now_utc or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "generator": {"tool": "tools/research_account_capture.py", "version": GENERATOR_VERSION},
        "run": {
            "topic": topic,
            "date": date_value,
            "operator": args.operator,
            "agent": args.agent,
            "session_label": session_label,
            "report_path": relative_or_absolute(report, repo_root),
            "telemetry": {
                "session_id": session_id,
                "ndjson_path": ndjson_sidecar_path,
                "fidelity": telemetry_fidelity(queries),
            },
            "mode": mode,
            "corpus_layers_queried": corpus_layers,
        },
        "queries": queries,
        "evidence": evidence,
        "claims": claims,
        "next_actions": nfc(claims_payload.get("next_actions") or []),
        "warnings": [],
        "report_status": top_report_status,
        "promotion_metadata": None,
        "future_compatibility": {
            "model_id": None,
            "prompt_hash": None,
            "index_version": None,
            "topology_version": None,
            "signature": None,
            "parent_account_ids": [],
            "generation_seed": None,
            "corpus_build_ids": {},
        },
    }
    doc["warnings"] = assign_warning_ids(warnings)
    validate_output(doc)
    return nfc(doc), json_path, md_path


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate HBOM Lite and ACCOUNT sidecars for a research report.")
    p.add_argument("report_path")
    p.add_argument("--topic")
    p.add_argument("--date")
    p.add_argument("--mode", default="fresh_report")
    p.add_argument("--operator", default="project_owner")
    p.add_argument("--agent", default="claude_opus")
    p.add_argument("--session-label")
    p.add_argument("--telemetry-session")
    p.add_argument("--telemetry-path")
    p.add_argument("--telemetry-root")
    p.add_argument("--claims-yaml")
    p.add_argument("--warnings-yaml")
    p.add_argument("--out-dir")
    p.add_argument("--force", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--now-utc")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        doc, json_path, md_path = build_doc(args)
        json_text = json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        md_text = render_account(doc)
        if args.check:
            if not args.quiet:
                print(
                    f"SUMMARY: 1 report, {len(doc['queries'])} queries, {len(doc['evidence'])} evidence refs, "
                    f"{len(doc['claims'])} claims, {len(doc['warnings'])} soft warnings, status={doc['report_status']}."
                )
            return 0
        atomic_write(json_path, json_text)
        atomic_write(md_path, md_text)
        if not args.quiet:
            print(f"Wrote {json_path}")
            print(f"Wrote {md_path}")
        return 0
    except HardError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - defensive operator surface
        print(f"ERROR: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
