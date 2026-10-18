"""Pipeline quotidien : télécharger -> charger -> contrôler -> noter le résultat.

Codes de sortie (lus par le planificateur Windows) :
  0 = succès   1 = échec   2 = un autre run est déjà en cours

Commandes :
  python -m fuel.run_daily             lance le pipeline
  python -m fuel.run_daily --status    affiche le dernier succès (code 1 si trop ancien)
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

import psycopg

from fuel.fetch import download, url_for
from fuel.load import LoadResult, load_zip

log = logging.getLogger("fuel.run")

OK, FAILED, ALREADY_RUNNING = 0, 1, 2
LOCK_KEY = 424242  # verrou PostgreSQL : empêche deux runs en même temps

# Seuils des contrôles qualité (volontairement larges : ils détectent une vraie panne,
# pas une variation normale).
MIN_STATIONS = 5000          # la France compte environ 15 000 stations dans le fichier
MAX_REJECT_RATIO = 0.05      # plus de 5 % de lignes rejetées = le format a sûrement changé
MAX_DATA_AGE = timedelta(days=2)       # le dernier prix doit dater de moins de 2 jours
STATUS_MAX_AGE = timedelta(hours=36)   # --status : alerte si le dernier succès est plus vieux


class QualityError(Exception):
    pass


def quality_problems(
    conn: psycopg.Connection,
    result: LoadResult,
    as_of: datetime,
    min_stations: int = MIN_STATIONS,
    max_reject_ratio: float = MAX_REJECT_RATIO,
) -> list[str]:
    problems: list[str] = []
    if not result.already_loaded:  # fichier identique au précédent : rien à re-vérifier
        total = result.prices_read + result.rejected
        if result.stations < min_stations:
            problems.append(f"seulement {result.stations} stations dans le fichier (minimum {min_stations})")
        if total == 0:
            problems.append("le fichier ne contient aucun prix")
        elif result.rejected / total > max_reject_ratio:
            problems.append(f"{result.rejected}/{total} lignes rejetées (> {max_reject_ratio:.0%})")
    last = conn.execute("SELECT max(updated_at) FROM price_event").fetchone()[0]
    if last is None or as_of - last > MAX_DATA_AGE:
        problems.append(f"données trop anciennes : dernier prix = {last}")
    return problems


def run_pipeline(
    conn: psycopg.Connection,
    fetch_fn: Callable[[], tuple[Path, str]],
    lock_key: int = LOCK_KEY,
    as_of: datetime | None = None,
    min_stations: int = MIN_STATIONS,
    max_reject_ratio: float = MAX_REJECT_RATIO,
) -> int:
    """`fetch_fn` renvoie (chemin du ZIP, url). La connexion doit être en autocommit."""
    as_of = as_of or datetime.now()
    if not conn.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,)).fetchone()[0]:
        log.error("un autre run est déjà en cours : abandon")
        return ALREADY_RUNNING
    try:
        # Un run resté en 'running' alors qu'on tient le verrou = un run précédent qui a planté.
        stale = conn.execute(
            """UPDATE pipeline_run SET status = 'failed', finished_at = now(),
                      error_message = %s WHERE status = 'running'""",
            ("interrompu : le processus s'est arrêté avant la fin",),
        ).rowcount
        if stale:
            log.warning("%d run(s) précédent(s) interrompu(s), marqués en échec", stale)

        run_id = conn.execute(
            "INSERT INTO pipeline_run DEFAULT VALUES RETURNING run_id"
        ).fetchone()[0]
        log.info("run %d démarré", run_id)
        try:
            path, url = fetch_fn()
            result = load_zip(conn, path, url)
            if result.already_loaded:
                log.info("fichier identique au précédent (même sha256) : rien à charger")
            problems = quality_problems(conn, result, as_of, min_stations, max_reject_ratio)
            if problems:
                raise QualityError("contrôle qualité : " + " ; ".join(problems))
        except Exception as e:  # réseau, base de données, qualité... tout est noté
            log.error("run %d en échec : %s", run_id, e, exc_info=not isinstance(e, QualityError))
            conn.execute(
                """UPDATE pipeline_run SET status = 'failed', finished_at = now(),
                          error_message = %s WHERE run_id = %s""",
                (f"{type(e).__name__}: {e}"[:1000], run_id),
            )
            return FAILED

        conn.execute(
            """UPDATE pipeline_run SET status = 'success', finished_at = now(),
                      rows_read = %s, rows_inserted = %s, rows_rejected = %s
               WHERE run_id = %s""",
            (result.prices_read, result.prices_inserted, result.rejected, run_id),
        )
        log.info("run %d réussi : %d prix lus, %d nouveaux, %d rejetés",
                 run_id, result.prices_read, result.prices_inserted, result.rejected)
        return OK
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))


def last_success(conn: psycopg.Connection):
    return conn.execute(
        "SELECT run_id, finished_at, rows_inserted FROM pipeline_run "
        "WHERE status = 'success' ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()


def print_status(conn: psycopg.Connection) -> int:
    row = last_success(conn)
    if row is None:
        print("Aucun run réussi pour l'instant.")
        return FAILED
    run_id, finished_at, inserted = row
    age = datetime.now(finished_at.tzinfo) - finished_at
    print(f"Dernier succès : run {run_id}, {finished_at:%Y-%m-%d %H:%M}, "
          f"{inserted} nouveaux prix (il y a {age.total_seconds() / 3600:.1f} h)")
    if age > STATUS_MAX_AGE:
        print(f"ALERTE : le dernier succès a plus de {STATUS_MAX_AGE.total_seconds() / 3600:.0f} h.")
        return FAILED
    return OK


def setup_logging(log_dir: Path = Path("logs")) -> None:
    log_dir.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handlers = [
        logging.StreamHandler(sys.stderr),
        RotatingFileHandler(log_dir / "pipeline.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"),
    ]
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pipeline quotidien des prix des carburants")
    parser.add_argument("--status", action="store_true", help="affiche le dernier succès")
    args = parser.parse_args(argv)
    setup_logging()

    from fuel.config import connect  # import ici : --help marche même sans .env

    try:
        conn = connect()
    except Exception as e:  # base arrêtée (Docker éteint ?) ou mauvais mot de passe
        log.error("connexion à PostgreSQL impossible : %s", e)
        return FAILED
    with conn:
        if args.status:
            return print_status(conn)
        url, label = url_for(None)  # fichier de l'année en cours
        return run_pipeline(conn, lambda: (download(url, label), url))


if __name__ == "__main__":
    sys.exit(main())