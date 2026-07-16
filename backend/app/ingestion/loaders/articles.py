"""ArticleLoader -- ingests a single adverse-media text fixture. One loader
instance per fixture (article_clean / article_adverse_hit / article_adversarial
-- see app/ingestion/loaders/registry.py), since each is registered as its
own source. `raw_text` is stored verbatim -- untrusted content, never
parsed or executed (see docs/phase-1-foundation.md's DATA IS DATA principle)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.ingestion.loaders.base import DatasetLoader
from app.ingestion.normalizers import build_provenance
from app.ingestion.results import IngestionResult, IngestionResultStatus
from app.repositories.article_repository import ArticleRepository


class ArticleLoader(DatasetLoader):
    def __init__(self, source_key: str, registry=None) -> None:
        self.source_key = source_key
        super().__init__(registry)

    def load(self, db: Session) -> IngestionResult:
        started_at = self._now()
        source = self.source()
        path = self.path()

        if not path.is_file():
            return self._not_found_result(started_at)

        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return IngestionResult(
                source_key=self.source_key,
                status=IngestionResultStatus.FAILED,
                started_at=started_at,
                completed_at=self._now(),
                records_read=1,
                records_invalid=1,
                notes="Article fixture is empty.",
            )

        repo = ArticleRepository(db)
        fields = {
            "raw_text": text,
            "contains_prompt_injection_flag": None,  # not computed until an NLP agent exists
            **build_provenance(
                source_dataset=source.relative_path,
                source_tier=source.source_tier,
                source_type=source.source_type,
            ),
        }
        _, created = repo.upsert(external_source_key=source.relative_path.split("/")[-1], **fields)
        db.commit()

        return IngestionResult(
            source_key=self.source_key,
            status=IngestionResultStatus.SUCCESS,
            started_at=started_at,
            completed_at=self._now(),
            records_read=1,
            records_valid=1,
            notes=f"{'created' if created else 'updated'}. {len(text)} bytes.",
        )
