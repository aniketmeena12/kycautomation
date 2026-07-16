"""Shared Pydantic base for read schemas backed by an ORM model."""

from pydantic import BaseModel, ConfigDict


class ORMReadModel(BaseModel):
    """Base for any schema constructed from a SQLAlchemy model instance."""

    model_config = ConfigDict(from_attributes=True)
