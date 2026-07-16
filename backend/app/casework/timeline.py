"""
TimelineBuilder -- the chronology, GENERATED from stored rows (brief SS3).

    "Never manually assemble timelines. Generate from stored events."

Taken literally, and it is the module's whole design. There is no `add_entry()`
that a caller could reach for, no narrative step written by hand, and no way to
put something on a timeline that did not already happen and get recorded. Every
entry is a projection of a row that exists: nine collectors, one per source
table, each turning rows into TimelineEntry objects.

That constraint is what makes the timeline evidence rather than storytelling. A
timeline you can append to is a timeline someone can append to *incorrectly* --
and in a compliance file, a plausible fabricated step is worse than a gap.

THREE RULES THIS MODULE ENFORCES
--------------------------------

1. NO DUPLICATES (brief SS10). Every entry carries `entry_key` = the source
   table plus the source row id. Dedup is on that key, never on the rendered
   title: two distinct risk events can legitimately have identical summaries,
   and keying on text would silently delete one from the record.

2. EVERY ENTRY HAS AN ACTOR. SYSTEM observed it, AGENT wrote it, or a HUMAN
   decided it. Collapsing those would destroy the single most important
   distinction in the audit story -- an LLM's opinion and a compliance
   officer's decision must never look alike in a timeline.

3. ORDER IS DETERMINISTIC. Sorted by timestamp, then by `entry_key` as a
   tiebreaker. Without the tiebreaker, rows sharing a timestamp -- routine,
   since a monitoring cycle writes a snapshot and several events in the same
   instant -- would order arbitrarily, and two reads of the same case would
   disagree. A timeline that reshuffles on refresh is one no reviewer will
   trust.

READ-ONLY, like the resolution pipeline (ADR-015) and the context builder.
Nothing here writes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.casework.schemas import CaseTimeline, TimelineEntry
from app.core.enums import ActorType, EvidenceType, TimelineEntryType
from app.models.case import Case
from app.repositories.evidence_repository import EvidenceRepository
from app.repositories.investigation_repository import InvestigationRepository
from app.repositories.risk_repository import AlertRepository, RiskEventRepository, RiskSnapshotRepository

MAX_PER_SOURCE = 200


def _utc(value: datetime | None) -> datetime:
    """Normalise to aware UTC.

    SQLite hands back naive datetimes even for DateTime(timezone=True) columns.
    Sorting naive and aware datetimes together raises TypeError, so a timeline
    mixing them would crash on the first case that had both -- which is every
    real case. Normalising here is what makes rule 3 possible at all.
    """
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _decode(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


class TimelineBuilder:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._events = RiskEventRepository(db)
        self._snapshots = RiskSnapshotRepository(db)
        self._alerts = AlertRepository(db)
        self._evidence = EvidenceRepository(db)
        self._investigations = InvestigationRepository(db)

    def build(self, case: Case) -> CaseTimeline:
        entries: list[TimelineEntry] = []
        client_id = case.client_id

        entries += self._from_snapshots(client_id)
        entries += self._from_risk_events(client_id)
        entries += self._from_evidence(client_id)
        entries += self._from_entity_matches(case)
        entries += self._from_alerts(client_id)
        entries += self._from_investigations(client_id)
        entries += self._from_reviews(case)
        entries += self._from_sars(case)

        # Rule 1: dedup on source identity. dict preserves first-seen order,
        # which does not matter because rule 3 sorts next -- but it does mean a
        # collector cannot accidentally shadow an earlier entry with a
        # differently-rendered version of the same row.
        unique: dict[str, TimelineEntry] = {}
        for entry in entries:
            unique.setdefault(entry.entry_key, entry)

        ordered = sorted(unique.values(), key=lambda e: (e.timestamp, e.entry_key))  # rule 3

        counts: dict[str, int] = {}
        for entry in ordered:
            counts[entry.entry_type.value] = counts.get(entry.entry_type.value, 0) + 1

        return CaseTimeline(
            case_id=case.id,
            entries=ordered,
            total=len(ordered),
            generated_at=datetime.now(timezone.utc),
            counts_by_type=counts,
        )

    # ------------------------------------------------------------------ #
    # Collectors. One per source table. Each is a pure projection.
    # ------------------------------------------------------------------ #

    def _from_snapshots(self, client_id: int) -> list[TimelineEntry]:
        """A snapshot is TWO facts: a monitoring cycle ran, and (sometimes) the
        score moved. They are separate entries because they are separately
        interesting -- "we looked and nothing changed" is the answer to a
        different question than "the score jumped 20 points"."""
        out: list[TimelineEntry] = []
        for snap in self._snapshots.history_for_client(client_id, limit=MAX_PER_SOURCE):
            ts = _utc(snap.computed_at)
            out.append(
                TimelineEntry(
                    entry_key=f"MONITORING:{snap.id}",
                    timestamp=ts,
                    entry_type=TimelineEntryType.MONITORING,
                    title=f"Monitoring cycle: risk {snap.current_score:g}/100 ({snap.risk_band.value})",
                    summary=snap.trigger_reason,
                    actor_type=ActorType.SYSTEM,
                    actor_id="monitoring_service",
                    source_table="risk_score_snapshots",
                    source_id=snap.id,
                    metadata={
                        "score": snap.current_score,
                        "band": snap.risk_band.value,
                        "scoring_logic_version": snap.scoring_logic_version,
                    },
                )
            )
            # Only when the score actually moved. Emitting a RISK_SCORE_CHANGE
            # for every cycle would bury the real changes under noise -- the
            # same reasoning behind Phase 4's change detection (ADR-019).
            if snap.delta is not None and snap.delta != 0:
                direction = "increased" if snap.delta > 0 else "decreased"
                previous = f"{snap.previous_score:g}" if snap.previous_score is not None else "n/a"
                out.append(
                    TimelineEntry(
                        entry_key=f"RISK_SCORE_CHANGE:{snap.id}",
                        timestamp=ts,
                        entry_type=TimelineEntryType.RISK_SCORE_CHANGE,
                        title=f"Risk score {direction} by {abs(snap.delta):g} to {snap.current_score:g}",
                        summary=(
                            f"{previous} -> {snap.current_score:g}"
                            + (
                                f" (band {snap.previous_band.value} -> {snap.risk_band.value})"
                                if snap.previous_band and snap.previous_band != snap.risk_band
                                else ""
                            )
                        ),
                        actor_type=ActorType.SYSTEM,
                        actor_id="risk_engine",
                        source_table="risk_score_snapshots",
                        source_id=snap.id,
                        metadata={"delta": snap.delta, "score": snap.current_score},
                    )
                )
        return out

    def _from_risk_events(self, client_id: int) -> list[TimelineEntry]:
        out = []
        for event in self._events.list_for_client(client_id, limit=MAX_PER_SOURCE):
            out.append(
                TimelineEntry(
                    entry_key=f"RISK_EVENT:{event.id}",
                    # event_timestamp is when the FINDING occurred; detected_at
                    # is when we noticed. The timeline is about the world, so it
                    # prefers the former and falls back to the latter.
                    timestamp=_utc(event.event_timestamp or event.detected_at),
                    entry_type=TimelineEntryType.RISK_EVENT,
                    title=f"Risk event: {event.event_type.value} ({event.severity.value})",
                    summary=event.summary,
                    actor_type=ActorType.SYSTEM,
                    actor_id=event.source or "monitoring_service",
                    related_entity=event.entity_ref,
                    related_event_id=event.id,
                    related_evidence_ids=[e.id for e in event.evidence],
                    source_table="risk_events",
                    source_id=event.id,
                    metadata={"factor_id": event.factor_id, "confidence": event.confidence},
                )
            )
        return out

    def _from_evidence(self, client_id: int) -> list[TimelineEntry]:
        """Evidence splits by type: a PROVIDER_RESPONSE row is a statement about
        COVERAGE ("we queried X"), not about the client. Rendering both as
        'evidence' would let "we checked and found nothing" read like a
        finding."""
        out = []
        for row in self._evidence.list_for_client(client_id):
            is_provider = row.evidence_type == EvidenceType.PROVIDER_RESPONSE
            entry_type = TimelineEntryType.PROVIDER_RESULT if is_provider else TimelineEntryType.EVIDENCE
            actor = ActorType.HUMAN if row.evidence_type == EvidenceType.MANUAL else ActorType.SYSTEM
            out.append(
                TimelineEntry(
                    entry_key=f"{entry_type.value}:{row.id}",
                    timestamp=_utc(row.retrieved_at or row.created_at),
                    entry_type=entry_type,
                    title=(
                        f"Provider queried: {row.provider_name or row.source_dataset}"
                        if is_provider
                        else f"Evidence recorded: {row.evidence_type.value}"
                    ),
                    summary=row.extracted_fact,
                    actor_type=actor,
                    actor_id=row.producing_component,
                    related_evidence_ids=[row.id],
                    source_table="evidence",
                    source_id=row.id,
                    metadata={
                        # Tier travels with every entry. ADR-002: a curated demo
                        # hit must never be presentable as an authoritative one,
                        # and a timeline is a presentation.
                        "source_tier": row.source_tier.value,
                        "confidence": row.confidence,
                        "provider": row.provider_name,
                    },
                )
            )
        return out

    def _from_entity_matches(self, case: Case) -> list[TimelineEntry]:
        from app.repositories.entity_match_repository import EntityMatchRepository

        subject_ref = f"client:{case.client.external_client_id}"
        out = []
        for match in EntityMatchRepository(self._db).list_for_subject(subject_ref, limit=MAX_PER_SOURCE):
            out.append(
                TimelineEntry(
                    entry_key=f"ENTITY_RESOLUTION:{match.id}",
                    timestamp=_utc(match.resolved_at),
                    entry_type=TimelineEntryType.ENTITY_RESOLUTION,
                    title=(
                        f"Entity resolution: {match.status.value} "
                        f"({match.combined_confidence:.0f}/100) vs {match.candidate_name}"
                    ),
                    summary=_decode(match.reasons) and "; ".join(_decode(match.reasons)[:3]) or None,
                    # CONFIRMED/HUMAN_REVIEWED are reachable only by a human
                    # (ADR-016), so the actor follows the status rather than
                    # being assumed -- the timeline must not credit a machine
                    # with a person's decision.
                    actor_type=(
                        ActorType.HUMAN
                        if match.status.value in ("CONFIRMED", "HUMAN_REVIEWED")
                        else ActorType.SYSTEM
                    ),
                    actor_id="entity_resolution_service",
                    related_entity=match.candidate_name,
                    source_table="entity_matches",
                    source_id=match.id,
                    metadata={
                        "status": match.status.value,
                        "confidence": match.combined_confidence,
                        "candidate_provider": match.candidate_provider,
                        "source_tier": match.candidate_source_tier,
                    },
                )
            )
        return out

    def _from_alerts(self, client_id: int) -> list[TimelineEntry]:
        out = []
        for alert in self._alerts.list(client_id=client_id, limit=MAX_PER_SOURCE):
            out.append(
                TimelineEntry(
                    entry_key=f"ALERT:{alert.id}",
                    timestamp=_utc(alert.opened_at),
                    entry_type=TimelineEntryType.ALERT,
                    title=f"Alert: {alert.trigger.value} ({alert.severity.value})",
                    summary=alert.reason,
                    actor_type=ActorType.SYSTEM,
                    actor_id="alert_engine",
                    related_event_id=alert.triggering_risk_event_id,
                    related_evidence_ids=[e.id for e in alert.evidence],
                    source_table="alerts",
                    source_id=alert.id,
                    metadata={"trigger": alert.trigger.value, "risk_delta": alert.risk_delta},
                )
            )
        return out

    def _from_investigations(self, client_id: int) -> list[TimelineEntry]:
        out = []
        for inv in self._investigations.list_for_client(client_id, limit=MAX_PER_SOURCE):
            failed = inv.status.value == "FAILED"
            out.append(
                TimelineEntry(
                    entry_key=f"INVESTIGATION:{inv.id}",
                    timestamp=_utc(inv.generated_at or inv.opened_at),
                    entry_type=TimelineEntryType.INVESTIGATION,
                    title=(
                        f"Investigation could not run ({inv.llm_provider or 'llm'})"
                        if failed
                        else f"Investigation report generated ({inv.llm_model or 'llm'})"
                    ),
                    # A failed investigation shows its ERROR, not a blank. "We
                    # could not investigate" and "we investigated and found
                    # nothing" must never look the same (ADR-029/ADR-021).
                    summary=inv.error_message if failed else inv.summary,
                    actor_type=ActorType.AGENT,
                    actor_id=f"investigation_agent:{inv.llm_provider}:{inv.llm_model}",
                    source_table="investigations",
                    source_id=inv.id,
                    metadata={
                        "status": inv.status.value,
                        "grounding_passed": inv.grounding_passed,
                        "hallucinated_citations": inv.hallucinated_citation_count,
                        "prompt_version": inv.prompt_version,
                    },
                )
            )
        return out

    def _from_reviews(self, case: Case) -> list[TimelineEntry]:
        out = []
        for review in case.reviews:
            transition = (
                f"{review.previous_state.value} -> {review.new_state.value}"
                if review.previous_state and review.new_state
                else None
            )
            out.append(
                TimelineEntry(
                    entry_key=f"HUMAN_REVIEW:{review.id}",
                    timestamp=_utc(review.decided_at),
                    entry_type=TimelineEntryType.HUMAN_REVIEW,
                    title=f"Human review: {review.action.value}",
                    summary=review.rationale,
                    actor_type=ActorType.HUMAN,
                    actor_id=review.reviewer_name,
                    source_table="human_reviews",
                    source_id=review.id,
                    metadata={
                        "action": review.action.value,
                        "transition": transition,
                        "target_type": review.target_type,
                        "target_id": review.target_id,
                    },
                )
            )
        return out

    def _from_sars(self, case: Case) -> list[TimelineEntry]:
        out = []
        for sar in case.sar_drafts:
            out.append(
                TimelineEntry(
                    entry_key=f"SAR:{sar.id}",
                    timestamp=_utc(sar.generated_at or sar.created_at),
                    entry_type=TimelineEntryType.SAR,
                    title=f"Draft SAR {sar.sar_ref or sar.id} generated ({sar.status.value})",
                    summary="DRAFT -- requires human approval. This system never files a SAR.",
                    # The narrative is model-written but the document is
                    # assembled deterministically, so the SAR is a system
                    # artefact with an agent contribution -- not an agent
                    # artefact. `narrative_generated_by` records the split.
                    actor_type=ActorType.SYSTEM,
                    actor_id="sar_generator",
                    related_evidence_ids=_decode(sar.cited_evidence_ids_json) or [],
                    source_table="sar_drafts",
                    source_id=sar.id,
                    metadata={
                        "status": sar.status.value,
                        "grounding_passed": sar.grounding_passed,
                        "narrative_generated_by": sar.narrative_generated_by,
                        "reviewed_by": sar.reviewed_by,
                    },
                )
            )
        return out


__all__ = ["TimelineBuilder"]
