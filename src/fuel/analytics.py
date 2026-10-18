"""Analytique : PostgreSQL (source de vérité) -> DuckDB + Parquet (calculs rapides).

Tout ce qui est produit ici est JETABLE : on le reconstruit à tout moment depuis PostgreSQL.

Règles de calcul (à recopier dans le README) :
  - les prix marqués "suspect" sont ignorés : on garde alors le dernier prix valide ;
  - seules les stations "sur la carte" comptent (France métropolitaine + Corse) ;
  - un prix reste valable tant qu'il a au plus FRESH_DAYS jours (sinon la station sort du calcul) ;
  - la 1re date analysée = 1re date de données + FRESH_DAYS : avant, l'historique commence
    à peine et trop de stations manqueraient (période de chauffe).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import duckdb
import psycopg

log = logging.getLogger("fuel.analytics")

FRESH_DAYS = 30
OUT_DIR = Path("data/analytics")

EVENT_SQL = ("SELECT station_id, fuel, updated_at, price_eur_l AS price, is_suspect "
             "FROM price_event")
STATION_SQL = ("SELECT station_id, address, city, postal_code, latitude, longitude, on_map "
               "FROM station")


def export_query(conn: psycopg.Connection, sql: str, path: Path) -> None:
    """Écrit le résultat d'une requête PostgreSQL dans un CSV (COPY = très rapide)."""
    with open(path, "wb") as f, conn.cursor().copy(
        f"COPY ({sql}) TO STDOUT WITH (FORMAT csv, HEADER true)"
    ) as copy:
        for chunk in copy:
            f.write(chunk)


# Format CSV écrit par PostgreSQL : séparateur virgule, texte entre guillemets, guillemet doublé
# pour écrire un guillemet. On le FIXE au lieu de laisser DuckDB le deviner (la détection
# automatique peut se tromper, par ex. sur une adresse comme "LE VIEUX ST MAURICE").
CSV_DIALECT = "delim = ',', quote = '\"', escape = '\"'"


def load_tables(con: duckdb.DuckDBPyConnection, events_csv: Path, stations_csv: Path) -> None:
    # Types écrits à la main : sinon DuckDB lirait le code postal "01000" comme le nombre 1000.
    con.execute(
        f"""CREATE OR REPLACE TABLE event AS SELECT * FROM read_csv(?, header = true, {CSV_DIALECT}, columns = {{
               'station_id': 'BIGINT', 'fuel': 'VARCHAR', 'updated_at': 'TIMESTAMP',
               'price': 'DECIMAL(6,3)', 'is_suspect': 'BOOLEAN'}})""",
        [str(events_csv)],
    )
    con.execute(
        f"""CREATE OR REPLACE TABLE station AS SELECT * FROM read_csv(?, header = true, {CSV_DIALECT}, columns = {{
               'station_id': 'BIGINT', 'address': 'VARCHAR', 'city': 'VARCHAR',
               'postal_code': 'VARCHAR', 'latitude': 'DOUBLE', 'longitude': 'DOUBLE',
               'on_map': 'BOOLEAN'}})""",
        [str(stations_csv)],
    )


def build_price_daily(
    con: duckdb.DuckDBPyConnection,
    fresh_days: int = FRESH_DAYS,
    start: date | None = None,
    end: date | None = None,
) -> int:
    """Table price_daily : pour chaque jour, station et carburant, le prix en vigueur ce jour-là.

    Principe : on "recopie" chaque prix vers l'avant jusqu'au prochain changement
    (ASOF JOIN = pour chaque jour, prendre le dernier événement avant la fin du jour).
    """
    con.execute(
        """CREATE OR REPLACE TEMP TABLE ev AS
           SELECT e.station_id, e.fuel, e.updated_at, e.price
           FROM event e JOIN station s USING (station_id)
           WHERE NOT e.is_suspect AND s.on_map"""
    )
    first_day, last_day = con.execute("SELECT min(updated_at)::DATE, max(updated_at)::DATE FROM ev").fetchone()
    if first_day is None:
        log.warning("aucun prix exploitable : price_daily vide")
        con.execute("CREATE OR REPLACE TABLE price_daily (day DATE, station_id BIGINT, fuel VARCHAR, "
                    "price DECIMAL(6,3), price_updated_at TIMESTAMP, age_days INTEGER)")
        return 0
    start = start or (first_day + timedelta(days=fresh_days))
    end = end or last_day
    con.execute(
        """CREATE OR REPLACE TABLE price_daily AS
           WITH pairs AS (SELECT DISTINCT station_id, fuel FROM ev),
                days  AS (SELECT unnest(generate_series(CAST(? AS DATE), CAST(? AS DATE), INTERVAL 1 DAY))::DATE AS day),
                grid  AS (SELECT p.station_id, p.fuel, d.day,
                                 CAST(d.day AS TIMESTAMP) + INTERVAL 1 DAY AS cutoff
                          FROM pairs p CROSS JOIN days d)
           SELECT g.day, g.station_id, g.fuel, ev.price,
                  ev.updated_at AS price_updated_at,
                  date_diff('day', CAST(ev.updated_at AS DATE), g.day) AS age_days
           FROM grid g
           ASOF JOIN ev ON g.station_id = ev.station_id AND g.fuel = ev.fuel
                       AND g.cutoff > ev.updated_at
           WHERE date_diff('day', CAST(ev.updated_at AS DATE), g.day) <= ?""",
        [start, end, fresh_days],
    )
    return con.execute("SELECT count(*) FROM price_daily").fetchone()[0]


