"""Exercise Alembic in a disposable database on the isolated Compose server."""

import os
import subprocess
import sys
from contextlib import ExitStack
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

from companies.db import database_url


def test_migration_upgrade_downgrade_and_reupgrade(monkeypatch):
    test_database_url = os.environ.get("TEST_DATABASE_URL")
    if not test_database_url:
        pytest.skip("Set TEST_DATABASE_URL for an isolated Compose Postgres server")

    monkeypatch.setenv("DATABASE_URL", test_database_url)
    db_name = f"onecredit_migration_{uuid4().hex}"
    temporary_url = database_url().set(database=db_name)
    env = {**os.environ, "DATABASE_URL": temporary_url.render_as_string(hide_password=False)}

    def alembic(*args):
        subprocess.run([sys.executable, "-m", "alembic", *args], env=env, check=True, timeout=15)

    with ExitStack() as stack:
        admin = create_engine(database_url(), isolation_level="AUTOCOMMIT", poolclass=NullPool)
        connection = stack.enter_context(admin.connect())
        connection.exec_driver_sql(f"CREATE DATABASE {db_name}")
        stack.callback(connection.exec_driver_sql, f"DROP DATABASE {db_name}")
        temporary_db = create_engine(temporary_url, poolclass=NullPool)

        alembic("upgrade", "0001_companies")
        with temporary_db.begin() as db:
            db.execute(
                text("INSERT INTO companies (registration_number, updated_at, active) VALUES ('LEGACY', now(), false)")
            )

        alembic("upgrade", "head")
        assert inspect(temporary_db).has_table("event_receipts")
        alembic("check")

        alembic("downgrade", "-1")
        assert not inspect(temporary_db).has_table("event_receipts")
        with temporary_db.connect() as db:
            assert db.scalar(text("SELECT registration_number FROM companies")) == "LEGACY"

        alembic("downgrade", "-1")
        assert not inspect(temporary_db).has_table("companies")

        alembic("upgrade", "head")
        assert inspect(temporary_db).has_table("companies")
        assert inspect(temporary_db).has_table("event_receipts")
