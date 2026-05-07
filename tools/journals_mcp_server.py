"""MCP tools for querying the local journals SQLite database."""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from mcp.server.fastmcp import FastMCP
except Exception:  # pragma: no cover - tests call functions directly
    FastMCP = None  # type: ignore

DEFAULT_DB_PATH = ROOT / "databases" / "journals" / "journals.db"
DB_PATH = Path(os.environ.get("JOURNALS_DB_PATH", DEFAULT_DB_PATH))
MAX_LIMIT = 200
SNIPPET_CONTEXT_CHARS = 220
MAX_SNIPPET_CHARS = 700
FTS_SNIPPET_TOKENS = 64
UNKNOWN_TOTAL_MATCHES = -1
LIKE_EXACT_COUNT_ROW_THRESHOLD = 5000
DEFAULT_FULL_TEXT_CHARS = 50000
MAX_FULL_TEXT_CHARS = 500000
MCP_SERVER_VERSION = "0.1.0"
ROUTE_LOCAL_SCORE_NOTE = (
    "Route-local diagnostic only. Compare scores only within this journals_search "
    "response and query_route; do not blend with UACP, EndNote, Calibre, or Logseq scores."
)
VALID_ROUTE_HINTS = {"auto", "fts", "raw_fts", "like", "metadata_like"}
FTS_ROUTES = {"fts5", "raw_fts", "fts_anchored_like"}
FTS_ERROR_INDICATORS = ("syntax error", "malformed", "match", "fts5", "snippet")
DB_LOCK_INDICATORS = ("database is locked", "database table is locked", "disk i/o error")

mcp = FastMCP("journals-library") if FastMCP else None


def tool(func):
    if mcp is None:
        return func
    return mcp.tool()(func)


def current_db_path() -> Path:
    return Path(DB_PATH).expanduser().resolve()


