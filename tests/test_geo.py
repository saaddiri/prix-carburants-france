"""Tests du lien station -> région et des statistiques régionales (petits fichiers au format de l'API Géo)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import duckdb
import pytest

from fuel.geo import build_geo, coverage, fetch_json
from fuel.regional import build_region_daily, last_day_table, main

COMMUNES = [
    {"nom": "Lyon", "code": "69123", "codesPostaux": ["69001", "69002"], "codeDepartement": "69", "codeRegion": "84"},
    {"nom": "Porto-Vecchio", "code": "2A247", "codesPostaux": ["20137"], "codeDepartement": "2A", "codeRegion": "94"},
    {"nom": "Paris", "code": "75056", "codesPostaux": ["75001"], "codeDepartement": "75", "codeRegion": "11"},
    # un même code postal à cheval sur deux régions (cas réel possible) :
    {"nom": "Commune A", "code": "00001", "codesPostaux": ["99001"], "codeDepartement": "28", "codeRegion": "28"},
    {"nom": "Commune B", "code": "00002", "codesPostaux": ["99001"], "codeDepartement": "45", "codeRegion": "24"},
]
REGIONS = [{"nom": "Auvergne-Rhône-Alpes", "code": "84"}, {"nom": "Corse", "code": "94"},
           {"nom": "Île-de-France", "code": "11"}, {"nom": "Normandie", "code": "28"},
           {"nom": "Centre-Val de Loire", "code": "24"}]


@pytest.fixture
def con(tmp_path):
    c = duckdb.connect()
    c.execute("CREATE TABLE station(station_id BIGINT, postal_code VARCHAR, on_map BOOLEAN)")
    c.executemany("INSERT INTO station VALUES (?, ?, true)", [
        (1, "69001"), (2, "69002"), (3, "69001"), (4, "20137"), (5, "75001"),
        (6, "99001"),   # ambigu
        (7, "00000"),   # code postal inconnu
    ])
    c.execute("INSERT INTO station VALUES (8, '69001', false)")  # hors carte : ne compte pas
    c.execute("CREATE TABLE price_daily(day DATE, station_id BIGINT, fuel VARCHAR, price DECIMAL(6,3), "
              "price_updated_at TIMESTAMP, age_days INTEGER)")
    prices = {1: 2.00, 2: 2.10, 3: 2.20, 4: 2.50, 5: 2.30, 6: 1.00, 7: 1.00, 8: 9.99}
    for sid, price in prices.items():
        c.execute("INSERT INTO price_daily VALUES ('2026-10-17', ?, 'Gazole', ?, TIMESTAMP '2026-10-16 08:00:00', 1)",
                  [sid, price])
    c.execute("CREATE TABLE daily_stats(day DATE, fuel VARCHAR, median_price DOUBLE)")
    c.execute("INSERT INTO daily_stats VALUES ('2026-10-17', 'Gazole', 2.10)")
    cp, rp = tmp_path / "c.json", tmp_path / "r.json"
    cp.write_text(json.dumps(COMMUNES), encoding="utf-8")
    rp.write_text(json.dumps(REGIONS), encoding="utf-8")
    build_geo(c, cp, rp)
    return c


def test_stations_get_their_region(con):
    rows = dict(con.execute("SELECT station_id, region_name FROM station_geo WHERE geo_status = 'ok'").fetchall())
    assert rows == {1: "Auvergne-Rhône-Alpes", 2: "Auvergne-Rhône-Alpes", 3: "Auvergne-Rhône-Alpes",
                    4: "Corse", 5: "Île-de-France", 8: "Auvergne-Rhône-Alpes"}


def test_ambiguous_and_unknown_postal_codes_are_flagged_not_guessed(con):
    status = dict(con.execute("SELECT station_id, geo_status FROM station_geo").fetchall())
    assert status[6] == "ambiguous_region" and status[7] == "unknown_postal"


def test_coverage_counts_only_stations_on_map(con):
    assert dict(coverage(con)) == {"ok": 5, "ambiguous_region": 1, "unknown_postal": 1}


def test_region_median_and_gap_with_national(con):
    build_region_daily(con)
    rows = {r[0]: r[1:5] for r in last_day_table(con, "Gazole")}
    # Auvergne-Rhône-Alpes : stations 1, 2, 3, 8 -> 2.00, 2.10, 2.20, 9.99 -> médiane 2.15
    assert rows["Auvergne-Rhône-Alpes"] == (4, 2.15, 0.05, 2.38)
    assert rows["Corse"] == (1, 2.5, 0.4, 19.05)
    assert "Normandie" not in rows                      # la station ambiguë n'est dans aucune région


def test_cli_exports_csv(con, tmp_path, capsys):
    db = tmp_path / "d.duckdb"
    con.execute(f"ATTACH '{db.as_posix()}' AS out")
    for t in ("station_geo", "price_daily", "daily_stats"):
        con.execute(f"CREATE TABLE out.{t} AS SELECT * FROM {t}")
    con.execute("DETACH out")
    out = tmp_path / "r.csv"
    assert main(["--db", str(db), "--out", str(out), "--fuel", "Gazole"]) == 0
    assert out.read_text().splitlines()[0] == "day,fuel,region_code,region_name,n_stations,median_price"
    assert "Corse" in capsys.readouterr().out


@pytest.fixture
def server():
    state = {"plan": [], "hits": 0}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            state["hits"] += 1
            code, body = state["plan"].pop(0) if state["plan"] else (200, json.dumps(REGIONS).encode())
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{httpd.server_port}/x"
    yield state
    httpd.shutdown()


def test_fetch_json_retries_then_saves(server, tmp_path):
    server["plan"] = [(503, b"panne")]
    dest = fetch_json(server["url"], tmp_path / "geo" / "r.json", backoff=0)
    assert server["hits"] == 2 and len(json.loads(dest.read_text())) == 5
    assert not list((tmp_path / "geo").glob("*.part"))


def test_fetch_json_refuses_too_small_answer_and_saves_nothing(server, tmp_path):
    with pytest.raises(RuntimeError, match="abandon"):
        fetch_json(server["url"], tmp_path / "r.json", min_items=100, retries=2, backoff=0)
    assert not (tmp_path / "r.json").exists()