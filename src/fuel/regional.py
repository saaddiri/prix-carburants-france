"""Statistiques régionales (médiane par région, jour et carburant).

Seules les stations dont le code postal donne UNE région sûre (geo_status = 'ok') comptent.
Prérequis : python -m fuel.analytics, puis python -m fuel.geo.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

DB_PATH = Path("data/analytics/fuel.duckdb")
OUT_CSV = Path("data/analytics/region_daily_stats.csv")


def build_region_daily(con: duckdb.DuckDBPyConnection) -> int:
    con.execute(
        """CREATE OR REPLACE TABLE region_daily_stats AS
           SELECT p.day, p.fuel, g.region_code, g.region_name,
                  count(*) AS n_stations,
                  round(median(p.price::DOUBLE), 4) AS median_price
           FROM price_daily p JOIN station_geo g USING (station_id)
           WHERE g.geo_status = 'ok'
           GROUP BY ALL ORDER BY day, fuel, region_name"""
    )
    return con.execute("SELECT count(*) FROM region_daily_stats").fetchone()[0]


def last_day_table(con: duckdb.DuckDBPyConnection, fuel: str) -> list[tuple]:
    """(région, nb stations, médiane, écart avec la médiane nationale en EUR et en %) au dernier jour."""
    return con.execute(
        """SELECT r.region_name, r.n_stations, r.median_price,
                  round(r.median_price - d.median_price, 4) AS vs_national_eur,
                  round(100 * (r.median_price / d.median_price - 1), 2) AS vs_national_pct,
                  r.day
           FROM region_daily_stats r JOIN daily_stats d USING (day, fuel)
           WHERE r.fuel = ? AND r.day = (SELECT max(day) FROM region_daily_stats)
           ORDER BY r.median_price""",
        [fuel],
    ).fetchall()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Médianes par région")
    p.add_argument("--fuel", default="Gazole")
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--out", default=str(OUT_CSV))
    args = p.parse_args(argv)
    con = duckdb.connect(args.db)
    n = build_region_daily(con)
    con.execute(f"COPY region_daily_stats TO '{Path(args.out).as_posix()}' (HEADER, DELIMITER ',')")
    print(f"{n} lignes -> {args.out}")
    rows = last_day_table(con, args.fuel)
    if not rows:
        print("Aucune donnée régionale pour ce carburant.")
        return 1
    print(f"\n{args.fuel} - médiane par région au {rows[0][5]} (écart avec la médiane nationale)")
    for region, n_st, med, d_eur, d_pct, _ in rows:
        print(f"  {region:<30} {med:.3f} EUR/L  {d_eur:+.3f} EUR ({d_pct:+.2f} %)  {n_st:>5} stations")
    return 0


if __name__ == "__main__":
    sys.exit(main())