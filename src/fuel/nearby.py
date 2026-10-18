"""Stations les moins chères autour d'un point ou d'une commune (prix du dernier jour).

Lit la base DuckDB créée par `python -m fuel.analytics`.

Règle "prix incohérent" (drapeau is_outlier, masqué par défaut, jamais supprimé) :
  - prix < 70 % de la médiane nationale du jour (tous les carburants) ;
  - prix > 150 % de la médiane (carburants classiques seulement : le E85 et le GPLc
    ont des écarts normaux plus larges, constatés dans les données).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

CLASSIC_FUELS = ("Gazole", "SP95", "SP98", "E10")
LOW_RATIO = 0.7
HIGH_RATIO = 1.5
EARTH_RADIUS_KM = 6371.0
DB_PATH = Path("data/analytics/fuel.duckdb")


def build_latest(con: duckdb.DuckDBPyConnection) -> int:
    """Table `latest` : le dernier prix frais de chaque station et carburant (dernier jour de price_daily)."""
    con.execute(
        f"""CREATE OR REPLACE TABLE latest AS
            SELECT p.station_id, p.fuel, p.price::DOUBLE AS price, p.price_updated_at, p.age_days,
                   s.address, s.city, s.postal_code, s.latitude, s.longitude,
                   p.price::DOUBLE / d.median_price AS ratio,
                   (p.price::DOUBLE / d.median_price < {LOW_RATIO}
                    OR (p.fuel IN {CLASSIC_FUELS} AND p.price::DOUBLE / d.median_price > {HIGH_RATIO})
                   ) AS is_outlier
            FROM price_daily p
            JOIN station s USING (station_id)
            JOIN daily_stats d USING (day, fuel)
            WHERE p.day = (SELECT max(day) FROM price_daily)"""
    )
    return con.execute("SELECT count(*) FROM latest").fetchone()[0]


def cheapest_near(
    con: duckdb.DuckDBPyConnection,
    lat: float,
    lon: float,
    fuel: str,
    radius_km: float = 10.0,
    limit: int = 10,
    include_outliers: bool = False,
) -> tuple[list[tuple], int]:
    """(stations triées par prix, nombre de stations masquées car prix incohérent)."""
    rows = con.execute(
        f"""WITH d AS (
                SELECT *, 2 * {EARTH_RADIUS_KM} * asin(sqrt(
                    pow(sin(radians(latitude - ?) / 2), 2)
                    + cos(radians(?)) * cos(radians(latitude)) * pow(sin(radians(longitude - ?) / 2), 2)
                )) AS distance_km
                FROM latest WHERE fuel = ?)
            SELECT station_id, city, address, price, round(distance_km, 2), age_days, is_outlier
            FROM d WHERE distance_km <= ?
            ORDER BY price, distance_km""",
        [lat, lat, lon, fuel, radius_km],
    ).fetchall()
    masked = sum(1 for r in rows if r[6])
    shown = rows if include_outliers else [r for r in rows if not r[6]]
    return shown[:limit], masked


def find_commune(con: duckdb.DuckDBPyConnection, name: str, postal_prefix: str | None = None) -> list[tuple]:
    """Communes dont le nom correspond (sans accents ni majuscules) : (ville, dept, nb stations, lat, lon).

    Le centre est la moyenne des coordonnées des stations de la commune (pas de géocodage externe).
    Si un nom correspond exactement, on ne garde que les correspondances exactes.
    """
    prefix_sql = "AND postal_code LIKE ? || '%'" if postal_prefix else ""
    params = [name] + ([postal_prefix] if postal_prefix else [])
    rows = con.execute(
        # On regroupe sur le nom normalisé : le fichier officiel écrit parfois "LYON" et "Lyon".
        f"""SELECT min(city) AS city, substr(postal_code, 1, 2) AS dept, count(DISTINCT station_id) AS n,
                   avg(latitude), avg(longitude),
                   (key = strip_accents(upper(?))) AS is_exact
            FROM (SELECT *, strip_accents(upper(city)) AS key FROM station WHERE on_map)
            WHERE key LIKE '%' || strip_accents(upper(?)) || '%' {prefix_sql}
            GROUP BY key, dept ORDER BY n DESC""",
        [name] + params,
    ).fetchall()
    if any(r[5] for r in rows):
        rows = [r for r in rows if r[5]]
    return [r[:5] for r in rows]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Stations les moins chères à proximité")
    p.add_argument("--commune", help="nom de commune (sans accents si tu veux)")
    p.add_argument("--cp", help="début de code postal pour choisir parmi plusieurs communes (ex. 69)")
    p.add_argument("--lat", type=float)
    p.add_argument("--lon", type=float)
    p.add_argument("--fuel", default="Gazole", help="Gazole, SP95, SP98, E10, E85 ou GPLc")
    p.add_argument("--radius", type=float, default=10.0, help="rayon en km")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--include-outliers", action="store_true", help="afficher aussi les prix incohérents")
    p.add_argument("--db", default=str(DB_PATH))
    args = p.parse_args(argv)

    con = duckdb.connect(args.db)
    build_latest(con)  # petite table (dernier jour) recalculée à chaque lancement : quelques secondes
    if args.commune:
        found = find_commune(con, args.commune, args.cp)
        if not found:
            print(f"Aucune commune ne correspond à « {args.commune} ».")
            return 1
        if len(found) > 1:
            print("Plusieurs communes correspondent, précise avec --cp :")
            for city, dept, n, *_ in found[:10]:
                print(f"  {city} (département {dept}) - {n} stations")
            return 1
        city, dept, n, lat, lon = found[0]
        print(f"Centre : {city} ({dept}), moyenne des {n} stations -> {lat:.4f}, {lon:.4f}")
    elif args.lat is not None and args.lon is not None:
        lat, lon = args.lat, args.lon
    else:
        print("Donne --commune NOM, ou --lat et --lon.")
        return 1

    rows, masked = cheapest_near(con, lat, lon, args.fuel, args.radius, args.limit, args.include_outliers)
    print(f"\n{args.fuel} - {len(rows)} station(s) les moins chères dans {args.radius:g} km :")
    for i, (sid, city, addr, price, dist, age, outlier) in enumerate(rows, 1):
        flag = "  [prix incohérent]" if outlier else ""
        print(f"  {i:>2}. {price:.3f} EUR/L  {dist:>5.1f} km  {city} - {addr}  (prix de {age} j){flag}")
    if masked and not args.include_outliers:
        print(f"\n{masked} station(s) masquée(s) : prix incohérent avec la médiane du jour "
              f"(--include-outliers pour les voir).")
    return 0


if __name__ == "__main__":
    sys.exit(main())