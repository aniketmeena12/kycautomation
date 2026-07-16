"""Explicit database initialization. Safe to run any number of times --
creates missing tables only, never drops or truncates existing ones.

Usage (from backend/):
    python init_db.py
"""

from app.core.config import get_settings
from app.core.database import init_db

if __name__ == "__main__":
    settings = get_settings()
    init_db()
    print(f"Database initialized at: {settings.database_url}")
