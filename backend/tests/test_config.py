from app.config import _ENV_FILES, _ROOT_ENV, Settings


def test_default_settings():
    settings = Settings()
    assert settings.PORT == 8000
    assert settings.ENV in ["development", "production", "test"]
    assert "mongodb://" in settings.MONGO_URI
    assert settings.TEMPORAL_NAMESPACE == "default"

def test_custom_settings():
    settings = Settings(ENV="production", PORT=9000, MONGO_DB_NAME="custom_db")
    assert settings.ENV == "production"
    assert settings.PORT == 9000
    assert settings.MONGO_DB_NAME == "custom_db"

def test_root_env_file_resolution():
    """Verify that root .env file path is correctly resolved in _ENV_FILES."""
    assert str(_ROOT_ENV) in _ENV_FILES
    assert _ROOT_ENV.name == ".env"

