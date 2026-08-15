"""Environment-backed application settings."""

import os

DEFAULT_DATABASE_URL = "postgresql+asyncpg://tianxu:tianxu_dev_password@localhost:5432/tianxu"
DEFAULT_NEO4J_URI = "bolt://localhost:7687"
DEFAULT_NEO4J_USERNAME = "neo4j"
DEFAULT_NEO4J_PASSWORD = "tianxu_neo4j_dev_password"
DEFAULT_NEO4J_DATABASE = "neo4j"


def database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def neo4j_uri() -> str:
    return os.getenv("NEO4J_URI", DEFAULT_NEO4J_URI).strip()


def neo4j_username() -> str:
    return os.getenv("NEO4J_USERNAME", DEFAULT_NEO4J_USERNAME).strip()


def neo4j_password() -> str:
    return os.getenv("NEO4J_PASSWORD", DEFAULT_NEO4J_PASSWORD)


def neo4j_database() -> str:
    return os.getenv("NEO4J_DATABASE", DEFAULT_NEO4J_DATABASE).strip()


def app_environment() -> str:
    return os.getenv("APP_ENV", "development").strip().lower()
