"""
Maps a registry source_key to the loader that actually ingests it.

Only sources with a real, bounded, full-load story get an entry here --
`FULL_LOAD` and `CURATED_FIXTURE` sources from app/registry/sources.py.
`LOOKUP_ONLY` sources (saml_d, ofac_sdn, ofac_alt, ofac_add, opensanctions)
deliberately have no loader; they're served by app/providers/ instead (see
docs/phase-2-ingestion.md SS3). `sample_ofac_alt` also has no loader of its
own -- it's ingested as part of `sample_ofac_sdn`'s loader (see
app/ingestion/loaders/sanctions_curated.py).
"""

from __future__ import annotations

from app.ingestion.loaders.accounts import AccountLoader
from app.ingestion.loaders.articles import ArticleLoader
from app.ingestion.loaders.base import DatasetLoader
from app.ingestion.loaders.clients import ClientLoader
from app.ingestion.loaders.ownership import OwnershipLoader
from app.ingestion.loaders.sanctions_curated import CuratedOfacLoader
from app.ingestion.loaders.sanctions_curated_opensanctions import CuratedOpenSanctionsLoader
from app.ingestion.loaders.transactions_shallow import ShallowTransactionLoader
from app.registry.sources import SourceRegistry

AUXILIARY_SOURCE_KEYS = frozenset({"sample_ofac_alt"})


def get_loader_for(source_key: str, registry: SourceRegistry | None = None) -> DatasetLoader | None:
    if source_key == "clients":
        return ClientLoader(registry)
    if source_key == "client_account_mapping":
        return AccountLoader(registry)
    if source_key == "transactions_shallow":
        return ShallowTransactionLoader(registry)
    if source_key in ("article_clean", "article_adverse_hit", "article_adversarial"):
        return ArticleLoader(source_key, registry)
    if source_key in ("ubo_simple", "ubo_showcase"):
        return OwnershipLoader(source_key, registry)
    if source_key == "sample_ofac_sdn":
        return CuratedOfacLoader(registry)
    if source_key == "sample_opensanctions":
        return CuratedOpenSanctionsLoader(registry)
    return None


# The order small-dataset ingestion must run in, respecting FK/lookup
# dependencies documented alongside each loader (e.g. AccountLoader needs
# Client rows to exist first). ingest_all() (app/ingestion/commands.py)
# iterates in exactly this order.
INGESTION_ORDER: tuple[str, ...] = (
    "clients",
    "client_account_mapping",
    "transactions_shallow",
    "sample_ofac_sdn",  # also ingests sample_ofac_alt
    "sample_opensanctions",
    "article_clean",
    "article_adverse_hit",
    "article_adversarial",
    "ubo_simple",
    "ubo_showcase",
)
