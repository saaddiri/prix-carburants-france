"""Chargement dans PostgreSQL : ZIP -> station, price_event, rejected_row.

Tout ou rien : un fichier est chargé dans UNE transaction. Si ça plante, rien n'est gardé
et on peut simplement relancer. Un fichier déjà chargé (même sha256) est ignoré.
Charger du plus ancien au plus récent : les infos de station les plus récentes gagnent.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import psycopg

from fuel.clean import Reject, clean_price, clean_station
from fuel.parse import RawStation, parse_zip


@dataclass(frozen=True)
class LoadResult:
    already_loaded: bool
    stations: int = 0
    prices_read: int = 0      # prix valides lus dans le fichier
    prices_inserted: int = 0  # prix réellement nouveaux en base
    rejected: int = 0


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def load_zip(conn: psycopg.Connection, zip_path: str | Path, url: str = "") -> LoadResult:
    zip_path = Path(zip_path)
    digest = sha256_of(zip_path)

    with conn.transaction():  # tout ou rien
        row = conn.execute(
            """INSERT INTO source_file (file_name, url, sha256, size_bytes)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (sha256) DO NOTHING
               RETURNING source_file_id""",
            (zip_path.name, url, digest, zip_path.stat().st_size),
        ).fetchone()
        if row is None:  # même fichier déjà chargé
            return LoadResult(already_loaded=True)
        file_id = row[0]

        # Les prix passent d'abord dans une table temporaire (COPY = très rapide).
        conn.execute(
            """CREATE TEMP TABLE tmp_price (
                   station_id BIGINT, fuel TEXT, updated_at TIMESTAMP,
                   price_eur_l NUMERIC(6,3), is_suspect BOOLEAN) ON COMMIT DROP"""
        )
        stations: dict[int, tuple] = {}
        rejects: list[Reject] = []
        n_prices = 0
        with conn.cursor() as cur, cur.copy("COPY tmp_price FROM STDIN") as copy:
            for item in parse_zip(zip_path):
                if isinstance(item, RawStation):
                    s = clean_station(item)
                    if isinstance(s, Reject):
                        rejects.append(s)
                    else:  # si une station apparaît 2 fois, la dernière gagne
                        stations[s.station_id] = (
                            s.station_id, s.address, s.city, s.postal_code,
                            s.latitude, s.longitude, s.road_type, s.on_map,
                        )
                else:
                    p = clean_price(item)
                    if isinstance(p, Reject):
                        rejects.append(p)
                    else:
                        copy.write_row((p.station_id, p.fuel, p.updated_at, p.price, p.is_suspect))
                        n_prices += 1

                # 1) les stations d'abord (les prix y font référence).
        # Une station déjà connue n'est mise à jour que si ce fichier est au moins aussi
        # récent que ce qu'on sait d'elle : charger l'archive 2024 après 2026 ne doit
        # pas remplacer une adresse récente par une ancienne.
        file_max = conn.execute("SELECT max(updated_at)::date FROM tmp_price").fetchone()[0]
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO station (station_id, address, city, postal_code,
                                        latitude, longitude, road_type, on_map)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (station_id) DO UPDATE SET
                       address = EXCLUDED.address, city = EXCLUDED.city,
                       postal_code = EXCLUDED.postal_code, latitude = EXCLUDED.latitude,
                       longitude = EXCLUDED.longitude, road_type = EXCLUDED.road_type,
                       on_map = EXCLUDED.on_map
                   WHERE station.last_seen IS NULL OR station.last_seen <= %s""",
                [row + (file_max,) for row in stations.values()],
            )

        # 2) les prix : les doublons sont ignorés grâce à la clé primaire
        inserted = conn.execute(
            """INSERT INTO price_event (station_id, fuel, updated_at, price_eur_l,
                                        is_suspect, source_file_id)
               SELECT station_id, fuel, updated_at, price_eur_l, is_suspect, %s FROM tmp_price
               ON CONFLICT (station_id, fuel, updated_at) DO NOTHING""",
            (file_id,),
        ).rowcount

        # 3) les lignes rejetées, avec la raison
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO rejected_row (source_file_id, station_id, fuel,
                                             raw_value, raw_updated_at, reason)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                [(file_id, r.station_id, r.fuel, r.raw_value, r.raw_updated_at, r.reason)
                 for r in rejects],
            )

        # 4) première et dernière date de prix connue pour chaque station
        conn.execute(
            """UPDATE station s SET first_seen = x.first_day, last_seen = x.last_day
               FROM (SELECT station_id, min(updated_at)::date AS first_day,
                            max(updated_at)::date AS last_day
                     FROM price_event GROUP BY station_id) x
               WHERE s.station_id = x.station_id"""
        )

    return LoadResult(False, len(stations), n_prices, inserted, len(rejects))


if __name__ == "__main__":
    import sys
    import time

    from fuel.config import connect

    t0 = time.time()
    with connect() as conn:
        result = load_zip(conn, sys.argv[1], url=sys.argv[2] if len(sys.argv) > 2 else "")
    print(result)
    print(f"durée : {time.time() - t0:.1f} s")