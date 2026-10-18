"""Tests du pipeline quotidien (base PostgreSQL de test, schéma temporaire)."""
import random
import zipfile
from datetime import datetime

import pytest

from fuel.config import connect
from fuel.fetch import FetchError
from fuel.run_daily import ALREADY_RUNNING, FAILED, OK, last_success, print_status, run_pipeline

AS_OF = datetime(2026, 10, 17, 8, 0, 0)

XML = """<?xml version="1.0" encoding="ISO-8859-1" standalone="yes"?>
<pdv_liste>
<pdv id="1000005" latitude="4620000" longitude="520000" cp="01000" pop="R">
<adresse>1 rue Test</adresse><ville>Bourg</ville>
<prix nom="Gazole" id="1" maj="{day} 08:00:00" valeur="2.369"/>
</pdv>
</pdv_liste>
"""


def make_zip(tmp_path, name, day="2026-10-16"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("data.xml", XML.format(day=day).encode("latin-1"))
    return path


def run(conn, fetch_fn, key, **kw):
    return run_pipeline(conn, fetch_fn, lock_key=key, as_of=AS_OF, min_stations=1, **kw)


@pytest.fixture
def key():
    return random.randint(10_000, 10_000_000)  # verrou propre à chaque test


def runs(conn):
    return conn.execute("SELECT status, rows_inserted, error_message FROM pipeline_run ORDER BY run_id").fetchall()


def test_success_is_recorded(conn, tmp_path, key):
    zip_path = make_zip(tmp_path, "a.zip")
    assert run(conn, lambda: (zip_path, "http://x"), key) == OK
    assert runs(conn) == [("success", 1, None)]
    assert last_success(conn) is not None


def test_same_file_again_is_success_with_nothing_inserted(conn, tmp_path, key):
    zip_path = make_zip(tmp_path, "a.zip")
    run(conn, lambda: (zip_path, "http://x"), key)
    assert run(conn, lambda: (zip_path, "http://x"), key) == OK
    assert [r[:2] for r in runs(conn)] == [("success", 1), ("success", 0)]
    assert conn.execute("SELECT count(*) FROM price_event").fetchone()[0] == 1


def test_network_failure_is_recorded_and_returns_failed(conn, key):
    def broken():
        raise FetchError("abandon après 4 essais : panne simulée")

    assert run(conn, broken, key) == FAILED
    status, _, message = runs(conn)[0]
    assert status == "failed" and "panne simulée" in message
    assert last_success(conn) is None


def test_old_data_fails_quality_check(conn, tmp_path, key):
    zip_path = make_zip(tmp_path, "old.zip", day="2026-09-01")
    assert run(conn, lambda: (zip_path, "http://x"), key) == FAILED
    status, _, message = runs(conn)[0]
    assert status == "failed" and "contrôle qualité" in message and "trop anciennes" in message


def test_too_few_stations_fails_quality_check(conn, tmp_path, key):
    zip_path = make_zip(tmp_path, "a.zip")
    code = run_pipeline(conn, lambda: (zip_path, "http://x"), lock_key=key,
                        as_of=AS_OF, min_stations=5000)
    assert code == FAILED
    assert "seulement 1 stations" in runs(conn)[0][2]


def test_interrupted_run_is_marked_failed_at_next_start(conn, tmp_path, key):
    conn.execute("INSERT INTO pipeline_run DEFAULT VALUES")  # reste 'running' : processus tué
    zip_path = make_zip(tmp_path, "a.zip")
    assert run(conn, lambda: (zip_path, "http://x"), key) == OK
    statuses = [r[0] for r in runs(conn)]
    assert statuses == ["failed", "success"]
    assert "interrompu" in runs(conn)[0][2]


def test_second_instance_is_refused_while_first_runs(conn, tmp_path, key):
    other = connect()  # une 2e connexion tient le verrou, comme un run déjà en cours
    try:
        other.execute("SELECT pg_advisory_lock(%s)", (key,))
        zip_path = make_zip(tmp_path, "a.zip")
        assert run(conn, lambda: (zip_path, "http://x"), key) == ALREADY_RUNNING
        assert runs(conn) == []  # aucun run créé, rien chargé
    finally:
        other.close()


def test_status_alerts_when_no_success(conn, capsys):
    assert print_status(conn) == FAILED
    assert "Aucun run réussi" in capsys.readouterr().out