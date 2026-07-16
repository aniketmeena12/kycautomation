"""
Lightweight source validators. Each reads a bounded sample only:

  - CSVHeaderValidator: pandas.read_csv(..., nrows=CSV_SAMPLE_ROWS). The C
    parser stops after N rows -- it does NOT scan the rest of the file, so
    this is safe even against SAML-D.csv (951 MB) or opensanctions_targets.csv
    (488 MB). See tests/test_ingestion_validation.py, which asserts this
    completes quickly against the real files.
  - JSONStructureValidator: the UBO fixture files are a few KB each -- a full
    read is fine and is not "full production dataset ingestion" in any
    meaningful sense.
  - TextFixtureValidator: the article fixtures are 1-2 KB each.

None of these validators persist anything themselves -- see
app/ingestion/validate_all.py, which calls these and writes the resulting
IngestionResult into DatasetSourceStatus.
"""

from __future__ import annotations

import json

import pandas as pd

from app.ingestion.base import SourceValidator
from app.ingestion.results import IngestionError, IngestionResult, IngestionResultStatus
from app.registry.sources import SourceDefinition

CSV_SAMPLE_ROWS = 5


class CSVHeaderValidator(SourceValidator):
    def validate(self, source: SourceDefinition) -> IngestionResult:
        started_at = self._now()
        path = self._registry.resolve_path(source)

        if not path.is_file():
            return IngestionResult(
                source_key=source.source_key,
                status=IngestionResultStatus.SKIPPED_NOT_FOUND,
                started_at=started_at,
                completed_at=self._now(),
                notes=f"File not found for source '{source.source_key}'.",
            )

        header_kwargs: dict = {"nrows": CSV_SAMPLE_ROWS, "on_bad_lines": "skip"}
        if not source.has_header:
            header_kwargs["header"] = None
            if source.expected_columns:
                header_kwargs["names"] = list(source.expected_columns)

        try:
            sample = pd.read_csv(path, **header_kwargs)
        except Exception as exc:
            return IngestionResult(
                source_key=source.source_key,
                status=IngestionResultStatus.FAILED,
                started_at=started_at,
                completed_at=self._now(),
                errors=[IngestionError(message=f"Failed to parse CSV sample: {exc}")],
            )

        errors: list[IngestionError] = []
        if source.expected_columns and source.has_header:
            missing = [c for c in source.expected_columns if c not in sample.columns]
            if missing:
                errors.append(IngestionError(message=f"Missing expected columns: {missing}"))
        elif source.expected_columns and not source.has_header:
            if len(sample.columns) != len(source.expected_columns):
                errors.append(
                    IngestionError(
                        message=(
                            f"Expected {len(source.expected_columns)} columns, "
                            f"found {len(sample.columns)}."
                        )
                    )
                )

        status = IngestionResultStatus.FAILED if errors else IngestionResultStatus.SUCCESS
        return IngestionResult(
            source_key=source.source_key,
            status=status,
            started_at=started_at,
            completed_at=self._now(),
            records_read=len(sample),
            records_valid=len(sample) if not errors else 0,
            records_invalid=len(sample) if errors else 0,
            errors=errors,
            notes=f"Header/schema validation only -- sampled {len(sample)} row(s), full file not read.",
        )


class JSONStructureValidator(SourceValidator):
    REQUIRED_TOP_LEVEL_KEYS = ("entities", "ownership_edges")

    def validate(self, source: SourceDefinition) -> IngestionResult:
        started_at = self._now()
        path = self._registry.resolve_path(source)

        if not path.is_file():
            return IngestionResult(
                source_key=source.source_key,
                status=IngestionResultStatus.SKIPPED_NOT_FOUND,
                started_at=started_at,
                completed_at=self._now(),
                notes=f"File not found for source '{source.source_key}'.",
            )

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            return IngestionResult(
                source_key=source.source_key,
                status=IngestionResultStatus.FAILED,
                started_at=started_at,
                completed_at=self._now(),
                errors=[IngestionError(message=f"Failed to parse JSON: {exc}")],
            )

        errors = [
            IngestionError(message=f"Missing required top-level key '{key}'.")
            for key in self.REQUIRED_TOP_LEVEL_KEYS
            if key not in data
        ]
        record_count = len(data.get("entities", [])) if isinstance(data, dict) else 0

        status = IngestionResultStatus.FAILED if errors else IngestionResultStatus.SUCCESS
        return IngestionResult(
            source_key=source.source_key,
            status=status,
            started_at=started_at,
            completed_at=self._now(),
            records_read=record_count,
            records_valid=record_count if not errors else 0,
            records_invalid=0 if not errors else record_count,
            errors=errors,
        )


class TextFixtureValidator(SourceValidator):
    def validate(self, source: SourceDefinition) -> IngestionResult:
        started_at = self._now()
        path = self._registry.resolve_path(source)

        if not path.is_file():
            return IngestionResult(
                source_key=source.source_key,
                status=IngestionResultStatus.SKIPPED_NOT_FOUND,
                started_at=started_at,
                completed_at=self._now(),
                notes=f"File not found for source '{source.source_key}'.",
            )

        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            return IngestionResult(
                source_key=source.source_key,
                status=IngestionResultStatus.FAILED,
                started_at=started_at,
                completed_at=self._now(),
                errors=[IngestionError(message=f"Failed to read text fixture: {exc}")],
            )

        errors = []
        if not text.strip():
            errors.append(IngestionError(message="Article fixture is empty."))

        status = IngestionResultStatus.FAILED if errors else IngestionResultStatus.SUCCESS
        return IngestionResult(
            source_key=source.source_key,
            status=status,
            started_at=started_at,
            completed_at=self._now(),
            records_read=1,
            records_valid=1 if not errors else 0,
            records_invalid=0 if not errors else 1,
            errors=errors,
            notes=f"{len(text)} bytes, {len(text.split())} words.",
        )


def get_validator_for(source: SourceDefinition, registry=None) -> SourceValidator:
    from app.core.enums import SourceFormat

    if source.format == SourceFormat.CSV:
        return CSVHeaderValidator(registry)
    if source.format == SourceFormat.JSON:
        return JSONStructureValidator(registry)
    if source.format == SourceFormat.TEXT:
        return TextFixtureValidator(registry)
    raise ValueError(f"No validator registered for format {source.format}")
