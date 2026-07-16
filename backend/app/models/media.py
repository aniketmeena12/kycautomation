"""
AdverseMediaArticle -- the 3 article fixtures from data/articles/.

Per docs/phase-0-dataset-audit.md SS4.6, these are tagged TIER_2_CURATED_DEMO:
they were deliberately written to interlock with the Tier-2 sanctions fixture
and the UBO showcase graph, not randomly sampled real news.

`raw_text` is stored verbatim and is explicitly untrusted content -- see the
"DATA IS DATA, NOT INSTRUCTIONS" principle in docs/phase-1-foundation.md.
`contains_prompt_injection_flag` is a placeholder column for a future NLP
agent to populate; Phase 1 does not compute it (no detection logic is
implemented here -- see the Security Baseline section of the Phase 1 doc).
"""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import ProvenanceMixin, TimestampMixin


class AdverseMediaArticle(Base, TimestampMixin, ProvenanceMixin):
    __tablename__ = "adverse_media_articles"
    __table_args__ = (UniqueConstraint("external_source_key", name="uq_article_external_source_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # e.g. "adverse_hit_article.txt" -- the fixture filename, the only stable
    # identifier these files have.
    external_source_key: Mapped[str] = mapped_column(String, nullable=False, index=True)

    raw_text: Mapped[str] = mapped_column(Text, nullable=False)

    # Not computed in Phase 1 -- reserved for the future Adverse Media Agent.
    contains_prompt_injection_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AdverseMediaArticle id={self.id} external_source_key={self.external_source_key!r}>"
