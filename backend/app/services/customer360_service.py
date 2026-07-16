"""
Customer360Service -- assembles the normalized, single-DTO view of a client
from internal data (always, fast) plus live provider lookups (opt-in, since
Tier-1/SAML-D scans can take seconds -- see docs/phase-2-ingestion.md SS3
for measured costs). No AI, no scoring -- this is a read-assembly service,
not a decision-making one.

The three `include_*` flags default to False so GET /customers/{id}/360
stays fast by default; a caller opts into the more expensive live lookups
explicitly. Every provider call goes through ProviderExecutionService, so a
slow/unavailable/misconfigured provider degrades this endpoint's *coverage*,
never its availability -- see provider_availability in the response for
exactly what was queried and what happened.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.enums import ProviderCategory
from app.providers.registry import ProviderRegistry, get_provider_registry
from app.repositories.account_repository import AccountRepository
from app.repositories.client_repository import ClientRepository
from app.repositories.evidence_repository import EvidenceRepository
from app.repositories.transaction_repository import TransactionRepository
from app.schemas.account import AccountRead
from app.schemas.client import ClientRead
from app.schemas.customer360 import (
    AdverseMediaCandidateRead,
    Customer360Response,
    DeepTransactionSummary,
    ProviderQuerySummary,
    SanctionsCandidateRead,
    SourceProvenanceSummary,
)
from app.schemas.evidence import EvidenceRead
from app.schemas.transaction import TransactionSummary
from app.services.provider_execution_service import ProviderExecutionService

DEEP_TRANSACTION_LIMIT_PER_ACCOUNT = 200
OPENSANCTIONS_TIMEOUT_SECONDS = 60.0
SAML_D_TIMEOUT_SECONDS = 45.0


class ClientNotFoundError(Exception):
    def __init__(self, client_id: int) -> None:
        self.client_id = client_id
        super().__init__(f"Client {client_id} not found")


class Customer360Service:
    def __init__(
        self,
        db: Session,
        provider_registry: ProviderRegistry | None = None,
        execution_service: ProviderExecutionService | None = None,
    ) -> None:
        self._db = db
        self._client_repo = ClientRepository(db)
        self._account_repo = AccountRepository(db)
        self._txn_repo = TransactionRepository(db)
        self._evidence_repo = EvidenceRepository(db)
        self._provider_registry = provider_registry or get_provider_registry()
        self._execution = execution_service or ProviderExecutionService()

    def get_customer_360(
        self,
        client_id: int,
        *,
        include_sanctions_lookup: bool = False,
        include_adverse_media_lookup: bool = False,
        include_deep_transactions: bool = False,
    ) -> Customer360Response:
        client = self._client_repo.get_by_id(client_id)
        if client is None:
            raise ClientNotFoundError(client_id)

        accounts = self._account_repo.list_for_client(client_id)
        shallow_summary_raw = self._txn_repo.summary_for_client(client_id)

        provider_summaries: list[ProviderQuerySummary] = []
        sanctions_candidates: list[SanctionsCandidateRead] = []
        adverse_media_candidates: list[AdverseMediaCandidateRead] = []
        deep_summaries: list[DeepTransactionSummary] = []

        if include_sanctions_lookup:
            self._run_sanctions_lookup(client.client_name, provider_summaries, sanctions_candidates)

        if include_adverse_media_lookup:
            self._run_adverse_media_lookup(client.client_name, provider_summaries, adverse_media_candidates)

        if include_deep_transactions and accounts:
            self._run_deep_transaction_lookup(accounts, provider_summaries, deep_summaries)

        evidence = [EvidenceRead.model_validate(e) for e in self._evidence_repo.list_for_client(client_id)]

        return Customer360Response(
            client=ClientRead.model_validate(client),
            accounts=[AccountRead.model_validate(a) for a in accounts],
            shallow_transaction_summary=TransactionSummary(client_id=client_id, **shallow_summary_raw),
            deep_transaction_summaries=deep_summaries,
            sanctions_candidates=sanctions_candidates,
            adverse_media_candidates=adverse_media_candidates,
            evidence=evidence,
            provider_availability=provider_summaries,
            source_provenance=SourceProvenanceSummary(
                source_dataset=client.source_dataset,
                source_tier=client.source_tier,
                ingested_at=client.ingested_at,
            ),
            generated_at=datetime.now(timezone.utc),
        )

    def _run_sanctions_lookup(self, client_name, provider_summaries, sanctions_candidates) -> None:
        for provider in self._provider_registry.get_providers(ProviderCategory.SANCTIONS):
            timeout = OPENSANCTIONS_TIMEOUT_SECONDS if "opensanctions" in provider.provider_name else None
            result = self._execution.execute(
                provider,
                lambda p=provider: p.search_entity(client_name),
                category=ProviderCategory.SANCTIONS,
                timeout_seconds=timeout,
            )
            provider_summaries.append(self._to_summary(result))
            for item in result.items:
                sanctions_candidates.append(
                    SanctionsCandidateRead(
                        provider=item.provider,
                        provider_kind=item.provider_kind,
                        source_tier=item.source_tier,
                        external_id=item.external_id,
                        name=item.name,
                        entity_type=item.entity_type,
                    )
                )

    def _run_adverse_media_lookup(self, client_name, provider_summaries, adverse_media_candidates) -> None:
        for provider in self._provider_registry.get_providers(ProviderCategory.ADVERSE_MEDIA):
            result = self._execution.execute(
                provider,
                lambda p=provider: p.search_entity(client_name),
                category=ProviderCategory.ADVERSE_MEDIA,
            )
            provider_summaries.append(self._to_summary(result))
            for item in result.items:
                adverse_media_candidates.append(
                    AdverseMediaCandidateRead(
                        provider=item.provider,
                        source_tier=item.source_tier,
                        external_id=item.external_id,
                        content_snippet=item.content_snippet,
                    )
                )

    def _run_deep_transaction_lookup(self, accounts, provider_summaries, deep_summaries) -> None:
        for provider in self._provider_registry.get_providers(ProviderCategory.TRANSACTION):
            for account in accounts:
                external_account = str(account.external_account_number)
                result = self._execution.execute(
                    provider,
                    lambda p=provider, a=external_account: p.get_recent_transactions(
                        a, limit=DEEP_TRANSACTION_LIMIT_PER_ACCOUNT
                    ),
                    category=ProviderCategory.TRANSACTION,
                    timeout_seconds=SAML_D_TIMEOUT_SECONDS,
                )
                provider_summaries.append(self._to_summary(result))
                laundering_count = sum(1 for row in result.items if str(row.get("Is_laundering")) == "1")
                deep_summaries.append(
                    DeepTransactionSummary(
                        account_external_id=external_account,
                        matched_count=len(result.items),
                        laundering_labelled_count=laundering_count,
                    )
                )

    @staticmethod
    def _to_summary(result) -> ProviderQuerySummary:
        return ProviderQuerySummary(
            provider_name=result.provider,
            provider_kind=result.provider_kind,
            category=result.category,
            status=result.status,
            result_count=len(result.items),
            error_message=result.error_message,
        )
