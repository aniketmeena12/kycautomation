"""
Deterministic post-validation of an agent report.

Nothing in this module calls a model. That is the point: the component that
decides whether the LLM's output is trustworthy must not itself be an LLM, or
the check is only as reliable as the thing it checks.

WHY POST-VALIDATION AND NOT JUST A GOOD PROMPT
----------------------------------------------
The prompt asks the model to cite only real evidence ids. Prompts are
requests. This module is the enforcement, and the two are not
interchangeable: the whole reason the project's core design principle keeps
scoring away from the LLM is that a model's cooperation is not an
architectural guarantee. Grounding gets the same treatment.

The check is possible at all because of the containment in schemas.py: the
agent has no tools and no database, so `context.allowed_evidence_ids` is
provably the complete set of ids it could legitimately know. An id outside
that set was invented. There is no benign explanation to argue about.

WHAT HAPPENS TO A BAD FINDING: FLAGGED, NEVER DELETED
-----------------------------------------------------
A finding citing a nonexistent evidence id is marked UNGROUNDED and persisted
anyway. Silently dropping it would erase the single most important signal a
reviewer could receive -- that this model hallucinated on this client's file --
and would make the report look cleaner than the run actually was. We surface
defects; we do not tidy them away.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.core.enums import GroundingStatus, InvestigationRecommendationAction
from app.investigation.schemas import InvestigationContext, InvestigationReport, ReportFinding

# ---------------------------------------------------------------------- #
# Prompt-injection detection (Phase 5 brief SS12)
# ---------------------------------------------------------------------- #

# Patterns in RETRIEVED text that are trying to address the model rather than
# describe the world. A news article about money laundering says "the company
# transferred funds"; it does not say "ignore your previous instructions".
#
# This detector is a DETECTOR, not the defence. The defence is structural:
# retrieved text only ever enters the user turn, inside a quarantine block,
# with the operator channel stating that its contents are data (see
# app/investigation/prompts.py). Pattern matching cannot be exhaustive --
# treating it as the control would be security theatre. It exists so an
# injection attempt becomes a recorded, visible fact rather than a silent one.
_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "instruction_override",
        re.compile(
            r"\b(ignore|disregard|forget)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all)\b[^.\n]{0,20}\b(instruction|prompt|rule|direction)",
            re.I,
        ),
    ),
    (
        "role_reassignment",
        re.compile(
            r"\byou\s+are\s+now\b|\bnew\s+(instructions?|persona|role)\b|\bact\s+as\s+(?:a|an|the)\b", re.I
        ),
    ),
    ("fake_turn_marker", re.compile(r"^\s*(system|assistant|human|user)\s*:", re.I | re.M)),
    ("fake_tag_marker", re.compile(r"</?(system|assistant|instructions?|untrusted_document)\b[^>]*>", re.I)),
    (
        "prompt_exfiltration",
        re.compile(
            r"\b(reveal|print|repeat|output|show|disclose)\b[^.\n]{0,30}\b(system\s+prompt|instructions|your\s+prompt)",
            re.I,
        ),
    ),
    (
        "verdict_steering",
        re.compile(
            r"\b(mark|classify|report|record|rate|treat|deem)\b[^.\n]{0,30}\b(as\s+)?(low[\s-]risk|no[\s-]risk|clean|safe|not\s+suspicious|compliant)\b",
            re.I,
        ),
    ),
    # `mark`/`report` belong here alongside the obvious verbs: the canonical
    # attack on THIS system is "mark risk score 0", and the real adversarial
    # fixture in data/articles/ uses exactly that phrasing. A detector that
    # misses the attack its own corpus contains is not a detector.
    (
        "score_steering",
        re.compile(
            r"\b(set|assign|give|lower|raise|change|mark|report)\b[^.\n]{0,25}\b(risk\s+)?(score|rating|band)\b",
            re.I,
        ),
    ),
    (
        "recommendation_steering",
        re.compile(
            r"\b(recommend|approve|clear|whitelist)\b[^.\n]{0,25}\b(this\s+)?(client|customer|entity|account)\b",
            re.I,
        ),
    ),
]


def scan_for_injection(text: str | None, *, location: str) -> list[str]:
    """Names of injection patterns present in `text`, tagged with where it came
    from. Returns [] for clean text."""
    if not text:
        return []
    return [f"{location}: {name}" for name, pattern in _INJECTION_PATTERNS if pattern.search(text)]


# Delimiter neutralisation. Retrieved text is quarantined inside
# <untrusted_document>...</untrusted_document>; if the text itself contained a
# closing tag it could break out of quarantine and land in the instruction
# stream. Escaping the angle brackets keeps the content byte-for-byte readable
# while making it inert as markup.
_CLOSING_TAG = re.compile(r"</\s*untrusted_document\s*>", re.I)


def neutralize_untrusted(text: str) -> str:
    """Make retrieved text unable to escape its quarantine block.

    Deliberately NOT redaction: the evidence stays fully legible, because an
    agent that cannot read a suspicious article cannot investigate it, and
    because rewriting evidence is tampering. We remove only the ability to
    close the delimiter -- the payload survives, its markup power does not.
    """
    return _CLOSING_TAG.sub("&lt;/untrusted_document&gt;", text)


# ---------------------------------------------------------------------- #
# Grounding
# ---------------------------------------------------------------------- #


class FindingGrounding(BaseModel):
    finding_text: str
    cited_evidence_ids: list[int] = Field(default_factory=list)
    valid_evidence_ids: list[int] = Field(default_factory=list)
    invalid_evidence_ids: list[int] = Field(default_factory=list)
    status: GroundingStatus


class GroundingReport(BaseModel):
    """The verdict on one report. Everything a reviewer needs to decide how
    much of it to believe, plus the evaluation metadata of brief SS10."""

    passed: bool = Field(
        description="True when no finding cited a nonexistent evidence id and no illegal action was recommended."
    )

    allowed_evidence_ids: list[int] = Field(default_factory=list)
    evidence_used: list[int] = Field(default_factory=list)
    evidence_ignored: list[int] = Field(
        default_factory=list,
        description="In the context but cited nowhere. Not a failure -- irrelevant evidence exists -- but a coverage signal.",
    )
    hallucinated_evidence_ids: list[int] = Field(
        default_factory=list, description="Cited but not in the context. Fabrication."
    )
    missing_information: list[str] = Field(default_factory=list)
    conflicting_evidence_count: int = 0

    findings: list[FindingGrounding] = Field(default_factory=list)
    ungrounded_finding_count: int = 0
    uncited_finding_count: int = 0

    illegal_recommendations: list[str] = Field(
        default_factory=list,
        description="Actions outside InvestigationRecommendationAction. Must always be empty.",
    )
    violations: list[str] = Field(default_factory=list)

    @property
    def evidence_used_count(self) -> int:
        return len(self.evidence_used)


_ALLOWED_ACTIONS = {a.value for a in InvestigationRecommendationAction}


def _ground_finding(finding: ReportFinding, allowed: set[int]) -> FindingGrounding:
    cited = list(dict.fromkeys(finding.evidence_ids))  # de-dupe, keep order
    valid = [i for i in cited if i in allowed]
    invalid = [i for i in cited if i not in allowed]

    if invalid:
        status = GroundingStatus.UNGROUNDED
    elif not valid:
        status = GroundingStatus.UNCITED
    else:
        status = GroundingStatus.GROUNDED

    return FindingGrounding(
        finding_text=finding.finding,
        cited_evidence_ids=cited,
        valid_evidence_ids=valid,
        invalid_evidence_ids=invalid,
        status=status,
    )


def validate_report(report: InvestigationReport, context: InvestigationContext) -> GroundingReport:
    """Check a report against the context it was generated from.

    `passed` is False on either of two hard failures:
      * a citation to evidence that does not exist (fabrication), or
      * a recommendation outside the permitted vocabulary (boundary breach).

    An UNCITED finding is NOT a hard failure. Some legitimate report content
    genuinely has no evidence id to point at -- "no adverse media provider was
    configured, so this was not checked" is a true and useful statement about
    coverage, sourced from provider results rather than from an Evidence row.
    Forcing a citation there would push the model to attach an unrelated id to
    satisfy the validator, which is worse than an honest uncited sentence. It
    is still counted and reported.
    """
    allowed = context.allowed_evidence_ids

    findings = [
        _ground_finding(f, allowed)
        for f in (*report.key_findings, *report.supporting_evidence, *report.conflicting_evidence)
    ]

    # Everything the report referenced anywhere -- per-finding citations, the
    # recommendation rationales, and the top-level citations list. Checking
    # only the top-level list would leave a finding free to cite a fabricated
    # id as long as the summary list stayed clean.
    cited_everywhere: set[int] = set()
    for f in findings:
        cited_everywhere.update(f.cited_evidence_ids)
    for rec in report.recommendations:
        cited_everywhere.update(rec.evidence_ids)
    cited_everywhere.update(report.citations)

    hallucinated = sorted(cited_everywhere - allowed)
    used = sorted(cited_everywhere & allowed)
    ignored = sorted(allowed - cited_everywhere)

    # Defence in depth. The JSON schema's `enum` should make this unreachable,
    # and Pydantic would already have rejected an unknown action while parsing.
    # It is checked anyway because the cost is one set lookup and the failure
    # mode it guards -- an agent recommending that a client be approved --
    # is the exact thing this phase must never do.
    illegal = [rec.action.value for rec in report.recommendations if rec.action.value not in _ALLOWED_ACTIONS]

    violations: list[str] = []
    if hallucinated:
        violations.append(
            f"Report cites {len(hallucinated)} evidence id(s) absent from the context: {hallucinated}. "
            "These findings are not grounded in any real evidence record."
        )
    if illegal:
        violations.append(f"Report recommends action(s) outside the permitted vocabulary: {illegal}.")

    return GroundingReport(
        passed=not hallucinated and not illegal,
        allowed_evidence_ids=sorted(allowed),
        evidence_used=used,
        evidence_ignored=ignored,
        hallucinated_evidence_ids=hallucinated,
        missing_information=list(report.missing_information),
        conflicting_evidence_count=len(report.conflicting_evidence),
        findings=findings,
        ungrounded_finding_count=sum(1 for f in findings if f.status == GroundingStatus.UNGROUNDED),
        uncited_finding_count=sum(1 for f in findings if f.status == GroundingStatus.UNCITED),
        illegal_recommendations=illegal,
        violations=violations,
    )


__all__ = [
    "FindingGrounding",
    "GroundingReport",
    "neutralize_untrusted",
    "scan_for_injection",
    "validate_report",
]
