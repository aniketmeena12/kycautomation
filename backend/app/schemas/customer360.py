"""
Customer 360 response contracts.

Two fields are deliberately honest about what Phase 2 does NOT do:

  - `sanctions_candidates` / `adverse_media_candidates` are UNCONFIRMED
    provider hits, not resolved matches -- no entity-resolution scoring or
    confidence exists yet (that's a future phase). The field names avoid the
    word "match" for this reason.
  - `ownership` is null for every real client. Phase 0 confirmed the UBO
    graph fixtures are not linked to any client_id in the source data --
    see docs/phase-0-dataset-audit.md SS5. This DTO does not fabricate that
    link; `ownership_note` explains why.
"""

from datetime import datetime

from pydantic import BaseModel

from app.core.enums import ProviderCategory, ProviderKind, ProviderResultStatus, SourceTier
from app.schemas.account import AccountRead
from app.schemas.client import ClientRead
from app.schemas.evidence import EvidenceRead
from app.schemas.transaction import TransactionSummary


class SourceProvenanceSummary(BaseModel):
    source_dataset: str
    source_tier: SourceTier
    ingested_at: datetime


class ProviderQuerySummary(BaseModel):
    """Which providers were actually queried while assembling this profile,
    and what happened -- the investigation/audit trail architecture
    requirement made concrete at the Customer 360 level."""

    provider_name: str
    provider_kind: ProviderKind
    category: ProviderCategory
    status: ProviderResultStatus
    result_count: int
    error_message: str | None = None


class SanctionsCandidateRead(BaseModel):
    provider: str
    provider_kind: ProviderKind
    source_tier: SourceTier
    external_id: str
    name: str
    entity_type: str | None = None


class AdverseMediaCandidateRead(BaseModel):
    provider: str
    source_tier: SourceTier
    external_id: str
    content_snippet: str | None = None


class DeepTransactionSummary(BaseModel):
    """Summarizes what app/providers/saml_d_transaction_provider.py found
    for one of the client's accounts. Only populated when
    include_deep_transactions=True was requested -- see
    Customer360Service.get_customer_360."""

    account_external_id: str
    matched_count: int
    laundering_labelled_count: int


class Customer360Response(BaseModel):
    client: ClientRead
    accounts: list[AccountRead]
    shallow_transaction_summary: TransactionSummary

    deep_transaction_summaries: list[DeepTransactionSummary] = []
    sanctions_candidates: list[SanctionsCandidateRead] = []
    adverse_media_candidates: list[AdverseMediaCandidateRead] = []

    ownership_note: str = (
        "No ownership graph is linked to this client. Phase 0 confirmed the UBO "
        "graph fixtures are a separate, unconnected demo universe -- see "
        "docs/phase-0-dataset-audit.md SS5. This will remain empty until a future "
        "phase establishes a real link (e.g. via entity resolution)."
    )
    evidence: list[EvidenceRead] = []

    provider_availability: list[ProviderQuerySummary] = []
    source_provenance: SourceProvenanceSummary
    generated_at: datetime
