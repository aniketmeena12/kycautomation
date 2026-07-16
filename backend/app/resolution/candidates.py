"""
Candidate generation -- search-space reduction before fuzzy matching
(Phase 3 brief SS6/SS14: "Never scan 1.3M sanctions records unless
unavoidable").

Two sources, with sharply different cost profiles:

  1. LOCAL DB (default, fast). Blocking query against the ingested
     SanctionsEntity/SanctionsAlias tables. Instead of loading every row and
     fuzzy-scoring it, we first narrow with an indexed SQL `LIKE` on
     significant name tokens, then fuzzy-score only what comes back. This is
     the actual "reduce search space before fuzzy matching" step.

  2. PROVIDERS (opt-in per provider). Phase 2 measured these: the Tier-1 OFAC
     stream is ~0.7s, but the Tier-1 OpenSanctions stream is ~40-45s over
     1.3M rows (docs/phase-2-ingestion.md SS3). So providers are opt-in by
     name, and `EXPENSIVE_PROVIDERS` is excluded unless explicitly requested.
     Calling the 1.3M-row scan on every resolve would violate SS14 outright.

Blocking is a genuine recall/latency trade, stated plainly: an entity whose
name shares NO significant token with the query will not be retrieved from
the local DB, no matter how high its fuzzy score would have been
('Mohamed' vs 'Muhammad' block differently). Accepted because the alternative
is loading the whole table per query. Documented in
docs/phase-3-entity-resolution.md as a known limitation, not hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.enums import ProviderCategory, SourceTier
from app.models.sanctions import SanctionsAlias, SanctionsEntity
from app.providers.registry import ProviderRegistry, get_provider_registry
from app.resolution.adapters import external_candidate_to_subject, sanctions_entity_to_subject
from app.resolution.normalization import COMPANY_SUFFIXES, normalize_for_matching
from app.resolution.schemas import ResolutionSubject
from app.services.provider_execution_service import ProviderExecutionService

MIN_TOKEN_LENGTH = 3
DEFAULT_DB_CANDIDATE_LIMIT = 200

# Measured at ~40-45s per query over 1.3M rows (docs/phase-2-ingestion.md SS3).
# Never queried unless a caller explicitly asks for it by name.
EXPENSIVE_PROVIDERS = frozenset({"tier1_opensanctions_lookup"})


@dataclass
class CandidateBatch:
    candidates: list[ResolutionSubject] = field(default_factory=list)
    providers_queried: list[str] = field(default_factory=list)
    provider_errors: list[str] = field(default_factory=list)
    db_rows_examined: int = 0


def blocking_tokens(name: str) -> list[str]:
    """Significant tokens used to narrow the SQL query.

    Legal-form tokens are dropped: blocking on 'ltd' would match a large
    fraction of every company list and defeat the point of blocking.
    """
    normalized = normalize_for_matching(name)
    tokens = [t for t in normalized.split() if len(t) >= MIN_TOKEN_LENGTH and t not in COMPANY_SUFFIXES]
    # Longest-first: the rarest tokens are the most selective.
    return sorted(set(tokens), key=len, reverse=True)


class CandidateGenerator:
    def __init__(
        self,
        db: Session,
        provider_registry: ProviderRegistry | None = None,
        execution_service: ProviderExecutionService | None = None,
    ) -> None:
        self._db = db
        self._registry = provider_registry or get_provider_registry()
        self._execution = execution_service or ProviderExecutionService()

    def generate(
        self,
        subject: ResolutionSubject,
        *,
        include_local_db: bool = True,
        include_providers: bool = False,
        allow_expensive_providers: bool = False,
        source_tier: SourceTier | None = None,
        db_limit: int = DEFAULT_DB_CANDIDATE_LIMIT,
    ) -> CandidateBatch:
        batch = CandidateBatch()
        seen_refs: set[str] = set()

        if include_local_db:
            for candidate in self._from_local_db(subject, source_tier=source_tier, limit=db_limit):
                if candidate.subject_ref not in seen_refs:
                    seen_refs.add(candidate.subject_ref)
                    batch.candidates.append(candidate)
            batch.db_rows_examined = len(batch.candidates)

        if include_providers:
            self._from_providers(subject, batch, seen_refs, allow_expensive_providers)

        return batch

    def _from_local_db(
        self, subject: ResolutionSubject, *, source_tier: SourceTier | None, limit: int
    ) -> list[ResolutionSubject]:
        tokens = blocking_tokens(subject.name)
        if not tokens:
            return []

        # Indexed LIKE over primary names OR aliases -- the alias join is what
        # lets 'M. Rashid' retrieve the entity whose primary name is
        # 'AL-RASHID, Mohammad'.
        name_filters = [SanctionsEntity.name.ilike(f"%{t}%") for t in tokens]
        alias_subquery = select(SanctionsAlias.sanctions_entity_id).where(
            or_(*[SanctionsAlias.alias_name.ilike(f"%{t}%") for t in tokens])
        )

        stmt = (
            select(SanctionsEntity)
            .options(selectinload(SanctionsEntity.aliases))
            .where(or_(or_(*name_filters), SanctionsEntity.id.in_(alias_subquery)))
        )
        if source_tier is not None:
            stmt = stmt.where(SanctionsEntity.source_tier == source_tier)
        stmt = stmt.limit(limit)

        return [sanctions_entity_to_subject(e) for e in self._db.scalars(stmt).unique()]

    def _from_providers(
        self,
        subject: ResolutionSubject,
        batch: CandidateBatch,
        seen_refs: set[str],
        allow_expensive: bool,
    ) -> None:
        for provider in self._registry.get_providers(ProviderCategory.SANCTIONS):
            provider_name = provider.provider_name
            if provider_name in EXPENSIVE_PROVIDERS and not allow_expensive:
                continue

            result = self._execution.execute(
                provider,
                lambda p=provider: p.search_entity(subject.name),
                category=ProviderCategory.SANCTIONS,
            )
            batch.providers_queried.append(provider_name)
            if result.error_message:
                batch.provider_errors.append(f"{provider_name}: {result.error_message}")

            for item in result.items:
                candidate = external_candidate_to_subject(item)
                if candidate.subject_ref not in seen_refs:
                    seen_refs.add(candidate.subject_ref)
                    batch.candidates.append(candidate)
