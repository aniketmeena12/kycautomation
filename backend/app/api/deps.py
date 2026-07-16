"""Shared FastAPI dependencies."""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.providers.registry import get_provider_registry
from app.registry.sources import SourceRegistry
from app.services.customer360_service import Customer360Service
from app.services.provider_execution_service import ProviderExecutionService

__all__ = [
    "get_db",
    "get_source_registry",
    "get_provider_registry",
    "get_execution_service",
    "get_customer360_service",
]


def get_source_registry() -> SourceRegistry:
    return SourceRegistry()


def get_execution_service() -> ProviderExecutionService:
    return ProviderExecutionService()


def get_customer360_service(db: Session = Depends(get_db)) -> Customer360Service:
    return Customer360Service(db)
