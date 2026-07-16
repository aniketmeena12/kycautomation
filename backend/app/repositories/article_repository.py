"""Persistence layer for AdverseMediaArticle."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.media import AdverseMediaArticle


class ArticleRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_external_key(self, external_source_key: str) -> AdverseMediaArticle | None:
        stmt = select(AdverseMediaArticle).where(
            AdverseMediaArticle.external_source_key == external_source_key
        )
        return self._db.scalars(stmt).one_or_none()

    def list_all(self) -> list[AdverseMediaArticle]:
        return list(self._db.scalars(select(AdverseMediaArticle)))

    def upsert(self, *, external_source_key: str, **fields) -> tuple[AdverseMediaArticle, bool]:
        existing = self.get_by_external_key(external_source_key)
        if existing is not None:
            for key, value in fields.items():
                setattr(existing, key, value)
            self._db.flush()
            return existing, False

        article = AdverseMediaArticle(external_source_key=external_source_key, **fields)
        self._db.add(article)
        self._db.flush()
        return article, True
