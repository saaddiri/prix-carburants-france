"""Fixtures partagées : une connexion PostgreSQL dans un schéma temporaire jetable."""
import uuid
from pathlib import Path

import psycopg
import pytest

from fuel.config import connect

SCHEMA_SQL = (Path(__file__).parent.parent / "sql" / "001_schema.sql").read_text(encoding="utf-8")


@pytest.fixture
def conn():
    try:
        connection = connect()
    except (psycopg.OperationalError, KeyError):
        pytest.skip("PostgreSQL injoignable (docker compose up -d ? fichier .env ?)")
    schema = f"test_{uuid.uuid4().hex[:8]}"
    connection.execute(f"CREATE SCHEMA {schema}")
    connection.execute(f"SET search_path TO {schema}")
    connection.execute(SCHEMA_SQL)
    yield connection
    connection.execute(f"DROP SCHEMA {schema} CASCADE")
    connection.close()