"""Build the dashboard card graph from CARDS_INDEX.json data."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any

from tools.lib.chain_role_aliases import normalize_argument_role

SCHEMA_VERSION = 1

CHAPTER_ORDER = [
    "Introduction",
    "Ch1",
    "Ch3",
    "Ch4",
    "Ch5",
    "Ch6",
    "Ch7",
    "Epilogue",
]

LANES = [
    ("setup", "Setup"),
    ("core_claims", "Core Claims"),
    ("evidence", "Evidence"),
    ("friction", "Friction"),
    ("bridges", "Bridges"),
]

LINKED_CARD_FIELDS = ("cites", "related", "contradicts", "refutes", "supersedes", "complicates")
FORBIDDEN_LINKED_CARD_FIELDS = {"cited_by", "related_by", "contradicted_by", "superseded_by"}

TYPE_EDGE_PRIORITY = {"type_field": 0, "inferred_inverse": 1, "linked_cards": 2}
RELATION_GROUPS = {
    "cites": "evidence",
    "evidence_for_claim": "evidence",
    "supported_by_evidence": "evidence",
    "synthesizes_from": "evidence",
    "refuted_by_snippet": "friction",
    "contradicts": "friction",
    "refutes": "friction",
    "complicates": "friction",
    "bridge_from": "argument",
    "bridge_to": "argument",
    "bridges_outward": "argument",
    "bridges_inward": "argument",
    "supersedes": "argument",
    "related": "context",
}


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.replace("\n", ";").split(";") if part.strip()]


def text_value(value: Any) -> str:
    return "" if value is None else str(value).strip()


def card_metadata(card: dict[str, Any]) -> dict[str, Any]:
    metadata = card.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def card_id(card: dict[str, Any]) -> str:
    return text_value(card.get("card_id") or card_metadata(card).get("id"))


def card_type(card: dict[str, Any]) -> str:
    return text_value(card.get("card_type") or card_metadata(card).get("card_type"))


def field_values(card: dict[str, Any], field: str) -> list[str]:
    metadata = card_metadata(card)
    if field in card:
        values = as_list(card.get(field))
        if values:
            return values
    return as_list(metadata.get(field))


def summary_for_card(card: dict[str, Any], ctype: str) -> str:
    metadata = card_metadata(card)
    fields = [
        "claim_text",
        "claim",
        "synthesis_text",
        "bridge_text",
        "translation_or_summary",
        "position_text",
        "question_text",
        "role_in_book",
        "spatial_argument",
        "event_label",
        "scaffold_text",
        "body",
    ]
    for field in fields:
        value = text_value(metadata.get(field) or card.get(field))
        if value:
            return value[:360]
    body = text_value(card.get("body"))
    return body[:360] if body else ctype.replace("_", " ")


def graph_warning(
    category: str,
    severity: str,
    path: str,
    message: str,
    node_or_edge_id: str | None = None,
) -> dict[str, Any]:
    """Return a dashboard-shaped warning, adding graph-specific fields by mutation."""
    from tools import build_dashboard

    warning = build_dashboard.dashboard_warning(category, path, message)
    warning["severity"] = severity
    if node_or_edge_id:
        warning["node_or_edge_id"] = node_or_edge_id
    return warning


def is_friction_card(card: dict[str, Any], ctype: str) -> bool:
    metadata = card_metadata(card)
    status = text_value(metadata.get("position_status") or card.get("position_status")).lower()
    return ctype == "counterargument" and status == "active"


def has_open_question(card: dict[str, Any], ctype: str) -> bool:
    metadata = card_metadata(card)
    status = text_value(metadata.get("question_status") or card.get("question_status") or card.get("status")).lower()
    return ctype == "question" and status in {"open", "opened", "active", "review"}


def is_load_bearing_card(card: dict[str, Any], ctype: str) -> bool:
    metadata = card_metadata(card)
    if ctype in {"bridge", "synthesis"}:
        return True
    if ctype == "claim":
        strength = text_value(metadata.get("strength") or card.get("strength")).lower()
        return strength in {"strong", "high", "top", "load_bearing"}
    if ctype in {"idea", "scaffold"}:
        structural = text_value(metadata.get("structural_pattern") or metadata.get("load_bearing")).lower()
        return structural in {"argument_chain", "true", "yes", "load_bearing"} or len(field_values(card, "arc_ids")) > 1
    return False


def compute_node_rank(card: dict[str, Any]) -> int:
    ctype = card_type(card)
    metadata = card_metadata(card)
    base = {
        "bridge": 90,
        "synthesis": 86,
        "claim": 78,
        "counterargument": 68,
        "question": 64,
        "source_snippet": 58,
        "idea": 50,
        "scaffold": 46,
        "timeline": 42,
        "place": 36,
        "entity": 34,
        "moc": 30,
    }.get(ctype, 20)
    if is_load_bearing_card(card, ctype):
        base += 24
    if is_friction_card(card, ctype) or has_open_question(card, ctype):
        base += 10
    strength = text_value(metadata.get("strength") or card.get("strength")).lower()
    base += {"strong": 12, "high": 10, "medium": 5, "weak": -4}.get(strength, 0)
    base += min(10, len(field_values(card, "arc_ids")) * 2)
    outgoing = len(as_list(card.get("linked_cards", {}).get("outgoing") if isinstance(card.get("linked_cards"), dict) else []))
    incoming = int(card.get("incoming_link_count") or 0)
    return int(base + min(10, outgoing + incoming))


def node_for_card(card: dict[str, Any]) -> dict[str, Any]:
    ctype = card_type(card)
    metadata = card_metadata(card)
    cid = card_id(card)
    title = text_value(card.get("title") or metadata.get("title") or cid)
    risk_level = text_value(card.get("risk_level") or metadata.get("risk_level"))
    citation_status = text_value(card.get("citation_status") or metadata.get("citation_status"))
    evidence_type = text_value(card.get("evidence_type") or metadata.get("evidence_type"))
    node = {
        "id": cid,
        "card_type": ctype,
        "label": title,
        "summary": summary_for_card(card, ctype),
        "status": text_value(card.get("status") or metadata.get("status")),
        "path": text_value(card.get("path")),
        "chapters": field_values(card, "chapters") or field_values(card, "chapter_relevance"),
        "arcs": field_values(card, "arc_ids"),
        "tags": field_values(card, "tags"),
        "register": field_values(card, "register"),
        "rank": compute_node_rank(card),
        "risk_level": risk_level,
        "citation_status": citation_status,
        "evidence_type": evidence_type,
        "strength": text_value(card.get("strength") or metadata.get("strength")),
        "claim_type": text_value(card.get("claim_type") or metadata.get("claim_type")),
        "is_bridge": ctype == "bridge",
        "is_friction": is_friction_card(card, ctype),
        "has_open_question": has_open_question(card, ctype),
        "is_load_bearing": is_load_bearing_card(card, ctype),
        "metrics": {
            "incoming_link_count": int(card.get("incoming_link_count") or 0),
            "outgoing_link_count": int(card.get("outgoing_link_count") or 0),
            "evidence_leaf_count": 0,
        },
    }
    if citation_status:
        node["citation_status"] = citation_status
    if evidence_type:
        node["evidence_type"] = evidence_type
    return node


def edge_id(source: str, relation: str, target: str) -> str:
    def clean(value: str) -> str:
        return "".join(ch if ch.isalnum() else "_" for ch in value).strip("_")

    return f"edge:{clean(source)}__{relation}__{clean(target)}"


def edge_payload(source: str, target: str, relation: str, origin: str, inferred: bool = False) -> dict[str, Any]:
    return {
        "id": edge_id(source, relation, target),
        "source": source,
        "target": target,
        "relation": relation,
        "relation_group": RELATION_GROUPS.get(relation, "structure"),
        "origin": origin,
        "inferred": inferred,
        "weight": 1,
    }


def add_edge_candidate(candidates: list[dict[str, Any]], source: str, target: str, relation: str, origin: str, inferred: bool = False) -> None:
    if source and target:
        candidates.append(edge_payload(source, target, relation, origin, inferred=inferred))


def linked_card_edges(card: dict[str, Any], warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cid = card_id(card)
    metadata = card_metadata(card)
    linked = metadata.get("linked_cards")
    if not isinstance(linked, dict):
        return []
    candidates: list[dict[str, Any]] = []
    flat_linked = card.get("linked_cards") if isinstance(card.get("linked_cards"), dict) else {}
    outgoing = set(as_list(flat_linked.get("outgoing"))) if isinstance(flat_linked, dict) and "outgoing" in flat_linked else None
    for field, raw_targets in linked.items():
        targets = as_list(raw_targets)
        if field in FORBIDDEN_LINKED_CARD_FIELDS or field.endswith("_by"):
            if targets:
                warnings.append(graph_warning(
                    "forbidden_relation",
                    "soft",
                    text_value(card.get("path")),
                    f"metadata.linked_cards.{field} is a reverse relation and is not emitted as a graph edge.",
                    cid,
                ))
            continue
        if field not in LINKED_CARD_FIELDS:
            continue
        for target in targets:
            if outgoing is not None and target not in outgoing:
                warnings.append(graph_warning(
                    "outgoing_divergence",
                    "soft",
                    text_value(card.get("path")),
                    f"metadata.linked_cards.{field} target {target} is absent from linked_cards.outgoing.",
                    cid,
                ))
            add_edge_candidate(candidates, cid, target, field, "linked_cards")
    return candidates


def type_field_edges(card: dict[str, Any], warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cid = card_id(card)
    ctype = card_type(card)
    metadata = card_metadata(card)
    candidates: list[dict[str, Any]] = []

    if ctype == "bridge":
        from_chapter = text_value(metadata.get("from_chapter"))
        to_chapter = text_value(metadata.get("to_chapter"))
        if from_chapter and to_chapter and from_chapter == to_chapter:
            warnings.append(graph_warning(
                "schema_violation",
                "soft",
                text_value(card.get("path")) or "cards/bridge/",
                "Bridge from_chapter and to_chapter are identical; book-spine placement may be ambiguous.",
                cid,
            ))
        for target in as_list(metadata.get("upstream_claims")):
            add_edge_candidate(candidates, cid, target, "bridge_from", "type_field")
        for target in as_list(metadata.get("downstream_claims")):
            add_edge_candidate(candidates, cid, target, "bridge_to", "type_field")

    if ctype == "synthesis":
        for target in as_list(metadata.get("inputs")):
            add_edge_candidate(candidates, cid, target, "synthesizes_from", "type_field")
        for target in as_list(metadata.get("output_claims")):
            add_edge_candidate(candidates, cid, target, "synthesis_outputs_claim", "type_field")

    if ctype == "counterargument":
        for target in as_list(metadata.get("refuting_snippets")):
            add_edge_candidate(candidates, cid, target, "refuted_by_snippet", "type_field")

    if ctype == "source_snippet":
        for target in as_list(metadata.get("claim_ids") or card.get("claim_ids")):
            add_edge_candidate(candidates, cid, target, "evidence_for_claim", "type_field")

    if ctype == "timeline":
        for target in as_list(metadata.get("evidence_cards")):
            add_edge_candidate(candidates, cid, target, "timeline_evidence", "type_field")

    if ctype == "moc":
        for target in as_list(metadata.get("child_cards")):
            add_edge_candidate(candidates, cid, target, "moc_child", "type_field")

    return candidates


def dedupe_edges(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    origins: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for edge in candidates:
        key = (edge["source"], edge["relation"], edge["target"])
        origins[key].add(edge["origin"])
        if key not in merged:
            merged[key] = dict(edge)
            continue
        current = merged[key]
        current["weight"] = int(current.get("weight") or 1) + 1
        if TYPE_EDGE_PRIORITY.get(edge["origin"], 99) < TYPE_EDGE_PRIORITY.get(current["origin"], 99):
            kept_weight = current["weight"]
            merged[key] = dict(edge)
            merged[key]["weight"] = kept_weight
    edges: list[dict[str, Any]] = []
    for key, edge in merged.items():
        edge["origin_paths"] = sorted(origins[key])
        edge["id"] = edge_id(edge["source"], edge["relation"], edge["target"])
        edge["relation_group"] = RELATION_GROUPS.get(edge["relation"], "structure")
        edges.append(edge)
    return sorted(edges, key=lambda item: (item["source"], item["target"], item["relation"], item["id"]))


def extract_edges(cards: list[dict[str, Any]], node_ids: set[str], warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for card in cards:
        if not card_id(card) or not card_type(card):
            continue
        candidates.extend(linked_card_edges(card, warnings))
        candidates.extend(type_field_edges(card, warnings))

    direct_edges = dedupe_edges(candidates)
    inverse_candidates: list[dict[str, Any]] = []
    for edge in direct_edges:
        if edge["relation"] == "evidence_for_claim":
            add_edge_candidate(inverse_candidates, edge["target"], edge["source"], "supported_by_evidence", "inferred_inverse", inferred=True)
        elif edge["relation"] == "bridge_from":
            add_edge_candidate(inverse_candidates, edge["target"], edge["source"], "bridges_outward", "inferred_inverse", inferred=True)
        elif edge["relation"] == "bridge_to":
            add_edge_candidate(inverse_candidates, edge["target"], edge["source"], "bridges_inward", "inferred_inverse", inferred=True)

    edges = dedupe_edges(direct_edges + inverse_candidates)
    for edge in edges:
        if edge["source"] not in node_ids or edge["target"] not in node_ids:
            warning = graph_warning(
                "missing_target",
                "soft",
                edge["id"],
                f"Edge endpoint is missing from graph nodes: {edge['source']} -> {edge['target']}.",
                edge["id"],
            )
            edge["warning"] = warning
    return edges


def role_map_from_chains(argument_chains: dict[str, Any] | None, warnings: list[dict[str, Any]]) -> dict[str, str]:
    if not isinstance(argument_chains, dict):
        return {}
    records: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for chain in argument_chains.get("chains", []) if isinstance(argument_chains.get("chains"), list) else []:
        if not isinstance(chain, dict):
            continue
        chain_id = text_value(chain.get("chain_id") or chain.get("id"))
        for item in chain.get("items", []) if isinstance(chain.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            raw_ids = [item.get("card_id"), item.get("snippet_id"), item.get("source_snippet_id")]
            raw_ids.extend(as_list(item.get("cited_card_ids")))
            role, _ = normalize_argument_role(item.get("argument_role") or item.get("role") or "supporting")
            for cid in [text_value(value) for value in raw_ids if text_value(value)]:
                records[cid].append((chain_id, role))

    resolved: dict[str, str] = {}
    for cid, values in sorted(records.items()):
        ordered = sorted(values, key=lambda item: (item[0], item[1]))
        resolved[cid] = ordered[0][1]
        losing = [item for item in ordered[1:] if item[1] != resolved[cid]]
        if losing:
            warnings.append(graph_warning(
                "multi_chain_role",
                "soft",
                "argument_chains.yaml",
                f"{cid} has conflicting argument roles; using {resolved[cid]} from {ordered[0][0]}.",
                cid,
            ))
    return resolved


def compute_lane_for_card(node: dict[str, Any], argument_role: str | None = None) -> str:
    ctype = node.get("card_type", "")
    if argument_role:
        if argument_role == "contextual":
            return "setup"
        if argument_role == "climactic":
            return "bridges"
        if argument_role == "synthesis":
            return "core_claims" if ctype in {"claim", "synthesis", "bridge", "idea", "scaffold"} else "evidence"
        if argument_role == "supporting":
            return "evidence" if ctype == "source_snippet" else ("core_claims" if ctype in {"claim", "synthesis"} else "evidence")

    if ctype == "bridge":
        return "bridges"
    if ctype in {"counterargument", "question"} or node.get("is_friction") or node.get("has_open_question"):
        return "friction"
    if ctype == "source_snippet":
        return "evidence"
    if ctype in {"claim", "synthesis"}:
        return "core_claims"
    return "setup"


def sort_node_ids(node_ids: list[str], nodes_by_id: dict[str, dict[str, Any]]) -> list[str]:
    unique = sorted(set(node_ids), key=lambda cid: (-int(nodes_by_id.get(cid, {}).get("rank") or 0), cid))
    return unique


def build_arc_subgraphs(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], role_map: dict[str, str]) -> dict[str, Any]:
    nodes_by_id = {node["id"]: node for node in nodes}
    arc_ids = sorted({arc for node in nodes for arc in node.get("arcs", [])})
    by_arc: dict[str, Any] = {}
    for arc_id in arc_ids:
        node_ids = sort_node_ids([node["id"] for node in nodes if arc_id in node.get("arcs", [])], nodes_by_id)
        node_id_set = set(node_ids)
        lanes = [{"lane_id": lane_id, "label": label, "node_ids": []} for lane_id, label in LANES]
        lane_by_id = {lane["lane_id"]: lane for lane in lanes}
        for node_id in node_ids:
            lane = compute_lane_for_card(nodes_by_id[node_id], role_map.get(node_id))
            lane_by_id.get(lane, lane_by_id["setup"])["node_ids"].append(node_id)
        for lane in lanes:
            lane["node_ids"] = sort_node_ids(lane["node_ids"], nodes_by_id)
        edge_ids = [
            edge["id"] for edge in edges
            if edge["source"] in node_id_set and edge["target"] in node_id_set
        ]
        warnings: list[dict[str, Any]] = []
        if node_ids and not any(nodes_by_id[node_id].get("is_load_bearing") for node_id in node_ids):
            warnings.append(graph_warning(
                "empty_subgraph",
                "soft",
                arc_id,
                "Arc has no load-bearing claim, bridge, or synthesis node.",
                arc_id,
            ))
        by_arc[arc_id] = {
            "arc_id": arc_id,
            "node_ids": node_ids,
            "edge_ids": sorted(edge_ids),
            "lanes": lanes,
            "warnings": warnings,
        }
    return by_arc


def build_chapter_subgraphs(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    nodes_by_id = {node["id"]: node for node in nodes}
    by_chapter: dict[str, Any] = {}
    for chapter in CHAPTER_ORDER:
        node_ids = sort_node_ids([node["id"] for node in nodes if chapter in node.get("chapters", [])], nodes_by_id)
        node_id_set = set(node_ids)
        edge_ids = [
            edge["id"] for edge in edges
            if edge["source"] in node_id_set and edge["target"] in node_id_set
        ]
        by_chapter[chapter] = {
            "chapter": chapter,
            "node_ids": node_ids,
            "edge_ids": sorted(edge_ids),
        }
    return by_chapter


def bridge_spine_chapter(node: dict[str, Any], cards_by_id: dict[str, dict[str, Any]]) -> str:
    card = cards_by_id.get(node["id"], {})
    metadata = card_metadata(card)
    return text_value(metadata.get("from_chapter")) or (node.get("chapters") or [""])[0]


def build_book_spine(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], cards_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    nodes_by_id = {node["id"]: node for node in nodes}
    columns = [{"column_id": chapter, "label": chapter, "node_ids": []} for chapter in CHAPTER_ORDER]
    column_by_id = {column["column_id"]: column for column in columns}
    for node in nodes:
        if node.get("card_type") in {"source_snippet", "entity"}:
            continue
        chapters = [bridge_spine_chapter(node, cards_by_id)] if node.get("card_type") == "bridge" else (node.get("chapters") or [])
        for chapter in chapters[:1]:
            if chapter in column_by_id:
                column_by_id[chapter]["node_ids"].append(node["id"])
    for column in columns:
        column["node_ids"] = sort_node_ids(column["node_ids"], nodes_by_id)
    spine_node_ids = {node_id for column in columns for node_id in column["node_ids"]}
    edge_ids = [
        edge["id"] for edge in edges
        if edge["source"] in spine_node_ids and edge["target"] in spine_node_ids
    ]
    return {"columns": columns, "edge_ids": sorted(edge_ids)}


def normalize_cards(cards_index: dict[str, Any]) -> list[dict[str, Any]]:
    cards = cards_index.get("cards")
    if isinstance(cards, list):
        return [card for card in cards if isinstance(card, dict)]
    cards_by_id = cards_index.get("cards_by_id")
    if isinstance(cards_by_id, dict):
        return [card for _, card in sorted(cards_by_id.items()) if isinstance(card, dict)]
    return []


def build_card_graph(cards_index: dict[str, Any], *, now_utc: str, argument_chains: dict[str, Any] | None = None) -> dict[str, Any]:
    # Defensive shallow/deep copies avoid accidental mutation of dashboard state.
    cards = deepcopy(normalize_cards(cards_index if isinstance(cards_index, dict) else {}))
    chains = deepcopy(argument_chains) if isinstance(argument_chains, dict) else argument_chains
    warnings: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    valid_cards: list[dict[str, Any]] = []

    for card in cards:
        cid = card_id(card)
        ctype = card_type(card)
        if not cid or not ctype:
            warnings.append(graph_warning(
                "schema_violation",
                "hard",
                text_value(card.get("path")),
                "Card payload is missing card_id or card_type and was skipped.",
                cid or text_value(card.get("title")) or "unknown_card",
            ))
            continue
        valid_cards.append(card)
        nodes.append(node_for_card(card))

    nodes = sorted(nodes, key=lambda node: node["id"])
    node_ids = {node["id"] for node in nodes}
    edges = extract_edges(valid_cards, node_ids, warnings)

    evidence_counts: dict[str, int] = defaultdict(int)
    for edge in edges:
        if edge["relation_group"] == "evidence" and edge["source"] in node_ids:
            evidence_counts[edge["source"]] += 1
    for node in nodes:
        node["metrics"]["evidence_leaf_count"] = evidence_counts.get(node["id"], 0)

    role_map = role_map_from_chains(chains, warnings)
    cards_by_id = {card_id(card): card for card in valid_cards}
    by_arc = build_arc_subgraphs(nodes, edges, role_map)
    by_chapter = build_chapter_subgraphs(nodes, edges)
    book_spine = build_book_spine(nodes, edges, cards_by_id)

    all_subgraph_warnings = [
        warning
        for subgraph in by_arc.values()
        for warning in subgraph.get("warnings", [])
    ]
    warnings = sorted(warnings + all_subgraph_warnings, key=lambda item: (
        item.get("category", ""),
        item.get("severity", ""),
        item.get("node_or_edge_id", ""),
        item.get("path", ""),
        item.get("message", ""),
    ))

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": now_utc,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "warning_count": len(warnings),
        "nodes": nodes,
        "edges": edges,
        "subgraphs": {
            "by_arc": by_arc,
            "by_chapter": by_chapter,
            "book_spine": book_spine,
        },
        "warnings": warnings,
    }
