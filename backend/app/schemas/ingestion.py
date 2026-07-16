"""Request/response contracts for the ingestion API endpoints."""

from pydantic import BaseModel, model_validator

from app.ingestion.results import IngestionResult


class IngestionValidateRequest(BaseModel):
    source_keys: list[str] | None = None  # None = validate every enabled source


class IngestionLoadRequest(BaseModel):
    source_key: str | None = None
    all: bool = False
    include_large: bool = False

    @model_validator(mode="after")
    def _require_target(self) -> "IngestionLoadRequest":
        if not self.source_key and not self.all:
            raise ValueError("Provide either source_key or all=true.")
        if self.source_key and self.all:
            raise ValueError("Provide either source_key or all=true, not both.")
        return self


class IngestionResultsResponse(BaseModel):
    results: list[IngestionResult]
