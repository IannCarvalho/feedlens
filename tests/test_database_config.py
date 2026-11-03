"""Config do banco: esquema da URL e opções do engine por tipo de banco."""
from app.config import (
    POSTGRES_POOL_RECYCLE_SECONDS,
    database_engine_options,
    migration_database_url,
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


POOLED = "postgresql://u:p@ep-x-pooler.sa-east-1.aws.neon.tech/neondb?sslmode=require"
DIRECT = "postgresql://u:p@ep-x.sa-east-1.aws.neon.tech/neondb?sslmode=require"


def test_migrations_use_the_direct_url_on_postgres():
    assert migration_database_url(POOLED, DIRECT) == DIRECT


def test_migrations_normalize_the_direct_url_scheme():
    legacy = DIRECT.replace("postgresql://", "postgres://", 1)
    assert migration_database_url(POOLED, legacy) == DIRECT


def test_migrations_keep_the_app_url_without_a_direct_one():
    assert migration_database_url(POOLED, None) == POOLED
    assert migration_database_url(POOLED, "") == POOLED


def test_a_stray_direct_url_never_redirects_a_sqlite_app():
    assert migration_database_url("sqlite:///:memory:", DIRECT) == "sqlite:///:memory:"
