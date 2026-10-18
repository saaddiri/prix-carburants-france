"""Tests de l'analytique : petits exemples dont on connaît la réponse à la main."""
import zipfile
from datetime import date

import duckdb
import pytest

from fuel.analytics import (build_daily_stats, build_price_daily, export_query, load_tables,
                            EVENT_SQL, STATION_SQL)
from fuel.load import load_zip

XML = """<?xml version="1.0" encoding="ISO-8859-1" standalone="yes"?>
<pdv_liste>
<pdv id="1000005" latitude="4620000" longitude="520000" cp="01000" pop="R">
<adresse>1 rue Test, avec virgule</adresse><ville>Bourg</ville>
<prix nom="Gazole" id="1" maj="2026-10-10 08:00:00" valeur="1.500"/>
<prix nom="Gazole" id="1" maj="2026-10-14 09:00:00" valeur="1.600"/>
<prix nom="Gazole" id="1" maj="2026-10-15 07:00:00" valeur="9.500"/>
</pdv>
<pdv id="1000007" latitude="4630000" longitude="530000" cp="75001" pop="A">
<adresse>2 rue Test</adresse><ville>Paris</ville>
<prix nom="Gazole" id="1" maj="2026-10-12 08:00:00" valeur="1.800"/>
</pdv>
<pdv id="1000009" latitude="0" longitude="0" cp="97400" pop="R">
<adresse>hors carte</adresse><ville>Ailleurs</ville>
<prix nom="Gazole" id="1" maj="2026-10-12 08:00:00" valeur="1.100"/>
</pdv>
</pdv_liste>
"""


@pytest.fixture
def duck(conn, tmp_path):
    """PostgreSQL de test -> CSV -> DuckDB, comme dans le vrai pipeline."""
    zip_path = tmp_path / "a.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("data.xml", XML.encode("latin-1"))
    load_zip(conn, zip_path)
    export_query(conn, EVENT_SQL, tmp_path / "e.csv")
    export_query(conn, STATION_SQL, tmp_path / "s.csv")
    con = duckdb.connect()
    load_tables(con, tmp_path / "e.csv", tmp_path / "s.csv")
    return con


def test_postal_code_keeps_leading_zero_and_text_with_comma(duck):
    row = duck.execute("SELECT postal_code, address, on_map FROM station WHERE station_id = 1000005").fetchone()
    assert row == ("01000", "1 rue Test, avec virgule", True)


def test_price_daily_follows_price_forward_and_respects_freshness(duck):
    build_price_daily(duck, fresh_days=2)  # 1re date = 10/10 + 2 jours = 12/10
    rows = duck.execute(
        "SELECT day, station_id, price::DOUBLE, age_days FROM price_daily ORDER BY day, station_id"
    ).fetchall()
    assert rows == [
        (date(2026, 10, 12), 1000005, 1.5, 2),   # prix du 10/10, encore valable (âge 2)
        (date(2026, 10, 12), 1000007, 1.8, 0),
        (date(2026, 10, 13), 1000007, 1.8, 1),   # le prix de 1000005 a 3 jours : trop vieux, il sort
        (date(2026, 10, 14), 1000005, 1.6, 0),   # nouveau prix
        (date(2026, 10, 14), 1000007, 1.8, 2),
    ]


def test_suspect_price_is_ignored_and_off_map_station_excluded(duck):
    build_price_daily(duck, fresh_days=2)
    # le 9,50 € du 15/10 (suspect) n'apparaît pas, et la dernière date reste le 14/10
    assert duck.execute("SELECT max(day) FROM price_daily").fetchone()[0] == date(2026, 10, 14)
    assert duck.execute("SELECT count(*) FROM price_daily WHERE price > 5").fetchone()[0] == 0
    # la station hors carte (1000009) n'est jamais comptée
    assert duck.execute("SELECT count(*) FROM price_daily WHERE station_id = 1000009").fetchone()[0] == 0


def test_daily_stats_median_and_spread(duck):
    build_price_daily(duck, fresh_days=2)
    build_daily_stats(duck)
    rows = duck.execute(
        "SELECT day, n_stations, median_price, min_price, max_price, spread_min_max, "
        "cheapest_station_id, dearest_station_id FROM daily_stats ORDER BY day"
    ).fetchall()
    assert rows == [
        (date(2026, 10, 12), 2, 1.65, 1.5, 1.8, 0.3, 1000005, 1000007),
        (date(2026, 10, 13), 1, 1.8, 1.8, 1.8, 0.0, 1000007, 1000007),
        (date(2026, 10, 14), 2, 1.7, 1.6, 1.8, 0.2, 1000005, 1000007),
    ]


def test_default_start_is_first_day_plus_fresh_days(duck):
    build_price_daily(duck, fresh_days=2)
    assert duck.execute("SELECT min(day) FROM price_daily").fetchone()[0] == date(2026, 10, 12)

def test_addresses_with_quotes_and_commas_survive_postgres_to_duckdb(conn, tmp_path):
    """Régression : sur les vraies données, une adresse écrite "LE VIEUX ST MAURICE" (avec
    guillemets) faisait échouer la lecture du CSV."""
    xml = """<?xml version="1.0" encoding="ISO-8859-1" standalone="yes"?>
<pdv_liste>
<pdv id="87800003" latitude="4570744" longitude="124139" cp="87800" pop="R">
<adresse>"LE VIEUX ST MAURICE"</adresse><ville>ST MAURICE LES BROUSSES</ville>
<prix nom="Gazole" id="1" maj="2026-10-10 08:00:00" valeur="1.500"/></pdv>
<pdv id="87800004" latitude="4570744" longitude="124139" cp="87800" pop="R">
<adresse>5 RUE DE L"EGLISE, ZONE 2</adresse><ville>AUTRE</ville>
<prix nom="Gazole" id="1" maj="2026-10-10 08:00:00" valeur="1.500"/></pdv>
</pdv_liste>
"""
    zip_path = tmp_path / "q.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("data.xml", xml.encode("latin-1"))
    load_zip(conn, zip_path)
    export_query(conn, EVENT_SQL, tmp_path / "e.csv")
    export_query(conn, STATION_SQL, tmp_path / "s.csv")
    con = duckdb.connect()
    load_tables(con, tmp_path / "e.csv", tmp_path / "s.csv")
    rows = dict(con.execute("SELECT station_id, address FROM station").fetchall())
    assert rows == {87800003: '"LE VIEUX ST MAURICE"', 87800004: '5 RUE DE L"EGLISE, ZONE 2'}