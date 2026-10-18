"""Évolution hebdomadaire des prix nationaux (à partir de daily_stats, dans fuel.duckdb).

Règles (à recopier dans le README) :
  - semaine = du lundi au dimanche ;
  - prix de la semaine = moyenne des 7 médianes nationales quotidiennes ;
  - seules les semaines COMPLÈTES (7 jours) sont gardées ;
  - la variation est calculée seulement si la semaine précédente existe (sinon vide).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

DB_PATH = Path("data/analytics/fuel.duckdb")
OUT_CSV = Path("data/analytics/weekly_stats.csv")


def build_weekly(con: duckdb.DuckDBPyConnection) -> int:
    con.execute(
        """CREATE OR REPLACE TABLE weekly_stats AS
           WITH w AS (
               SELECT date_trunc('week', day)::DATE AS week_start, fuel,
                      count(*) AS n_days,
                      round(avg(median_price), 4) AS median_price,
                      round(min(median_price), 4) AS min_daily_median,
                      round(max(median_price), 4) AS max_daily_median,
                      round(avg(n_stations)) AS avg_stations
               FROM daily_stats GROUP BY 1, 2 HAVING count(*) = 7),
           p AS (
               SELECT *,
                      lag(week_start) OVER (PARTITION BY fuel ORDER BY week_start) AS prev_week,
                      lag(median_price) OVER (PARTITION BY fuel ORDER BY week_start) AS prev_price
               FROM w)
           SELECT week_start, fuel, n_days, median_price, min_daily_median, max_daily_median, avg_stations,
                  CASE WHEN prev_week = (week_start - INTERVAL 7 DAY)::DATE
                       THEN round(median_price - prev_price, 4) END AS delta_eur,
                  CASE WHEN prev_week = (week_start - INTERVAL 7 DAY)::DATE
                       THEN round(100 * (median_price / prev_price - 1), 2) END AS delta_pct
           FROM p ORDER BY fuel, week_start"""
    )
    return con.execute("SELECT count(*) FROM weekly_stats").fetchone()[0]


def show_last_weeks(con: duckdb.DuckDBPyConnection, n: int = 8) -> None:
    for (fuel,) in con.execute("SELECT DISTINCT fuel FROM weekly_stats ORDER BY fuel").fetchall():
        print(f"\n{fuel} - {n} dernières semaines complètes")
        rows = con.execute(
            "SELECT week_start, median_price, delta_eur, delta_pct FROM weekly_stats "
            "WHERE fuel = ? ORDER BY week_start DESC LIMIT ?", [fuel, n]).fetchall()
        for week, price, d_eur, d_pct in reversed(rows):
            delta = "      -" if d_eur is None else f"{d_eur:+.3f} EUR ({d_pct:+.2f} %)"
            print(f"  semaine du {week}  {price:.3f} EUR/L  {delta}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Évolution hebdomadaire des prix")
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--out", default=str(OUT_CSV))
    p.add_argument("--weeks", type=int, default=8)
    args = p.parse_args(argv)
    con = duckdb.connect(args.db)
    n = build_weekly(con)
    con.execute(f"COPY weekly_stats TO '{Path(args.out).as_posix()}' (HEADER, DELIMITER ',')")
    first, last = con.execute("SELECT min(week_start), max(week_start) FROM weekly_stats").fetchone()
    print(f"{n} lignes (semaines complètes du {first} au {last}) -> {args.out}")
    show_last_weeks(con, args.weeks)
    return 0


if __name__ == "__main__":
    sys.exit(main())