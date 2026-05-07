"""Derived self-auditing diagnostics for the cards apparatus."""
from __future__ import annotations

import datetime as dt
import hashlib
from collections import defaultdict
from typing import Any

from tools.lib.card_dates import parse_date_or_range, precision_rank, strictly_after


CONFLICT_DEGREE_RANK = {
    "none": 0,
    "complicated": 1,
    "contradicted": 2,
    "refuted": 3,
    "superseded": 4,
}


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value).strip()


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            text = as_text(item)
            if text:
                out.append(text)
        return out
    text = as_text(value)
    return [text] if text else []


def ticket(
    priority: str,
    kind: str,
    card_id: str,
    message: str,
    *,
    severity: str = "warning",
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    basis = f"{kind}|{card_id}|{message}|{evidence or {}}"
    ticket_id = "carddiag:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
    return {
        "id": ticket_id,
        "source": "cards",
        "priority": priority,
        "kind": kind,
        "category": kind,
        "card_id": card_id,
        "card_or_claim_id": card_id,
        "severity": severity,
        "message": message,
        "evidence": evidence or {},
    }


def _empty_conflict_profile() -> dict[str, Any]:
    return {
        "active_conflict_degree": "none",
        "conflict_severity": "none",
        "active_conflicts": [],
        "refuted_by": [],
        "superseded_by": [],
        "complicated_by": [],
        "is_refuter": False,
        "is_superseder": False,
    }


def _set_degree(profile: dict[str, Any], degree: str, severity: str) -> None:
    current = profile.get("active_conflict_degree", "none")
    if CONFLICT_DEGREE_RANK[degree] > CONFLICT_DEGREE_RANK.get(current, 0):
        profile["active_conflict_degree"] = degree
        profile["conflict_severity"] = severity


def _evidence_stage(card: Any) -> tuple[str, bool]:
    explicit = as_text(card.metadata.get("evidence_stage"))
    if explicit:
        return explicit, False
    if as_text(card.status) == "stable" and as_list(card.metadata.get("chapter_relevance")):
        return "reviewed", True
    return "processed", True


def _reliability_profile(card: Any) -> dict[str, Any]:
    archival = as_text(card.metadata.get("archival_validity")) or "unknown"
    analytical = as_text(card.metadata.get("analytical_credibility")) or "unknown"
    missing = []
    if not as_text(card.metadata.get("archival_validity")):
        missing.append("archival_validity")
    if not as_text(card.metadata.get("analytical_credibility")):
        missing.append("analytical_credibility")
    return {
        "archival_validity": archival,
        "analytical_credibility": analytical,
        "prose_risk": as_text(card.metadata.get("risk_level")) or "medium",
        "missing_axes": missing,
    }


def _chronology_profile(card: Any) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    event = parse_date_or_range(card.metadata.get("event_date"))
    document = parse_date_or_range(card.metadata.get("document_date") or card.metadata.get("publication_date"))
    if strictly_after(event, document):
        warnings.append("event_after_document")
    profile: dict[str, Any] = {
        "event_date": as_text(card.metadata.get("event_date")),
        "document_date": as_text(card.metadata.get("document_date")),
        "publication_date": as_text(card.metadata.get("publication_date")),
        "assertion_date": as_text(card.metadata.get("assertion_date")),
        "date_relation": as_text(card.metadata.get("date_relation")),
        "warnings": warnings,
    }
    if event:
        profile["event_interval"] = event.to_dict()
    if document:
        profile["document_interval"] = document.to_dict()
    return profile, warnings


def _binding_list(claim: Any, resolver: Any) -> list[dict[str, Any]]:
    raw = claim.metadata.get("evidence_bindings")
    if isinstance(raw, list) and raw:
        bindings = [dict(item) for item in raw if isinstance(item, dict)]
    else:
        bindings = []
        for entry in claim.resolved_outgoing_links_by_relation.get("cites", []):
            if entry.get("canonical"):
                bindings.append({"card_id": entry["canonical"], "chronology_use": "unknown"})
    out: list[dict[str, Any]] = []
    for binding in bindings:
        raw_id = as_text(binding.get("card_id"))
        resolved = resolver.resolve(raw_id) if resolver else {"canonical": raw_id}
        canonical = resolved.get("canonical") or raw_id
        item = dict(binding)
        item["raw_card_id"] = raw_id
        item["card_id"] = canonical
        out.append(item)
    return out


def _claim_date(claim: Any):
    return parse_date_or_range(claim.metadata.get("event_date") or claim.metadata.get("assertion_date"))


def _source_ids_for_claim(claim: Any, cards_by_id: dict[str, Any]) -> tuple[list[str], dict[str, list[str]]]:
    sources: list[str] = []
    relation_sources: dict[str, list[str]] = defaultdict(list)

    def add(source_id: str, path: str) -> None:
        if source_id and source_id in cards_by_id and source_id not in sources:
            sources.append(source_id)
        if source_id:
            relation_sources[source_id].append(path)

    for entry in claim.resolved_outgoing_links_by_relation.get("cites", []):
        target = entry.get("canonical")
        if not target or target not in cards_by_id:
            continue
        target_card = cards_by_id[target]
        if target_card.card_type == "synthesis":
            for input_id in as_list(target_card.metadata.get("inputs")):
                add(input_id, f"{claim.card_id} -> {target} -> {input_id}")
        else:
            add(target, f"{claim.card_id} -> {target}")

    for card in cards_by_id.values():
        if card.card_type == "source_snippet" and claim.card_id in as_list(card.metadata.get("claim_ids")):
            add(card.card_id, f"{card.card_id}.claim_ids -> {claim.card_id}")
        if card.card_type == "synthesis" and claim.card_id in as_list(card.metadata.get("output_claims")):
            for input_id in as_list(card.metadata.get("inputs")):
                add(input_id, f"{input_id} -> {card.card_id} -> {claim.card_id}")

    return sorted(sources), {key: sorted(values) for key, values in sorted(relation_sources.items())}


def _readiness(vector: dict[str, int]) -> tuple[str, str]:
    if vector["active_conflict_count"] > 0:
        return "blocked", "blocked: active_conflict_count > 0 after binding overrides"
    if vector["chronology_warning_count"] > 0:
        return "blocked", "blocked: chronology_warning_count > 0"
    if vector["primary_quote_count"] >= 3 and vector["source_verified_count"] >= 2:
        return "strong", "strong: primary_quote_count >= 3 and source_verified_count >= 2"
    if vector["primary_quote_count"] >= 2 and vector["source_verified_count"] >= 1:
        return "usable", "usable: primary_quote_count >= 2 and source_verified_count >= 1"
    return "review", "review: thin evidence or insufficient source verification"


def build_self_audit(cards: list[Any], resolver: Any, *, generated_at_utc: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    now = generated_at_utc or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    cards_by_id = {card.card_id: card for card in cards}
    tickets: list[dict[str, Any]] = []
    card_diags: dict[str, dict[str, Any]] = {}

    for card in sorted(cards, key=lambda item: item.card_id):
        stage, stage_inferred = _evidence_stage(card)
        chronology, chronology_warnings = _chronology_profile(card)
        profile = {
            "alias_resolution": {
                "incoming_aliases": resolver.incoming_aliases(card.card_id) if resolver else [],
                "resolved_outgoing_links": card.resolved_outgoing_links_by_relation,
                "unresolved_outgoing_links": card.unresolved_outgoing_links,
            },
            "reliability_profile": _reliability_profile(card),
            "conflict_profile": _empty_conflict_profile(),
            "chronology_profile": chronology,
            "evidence_stage": stage,
            "evidence_stage_inferred": stage_inferred,
        }
        card_diags[card.card_id] = profile
        if chronology_warnings:
            for warning in chronology_warnings:
                tickets.append(ticket("high", warning, card.card_id, f"{card.card_id} has chronology anomaly: {warning}", severity="warning"))

    for card in sorted(cards, key=lambda item: item.card_id):
        for relation, degree, severity, ticket_kind, target_field in [
            ("refutes", "refuted", "high", "card_actively_refuted_by", "refuted_by"),
            ("supersedes", "superseded", "high", "card_actively_superseded_by", "superseded_by"),
        ]:
            for entry in card.resolved_outgoing_links_by_relation.get(relation, []):
                target = entry.get("canonical")
                if target not in card_diags:
                    continue
                source_profile = card_diags[card.card_id]["conflict_profile"]
                source_profile["is_refuter"] = source_profile["is_refuter"] or relation == "refutes"
                source_profile["is_superseder"] = source_profile["is_superseder"] or relation == "supersedes"
                target_profile = card_diags[target]["conflict_profile"]
                target_profile[target_field].append({"source": card.card_id, "relation": relation})
                target_profile["active_conflicts"].append({"source": card.card_id, "relation": relation})
                _set_degree(target_profile, degree, severity)
                tickets.append(ticket("high", ticket_kind, target, f"{target} is {degree} by {card.card_id}", severity="warning"))

        for entry in card.resolved_outgoing_links_by_relation.get("contradicts", []):
            target = entry.get("canonical")
            if target not in card_diags:
                continue
            for source, other in [(card.card_id, target), (target, card.card_id)]:
                profile = card_diags[source]["conflict_profile"]
                profile["active_conflicts"].append({"source": other, "relation": "contradicts"})
                _set_degree(profile, "contradicted", "medium")

        for entry in card.resolved_outgoing_links_by_relation.get("complicates", []):
            target = entry.get("canonical")
            if target in card_diags:
                card_diags[target]["conflict_profile"]["complicated_by"].append({"source": card.card_id, "relation": "complicates"})

    claim_diags: dict[str, dict[str, Any]] = {}
    for claim in sorted((card for card in cards if card.card_type == "claim"), key=lambda item: item.card_id):
        evidence_ids, relation_sources = _source_ids_for_claim(claim, cards_by_id)
        bindings = _binding_list(claim, resolver)
        binding_by_card = {item.get("card_id"): item for item in bindings}
        vector = {
            "primary_quote_count": 0,
            "primary_paraphrase_count": 0,
            "secondary_synthesis_count": 0,
            "source_verified_count": 0,
            "report_verified_count": 0,
            "negative_finding_count": 0,
            "direct_evidence_count": 0,
            "indirect_evidence_count": 0,
            "active_conflict_count": 0,
            "chronology_warning_count": 0,
            "high_prose_risk_count": 0,
        }
        resolved_bindings: list[dict[str, Any]] = []
        claim_chronology_warnings: list[str] = []
        claim_date = _claim_date(claim)

        for source_id in evidence_ids:
            source_card = cards_by_id.get(source_id)
            if not source_card:
                continue
            meta = source_card.metadata
            evidence_type = as_text(meta.get("evidence_type")) or "primary_quote"
            citation_status = as_text(meta.get("citation_status")) or "unverified"
            if evidence_type == "primary_quote":
                vector["primary_quote_count"] += 1
            elif evidence_type == "primary_paraphrase":
                vector["primary_paraphrase_count"] += 1
            elif evidence_type == "secondary_synthesis":
                vector["secondary_synthesis_count"] += 1
            elif evidence_type == "negative_finding":
                vector["negative_finding_count"] += 1
            if citation_status == "source_verified":
                vector["source_verified_count"] += 1
            if citation_status == "report_verified":
                vector["report_verified_count"] += 1
            if as_text(meta.get("analytical_credibility")) == "direct_evidence":
                vector["direct_evidence_count"] += 1
            elif as_text(meta.get("analytical_credibility")):
                vector["indirect_evidence_count"] += 1
            if as_text(meta.get("risk_level")) in {"high", "critical"}:
                vector["high_prose_risk_count"] += 1

            binding = dict(binding_by_card.get(source_id, {"card_id": source_id}))
            conflict_degree = card_diags.get(source_id, {}).get("conflict_profile", {}).get("active_conflict_degree", "none")
            override_text = as_text(binding.get("override_rationale"))
            override_active = conflict_degree in {"refuted", "superseded", "contradicted"} and len(override_text) >= 50
            binding["override_active"] = override_active
            if conflict_degree in {"refuted", "superseded", "contradicted"} and not override_active:
                vector["active_conflict_count"] += 1
            resolved_bindings.append(binding)

        for binding in bindings:
            source_card = cards_by_id.get(binding.get("card_id"))
            if not source_card:
                continue
            date_relation = as_text(source_card.metadata.get("date_relation"))
            chronology_use = as_text(binding.get("chronology_use")) or "unknown"
            if date_relation == "later_codification" and chronology_use == "origin":
                claim_chronology_warnings.append("later_codification_used_as_origin")
                tickets.append(ticket("high", "later_codification_used_as_origin", claim.card_id, f"{claim.card_id} uses later codification as origin evidence", severity="warning"))
            card_event = parse_date_or_range(source_card.metadata.get("event_date"))
            if chronology_use == "contemporaneous_evidence" and claim_date and card_event:
                if precision_rank(claim_date) > precision_rank(card_event) and claim_date.precision == "day":
                    claim_chronology_warnings.append("date_precision_mismatch")
                    tickets.append(ticket("medium", "date_precision_mismatch", claim.card_id, f"{claim.card_id} asserts a more precise date than its evidence card", severity="warning"))

        vector["chronology_warning_count"] = len(set(claim_chronology_warnings))
        readiness, rationale = _readiness(vector)
        if readiness == "blocked":
            tickets.append(ticket("high", "claim_evidence_vector_blocked", claim.card_id, rationale, severity="warning", evidence=vector))
        elif readiness == "review":
            tickets.append(ticket("medium", "claim_evidence_vector_review", claim.card_id, rationale, severity="warning", evidence=vector))

        claim_diags[claim.card_id] = {
            "evidence_vector": vector,
            "argument_readiness": readiness,
            "argument_readiness_rationale": rationale,
            "relation_sources": relation_sources,
            "evidence_bindings_resolved": sorted(resolved_bindings, key=lambda item: item.get("card_id", "")),
            "chronology_warnings": sorted(set(claim_chronology_warnings)),
        }

        missing_axes_targets = []
        if as_text(claim.metadata.get("strength")) == "strong":
            for source_id in evidence_ids:
                missing = card_diags.get(source_id, {}).get("reliability_profile", {}).get("missing_axes", [])
                if missing:
                    missing_axes_targets.append(source_id)
        for source_id in sorted(set(missing_axes_targets)):
            tickets.append(ticket("medium", "missing_reliability_axes", source_id, f"{source_id} lacks reliability axes but supports a load-bearing claim", severity="warning"))

    readiness_breakdown = {"blocked": 0, "review": 0, "usable": 0, "strong": 0}
    for diag in claim_diags.values():
        readiness = diag.get("argument_readiness")
        if readiness in readiness_breakdown:
            readiness_breakdown[readiness] += 1
    unresolved_entries = [
        item
        for card in cards
        for item in card.unresolved_outgoing_links
    ]
    unresolved_targets = {
        as_text(item.get("raw") if isinstance(item, dict) else item)
        for item in unresolved_entries
        if as_text(item.get("raw") if isinstance(item, dict) else item)
    }

    payload = {
        "schema_version": 1,
        "generated_at_utc": now,
        "summary": {
            "card_count": len(cards),
            "claim_count": len(claim_diags),
            "active_conflict_count": sum(
                1 for diag in card_diags.values()
                if diag["conflict_profile"]["active_conflict_degree"] in {"contradicted", "refuted", "superseded"}
            ),
            "chronology_warning_count": len([ticket for ticket in tickets if str(ticket.get("kind", "")).startswith(("event_after_document", "later_codification", "date_precision"))]),
            "unresolved_link_count": len(unresolved_entries),
            "unresolved_link_target_count": len(unresolved_targets),
            "unresolved_link_duplicate_count": max(0, len(unresolved_entries) - len(unresolved_targets)),
            "argument_readiness_breakdown": readiness_breakdown,
        },
        "cards": {key: card_diags[key] for key in sorted(card_diags)},
        "claims": {key: claim_diags[key] for key in sorted(claim_diags)},
    }
    return payload, sorted(tickets, key=lambda item: item["id"])
