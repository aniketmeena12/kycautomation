"""Transaction schemas. Per docs/data-dictionary.md, the two source datasets
(shallow file vs. SAML-D) carry different flag sets -- TransactionRead exposes
the full normalized row; TransactionSummary is the aggregate view a future
Customer 360 endpoint will actually return (no per-row dump of 9.5M SAML-D
rows to a client)."""

from datetime import datetime

from pydantic import BaseModel

from app.core.enums import TransactionSourceType
from app.schemas.base import ORMReadModel


class TransactionRead(ORMReadModel):
    id: int
    external_transaction_id: int | None
    transaction_source: TransactionSourceType
    client_id: int | None
    account_id: int | None
    amount: float
    currency: str | None
    transaction_type: str
    occurred_at: datetime
    counterparty_country: str | None
    ofac_match_flag: bool | None
    fatf_country_flag: bool | None
    structuring_pattern_flag: bool | None
    rapid_movement_flag: bool | None
    trade_mispricing_flag: bool | None
    is_laundering: bool | None
    laundering_type: str | None


class TransactionSummary(BaseModel):
    """Aggregate view for a single client -- what Customer 360 will surface,
    not a raw transaction dump."""

    client_id: int
    transaction_count: int
    total_amount: float
    flagged_count: int
    # None (not 0) when the underlying source has no Is_laundering label at
    # all -- the shallow file doesn't carry one. 0 would wrongly imply "we
    # checked and found none." See app/repositories/transaction_repository.py.
    laundering_labelled_count: int | None = None
    earliest_transaction_at: datetime | None
    latest_transaction_at: datetime | None
