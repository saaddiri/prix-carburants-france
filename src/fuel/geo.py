"""Lien station -> département -> région, à partir de l'API Géo (source officielle : geo.api.gouv.fr).

Principe : l'API donne, pour chaque commune, ses codes postaux, son département et sa région.
On en déduit, pour chaque code postal, la région. Si un code postal touche PLUSIEURS régions,
la station est marquée 'ambiguous_region' et n'entre pas dans les statistiques régionales.
Les fichiers téléchargés sont gardés (data/raw/geo), jamais supprimés.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import duckdb

log = logging.getLogger("fuel.geo")

BASE = "https://geo.api.gouv.fr"
COMMUNES_URL = BASE + "/communes?fields=nom,code,codesPostaux,codeDepartement,codeRegion"
REGIONS_URL = BASE + "/regions"
RAW_DIR = Path("data/raw/geo")
DB_PATH = Path("data/analytics/fuel.duckdb")


def fetch_json(url: str, dest: Path, min_items: int = 1, retries: int = 3, backoff: float = 2.0,
               timeout: float = 60) -> Path:
    """Télécharge un JSON (liste) avec reprises ; n'écrit le fichier que s'il est valide."""
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                body = resp.read()
            data = json.loads(body)
            if not isinstance(data, list) or len(data) < min_items:
                raise ValueError(f"réponse inattendue ({len(data) if isinstance(data, list) else type(data).__name__} "
                                 f"éléments, au moins {min_items} attendus)")
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(body)
            tmp.replace(dest)
            return dest
        except (urllib.error.URLError, ValueError, OSError) as exc:
            last = exc
            log.warning("essai %d/%d échoué : %s", attempt, retries, exc)
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise RuntimeError(f"abandon après {retries} essais : {last}")


def fetch_all(raw_dir: Path = RAW_DIR) -> tuple[Path, Path]:
    stamp = date.today().strftime("%Y%m%d")
    communes = fetch_json(COMMUNES_URL, raw_dir / f"communes_{stamp}.json", min_items=30000)  # ~35 000 communes
    regions = fetch_json(REGIONS_URL, raw_dir / f"regions_{stamp}.json", min_items=10)
    return communes, regions


def latest_raw(raw_dir: Path = RAW_DIR) -> tuple[Path, Path]:
    """Les fichiers déjà téléchargés les plus récents."""
    communes, regions = sorted(raw_dir.glob("communes_*.json")), sorted(raw_dir.glob("regions_*.json"))
    if not communes or not regions:
        raise FileNotFoundError(f"aucun fichier dans {raw_dir} : lance d'abord sans --no-fetch")
    return communes[-1], regions[-1]


def build_geo(con: duckdb.DuckDBPyConnection, communes_path: Path, regions_path: Path) -> None:
    """Crée region, commune_postal, postal_geo et station_geo dans DuckDB (table station requise)."""
    communes = json.loads(Path(communes_path).read_text(encoding="utf-8"))
    regions = json.loads(Path(regions_path).read_text(encoding="utf-8"))
    con.execute("CREATE OR REPLACE TABLE region(region_code VARCHAR, region_name VARCHAR)")
    con.executemany("INSERT INTO region VALUES (?, ?)", [(r["code"], r["nom"]) for r in regions])
    con.execute("CREATE OR REPLACE TABLE commune_postal(postal_code VARCHAR, dept VARCHAR, "
                "region_code VARCHAR, commune_code VARCHAR)")
    con.executemany(
        "INSERT INTO commune_postal VALUES (?, ?, ?, ?)",
        [(cp, c.get("codeDepartement"), c.get("codeRegion"), c["code"])
         for c in communes for cp in c.get("codesPostaux", [])],
    )
    con.execute(
        """CREATE OR REPLACE TABLE postal_geo AS
           WITH c AS (SELECT postal_code, dept, region_code, count(*) AS n
                      FROM commune_postal WHERE region_code IS NOT NULL GROUP BY ALL)
           SELECT postal_code, arg_max(dept, n) AS dept, arg_max(region_code, n) AS region_code,
                  count(DISTINCT region_code) AS n_regions
           FROM c GROUP BY postal_code"""
    )
    con.execute(
        """CREATE OR REPLACE TABLE station_geo AS
           SELECT s.station_id, g.dept, g.region_code, r.region_name,
                  CASE WHEN g.postal_code IS NULL THEN 'unknown_postal'
                       WHEN g.n_regions > 1 THEN 'ambiguous_region'
                       ELSE 'ok' END AS geo_status
           FROM station s
           LEFT JOIN postal_geo g ON g.postal_code = s.postal_code
           LEFT JOIN region r ON r.region_code = g.region_code"""
    )


def coverage(con: duckdb.DuckDBPyConnection) -> list[tuple[str, int]]:
    """Nombre de stations (sur la carte) par statut de rapprochement."""
    return con.execute(
        """SELECT g.geo_status, count(*) FROM station_geo g JOIN station s USING (station_id)
           WHERE s.on_map GROUP BY 1 ORDER BY 2 DESC"""
    ).fetchall()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Télécharge l'API Géo et relie les stations aux régions")
    p.add_argument("--no-fetch", action="store_true", help="réutiliser les fichiers déjà téléchargés")
    p.add_argument("--db", default=str(DB_PATH))
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    communes, regions = latest_raw() if args.no_fetch else fetch_all()
    print(f"fichiers : {communes.name}, {regions.name}")
    con = duckdb.connect(args.db)
    build_geo(con, communes, regions)
    rows = coverage(con)
    total = sum(n for _, n in rows)
    print(f"\nStations sur la carte : {total}")
    for status, n in rows:
        print(f"  {status:<18} {n:>6}  ({100 * n / total:.2f} %)")
    return 0


if __name__ == "__main__":
    sys.exit(main())