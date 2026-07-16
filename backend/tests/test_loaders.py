"""
Loader tests against the REAL small Phase 0 files (clients, accounts,
curated sanctions, articles, ownership -- all fast: seconds or less).

The 50,000-row shallow transaction file is deliberately NOT re-ingested here
on every test run (it costs ~40s) -- its loader logic is instead tested
against a small synthetic CSV in test_shallow_transaction_loader_synthetic,
and the real file is exercised exactly once across the whole suite in
test_ingestion_pipeline.py.
"""

from app.ingestion.loaders.accounts import AccountLoader
from app.ingestion.loaders.articles import ArticleLoader
from app.ingestion.loaders.clients import ClientLoader
from app.ingestion.loaders.ownership import OwnershipLoader
from app.ingestion.loaders.sanctions_curated import CuratedOfacLoader
from app.ingestion.loaders.sanctions_curated_opensanctions import CuratedOpenSanctionsLoader
from app.ingestion.results import IngestionResultStatus
from app.models.client import Client
from app.models.sanctions import SanctionsEntity


def test_client_loader_ingests_all_2000_real_rows(db_session):
    result = ClientLoader().load(db_session)
    assert result.status == IngestionResultStatus.SUCCESS
    assert result.records_read == 2000
    assert result.records_valid == 2000
    assert db_session.query(Client).count() == 2000


def test_client_loader_is_idempotent_on_rerun(db_session):
    ClientLoader().load(db_session)
    result2 = ClientLoader().load(db_session)
    assert result2.records_valid == 2000
    assert "2000 updated" in result2.notes
    assert db_session.query(Client).count() == 2000  # not 4000


def test_account_loader_requires_clients_first(db_session):
    """A mapping row whose client_id isn't ingested yet is a recorded error,
    not a crash or a fabricated client."""
    result = AccountLoader().load(db_session)
    assert result.status == IngestionResultStatus.PARTIAL
    assert result.records_invalid == 120  # none of the 120 rows can resolve


def test_account_loader_succeeds_after_clients(db_session):
    ClientLoader().load(db_session)
    result = AccountLoader().load(db_session)
    assert result.status == IngestionResultStatus.SUCCESS
    assert result.records_valid == 120


def test_curated_ofac_loader_ingests_sample_sdn_and_alt(db_session):
    result = CuratedOfacLoader().load(db_session)
    assert result.status == IngestionResultStatus.SUCCESS
    assert "sample_ofac_alt" in result.notes
    entities = db_session.query(SanctionsEntity).all()
    assert len(entities) == 17
    from app.core.enums import SourceTier

    assert all(e.source_tier == SourceTier.TIER_2_CURATED_DEMO for e in entities)
    al_rashid = [e for e in entities if e.external_entity_id == "001923"]
    assert len(al_rashid) == 1
    assert al_rashid[0].name == "AL-RASHID, Mohammad"
    assert al_rashid[0].birth_date is not None  # extracted from Remarks via generic regex
    assert len(al_rashid[0].aliases) >= 1


def test_curated_opensanctions_loader_flags_known_malformed_row(db_session):
    """Regression test for the real column-shift defect found in Phase 0
    (docs/data-dictionary.md): os-003401 / Sokolov."""
    result = CuratedOpenSanctionsLoader().load(db_session)
    assert result.status == IngestionResultStatus.PARTIAL
    assert result.records_invalid == 1
    assert any("column-shifted" in e.message for e in result.errors)

    from app.core.enums import SourceType

    sokolov = (
        db_session.query(SanctionsEntity)
        .filter_by(source_type=SourceType.CURATED_OPENSANCTIONS, external_entity_id="os-003401")
        .one()
    )
    # Reliable leading fields still ingested...
    assert "Sokolov" in sokolov.name
    # ...unreliable shifted fields nulled rather than stored wrong.
    assert sokolov.program_or_dataset is None


