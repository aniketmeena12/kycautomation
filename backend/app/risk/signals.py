"""
Signal collectors -- everything observable about a client -> `RiskSignal`.

This module is the Phase 4 analogue of Phase 3's `adapters.py`: it is the
only place that knows what a Client row, a resolution result, or a provider
failure looks like. The risk engine downstream sees nothing but RiskSignals,
which is what lets the Risk Factor Registry be pure configuration.

Collectors are deliberately dumb: they OBSERVE and report, they never judge.
A collector does not know a factor's weight, whether a signal is "bad", or
what it will score -- it just states what is true, with a confidence and a
stable dedup_key. All judgement lives in the registry + engine.

NO LLM ANYWHERE. Every collector reads structured data or a provider result.

--------------------------------------------------------------------------
DEDUP KEYS
--------------------------------------------------------------------------
A dedup_key must fingerprint the FINDING, not the observation event. It must
never contain a timestamp, a run id, or anything that varies between two
cycles that saw the same thing -- otherwise every cycle invents "new"
findings and change detection is meaningless (Phase 4 brief SS9).

Where a finding's identity is genuinely long (an article snippet, a set of
attributes), a short stable hash is used rather than truncating text, so two
different findings can't collide into one key.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.enums import EntityMatchStatus, ProviderCategory, ProviderResultStatus, SectorRisk, SourceTier
from app.models.client import Client
from app.providers.registry import ProviderRegistry, get_provider_registry
from app.repositories.transaction_repository import TransactionRepository
from app.resolution.schemas import EntityResolutionResult
from app.risk.schemas import RiskSignal
from app.services.provider_execution_service import ProviderExecutionService

SIGNAL_PROFILE_FLAG = "PROFILE_FLAG"
SIGNAL_GEOGRAPHY_RISK = "GEOGRAPHY_RISK"
SIGNAL_SECTOR_RISK = "SECTOR_RISK"
SIGNAL_OWNERSHIP_OPACITY = "OWNERSHIP_OPACITY"
SIGNAL_TRANSACTION_TYPOLOGY = "TRANSACTION_TYPOLOGY"
SIGNAL_SANCTIONS_RESOLUTION = "SANCTIONS_RESOLUTION"
SIGNAL_ENTITY_CONFLICT = "ENTITY_CONFLICT"
SIGNAL_ADVERSE_MEDIA = "ADVERSE_MEDIA"
# A live third-party news name-match is NOT the same signal as a curated,
# verified adverse-media hit. It is an unverified lead: the article may concern
# a different entity that merely shares the name. It is recorded as evidence for
# a human to triage, but it must not carry the 30-point ADVERSE_MEDIA weight on a
# bare name-match -- so it gets its own signal_type that no scoring factor
# claims, contributing 0 until a human confirms relevance.
SIGNAL_ADVERSE_MEDIA_UNVERIFIED = "ADVERSE_MEDIA_UNVERIFIED"
SIGNAL_OWNERSHIP_EXPOSURE = "OWNERSHIP_EXPOSURE"
SIGNAL_PROVIDER_FAILURE = "PROVIDER_FAILURE"


def _short_hash(*parts: str) -> str:
    """Stable 12-char fingerprint. Deterministic across processes and runs
    (unlike Python's salted hash())."""
    joined = "|".join(p or "" for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InternalSignalCollector:
    """Signals derivable from what's already in our database. Fast, no I/O
    beyond local queries, no providers."""

    def collect(self, db: Session, client: Client) -> list[RiskSignal]:
        signals: list[RiskSignal] = []
        signals.extend(self._profile_flags(client))
        signals.extend(self._geography(client))
        signals.extend(self._sector(client))
        signals.extend(self._ownership_opacity(client))
        signals.extend(self._transactions(db, client))
        return signals

    def _profile_flags(self, client: Client) -> list[RiskSignal]:
        """Upstream labels from the client master.

        Confidence 1.0 means "the label is definitely present", NOT "the
        underlying claim is definitely true". These flags are upstream
        assertions this system did not derive and cannot corroborate --
        docs/phase-0-dataset-audit.md SS3 measured 0/2000 client names matching
        the authoritative lists. The summary says so explicitly, so a reviewer
        is never misled into thinking we found this ourselves.
        """
        out: list[RiskSignal] = []
        flags = [
            ("pep", client.pep_flag, "Client master carries an upstream PEP label."),
            ("sanctions", client.sanctions_flag, "Client master carries an upstream sanctions label."),
        ]
        for flag_name, present, summary in flags:
            if not present:
                continue
            out.append(
                RiskSignal(
                    signal_type=SIGNAL_PROFILE_FLAG,
                    confidence=1.0,
                    source="internal_kyc",
                    summary=f"{summary} Not independently verified by this system.",
                    dedup_key=f"profile_flag:{flag_name}",
                    occurred_at=_now(),
                    entity_ref=f"client:{client.external_client_id}",
                    metadata={"flag": flag_name},
                )
            )
        return out

    def _geography(self, client: Client) -> list[RiskSignal]:
        reasons = []
        if client.fatf_country_flag:
            reasons.append("FATF-listed")
        if client.ofac_country_flag:
            reasons.append("OFAC-sanctioned")
        if client.sectoral_sanctions_flag:
            reasons.append("sectoral-sanctions")
        if not reasons:
            return []
        return [
            RiskSignal(
                signal_type=SIGNAL_GEOGRAPHY_RISK,
                confidence=1.0,
                source="internal_kyc",
                summary=f"Client domiciled in {client.country} ({', '.join(reasons)} jurisdiction).",
                dedup_key=f"geography:{client.country}:{'+'.join(sorted(reasons))}",
                occurred_at=_now(),
                entity_ref=f"client:{client.external_client_id}",
                metadata={"country": client.country, "reasons": sorted(reasons)},
            )
        ]

    def _sector(self, client: Client) -> list[RiskSignal]:
        if client.sector_risk != SectorRisk.HIGH:
            return []
        return [
            RiskSignal(
                signal_type=SIGNAL_SECTOR_RISK,
                confidence=1.0,
                source="internal_kyc",
                summary=f"Client operates in a High-risk sector ({client.sector}).",
                dedup_key=f"sector:{client.sector}",
                occurred_at=_now(),
                entity_ref=f"client:{client.external_client_id}",
                metadata={"sector": client.sector},
            )
        ]

    def _ownership_opacity(self, client: Client) -> list[RiskSignal]:
        score = client.ownership_opacity_score or 0.0
        if score <= 0:
            return []
        return [
            RiskSignal(
                # Confidence carries the magnitude here: the source value is
                # already a 0-1 measure, so weight x confidence scales the
                # contribution to how opaque the structure actually is.
                signal_type=SIGNAL_OWNERSHIP_OPACITY,
                confidence=min(1.0, score),
                source="internal_kyc",
                summary=f"Ownership opacity score {score:.2f}.",
                dedup_key=f"ownership_opacity:{score:.2f}",
                occurred_at=_now(),
                entity_ref=f"client:{client.external_client_id}",
                metadata={"opacity": score},
            )
        ]

    def _transactions(self, db: Session, client: Client) -> list[RiskSignal]:
        summary = TransactionRepository(db).summary_for_client(client.id)
        total = summary["transaction_count"]
        flagged = summary["flagged_count"]
        if not total or not flagged:
            return []
        ratio = flagged / total
        return [
            RiskSignal(
                signal_type=SIGNAL_TRANSACTION_TYPOLOGY,
                confidence=min(1.0, ratio),
                source="internal_kyc",
                summary=(
                    f"{flagged} of {total} transactions carry an AML typology flag " f"({ratio * 100:.0f}%)."
                ),
                # Ratio-bucketed rather than raw-count: a client whose flagged
                # ratio is materially unchanged should not generate a "new
                # finding" every cycle just because one more transaction landed.
                dedup_key=f"transaction_typology:{round(ratio, 2):.2f}",
                occurred_at=_now(),
                entity_ref=f"client:{client.external_client_id}",
                metadata={"flagged": flagged, "total": total, "ratio": round(ratio, 4)},
            )
        ]


class ResolutionSignalCollector:
    """Turns entity-resolution output into signals. Takes results as an
    argument rather than running resolution itself -- the monitoring service
    owns orchestration, so this stays independently testable."""

    def collect(self, client: Client, results: list[EntityResolutionResult]) -> list[RiskSignal]:
        signals: list[RiskSignal] = []
        for result in results:
            candidate_ref = result.candidate.subject_ref
            if result.status in (EntityMatchStatus.HIGH_CONFIDENCE, EntityMatchStatus.POSSIBLE):
                signals.append(
                    RiskSignal(
                        signal_type=SIGNAL_SANCTIONS_RESOLUTION,
                        # The engine's 0-100 confidence becomes the signal's
                        # 0-1 confidence, so `weight x confidence` inherits the
                        # resolution engine's own corroboration judgement
                        # rather than re-deriving it.
                        confidence=result.confidence / 100.0,
                        source=result.candidate.provider or "unknown",
                        summary=(
                            f"Resolved '{result.subject.name}' to '{result.candidate.name}' "
                            f"({result.confidence:.0f}/100, {result.status.value}, "
                            f"tier={result.candidate.source_tier})."
                        ),
                        dedup_key=f"sanctions_resolution:{_short_hash(candidate_ref or result.candidate.name)}",
                        occurred_at=result.resolved_at,
                        entity_ref=candidate_ref,
                        metadata={
                            "status": result.status.value,
                            "source_tier": result.candidate.source_tier,
                            "matched_attributes": result.matched_attributes,
                        },
                    )
                )
            if result.conflicting_attributes:
                signals.append(
                    RiskSignal(
                        signal_type=SIGNAL_ENTITY_CONFLICT,
                        confidence=1.0,
                        source=result.candidate.provider or "unknown",
                        summary=(
                            f"Candidate '{result.candidate.name}' conflicts on "
                            f"{result.conflicting_attributes} -- recorded for transparency, "
                            "contributes no risk."
                        ),
                        dedup_key=(
                            f"entity_conflict:{_short_hash(candidate_ref or result.candidate.name, *sorted(result.conflicting_attributes))}"
                        ),
                        occurred_at=result.resolved_at,
                        entity_ref=candidate_ref,
                        metadata={"conflicts": result.conflicting_attributes},
                    )
                )
        return signals


class ProviderSignalCollector:
    """Queries registered providers and turns their outcomes -- including
    failures -- into signals.

    A provider failure becomes a SIGNAL, not an exception. That is how the
    monitoring engine survives a dead provider (Phase 4 brief SS10) while
    still recording that the cycle's coverage was incomplete. The
    corresponding factor has weight 0: an outage must never raise a client's
    risk, but it must never be silent either.
    """

    def __init__(
        self,
        provider_registry: ProviderRegistry | None = None,
        execution_service: ProviderExecutionService | None = None,
    ) -> None:
        self._registry = provider_registry or get_provider_registry()
        self._execution = execution_service or ProviderExecutionService()

    def collect_adverse_media(self, client: Client) -> tuple[list[RiskSignal], list[str], list[str]]:
        """Returns (signals, providers_queried, failures)."""
        signals: list[RiskSignal] = []
        queried: list[str] = []
        failures: list[str] = []

        for provider in self._registry.get_providers(ProviderCategory.ADVERSE_MEDIA):
            result = self._execution.execute(
                provider,
                lambda p=provider: p.search_entity(client.client_name),
                category=ProviderCategory.ADVERSE_MEDIA,
            )
            queried.append(result.provider)

            if result.status in (
                ProviderResultStatus.ERROR,
                ProviderResultStatus.TIMEOUT,
                ProviderResultStatus.RATE_LIMITED,
            ):
                failures.append(f"{result.provider}: {result.status.value}")
                signals.append(self._failure_signal(client, result))
                continue
            # NOT_CONFIGURED is not a failure -- it is a provider that was
            # never expected to answer. Treating it as degraded coverage would
            # cry wolf on every cycle in a default install.
            if result.status == ProviderResultStatus.NOT_CONFIGURED:
                continue

            for article in result.items:
                # A live (EXTERNAL_LIVE) hit is an unverified name-match; a
                # curated hit is a verified demo finding. They are different
                # signals, and only the verified one carries scoring weight.
                is_live = article.source_tier == SourceTier.EXTERNAL_LIVE
                signal_type = SIGNAL_ADVERSE_MEDIA_UNVERIFIED if is_live else SIGNAL_ADVERSE_MEDIA
                signals.append(
                    RiskSignal(
                        signal_type=signal_type,
                        # Provider hit confidence is not something this system
                        # can measure without NLP (a future phase). 1.0 means
                        # "the provider returned this", and the factor weight
                        # is what expresses how much that is worth.
                        confidence=1.0,
                        source=result.provider,
                        summary=(
                            f"{'UNVERIFIED live ' if is_live else ''}adverse-media provider "
                            f"'{result.provider}' returned article '{article.external_id}'"
                            + (f": {article.title}" if article.title else "")
                        ),
                        dedup_key=f"adverse_media:{_short_hash(result.provider, article.external_id)}",
                        occurred_at=article.retrieved_at,
                        entity_ref=f"article:{article.external_id}",
                        # Carry the full article so the monitoring layer can
                        # persist it as evidence without re-querying the provider.
                        metadata={
                            "article_id": article.external_id,
                            "source_tier": article.source_tier.value if article.source_tier else None,
                            "provider_kind": article.provider_kind.value if article.provider_kind else None,
                            "title": article.title,
                            "snippet": article.content_snippet,
                            "url": article.url,
                            "source_name": article.source_name,
                            "unverified": is_live,
                        },
                    )
                )
        return signals, queried, failures

    def _failure_signal(self, client: Client, result) -> RiskSignal:
        return RiskSignal(
            signal_type=SIGNAL_PROVIDER_FAILURE,
            confidence=1.0,
            source=result.provider,
            summary=(
                f"Provider '{result.provider}' was unavailable ({result.status.value}): "
                f"{result.error_message or 'no detail'}. This cycle's coverage is incomplete."
            ),
            # Keyed on provider+status, NOT the error text -- a flapping
            # provider whose message varies must not spawn endless "new" events.
            dedup_key=f"provider_failure:{result.provider}:{result.status.value}",
            occurred_at=result.queried_at,
            entity_ref=f"client:{client.external_client_id}",
            metadata={"provider": result.provider, "status": result.status.value},
        )