def connect() -> sqlite3.Connection:
    path = current_db_path()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def as_int(value: int | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def cap_limit(value: int | None) -> int:
    return max(0, min(as_int(value, 50), MAX_LIMIT))


def cap_full_text_chars(value: int | None) -> int:
    return max(0, min(as_int(value, DEFAULT_FULL_TEXT_CHARS), MAX_FULL_TEXT_CHARS))


def clean_query(query: str) -> str:
    return re.sub(r"\s+", " ", str(query or "")).strip()


def compact_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def query_terms(query: str) -> list[str]:
    return [term for term in clean_query(query).split(" ") if term]


BOOLEAN_OPERATORS = {"AND", "OR", "NOT"}


@dataclass(frozen=True)
class SearchPlan:
    sql: str
    params: list[Any]
    exact_count: bool
    route: str
    long_terms: list[str]
    short_terms: list[str]
    fts_match: str
    requested_route_hint: str = "auto"
    branch: str = ""
    execution_route: str = ""
    fts_used: bool = False
    fts_tables: list[str] | None = None
    route_reason: str = ""
    fallback_from: str | None = None
    fallback_error: str | None = None


@dataclass(frozen=True)
class SearchPayloadPlan:
    search_plan: SearchPlan
    query: str
    data_sql: str
    data_params: list[Any]
    count_sql: str
    count_params: list[Any]
    exact_count: bool
    limit: int
    offset: int
    query_executed: str
    query_params: list[Any]


@dataclass(frozen=True)
class SearchPayloadResult:
    rows: list[sqlite3.Row]
    total_matches: int
    total_matches_exact: bool
    query_executed: str
    query_params: list[Any]


def split_query_terms(query: str) -> tuple[list[str], list[str]]:
    long_terms: list[str] = []
    short_terms: list[str] = []
    for term in query_terms(query):
        cleaned = term.strip("()\"")
        if not cleaned or cleaned.upper() in BOOLEAN_OPERATORS:
            continue
        if len(cleaned) < 3:
            short_terms.append(cleaned)
        else:
            long_terms.append(cleaned)
    return long_terms, short_terms


def has_boolean_syntax(query: str) -> bool:
    cleaned = clean_query(query)
    return bool(
        re.search(r"\b(?:AND|OR|NOT)\b", cleaned)
        or re.search(r"\bNEAR\s*\(", cleaned)
        or "*" in cleaned
        or '"' in cleaned
        or "(" in cleaned
        or ")" in cleaned
    )


def quote_fts_term(term: str) -> str:
    return f'"{term.replace(chr(34), chr(34) + chr(34))}"'


def fts_query(query: str) -> str:
    return " ".join(quote_fts_term(term) for term in query_terms(query))


def tokenize_fts_query(query: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    in_quote = False
    i = 0
    while i < len(query):
        char = query[i]
        if char == '"':
            if in_quote:
                tokens.append("".join(current))
                current = []
                in_quote = False
            else:
                if current:
                    tokens.append("".join(current))
                    current = []
                in_quote = True
            i += 1
            continue
        if in_quote:
            current.append(char)
            i += 1
            continue
        if char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
            i += 1
            continue
        if char in "(),":
            if current:
                tokens.append("".join(current))
                current = []
            tokens.append(char)
            i += 1
            continue
        current.append(char)
        i += 1
    if current:
        tokens.append("".join(current))
    return tokens


def parse_fts_query(query: str) -> tuple[str, list[str], list[str]]:
    """Return a conservative FTS5 MATCH expression plus skipped short terms.

    Uppercase FTS operators are preserved; operands are quoted unless they are
    simple prefix terms. Short CJK terms are omitted from the MATCH expression
    because trigram FTS cannot satisfy them reliably; callers can apply them as
    anchored LIKE filters over the FTS candidate set.
    """
    near_match = re.fullmatch(r"\s*NEAR\s*\((.+),\s*(\d+)\s*\)\s*", query)
    if near_match:
        near_terms, distance = near_match.groups()
        long_terms, short_terms = split_query_terms(near_terms)
        if long_terms and not short_terms:
            operands = " ".join(quote_fts_term(term) for term in long_terms)
            return f"NEAR({operands}, {distance})", long_terms, short_terms

    raw_tokens = tokenize_fts_query(clean_query(query))
    output: list[str] = []
    long_terms: list[str] = []
    short_terms: list[str] = []
    for token in raw_tokens:
        upper = token.upper()
        if upper in BOOLEAN_OPERATORS:
            output.append(upper)
            continue
        if token in {"(", ")"}:
            output.append(token)
            continue
        if token == ",":
            output.append(token)
            continue
        if upper == "NEAR":
            output.append("NEAR")
            continue
        if not token:
            continue

        term = token.strip()
        prefix = term.endswith("*") and len(term) > 1
        bare_term = term[:-1] if prefix else term
        if len(bare_term) < 3:
            short_terms.append(bare_term)
            continue
        long_terms.append(bare_term)
        output.append(f"{quote_fts_term(bare_term)}*" if prefix else quote_fts_term(bare_term))

    sanitized = normalize_fts_tokens(output)
    if not sanitized and long_terms:
        sanitized = " ".join(quote_fts_term(term) for term in long_terms)
    return sanitized, long_terms, short_terms


def normalize_fts_tokens(tokens: list[str]) -> str:
    normalized: list[str] = []
    previous_kind = "start"
    for token in tokens:
        kind = (
            "operator" if token in BOOLEAN_OPERATORS
            else "lparen" if token == "("
            else "rparen" if token == ")"
            else "comma" if token == ","
            else "near" if token == "NEAR"
            else "operand"
        )
        if kind == "operator" and previous_kind in {"start", "operator", "lparen", "near", "comma"}:
            continue
        if kind == "rparen" and previous_kind in {"start", "operator", "lparen", "near", "comma"}:
            continue
        normalized.append(token)
        previous_kind = kind
    while normalized and normalized[-1] in BOOLEAN_OPERATORS | {"(", ",", "NEAR"}:
        normalized.pop()
    expression = " ".join(normalized)
    expression = re.sub(r"\(\s*\)", " ", expression)
    expression = re.sub(r"\s+", " ", expression).strip()
    return expression


def like_pattern(query: str) -> str:
    return f"%{clean_query(query)}%"


def bounded_snippet(text: str | None, query: str) -> str:
    content = compact_text(text)
    if not content:
        return ""

    terms = query_terms(query)
    if any(f"[{term}]" in content for term in terms):
        return content[:MAX_SNIPPET_CHARS - 3] + "..." if len(content) > MAX_SNIPPET_CHARS else content

    lower_content = content.lower()
    best: tuple[int, int] | None = None
    for term in terms:
        idx = lower_content.find(term.lower())
        if idx >= 0 and (best is None or idx < best[0]):
            best = (idx, len(term))

    if best is None:
        snippet = content[:MAX_SNIPPET_CHARS]
        return snippet + ("..." if len(content) > MAX_SNIPPET_CHARS else "")

    idx, term_len = best
    start = max(0, idx - SNIPPET_CONTEXT_CHARS)
    end = min(len(content), idx + term_len + SNIPPET_CONTEXT_CHARS)
    snippet = content[start:idx] + "[" + content[idx:idx + term_len] + "]" + content[idx + term_len:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet += "..."
    if len(snippet) > MAX_SNIPPET_CHARS:
        snippet = snippet[:MAX_SNIPPET_CHARS - 3] + "..."
    return snippet


def filters_sql(
    year_from: int | None,
    year_to: int | None,
    journal: str | None,
    language: str | None,
    author: str | None,
    include_all_record_types: bool,
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if not include_all_record_types:
        clauses.append("a.is_journal_article = 1")
    if year_from is not None:
        clauses.append("a.year >= ?")
        params.append(year_from)
    if year_to is not None:
        clauses.append("a.year <= ?")
        params.append(year_to)
    if journal:
        clauses.append("a.journal LIKE ?")
        params.append(f"%{journal}%")
    if language:
        clauses.append("a.language = ?")
        params.append(language)
    if author:
        clauses.append("a.authors LIKE ?")
        params.append(f"%{author}%")
    return clauses, params


def provenance_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def compute_corpus_build_id(db_path: Path, st: os.stat_result, counts: dict[str, int]) -> str:
    canonical = (
        f"{counts['metadata_row_count']}|"
        f"{counts['full_text_row_count']}|"
        f"{counts['metadata_fts_row_count']}|"
        f"{counts['full_text_fts_row_count']}|"
        f"{st.st_size}"
    )
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def utc_mtime(path: Path) -> str:
    st = path.stat()
    return datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat()


class CorpusMetadataCache:
    def __init__(self) -> None:
        self._cache: dict[Path, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def get(self, db_path: Path) -> dict[str, Any]:
        resolved = Path(db_path).expanduser().resolve()
        st = resolved.stat()
        cached = self._cache.get(resolved)
        if self._fresh(cached, st):
            return dict(cached)

        with self._lock:
            st = resolved.stat()
            cached = self._cache.get(resolved)
            if self._fresh(cached, st):
                return dict(cached)
            counts = self._count_corpus_tables(resolved)
            result: dict[str, Any] = {
                **counts,
                "st_mtime_ns": st.st_mtime_ns,
                "st_size": st.st_size,
                "corpus_build_id": compute_corpus_build_id(resolved, st, counts),
            }
            self._cache[resolved] = result
            return dict(result)

    @staticmethod
    def _fresh(cached: dict[str, Any] | None, st: os.stat_result) -> bool:
        return bool(
            cached
            and cached.get("st_mtime_ns") == st.st_mtime_ns
            and cached.get("st_size") == st.st_size
        )

    @staticmethod
    def _count_corpus_tables(db_path: Path) -> dict[str, int]:
        table_map = {
            "metadata_row_count": "journal_articles",
            "full_text_row_count": "full_text",
            "metadata_fts_row_count": "journal_articles_fts",
            "full_text_fts_row_count": "full_text_fts",
        }
        counts: dict[str, int] = {}
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            for field, table in table_map.items():
                try:
                    row = conn.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()
                except sqlite3.OperationalError as exc:
                    if "no such table" not in str(exc).lower():
                        raise
                    counts[field] = 0
                else:
                    counts[field] = int(row[0] if row is not None else 0)
        return counts


CORPUS_METADATA_CACHE = CorpusMetadataCache()


def corpus_shape(corpus_meta: dict[str, Any]) -> dict[str, int]:
    return {
        "metadata_row_count": int(corpus_meta.get("metadata_row_count", 0)),
        "full_text_row_count": int(corpus_meta.get("full_text_row_count", 0)),
        "metadata_fts_row_count": int(corpus_meta.get("metadata_fts_row_count", 0)),
        "full_text_fts_row_count": int(corpus_meta.get("full_text_fts_row_count", 0)),
    }


def server_metadata(db_path: Path, corpus_meta: dict[str, Any]) -> dict[str, Any]:
    resolved = Path(db_path).expanduser().resolve()
    shape = corpus_shape(corpus_meta)
    st = resolved.stat()
    corpus_build_id = corpus_meta.get("corpus_build_id")
    if not corpus_build_id:
        corpus_build_id = compute_corpus_build_id(resolved, st, shape)
    return {
        "mcp_server_version": MCP_SERVER_VERSION,
        "db_path": str(resolved),
        "db_mtime": utc_mtime(resolved),
        "corpus_build_id": str(corpus_build_id),
        "corpus_shape": shape,
    }


def slow_query_threshold_ms() -> int:
    try:
        return max(0, int(os.environ.get("JOURNALS_SLOW_QUERY_THRESHOLD_MS", "5000")))
    except ValueError:
        return 5000


def normalize_route_hint(route_hint: str | None) -> str:
    route = (route_hint or "auto").strip().lower() or "auto"
    return route if route in VALID_ROUTE_HINTS else "auto"


def route_branch(route: str) -> str:
    return "fts5" if route in FTS_ROUTES else "like_fallback"


def route_execution(route: str) -> str:
    return {
        "fts5": "fts5_standard",
        "raw_fts": "raw_fts",
        "fts_anchored_like": "fts_anchored_like",
        "like": "like_standard",
        "metadata_like": "metadata_like",
    }.get(route, "like_standard")


def route_reason_for(plan: SearchPlan, requested_route_hint: str) -> str:
    if requested_route_hint != "auto":
        return "explicit_hint"
    if plan.route == "fts_anchored_like":
        return "auto_anchored"
    if plan.route == "metadata_like":
        return "auto_short_query_metadata_like"
    if plan.route == "like":
        return "auto_short_query_like"
    return "auto_fts"


def fts_tables_for(plan: SearchPlan, search_full_text: bool) -> list[str]:
    if plan.route not in FTS_ROUTES:
        return []
    tables = ["journal_articles_fts"]
    if search_full_text:
        tables.append("full_text_fts")
    return tables


def attach_route_telemetry(plan: SearchPlan, query: str, search_full_text: bool, route_hint: str | None) -> SearchPlan:
    requested = normalize_route_hint(route_hint)
    return replace(
        plan,
        requested_route_hint=requested,
        branch=route_branch(plan.route),
        execution_route=route_execution(plan.route),
        fts_used=plan.route in FTS_ROUTES,
        fts_tables=fts_tables_for(plan, search_full_text),
        route_reason=route_reason_for(plan, requested),
    )


def validate_boolean_query_syntax(query: str) -> str | None:
    cleaned = clean_query(query)
    if not cleaned:
        return "empty or whitespace-only query rejected; FTS5 cannot evaluate empty MATCH expressions."
    if cleaned.count('"') % 2:
        return "unbalanced quotes"

    depth = 0
    in_quote = False
    for char in cleaned:
        if char == '"':
            in_quote = not in_quote
            continue
        if in_quote:
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return "unbalanced parentheses"
    if depth != 0:
        return "unbalanced parentheses"

    if re.search(r"\bNEAR\s*\(", cleaned, flags=re.IGNORECASE):
        near_calls = list(re.finditer(r"\bNEAR\s*\(([^)]*)\)", cleaned, flags=re.IGNORECASE))
        if not near_calls:
            return "malformed NEAR(...)"
        for match in near_calls:
            inner = match.group(1).strip()
            if not re.fullmatch(r".+,\s*\d+", inner):
                return "malformed NEAR(...)"

    tokens = [token for token in tokenize_fts_query(cleaned) if token not in {"(", ")", ","}]
    if tokens and tokens[-1].upper() in BOOLEAN_OPERATORS:
        return "dangling operator"
    if tokens and tokens[0].upper() in {"AND", "OR"}:
        return "dangling operator"
    return None


def short_like_sql(short_terms: list[str], search_full_text: bool) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for term in short_terms:
        pattern = f"%{term}%"
        term_clauses = [
            "a.title LIKE ?",
            "a.authors LIKE ?",
            "a.journal LIKE ?",
            "a.abstract LIKE ?",
            "a.keywords LIKE ?",
            "a.notes LIKE ?",
        ]
        params.extend([pattern] * len(term_clauses))
        if search_full_text:
            term_clauses.append("ft.text LIKE ?")
            params.append(pattern)
        clauses.append("(" + " OR ".join(term_clauses) + ")")
    return " AND ".join(clauses), params


def search_sql(query: str, search_full_text: bool, route_hint: str = "auto") -> SearchPlan:
    route = (route_hint or "auto").strip().lower()
    long_terms, short_terms = split_query_terms(query)
    if route not in {"auto", "fts", "raw_fts", "like", "metadata_like"}:
        route = "auto"

    force_like = route in {"like", "metadata_like"}
    metadata_only_like = route == "metadata_like"
    use_hybrid = route == "auto" and long_terms and short_terms
    use_raw_fts = route in {"fts", "raw_fts"} or (route == "auto" and has_boolean_syntax(query) and long_terms)

    if force_like or (route == "auto" and not long_terms):
        clean = clean_query(query)
        pattern = like_pattern(query)
        full_text_sql = """
            UNION ALL
            SELECT reference_id, 'full_text' AS layer,
                   substr(text, max(1, instr(text, ?) - ?), ?) AS snippet,
                   0.0 AS score
            FROM full_text
            WHERE text LIKE ?
        """ if search_full_text and not metadata_only_like else ""
        params = [pattern, pattern, pattern, pattern, pattern, pattern]
        if search_full_text and not metadata_only_like:
            params.extend([clean, SNIPPET_CONTEXT_CHARS, MAX_SNIPPET_CHARS, pattern])
        exact_count = not (search_full_text and not metadata_only_like)
        return SearchPlan(f"""
            WITH merged AS (
                SELECT reference_id, 'metadata' AS layer,
                       COALESCE(title, abstract, journal, authors, keywords, notes, '') AS snippet,
                       0.0 AS score
                FROM journal_articles
                WHERE title LIKE ? OR authors LIKE ? OR journal LIKE ?
                   OR abstract LIKE ? OR keywords LIKE ? OR notes LIKE ?
                {full_text_sql}
            ),
            aggregated AS (
                SELECT reference_id,
                       CASE
                         WHEN SUM(CASE WHEN layer='metadata' THEN 1 ELSE 0 END) > 0
                          AND SUM(CASE WHEN layer='full_text' THEN 1 ELSE 0 END) > 0 THEN 'both'
                         WHEN SUM(CASE WHEN layer='metadata' THEN 1 ELSE 0 END) > 0 THEN 'metadata'
                         ELSE 'full_text'
                       END AS matched_layer,
                       MIN(score) AS score,
                       COALESCE(
                         MAX(CASE WHEN layer='metadata' THEN snippet END),
                         MAX(CASE WHEN layer='full_text' THEN snippet END),
                         ''
                       ) AS snippet
                FROM merged
                GROUP BY reference_id
            )
            SELECT * FROM aggregated
        """, params, exact_count, "metadata_like" if metadata_only_like else "like", long_terms, short_terms, "")

    if use_raw_fts:
        match, parsed_long_terms, parsed_short_terms = parse_fts_query(query)
        long_terms = parsed_long_terms or long_terms
        short_terms = parsed_short_terms or short_terms
    else:
        match = " ".join(quote_fts_term(term) for term in long_terms)

    if not match:
        return search_sql(query, search_full_text, "metadata_like" if route == "auto" else "like")

    full_text_sql = """
        UNION ALL
        SELECT rowid AS reference_id, 'full_text' AS layer,
               snippet(full_text_fts, -1, '[', ']', '...', {snippet_tokens}) AS snippet,
               rank AS score
        FROM full_text_fts
        WHERE full_text_fts MATCH ?
    """ if search_full_text else ""
    params = [match]
    if search_full_text:
        params.append(match)
    full_text_sql = full_text_sql.format(snippet_tokens=FTS_SNIPPET_TOKENS)

    if use_hybrid and short_terms:
        like_where, like_params = short_like_sql(short_terms, search_full_text)
        params.extend(like_params)
        return SearchPlan(f"""
            WITH fts_merged AS (
                SELECT rowid AS reference_id, 'metadata' AS layer,
                       snippet(journal_articles_fts, -1, '[', ']', '...', {FTS_SNIPPET_TOKENS}) AS snippet,
                       rank AS score
                FROM journal_articles_fts
                WHERE journal_articles_fts MATCH ?
                {full_text_sql}
            ),
            aggregated AS (
                SELECT reference_id,
                       CASE
                         WHEN SUM(CASE WHEN layer='metadata' THEN 1 ELSE 0 END) > 0
                          AND SUM(CASE WHEN layer='full_text' THEN 1 ELSE 0 END) > 0 THEN 'both'
                         WHEN SUM(CASE WHEN layer='metadata' THEN 1 ELSE 0 END) > 0 THEN 'metadata'
                         ELSE 'full_text'
                       END AS matched_layer,
                       MIN(score) AS score,
                       COALESCE(
                         MAX(CASE WHEN layer='metadata' THEN snippet END),
                         MAX(CASE WHEN layer='full_text' THEN snippet END),
                         ''
                       ) AS snippet
                FROM fts_merged
                GROUP BY reference_id
            )
            SELECT aggregated.*
            FROM aggregated
            JOIN journal_articles a ON a.reference_id = aggregated.reference_id
            LEFT JOIN full_text ft ON ft.reference_id = aggregated.reference_id
            WHERE {like_where}
        """, params, True, "fts_anchored_like", long_terms, short_terms, match)

    return SearchPlan(f"""
        WITH merged AS (
            SELECT rowid AS reference_id, 'metadata' AS layer,
                   snippet(journal_articles_fts, -1, '[', ']', '...', {FTS_SNIPPET_TOKENS}) AS snippet,
                   rank AS score
            FROM journal_articles_fts
            WHERE journal_articles_fts MATCH ?
            {full_text_sql}
        ),
        aggregated AS (
            SELECT reference_id,
                   CASE
                     WHEN SUM(CASE WHEN layer='metadata' THEN 1 ELSE 0 END) > 0
                      AND SUM(CASE WHEN layer='full_text' THEN 1 ELSE 0 END) > 0 THEN 'both'
                     WHEN SUM(CASE WHEN layer='metadata' THEN 1 ELSE 0 END) > 0 THEN 'metadata'
                     ELSE 'full_text'
                   END AS matched_layer,
                   MIN(score) AS score,
                   COALESCE(
                     MAX(CASE WHEN layer='metadata' THEN snippet END),
                     MAX(CASE WHEN layer='full_text' THEN snippet END),
                     ''
                   ) AS snippet
            FROM merged
            GROUP BY reference_id
        )
        SELECT * FROM aggregated
    """, params, True, "raw_fts" if use_raw_fts else "fts5", long_terms, short_terms, match)


def row_to_hit(row: sqlite3.Row, query: str, query_route: str = "unknown") -> dict[str, Any]:
    reference_id = int(row["reference_id"])
    score = float(row["score"] or 0.0)
    return {
        "reference_id": reference_id,
        "record_key": f"journal:endnote:{reference_id}",
        "title": row["title"] or "",
        "authors": row["authors"] or "",
        "year": row["year"],
        "journal": row["journal"] or "",
        "language": row["language"] or "unknown",
        "record_type": row["record_type"] or "",
        "is_journal_article": bool(row["is_journal_article"]),
        "matched_layer": row["matched_layer"],
        "snippet": bounded_snippet(row["snippet"], query),
        "score": score,
        "route_local_score": score,
        "score_scope": f"journals:{query_route}",
        "score_note": ROUTE_LOCAL_SCORE_NOTE,
    }


def build_search_payload(
    plan: SearchPlan,
    query: str,
    year_from: int | None,
    year_to: int | None,
    journal: str | None,
    language: str | None,
    author: str | None,
    include_all_record_types: bool,
    search_full_text: bool,
    limit_value: int,
    offset_value: int,
) -> SearchPayloadPlan:
    clauses, filter_params = filters_sql(year_from, year_to, journal, language, author, include_all_record_types)
    if not search_full_text and plan.fts_match:
        metadata_clauses = list(clauses)
        metadata_params = list(filter_params)
        if plan.route == "fts_anchored_like" and plan.short_terms:
            like_where, like_params = short_like_sql(plan.short_terms, False)
            metadata_clauses.append(f"({like_where})")
            metadata_params.extend(like_params)
        if metadata_clauses:
            metadata_where_sql = "WHERE " + " AND ".join(metadata_clauses) + " AND journal_articles_fts MATCH ?"
        else:
            metadata_where_sql = "WHERE journal_articles_fts MATCH ?"
        base_params = metadata_params + [plan.fts_match]
        metadata_select_sql = f"""
            SELECT
                a.reference_id, a.title, a.authors, a.year, a.journal, a.language,
                a.record_type, a.is_journal_article, 'metadata' AS matched_layer,
                snippet(journal_articles_fts, -1, '[', ']', '...', {FTS_SNIPPET_TOKENS}) AS snippet,
                rank AS score
            FROM journal_articles_fts
            JOIN journal_articles a ON a.reference_id = journal_articles_fts.rowid
            {metadata_where_sql}
            ORDER BY score ASC, a.year DESC, a.reference_id ASC
            LIMIT ? OFFSET ?
        """
        metadata_count_sql = f"""
            SELECT COUNT(*) AS total
            FROM journal_articles_fts
            JOIN journal_articles a ON a.reference_id = journal_articles_fts.rowid
            {metadata_where_sql}
        """
        return SearchPayloadPlan(
            search_plan=plan,
            query=query,
            data_sql=metadata_select_sql,
            data_params=base_params + [limit_value, offset_value],
            count_sql=metadata_count_sql,
            count_params=base_params,
            exact_count=True,
            limit=limit_value,
            offset=offset_value,
            query_executed=provenance_sql(metadata_select_sql),
            query_params=base_params + [limit_value, offset_value],
        )

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    select_sql = f"""
        WITH search_hits AS ({plan.sql})
        SELECT
            a.reference_id, a.title, a.authors, a.year, a.journal, a.language,
            a.record_type, a.is_journal_article, h.matched_layer, h.snippet, h.score
        FROM search_hits h
        JOIN journal_articles a ON a.reference_id = h.reference_id
        {where_sql}
        ORDER BY h.score ASC, a.year DESC, a.reference_id ASC
    """
    count_sql = f"""
        WITH search_hits AS ({plan.sql})
        SELECT COUNT(*) AS total
        FROM search_hits h
        JOIN journal_articles a ON a.reference_id = h.reference_id
        {where_sql}
    """
    data_sql = select_sql + " LIMIT ? OFFSET ?"
    final_params = plan.params + filter_params
    return SearchPayloadPlan(
        search_plan=plan,
        query=query,
        data_sql=data_sql,
        data_params=final_params + [limit_value, offset_value],
        count_sql=count_sql,
        count_params=final_params,
        exact_count=plan.exact_count,
        limit=limit_value,
        offset=offset_value,
        query_executed=provenance_sql(data_sql),
        query_params=final_params + [limit_value, offset_value],
    )


def _execute_payload_sql(plan: SearchPayloadPlan, conn: sqlite3.Connection) -> SearchPayloadResult:
    should_count = plan.exact_count
    if not should_count:
        row_count = int(conn.execute("SELECT COUNT(*) AS total FROM journal_articles").fetchone()["total"])
        should_count = row_count <= LIKE_EXACT_COUNT_ROW_THRESHOLD
    total = (
        int(conn.execute(plan.count_sql, plan.count_params).fetchone()["total"])
        if should_count
        else UNKNOWN_TOTAL_MATCHES
    )
    rows = conn.execute(plan.data_sql, plan.data_params).fetchall()
    return SearchPayloadResult(
        rows=list(rows),
        total_matches=total,
        total_matches_exact=should_count,
        query_executed=plan.query_executed,
        query_params=plan.query_params,
    )


def _execute_fts_with_telemetry(plan: SearchPayloadPlan, conn: sqlite3.Connection) -> SearchPayloadResult:
    return _execute_payload_sql(plan, conn)


def _execute_like_with_telemetry(plan: SearchPayloadPlan, conn: sqlite3.Connection) -> SearchPayloadResult:
    return _execute_payload_sql(plan, conn)


def _perform_search_payload(plan: SearchPayloadPlan, conn: sqlite3.Connection) -> SearchPayloadResult:
    if plan.search_plan.fts_used:
        return _execute_fts_with_telemetry(plan, conn)
    return _execute_like_with_telemetry(plan, conn)


def response_from_payload(plan: SearchPayloadPlan, result: SearchPayloadResult) -> dict[str, Any]:
    route = plan.search_plan.route
    return {
        "results": [row_to_hit(row, plan.query, route) for row in result.rows],
        "total_matches": int(result.total_matches),
        "query": clean_query(plan.query),
        "query_route": route,
        "score_scope": f"journals:{route}",
        "score_note": ROUTE_LOCAL_SCORE_NOTE,
        "query_terms": {
            "long": plan.search_plan.long_terms,
            "short": plan.search_plan.short_terms,
        },
        "fts_match": plan.search_plan.fts_match,
        "total_matches_exact": bool(result.total_matches_exact),
        "query_executed": result.query_executed,
        "query_params": result.query_params,
    }


def empty_response(query: str, plan: SearchPlan, total_matches: int = 0) -> dict[str, Any]:
    return {
        "results": [],
        "total_matches": int(total_matches),
        "query": clean_query(query),
        "query_route": plan.route,
        "score_scope": f"journals:{plan.route}",
        "score_note": ROUTE_LOCAL_SCORE_NOTE,
        "query_terms": {
            "long": plan.long_terms,
            "short": plan.short_terms,
        },
        "fts_match": plan.fts_match,
        "total_matches_exact": True,
        "query_executed": "",
        "query_params": [],
    }


def _compute_has_more(total_matches: int, returned_count: int, limit: int, offset: int = 0) -> bool:
    if total_matches >= 0:
        return total_matches > offset + returned_count
    return bool(limit > 0 and returned_count >= limit)


def route_would_include_full_text(plan: SearchPlan, search_full_text: bool) -> bool:
    return bool(
        search_full_text
        and plan.route != "metadata_like"
        and plan.route_reason != "malformed_query_short_circuit"
    )


def truncate_error(exc: BaseException | str, limit: int = 500) -> str:
    text = str(exc)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def is_lock_operational_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return any(indicator in message for indicator in DB_LOCK_INDICATORS)


def is_fts_operational_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return any(indicator in message for indicator in FTS_ERROR_INDICATORS)


def query_plan_payload(plan: SearchPlan, response: dict[str, Any], elapsed_ms: float) -> dict[str, Any]:
    return {
        "branch": plan.branch or route_branch(plan.route),
        "execution_route": plan.execution_route or route_execution(plan.route),
        "query_route": plan.route,
        "query_terms": response.get("query_terms", {"long": plan.long_terms, "short": plan.short_terms}),
        "fts_match": response.get("fts_match", plan.fts_match or ""),
        "fts_used": bool(plan.fts_used),
        "fts_tables": list(plan.fts_tables or []),
        "query_executed": response.get("query_executed", ""),
        "query_params": response.get("query_params", []),
        "elapsed_ms": elapsed_ms,
        "requested_route_hint": plan.requested_route_hint or "auto",
        "route_reason": plan.route_reason or route_reason_for(plan, plan.requested_route_hint or "auto"),
        "fallback_from": plan.fallback_from,
        "fallback_error": plan.fallback_error,
    }


def finalize_search_response(
    response: dict[str, Any],
    plan: SearchPlan,
    started_at: float,
    warnings: list[str],
    corpus_meta: dict[str, Any],
    limit_value: int,
    offset_value: int,
    search_full_text: bool,
    metadata_warning_plan: SearchPlan | None = None,
) -> dict[str, Any]:
    elapsed_ms = max((time.perf_counter() - started_at) * 1000.0, 0.001)
    out = dict(response)
    out["warnings"] = list(warnings)
    returned_count = len(out.get("results", []))
    total_matches = int(out.get("total_matches", 0))

    shape = corpus_shape(corpus_meta)
    warning_plan = metadata_warning_plan or plan
    if shape["full_text_row_count"] == 0 and route_would_include_full_text(warning_plan, search_full_text):
        out["warnings"].append(
            "queried DB has 0 full_text rows; results limited to metadata. "
            "Use C:\\Endnote\\journals.db for full-text coverage (or the equivalent path on this host)."
        )
    if plan.route_reason in {"auto_short_query_like", "auto_short_query_metadata_like", "fts_execution_error_fallback"}:
        out["warnings"].append(f"query '{clean_query(out.get('query', ''))}' fell back to LIKE scan; results may be incomplete or slow")
    elif plan.requested_route_hint == "like" and len(clean_query(out.get("query", ""))) <= 1:
        out["warnings"].append(f"query '{clean_query(out.get('query', ''))}' fell back to LIKE scan; results may be incomplete or slow")
    if total_matches > 0 and returned_count == 0:
        out["warnings"].append(
            f"query matched {total_matches} rows but returned 0 after filtering; check date range or limit/offset"
        )
    if total_matches == UNKNOWN_TOTAL_MATCHES:
        out["warnings"].append(
            "total_matches_unknown: this LIKE route does not produce an exact match count; "
            "pagination uses returned_count >= limit as a capacity heuristic."
        )
    threshold = slow_query_threshold_ms()
    if elapsed_ms > threshold:
        seconds = threshold / 1000.0
        out["warnings"].append(
            f"journals search exceeded {seconds:g} seconds; refine query, narrow date range, or inspect narrower terms"
        )

    out["elapsed_ms"] = elapsed_ms
    out["server_metadata"] = server_metadata(current_db_path(), corpus_meta)
    out["branch"] = plan.branch or route_branch(plan.route)
    out["execution_route"] = plan.execution_route or route_execution(plan.route)
    out["returned_count"] = returned_count
    out["has_more"] = _compute_has_more(total_matches, returned_count, limit_value, offset_value)
    out["telemetry_fidelity"] = "complete"
    out["query_plan"] = query_plan_payload(plan, out, elapsed_ms)
    return out


@tool
def journals_search(
    query: str,
    year_from: int | None = None,
    year_to: int | None = None,
    journal: str | None = None,
    language: str | None = None,
    author: str | None = None,
    include_all_record_types: bool = False,
    search_full_text: bool = True,
    route_hint: str = "auto",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Search the local journals database with metadata/full-text provenance.

    Args:
        route_hint: Optional route override: auto, fts, raw_fts, like, or metadata_like.

    Returns:
        Results plus routing telemetry and per-response journal MCP metadata.
    """
    started_at = time.perf_counter()
    limit_value = cap_limit(limit)
    offset_value = max(0, as_int(offset, 0))
    db_path = current_db_path()
    corpus_meta = CORPUS_METADATA_CACHE.get(db_path)

    validation_error = validate_boolean_query_syntax(query)
    if validation_error is not None:
        long_terms, short_terms = split_query_terms(query)
        plan = SearchPlan(
            sql="",
            params=[],
            exact_count=True,
            route="raw_fts",
            long_terms=long_terms,
            short_terms=short_terms,
            fts_match="",
            requested_route_hint=normalize_route_hint(route_hint),
            branch="fts5",
            execution_route="raw_fts",
            fts_used=False,
            fts_tables=[],
            route_reason="malformed_query_short_circuit",
        )
        return finalize_search_response(
            empty_response(query, plan),
            plan,
            started_at,
            [f"query error: {validation_error}"],
            corpus_meta,
            limit_value,
            offset_value,
            search_full_text,
            metadata_warning_plan=plan,
        )

    plan = attach_route_telemetry(search_sql(query, search_full_text, route_hint), query, search_full_text, route_hint)
    payload = build_search_payload(
        plan,
        query,
        year_from,
        year_to,
        journal,
        language,
        author,
        include_all_record_types,
        search_full_text,
        limit_value,
        offset_value,
    )
    warnings: list[str] = []
    metadata_warning_plan = plan

    with connect() as conn:
        try:
            payload_result = _perform_search_payload(payload, conn)
            final_plan = plan
            response = response_from_payload(payload, payload_result)
        except sqlite3.OperationalError as exc:
            if not plan.fts_used:
                raise
            if is_lock_operational_error(exc):
                raise
            if not is_fts_operational_error(exc):
                raise

            fallback_route = "metadata_like" if (not search_full_text or corpus_shape(corpus_meta)["full_text_row_count"] == 0) else "like"
            fallback_error = truncate_error(exc)
            fallback_plan = attach_route_telemetry(
                search_sql(query, search_full_text, fallback_route),
                query,
                search_full_text,
                fallback_route,
            )
            fallback_plan = replace(
                fallback_plan,
                requested_route_hint=plan.requested_route_hint,
                route_reason="fts_execution_error_fallback",
                fallback_from=plan.route,
                fallback_error=fallback_error,
            )
            fallback_payload = build_search_payload(
                fallback_plan,
                query,
                year_from,
                year_to,
                journal,
                language,
                author,
                include_all_record_types,
                search_full_text,
                limit_value,
                offset_value,
            )
            warnings.append(
                f"FTS execution error on route '{plan.route}': {fallback_error}; fell back to {fallback_route}."
            )
            try:
                fallback_result = _perform_search_payload(fallback_payload, conn)
            except Exception as fallback_exc:
                double_fault_error = (
                    f"fts_failed: {truncate_error(exc, 240)} | "
                    f"fallback_failed: {truncate_error(fallback_exc, 240)}"
                )
                final_plan = replace(fallback_plan, fallback_error=double_fault_error)
                response = empty_response(query, final_plan, total_matches=0)
            else:
                warnings.append(
                    "fts_fallback_triggered: term proximity semantics may differ from the original FTS query; "
                    f"results derived from {fallback_route} on the same corpus."
                )
                final_plan = fallback_plan
                response = response_from_payload(fallback_payload, fallback_result)

    return finalize_search_response(
        response,
        final_plan,
        started_at,
        warnings,
        corpus_meta,
        limit_value,
        offset_value,
        search_full_text,
        metadata_warning_plan=metadata_warning_plan,
    )


@tool
def journals_get_record(
    reference_id: int,
    include_full_text: bool = True,
    max_full_text_chars: int = DEFAULT_FULL_TEXT_CHARS,
) -> dict[str, Any]:
    """Retrieve a bibliographic record and optional bounded full text."""
    sql = """
        SELECT a.*, ft.text AS full_text, ft.extraction_method
        FROM journal_articles a
        LEFT JOIN full_text ft ON ft.reference_id = a.reference_id
        WHERE a.reference_id = ?
    """
    attachments_sql = """
        SELECT original_path, normalized_path, path_exists, file_type, file_size
        FROM attachments
        WHERE reference_id = ?
        ORDER BY attachment_id
    """
    with connect() as conn:
        row = conn.execute(sql, [reference_id]).fetchone()
        if row is None:
            return {
                "error": f"reference_id not found: {reference_id}",
                "query_executed": provenance_sql(sql),
                "query_params": [reference_id],
            }
        attachments = conn.execute(attachments_sql, [reference_id]).fetchall()

    rid = int(row["reference_id"])
    full_text = row["full_text"] or ""
    full_text_limit = cap_full_text_chars(max_full_text_chars)
    returned_full_text = full_text[:full_text_limit] if include_full_text else None
    return {
        "reference_id": rid,
        "record_key": f"journal:endnote:{rid}",
        "title": row["title"] or "",
        "authors": row["authors"] or "",
        "year": row["year"],
        "journal": row["journal"] or "",
        "volume": row["volume"] or "",
        "issue": row["issue"] or "",
        "pages": row["pages"] or "",
        "language": row["language"] or "unknown",
        "record_type": row["record_type"] or "",
        "is_journal_article": bool(row["is_journal_article"]),
        "abstract": row["abstract"] or "",
        "keywords": row["keywords"] or "",
        "doi": row["doi"] or "",
        "notes": row["notes"] or "",
        "full_text": returned_full_text,
        "full_text_total_chars": len(full_text),
        "full_text_returned_chars": len(returned_full_text or ""),
        "full_text_truncated": bool(include_full_text and len(full_text) > full_text_limit),
        "max_full_text_chars": full_text_limit,
        "extraction_method": row["extraction_method"],
        "attachments": [
            {
                "original_path": item["original_path"] or "",
                "normalized_path": item["normalized_path"] or "",
                "path_exists": bool(item["path_exists"]),
                "file_type": item["file_type"] or "",
                "file_size": item["file_size"],
            }
            for item in attachments
        ],
        "query_executed": provenance_sql(sql),
        "query_params": [reference_id],
    }


@tool
def journals_list_journals() -> dict[str, Any]:
    """List distinct journal titles with counts and language breakdown."""
    sql = """
        SELECT journal, COUNT(*) AS article_count,
               MIN(year) AS min_year, MAX(year) AS max_year,
               GROUP_CONCAT(DISTINCT language) AS languages
        FROM journal_articles
        WHERE COALESCE(journal, '') != ''
        GROUP BY journal
        ORDER BY journal
    """
    with connect() as conn:
        rows = conn.execute(sql).fetchall()
        journals = []
        for row in rows:
            languages = sorted(
                language
                for language in (row["languages"] or "").split(",")
                if language
            )
            min_year = row["min_year"]
            max_year = row["max_year"]
            year_range = "" if min_year is None else (str(min_year) if min_year == max_year else f"{min_year}-{max_year}")
            journals.append({
                "journal": row["journal"],
                "article_count": int(row["article_count"]),
                "languages": languages,
                "year_range": year_range,
            })
    return {
        "journals": journals,
        "query_executed": provenance_sql(sql),
        "query_params": [],
    }


@tool
def journals_get_by_author(author: str, language: str | None = None, limit: int = 100) -> dict[str, Any]:
    """Return articles by author substring."""
    limit_value = cap_limit(limit)
    clauses = ["authors LIKE ?"]
    params: list[Any] = [f"%{author}%"]
    if language:
        clauses.append("language = ?")
        params.append(language)
    sql = f"""
        SELECT reference_id, title, year, journal
        FROM journal_articles
        WHERE {' AND '.join(clauses)}
        ORDER BY year DESC, reference_id ASC
        LIMIT ?
    """
    params.append(limit_value)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {
        "results": [
            {
                "reference_id": int(row["reference_id"]),
                "title": row["title"] or "",
                "year": row["year"],
                "journal": row["journal"] or "",
            }
            for row in rows
        ],
        "query_executed": provenance_sql(sql),
        "query_params": params,
    }


def main(argv: list[str] | None = None) -> int:
    global DB_PATH
    parser = argparse.ArgumentParser(description="Run the journals-library MCP server.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="Path to journals.db.")
    args = parser.parse_args(argv)

    DB_PATH = args.db
    if mcp is None:
        print("ERROR: mcp package is not installed.")
        return 1
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