def test_article_loader_ingests_adversarial_fixture_verbatim(db_session):
    result = ArticleLoader("article_adversarial").load(db_session)
    assert result.status == IngestionResultStatus.SUCCESS
    from app.models.media import AdverseMediaArticle

    article = (
        db_session.query(AdverseMediaArticle).filter_by(external_source_key="adversarial_article.txt").one()
    )
    assert "IGNORE ALL PRIOR INSTRUCTIONS" in article.raw_text  # stored verbatim, never sanitized/executed
    assert article.contains_prompt_injection_flag is None  # not computed -- no NLP agent exists yet


def test_ownership_loader_showcase_graph_effective_ownership(db_session):
    result = OwnershipLoader("ubo_showcase").load(db_session)
    assert result.status == IngestionResultStatus.SUCCESS

    from app.repositories.ownership_repository import OwnershipRepository

    entities, edges = OwnershipRepository(db_session).get_graph("showcase_structure")
    assert len(entities) == 4
    assert len(edges) == 3
    percentages = sorted(e.percentage for e in edges)
    assert percentages == [
        60.0,
        80.0,
        100.0,
    ]  # 0.80 * 0.60 * 1.00 = 48% effective, per docs/phase-0-dataset-audit.md


def test_shallow_transaction_loader_synthetic(db_session, tmp_path):
    """Fast loader-logic test against a small synthetic CSV -- proves
    idempotency, duplicate-key detection, and client-resolution error
    handling without paying the real file's ~40s cost."""
    import pandas as pd

    ClientLoader().load(db_session)  # need real client_id=1 to exist

    csv_path = tmp_path / "transactions_with_fatf_ofac.csv"
    pd.DataFrame(
        [
            {
                "transaction_id": 1,
                "client_id": 1,
                "amount": 100.0,
                "transaction_type": "Wire",
                "timestamp": "2025-08-01 10:00:00",
                "client_country": "US",
                "counterparty_country": "IR",
                "ofac_match_flag": 1,
                "fatf_country_flag": 0,
                "structuring_pattern_flag": 0,
                "rapid_movement_flag": 0,
                "trade_mispricing_flag": 0,
            },
            {
                "transaction_id": 1,
                "client_id": 1,
                "amount": 999.0,
                "transaction_type": "Wire",  # duplicate key
                "timestamp": "2025-08-02 10:00:00",
                "client_country": "US",
                "counterparty_country": "IR",
                "ofac_match_flag": 0,
                "fatf_country_flag": 0,
                "structuring_pattern_flag": 0,
                "rapid_movement_flag": 0,
                "trade_mispricing_flag": 0,
            },
            {
                "transaction_id": 2,
                "client_id": 999999,
                "amount": 5.0,
                "transaction_type": "ACH",  # unknown client
                "timestamp": "2025-08-01 10:00:00",
                "client_country": "US",
                "counterparty_country": "US",
                "ofac_match_flag": 0,
                "fatf_country_flag": 0,
                "structuring_pattern_flag": 0,
                "rapid_movement_flag": 0,
                "trade_mispricing_flag": 0,
            },
        ]
    ).to_csv(csv_path, index=False)

    from unittest.mock import patch

    from app.ingestion.loaders.transactions_shallow import ShallowTransactionLoader

    loader = ShallowTransactionLoader()
    with patch.object(loader, "path", return_value=csv_path):
        result = loader.load(db_session)

    assert result.records_read == 3
    # 2 successful upsert operations (row 1 creates, row 2 updates the same
    # row via the duplicate key) converging to 1 final row -- plus 1
    # duplicate-key warning and 1 unresolved-client error recorded alongside.
    assert result.records_valid == 2
    assert result.records_invalid == 2
    from app.models.transaction import Transaction

    txn = db_session.query(Transaction).one()
    assert txn.amount == 999.0  # second (duplicate-key) row's values won the upsert, as expected
