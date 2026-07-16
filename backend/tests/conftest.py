"""
Shared pytest fixtures.

Critical: DATABASE_URL is redirected to a temporary file BEFORE any `app.*`
module is imported, so the real backend/data/continuous_kyc.db is never
touched by the test suite. Settings' lru_cache is cleared immediately after
so this test-only value is what every module actually resolves.
"""

from __future__ import annotations

import os
import tempfile

_TEST_DB_FD, _TEST_DB_PATH = tempfile.mkstemp(suffix=".db", prefix="continuous_kyc_test_")
os.close(_TEST_DB_FD)
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"

# --- No test may ever reach a live LLM API. ---
#
# Settings reads backend/.env, so the moment a developer configures a REAL key
# there, every test that builds an orchestrator without injecting an agent
# silently starts making billed network calls against a live model. That is
# exactly what happened the first time a real GROQ_API_KEY landed in .env: the
# suite went from ~30s to 62 MINUTES, and two tests asserting the
# "no key configured" path failed -- because a key was, in fact, configured.
#
# Blanking the credentials here (env vars outrank the .env file in
# pydantic-settings) makes the suite hermetic and its result independent of
# whoever's machine it runs on. Tests that need a model inject a provider via
# tests/fake_llm.py; tests that need a REAL call are not tests -- they are the
# live verification in docs/phase-5-investigation-agent.md SS10.
#
# Empty string, not deletion: an empty value still overrides the .env file,
# whereas deleting the key from os.environ would let the file's value through.
for _secret in ("LLM_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY"):
    os.environ[_secret] = ""

# Pin the provider too, so a developer's LLM_PROVIDER choice cannot change what
# the default-construction tests assert.
os.environ["LLM_PROVIDER"] = "anthropic"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.core.database import Base, SessionLocal, engine, get_db, init_db  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    init_db()
    yield
    engine.dispose()
    try:
        os.remove(_TEST_DB_PATH)
    except OSError:
        pass


@pytest.fixture()
def db_session():
    """A fresh session per test. Tables are shared across the test session
    (created once), but every table is cleared after each test so tests
    don't leak state into each other."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        with engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(table.delete())


@pytest.fixture()
def client(db_session):
    def _override_get_db():
        yield db_session

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    with TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()
