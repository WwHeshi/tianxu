"""Environment-backed application settings."""

import os

DEFAULT_DATABASE_URL = "postgresql+asyncpg://tianxu:tianxu_dev_password@localhost:5432/tianxu"


def database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def app_environment() -> str:
    return os.getenv("APP_ENV", "development").strip().lower()


def model_settings_enabled() -> bool:
    return app_environment() in {"development", "local", "test"}
