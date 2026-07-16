"""Application imports successfully."""


def test_app_imports():
    from app.main import app

    assert app is not None
    assert app.title


def test_settings_load_without_secrets():
    from app.core.config import get_settings

    settings = get_settings()
    assert settings.database_url
    assert settings.raw_data_dir is not None
    # No API keys are set by default -- nothing required to run locally.
    assert settings.news_api_key is None
    assert settings.sanctions_api_key is None
    assert settings.corporate_registry_api_key is None
