"""
Candidate generation: blocking, search-space reduction, and the hard rule
that the 1.3M-row provider is never queried unless explicitly requested.
"""

from datetime import datetime, timezone

from app.core.enums import ProviderCategory, ProviderKind, ProviderResultStatus, SourceTier
from app.ingestion.commands import ingest_dataset
from app.providers.registry import ProviderRegistry
from app.providers.schemas import ExternalEntityCandidate, ProviderResult
from app.resolution.candidates import EXPENSIVE_PROVIDERS, CandidateGenerator, blocking_tokens
from app.resolution.schemas import ResolutionSubject


def subj(name, ref="s:1", **kw) -> ResolutionSubject:
    return ResolutionSubject(subject_ref=ref, name=name, **kw)


# ---------------------------------------------------------------- blocking


def test_blocking_tokens_drops_legal_suffixes_and_short_tokens():
    tokens = blocking_tokens("Greenfield Technologies Pte Ltd")
    assert "greenfield" in tokens
    assert "technologies" in tokens
    assert "ltd" not in tokens  # would match a huge fraction of any company list
    assert "pte" not in tokens


def test_blocking_tokens_are_longest_first_for_selectivity():
    tokens = blocking_tokens("Meridian Holdings International")
    assert tokens == sorted(tokens, key=len, reverse=True)


def test_blocking_tokens_empty_for_unusable_name():
    assert blocking_tokens("") == []
    assert blocking_tokens("Ltd") == []


# --------------------------------------------------------------- local db


def test_local_db_candidates_are_narrowed_not_full_table(db_session):
    ingest_dataset(db_session, "sample_ofac_sdn")
    from app.models.sanctions import SanctionsEntity

    total_rows = db_session.query(SanctionsEntity).count()
    assert total_rows == 17

    batch = CandidateGenerator(db_session).generate(subj("Mohammad Al-Rashid"))
    # Blocking must return strictly fewer than the whole table.
    assert 0 < len(batch.candidates) < total_rows


def test_local_db_candidate_generation_finds_via_alias(db_session):
    """'M. Rashid' is an ALIAS; the primary name is 'AL-RASHID, Mohammad'.
    Blocking must reach it through the alias join."""
    ingest_dataset(db_session, "sample_ofac_sdn")
    batch = CandidateGenerator(db_session).generate(subj("M. Rashid"))
    assert any("AL-RASHID" in c.name for c in batch.candidates)


def test_unmatched_name_yields_no_candidates(db_session):
    ingest_dataset(db_session, "sample_ofac_sdn")
    batch = CandidateGenerator(db_session).generate(subj("Qxzjklm Nonexistent Synthetic 000111"))
    assert batch.candidates == []


def test_source_tier_filter_isolates_tiers(db_session):
    ingest_dataset(db_session, "sample_ofac_sdn")
    generator = CandidateGenerator(db_session)

    tier2 = generator.generate(subj("Mohammad Al-Rashid"), source_tier=SourceTier.TIER_2_CURATED_DEMO)
    tier1 = generator.generate(subj("Mohammad Al-Rashid"), source_tier=SourceTier.TIER_1_AUTHORITATIVE)

    assert len(tier2.candidates) > 0
    assert tier1.candidates == []  # nothing Tier-1 is bulk-loaded, by design
    assert all(c.source_tier == SourceTier.TIER_2_CURATED_DEMO.value for c in tier2.candidates)


# --------------------------------------------------------------- providers


class _SpyProvider:
    provider_name = "tier1_opensanctions_lookup"  # the expensive one, by name
    provider_kind = ProviderKind.LOCAL_REFERENCE_DATASET

    def __init__(self):
        self.calls = 0

    def is_configured(self):
        return True

    def search_entity(self, name, *, country=None, entity_type=None):
        self.calls += 1
        return ProviderResult(
            status=ProviderResultStatus.NO_RESULTS,
            provider=self.provider_name,
            provider_kind=self.provider_kind,
            category=ProviderCategory.SANCTIONS,
            queried_at=datetime.now(timezone.utc),
        )

    def get_entity(self, external_id):
        raise NotImplementedError


class _CheapProvider(_SpyProvider):
    provider_name = "cheap_test_provider"

    def search_entity(self, name, *, country=None, entity_type=None):
        self.calls += 1
        return ProviderResult(
            status=ProviderResultStatus.SUCCESS,
            provider=self.provider_name,
            provider_kind=self.provider_kind,
            category=ProviderCategory.SANCTIONS,
            queried_at=datetime.now(timezone.utc),
            items=[
                ExternalEntityCandidate(
                    provider=self.provider_name,
                    provider_kind=self.provider_kind,
                    source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                    external_id="p1",
                    name=name,
                    retrieved_at=datetime.now(timezone.utc),
                )
            ],
        )


def test_expensive_provider_is_never_queried_by_default(db_session):
    """The core SS14 guard: no 1.3M-row scan unless explicitly asked for."""
    spy = _SpyProvider()
    registry = ProviderRegistry()
    registry.register(ProviderCategory.SANCTIONS, spy)

    generator = CandidateGenerator(db_session, provider_registry=registry)
    generator.generate(subj("anything"), include_local_db=False, include_providers=True)

    assert spy.calls == 0
    assert spy.provider_name in EXPENSIVE_PROVIDERS


def test_expensive_provider_runs_only_when_explicitly_allowed(db_session):
    spy = _SpyProvider()
    registry = ProviderRegistry()
    registry.register(ProviderCategory.SANCTIONS, spy)

    generator = CandidateGenerator(db_session, provider_registry=registry)
    generator.generate(
        subj("anything"), include_local_db=False, include_providers=True, allow_expensive_providers=True
    )
    assert spy.calls == 1


def test_providers_are_not_queried_unless_requested(db_session):
    cheap = _CheapProvider()
    registry = ProviderRegistry()
    registry.register(ProviderCategory.SANCTIONS, cheap)

    generator = CandidateGenerator(db_session, provider_registry=registry)
    generator.generate(subj("anything"), include_local_db=False, include_providers=False)
    assert cheap.calls == 0


def test_provider_candidates_are_normalized_and_deduped(db_session):
    cheap = _CheapProvider()
    registry = ProviderRegistry()
    registry.register(ProviderCategory.SANCTIONS, cheap)
    registry.register(ProviderCategory.SANCTIONS, cheap)  # same provider twice -> same ref

    generator = CandidateGenerator(db_session, provider_registry=registry)
    batch = generator.generate(subj("Testco"), include_local_db=False, include_providers=True)

    assert len(batch.candidates) == 1  # deduped by subject_ref
    assert batch.candidates[0].provider == "cheap_test_provider"
    assert batch.providers_queried == ["cheap_test_provider", "cheap_test_provider"]
