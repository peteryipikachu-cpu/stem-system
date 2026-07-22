from app.config import Settings
from app.db_init import postgres_url, quote_identifier


def test_database_url_is_normalized_to_asyncpg() -> None:
    settings = Settings(database_url="postgresql://postgres@localhost:5432/stem")

    assert settings.database_url == "postgresql+asyncpg://postgres@localhost:5432/stem"


def test_postgres_url_uses_maintenance_database() -> None:
    maintenance_url, database_name = postgres_url("postgresql+asyncpg://postgres@localhost:5432/stem")

    assert maintenance_url == "postgresql+asyncpg://postgres@localhost:5432/postgres"
    assert database_name == "stem"


def test_database_identifier_is_quoted() -> None:
    assert quote_identifier('stem"test') == '"stem""test"'
