"""Tests d'intégration : ils utilisent ta vraie base PostgreSQL, mais dans un schéma
temporaire (test_xxxx) créé puis supprimé. Tes vraies tables ne sont jamais touchées."""
import uuid
import zipfile
from datetime import date
from pathlib import Path

import psycopg
import pytest

from fuel import load
from fuel.config import connect
from fuel.load import load_zip

SCHEMA_SQL = (Path(__file__).parent.parent / "sql" / "001_schema.sql").read_text(encoding="utf-8")

XML_1 = """<?xml version="1.0" encoding="ISO-8859-1" standalone="yes"?>
<pdv_liste>
<pdv id="1000005" latitude="4620000" longitude="520000" cp="01000" pop="R">
<adresse>1 rue Test</adresse><ville>Bourg</ville>
<prix nom="Gazole" id="1" maj="2026-10-16 08:00:00" valeur="2.369"/>
<prix nom="E85" id="3" maj="2026-10-16 08:00:00" valeur="9.919"/>
<prix/>
</pdv>
<pdv id="1000006" latitude="0" longitude="0" cp="01000" pop="A">
<adresse>2 rue Test</adresse><ville>Bourg</ville>
<prix nom="E10" id="5" maj="2026-10-16 09:00:00" valeur="2.182"/>
</pdv>
</pdv_liste>
"""

# Le lendemain : mêmes prix + UN nouveau prix pour la station 1000005
XML_2 = XML_1.replace(
    '<prix/>',
    '<prix nom="Gazole" id="1" maj="2026-10-16 18:30:00" valeur="2.372"/>',
)


def make_zip(tmp_path, name, xml):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("data.xml", xml.encode("latin-1"))
    return path


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


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_first_load(conn, tmp_path):
    result = load_zip(conn, make_zip(tmp_path, "j1.zip", XML_1))
    assert (result.stations, result.prices_read, result.prices_inserted, result.rejected) == (2, 3, 3, 1)
    assert count(conn, "station") == 2
    assert count(conn, "price_event") == 3
    assert conn.execute("SELECT reason FROM rejected_row").fetchone()[0] == "prix_vide"
    # le prix absurde est gardé mais marqué suspect
    assert conn.execute("SELECT is_suspect FROM price_event WHERE fuel='E85'").fetchone()[0] is True
    # coordonnées à zéro : station gardée mais pas sur la carte
    assert conn.execute("SELECT on_map FROM station WHERE station_id=1000006").fetchone()[0] is False
    assert conn.execute("SELECT first_seen FROM station WHERE station_id=1000005").fetchone()[0] == date(2026, 10, 16)


def test_same_file_twice_is_skipped(conn, tmp_path):
    zip_path = make_zip(tmp_path, "j1.zip", XML_1)
    load_zip(conn, zip_path)
    second = load_zip(conn, zip_path)
    assert second.already_loaded is True
    assert count(conn, "price_event") == 3
    assert count(conn, "source_file") == 1


def test_new_file_adds_only_new_prices(conn, tmp_path):
    load_zip(conn, make_zip(tmp_path, "j1.zip", XML_1))
    result = load_zip(conn, make_zip(tmp_path, "j2.zip", XML_2))
    assert result.prices_read == 4       # 4 prix valides dans le fichier
    assert result.prices_inserted == 1   # mais 3 déjà connus : un seul nouveau
    assert count(conn, "price_event") == 4
    assert count(conn, "source_file") == 2


def test_failure_rolls_back_everything(conn, tmp_path, monkeypatch):
    calls = {"n": 0}
    real = load.clean_price

    def flaky(raw, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("panne simulée")
        return real(raw, *a, **kw)

    monkeypatch.setattr(load, "clean_price", flaky)
    with pytest.raises(RuntimeError):
        load_zip(conn, make_zip(tmp_path, "j1.zip", XML_1))
    assert count(conn, "source_file") == 0   # rien n'est resté
    assert count(conn, "station") == 0
    assert count(conn, "price_event") == 0

def test_older_file_does_not_overwrite_newer_station_info(conn, tmp_path):
    recent = XML_1.replace("1 rue Test", "Adresse RECENTE")
    old = (XML_1.replace("1 rue Test", "Adresse ANCIENNE")
                .replace("2026-10-16", "2024-05-01"))
    load_zip(conn, make_zip(tmp_path, "recent.zip", recent))
    result = load_zip(conn, make_zip(tmp_path, "old.zip", old))
    assert result.prices_inserted == 3  # les vieux prix sont bien ajoutés à l'historique
    address = conn.execute("SELECT address FROM station WHERE station_id=1000005").fetchone()[0]
    assert address == "Adresse RECENTE"
    # et l'historique de la station commence bien en 2024
    assert conn.execute("SELECT first_seen FROM station WHERE station_id=1000005").fetchone()[0] == date(2024, 5, 1)