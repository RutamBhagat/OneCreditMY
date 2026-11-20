"""Database configuration"""

import argparse
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL, make_url


def database_url() -> URL:
    port = os.environ.get("POSTGRES_PORT", "5432")
    url = os.environ.get("DATABASE_URL", f"postgresql://postgres:postgres@localhost:{port}/companies")
    return make_url(url).set(drivername="postgresql+psycopg")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init"])
    parser.parse_args()

    config = Config(Path(__file__).resolve().parents[1] / "alembic.ini")
    command.upgrade(config, "head")


if __name__ == "__main__":
    main()
