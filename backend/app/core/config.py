"""
Environment-aware application configuration.

All paths are computed relative to this file's location, never hard-coded to
a specific machine. Defaults are safe and let the app run locally with no
secrets and no manual setup:

  - SQLite database at backend/data/continuous_kyc.db (created on first run)
  - Raw dataset directory resolved to <project_root>/data (the Phase 0
    dataset tree, one level above backend/) -- this is DIFFERENT from
    backend/data/, which only holds the application's own SQLite file.

Override any of these via a `.env` file in backend/ (see .env.example) or
real environment variables. Nothing here reads or requires secrets.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> backend/app/core -> backend/app -> backend/
BACKEND_DIR = Path(__file__).resolve().parents[2]
# backend/ -> project root (techm/), which contains data/, docs/, scripts/
PROJECT_ROOT = BACKEND_DIR.parent

DEFAULT_DATABASE_PATH = BACKEND_DIR / "data" / "continuous_kyc.db"
DEFAULT_RAW_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Continuous KYC Autonomous Auditor"
    app_version: str = "0.1.0"
    environment: str = "local"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    database_url: str = f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"

    raw_data_dir: Path = DEFAULT_RAW_DATA_DIR
    processed_data_dir: Path = DEFAULT_PROCESSED_DATA_DIR

    # Entity-resolution confidence weights (Phase 3). Externalized to a file
    # so weights are configuration, never code -- see app/resolution/config.py.
    resolution_weights_path: Path | None = None

    # Risk Factor Registry (Phase 4). Same rationale -- see app/risk/config.py.
    risk_factors_path: Path | None = None

    log_level: str = "INFO"

    # --- External provider configuration (Phase 1: contracts + config only) ---
    # None of these are required to run the app. Every provider built on top of
    # them must check is_configured() and degrade gracefully (NOT_CONFIGURED)
    # when unset -- see app/providers/. No real integration is implemented
    # against these in Phase 1; they exist so Phase 2+ has a place to plug in
    # without touching config plumbing again.
    news_api_key: str | None = None
    news_api_base_url: str | None = None
    sanctions_api_key: str | None = None
    sanctions_api_base_url: str | None = None
    corporate_registry_api_key: str | None = None
    corporate_registry_api_base_url: str | None = None

    # --- LLM provider configuration (Phase 5: the Investigation Agent) ---
    # Same discipline as the data providers above: no key here means the
    # provider reports NOT_CONFIGURED and the investigation is recorded as
    # "could not be generated" -- never faked, never silently skipped.
    #
    # `llm_provider` selects which registered LLMProvider is used
    # (app/providers/llm_registry.py). Each provider owns its own key/model
    # namespace below, because a model id is vendor-specific by nature -- a
    # single shared `llm_model` would silently mean different things depending
    # on `llm_provider`, which is how someone eventually points an Anthropic
    # model id at Groq.
    llm_provider: str = "anthropic"

    # --- Vendor-neutral knobs (every provider honours these) ---
    llm_max_output_tokens: int = 8000
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 2

    # Sent ONLY by providers whose API accepts sampling parameters. Groq does,
    # and reports the real value back. Anthropic does NOT -- current models
    # reject temperature with HTTP 400, so its provider ignores this and
    # records null (ADR-025). Defaults to 0.0: for a grounded compliance
    # investigation the least output variance is the right default, and a
    # re-run should differ because the evidence changed, not because the
    # sampler rolled differently.
    llm_temperature: float | None = 0.0

    # --- Anthropic ---
    # The model is configuration, NOT a constant in the agent: the
    # anti-hardcoding rule that bans pinning client ids applies equally to
    # pinning a model id.
    llm_model: str = "claude-opus-4-8"
    llm_api_key: str | None = None
    llm_base_url: str | None = None

    # --- Groq ---
    groq_api_key: str | None = None
    # Default is one of the two models supporting STRICT structured outputs.
    # Groq's strict mode uses constrained decoding, which is the same
    # generation-time guarantee the Anthropic path relies on -- so the report
    # schema is enforced identically on both vendors.
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str | None = None
    # Strict json_schema is supported on openai/gpt-oss-120b and
    # openai/gpt-oss-20b only. Other schema-capable models (e.g.
    # meta-llama/llama-4-scout-17b-16e-instruct) accept best-effort mode --
    # set this false for those. This is a documented vendor capability
    # boundary, not a preference knob.
    groq_strict_schema: bool = True

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sqlite_file_path(self) -> Path | None:
        """Filesystem path of the SQLite DB file, if the configured URL is a
        file-based SQLite URL (not ':memory:'). Used to ensure the parent
        directory exists before the engine connects."""
        if not self.is_sqlite:
            return None
        # sqlite:///relative/path or sqlite:////absolute/path
        raw = self.database_url.split("sqlite:///", 1)[-1]
        if raw in (":memory:", ""):
            return None
        return Path(raw)


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton. Use this, not Settings() directly, so the
    whole app shares one resolved configuration."""
    return Settings()
