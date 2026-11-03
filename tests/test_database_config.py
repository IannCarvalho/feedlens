"""Config do banco: esquema da URL e opções do engine por tipo de banco."""
from app.config import (
    POSTGRES_POOL_RECYCLE_SECONDS,
    database_engine_options,
    normalize_database_url,
)


def test_legacy_postgres_scheme_is_rewritten():
    url = "postgres://user:pw@ep-x.sa-east-1.aws.neon.tech/feedlens?sslmode=require"
    assert normalize_database_url(url) == (
        "postgresql://user:pw@ep-x.sa-east-1.aws.neon.tech/feedlens?sslmode=require")


def test_other_schemes_pass_unchanged():
    for url in ("postgresql://u:p@h/db", "sqlite:///feedlens.db", "sqlite:///:memory:"):
        assert normalize_database_url(url) == url


def test_postgres_gets_pre_ping_and_recycle():
    assert database_engine_options("postgresql://u:p@h/db") == {
        "pool_pre_ping": True,
        "pool_recycle": POSTGRES_POOL_RECYCLE_SECONDS,
    }


def test_sqlite_keeps_default_engine_options():
    assert database_engine_options("sqlite:///:memory:") == {}