def build_daily_stats(con: duckdb.DuckDBPyConnection) -> None:
    """Statistiques nationales par jour et par carburant (médiane, écart min-max...)."""
    con.execute(
        """CREATE OR REPLACE TABLE daily_stats AS
           SELECT day, fuel,
                  count(*)                                       AS n_stations,
                  round(median(price::DOUBLE), 4)                AS median_price,
                  round(quantile_cont(price::DOUBLE, 0.01), 4)   AS p01_price,
                  round(quantile_cont(price::DOUBLE, 0.99), 4)   AS p99_price,
                  min(price)::DOUBLE                             AS min_price,
                  max(price)::DOUBLE                             AS max_price,
                  arg_min(station_id, price)                     AS cheapest_station_id,
                  arg_max(station_id, price)                     AS dearest_station_id,
                  round(max(price)::DOUBLE - min(price)::DOUBLE, 4)                AS spread_min_max,
                  round(quantile_cont(price::DOUBLE, 0.99) - quantile_cont(price::DOUBLE, 0.01), 4) AS spread_p01_p99
           FROM price_daily GROUP BY day, fuel ORDER BY day, fuel"""
    )


def export_outputs(con: duckdb.DuckDBPyConnection, out_dir: Path) -> None:
    for table in ("price_daily", "daily_stats"):
        con.execute(f"COPY {table} TO '{(out_dir / (table + '.parquet')).as_posix()}' (FORMAT parquet)")
    con.execute(f"COPY daily_stats TO '{(out_dir / 'daily_stats.csv').as_posix()}' (HEADER, DELIMITER ',')")


def run(conn: psycopg.Connection, out_dir: Path = OUT_DIR, fresh_days: int = FRESH_DAYS,
        memory_limit: str = "6GB") -> duckdb.DuckDBPyConnection:
    out_dir.mkdir(parents=True, exist_ok=True)
    events_csv, stations_csv = out_dir / "_event.csv", out_dir / "_station.csv"
    log.info("export depuis PostgreSQL...")
    export_query(conn, EVENT_SQL, events_csv)
    export_query(conn, STATION_SQL, stations_csv)

    db_path = out_dir / "fuel.duckdb"
    db_path.unlink(missing_ok=True)  # base dérivée : on repart de zéro à chaque fois
    con = duckdb.connect(str(db_path))
    # Plafond de mémoire : au-delà, DuckDB écrit sur le disque au lieu de saturer la RAM
    # (PostgreSQL/Docker en a aussi besoin).
    con.execute(f"SET memory_limit = '{memory_limit}'")
    con.execute(f"SET temp_directory = '{(out_dir / '_tmp').as_posix()}'")
    load_tables(con, events_csv, stations_csv)
    log.info("calcul de price_daily...")
    n = build_price_daily(con, fresh_days)
    log.info("price_daily : %d lignes", n)
    build_daily_stats(con)
    export_outputs(con, out_dir)
    events_csv.unlink()  # copies temporaires (les vraies données sont dans PostgreSQL)
    stations_csv.unlink()
    return con


def summary(con: duckdb.DuckDBPyConnection) -> None:
    first, last, days = con.execute("SELECT min(day), max(day), count(DISTINCT day) FROM price_daily").fetchone()
    print(f"jours analysés : {first} -> {last} ({days} jours)")
    print("\nCouverture : nombre de stations par carburant (1er jour / jour du milieu / dernier jour)")
    rows = con.execute(
        """WITH d AS (SELECT day, row_number() OVER (ORDER BY day) AS rn, count(*) OVER () AS total
                      FROM (SELECT DISTINCT day FROM daily_stats)),
                pick AS (SELECT day FROM d WHERE rn IN (1, (total + 1) // 2, total))
           SELECT s.day, s.fuel, s.n_stations, s.median_price FROM daily_stats s
           JOIN pick USING (day) ORDER BY s.day, s.fuel"""
    ).fetchall()
    for day, fuel, n, med in rows:
        print(f"  {day}  {fuel:<7} stations={n:<6} médiane={med}")
    print("\nDernier jour : écart entre la station la moins chère et la plus chère")
    for fuel, mn, mx, p1, p99 in con.execute(
        "SELECT fuel, min_price, max_price, p01_price, p99_price FROM daily_stats "
        "WHERE day = (SELECT max(day) FROM daily_stats) ORDER BY fuel"
    ).fetchall():
        print(f"  {fuel:<7} min={mn}  max={mx}  (p1={p1}  p99={p99})")


if __name__ == "__main__":
    import sys
    import time

    from fuel.config import connect

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    t0 = time.time()
    with connect() as pg:
        duck = run(pg)
    summary(duck)
    print(f"\ndurée : {time.time() - t0:.1f} s")