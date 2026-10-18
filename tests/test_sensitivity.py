"""Test de la sensibilité à la durée de validité (exemple calculable à la main)."""
from datetime import date

import duckdb

from fuel.sensitivity import sensitivity


def make_con():
    con = duckdb.connect()
    con.execute("CREATE TABLE station(station_id BIGINT, on_map BOOLEAN)")
    con.execute("INSERT INTO station VALUES (1,true),(2,true),(3,true),(4,true),(5,false)")
    con.execute("CREATE TABLE event(station_id BIGINT, fuel VARCHAR, updated_at TIMESTAMP, price DECIMAL(6,3), is_suspect BOOLEAN)")
    con.execute("""INSERT INTO event VALUES
        (1,'Gazole','2026-10-17 08:00:00',2.00,false),   -- age 0
        (2,'Gazole','2026-10-07 08:00:00',2.20,false),   -- age 10
        (3,'Gazole','2026-08-18 08:00:00',3.00,false),   -- age 60
        (3,'Gazole','2026-10-17 20:00:00',9.99,true),    -- suspect : ignoré
        (4,'Gazole','2026-10-18 08:00:00',5.00,false),   -- après le jour étudié : ignoré
        (5,'Gazole','2026-10-17 08:00:00',7.00,false)    -- hors carte : ignoré
    """)
    return con


def test_more_validity_days_means_more_stations():
    rows = {w: (n, pct, med) for _, w, n, pct, med in sensitivity(make_con(), date(2026, 10, 17), (7, 14, 60))}
    assert rows[7] == (1, 25.0, 2.0)       # seulement la station 1
    assert rows[14] == (2, 50.0, 2.1)      # + station 2 (médiane de 2.00 et 2.20)
    assert rows[60] == (3, 75.0, 2.2)      # + station 3 ; la station 4 n'existe pas encore ce jour-là