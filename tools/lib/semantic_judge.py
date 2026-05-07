"""Pluggable semantic judgment used by prose verification."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
import re


@dataclass(frozen=True)
class SemanticJudgment:
    status: str
    score: float | None = None
    reason: str = ""
    extracted_evidence: str | None = None


class SemanticJudge:
    def judge(self, prompt: str, *, evidence: str = "", claim: str = "") -> SemanticJudgment:
        raise NotImplementedError


class DeterministicSemanticJudge(SemanticJudge):
    """Offline test double: requires lexical evidence overlap."""

    def judge(self, prompt: str, *, evidence: str = "", claim: str = "") -> SemanticJudgment:
        claim_tokens = {token.lower() for token in re.findall(r"[\w'-]+", claim) if len(token) > 3}
        evidence_tokens = {token.lower() for token in re.findall(r"[\w'-]+", evidence) if len(token) > 3}
        overlap = sorted(claim_tokens & evidence_tokens)
        if claim and claim.lower() in evidence.lower():
            return SemanticJudgment("pass", 1.0, "claim text present in evidence", claim[:200])
        if len(overlap) >= 3:
            return SemanticJudgment("pass", 0.8, f"deterministic lexical support: {', '.join(overlap[:5])}", ", ".join(overlap[:5]))
        return SemanticJudgment("manual_review_required", None, "deterministic judge found insufficient lexical overlap")


class ManualReviewJudge(SemanticJudge):
    def judge(self, prompt: str, *, evidence: str = "", claim: str = "") -> SemanticJudgment:
        return SemanticJudgment("manual_review_required", None, "no live semantic judge configured")


class LLMSemanticJudge(SemanticJudge):
    """Placeholder production adapter; falls back until an approved model client exists.

    Future live clients must require JSON:
      {"verdict": "yes|no|unclear", "reasoning": "...", "extracted_evidence": "..."}
    If verdict is "yes", extracted_evidence must be a contiguous substring of
    the paragraph, at least 8 chars, and no more than 200 chars. Otherwise the
    wrapper coerces the judgment to no per the anti-sycophancy guard.
    """

    def judge(self, prompt: str, *, evidence: str = "", claim: str = "") -> SemanticJudgment:
        provider = os.environ.get("RESEARCH_ALPHA_SEMANTIC_PROVIDER", "").strip()
        if not provider:
            return ManualReviewJudge().judge(prompt, evidence=evidence, claim=claim)
        return ManualReviewJudge().judge(prompt, evidence=evidence, claim=claim)


def validate_extracted_evidence(paragraph: str, judgment: SemanticJudgment) -> SemanticJudgment:
    if judgment.status != "pass":
        return judgment
    extracted = judgment.extracted_evidence or ""
    if not extracted or len(extracted) < 8 or len(extracted) > 200 or extracted not in paragraph:
        return SemanticJudgment(
            "manual_review_required",
            None,
            "Model claimed survival but produced no literal substring; coerced to no per anti-sycophancy guard.",
            extracted or None,
        )
    return judgment


def load_judge(name: str | None) -> SemanticJudge:
    if (name or "").strip() == "deterministic":
        return DeterministicSemanticJudge()
    if (name or "").strip() == "manual":
        return ManualReviewJudge()
    return LLMSemanticJudge()
