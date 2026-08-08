"""Environment-backed application settings."""

import os

DEFAULT_DATABASE_URL = "postgresql+asyncpg://tianxu:tianxu_dev_password@localhost:5432/tianxu"


def database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def app_environment() -> str:
    return os.getenv("APP_ENV", "development").strip().lower()


def model_settings_enabled() -> bool:
    if app_environment() not in {"development", "local", "test"}:
        return False
    configured = os.getenv("MODEL_SETTINGS_ENABLED")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return True


def encryption_key_version() -> str:
    return os.getenv("APP_ENCRYPTION_KEY_VERSION", "v1").strip() or "v1"
