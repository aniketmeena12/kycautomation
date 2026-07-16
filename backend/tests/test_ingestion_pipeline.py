"""
The one deliberately comprehensive, slow (~45s) integration test: runs the
REAL full small-dataset ingestion pipeline (all 10 sources, including the
50,000-row shallow transaction file) against the actual Phase 0 data, then
builds a real Customer360 response from it. Every other test file uses
synthetic fixtures specifically to avoid repeating this cost -- this is the
single end-to-end proof that the whole pipeline genuinely works together
against real data, not just in isolated pieces.
"""

from app.ingestion.commands import ingest_all
from app.ingestion.results import IngestionResultStatus
from app.repositories.client_repository import ClientRepository
from app.services.customer360_service import Customer360Service


def test_full_small_dataset_pipeline_against_real_data(db_session):
    results = ingest_all(db_session, include_large=False)

    by_key = {r.source_key: r for r in results}
    assert by_key["clients"].status == IngestionResultStatus.SUCCESS
    assert by_key["clients"].records_valid == 2000
    assert by_key["client_account_mapping"].records_valid == 120
    assert by_key["transactions_shallow"].records_valid == 50000
    assert by_key["sample_ofac_sdn"].status == IngestionResultStatus.SUCCESS
    # sample_opensanctions is PARTIAL by design -- one known malformed row, documented.
    assert by_key["sample_opensanctions"].status == IngestionResultStatus.PARTIAL
    for key in ("article_clean", "article_adverse_hit", "article_adversarial", "ubo_simple", "ubo_showcase"):
        assert by_key[key].status == IngestionResultStatus.SUCCESS

    from app.models.client import Client
    from app.models.transaction import Transaction

    assert db_session.query(Client).count() == 2000
    assert db_session.query(Transaction).count() == 50000

    # Re-running is idempotent -- row counts must not double.
    ingest_all(db_session, include_large=False)
    assert db_session.query(Client).count() == 2000
    assert db_session.query(Transaction).count() == 50000

    # Real Customer 360 for the strongest demo candidate from Phase 0
    # (docs/phase-0-dataset-audit.md SS10: client_id=3, "Phillips-Hanson").
    client = ClientRepository(db_session).get_by_external_id(3)
    service = Customer360Service(db_session)
    profile = service.get_customer_360(client.id)

    assert profile.client.client_name == "Phillips-Hanson"
    assert len(profile.accounts) == 2
    assert profile.shallow_transaction_summary.transaction_count == 25
    assert profile.shallow_transaction_summary.flagged_count == 22
