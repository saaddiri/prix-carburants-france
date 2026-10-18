"""Sensibilité à la durée de validité d'un prix (7, 14, 30, 60, 90 jours).

Question : si on accepte des prix plus anciens, combien de stations de plus compte-t-on,
et la médiane nationale change-t-elle ? Lecture seule : ne modifie rien dans fuel.duckdb.
Prérequis : python -m fuel.analytics (tables event et station).
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import duckdb

DB_PATH = Path("data/analytics/fuel.duckdb")
WINDOWS = (7, 14, 30, 60, 90)


def sensitivity(con: duckdb.DuckDBPyConnection, day: date, windows=WINDOWS) -> list[tuple]:
    """(carburant, jours de validité, stations avec prix, % des stations de la carte, médiane)."""
    n_map = con.execute("SELECT count(*) FROM station WHERE on_map").fetchone()[0]
    con.execute(
        """CREATE OR REPLACE TEMP TABLE last_ev AS
           SELECT station_id, fuel, price, updated_at,
                  date_diff('day', CAST(updated_at AS DATE), ?) AS age_days
           FROM (SELECT e.*, row_number() OVER (PARTITION BY e.station_id, e.fuel
                                                ORDER BY e.updated_at DESC) AS rn
                 FROM event e JOIN station s USING (station_id)
                 WHERE NOT e.is_suspect AND s.on_map
                   AND e.updated_at < CAST(? AS TIMESTAMP) + INTERVAL 1 DAY)
           WHERE rn = 1""",
        [day, day],
    )
    rows = []
    for fuel, in con.execute("SELECT DISTINCT fuel FROM last_ev ORDER BY fuel").fetchall():
        for w in windows:
            n, med = con.execute(
                "SELECT count(*), median(price::DOUBLE) FROM last_ev WHERE fuel = ? AND age_days <= ?",
                [fuel, w]).fetchone()
            rows.append((fuel, w, n, round(100 * n / n_map, 1), None if med is None else round(med, 4)))
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sensibilité à la durée de validité des prix")
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--day", default=None, help="AAAA-MM-JJ (défaut : dernier jour des données)")
    args = p.parse_args(argv)
    con = duckdb.connect(args.db, read_only=True)
    day = date.fromisoformat(args.day) if args.day else con.execute(
        "SELECT max(updated_at)::DATE FROM event").fetchone()[0]
    n_map = con.execute("SELECT count(*) FROM station WHERE on_map").fetchone()[0]
    print(f"Jour étudié : {day} | stations sur la carte : {n_map}\n")
    print(f"{'carburant':<9}{'validité':>9}{'stations':>10}{'% carte':>9}{'médiane':>10}")
    last = None
    for fuel, w, n, pct, med in sensitivity(con, day):
        if last and last != fuel:
            print()
        last = fuel
        print(f"{fuel:<9}{w:>7} j{n:>10}{pct:>8.1f}%{'' if med is None else f'{med:>10.3f}'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())