"""Tests de la recherche à proximité (DuckDB, exemples calculables à la main)."""
import duckdb
import pytest

from fuel.nearby import build_latest, cheapest_near, find_commune, main

# (id, ville, code postal, latitude, longitude)
STATIONS = [
    (1, "LYON", "69001", 45.7600, 4.8300),     # centre de la recherche
    (2, "LYON", "69003", 45.7870, 4.8300),     # ~3 km au nord
    (3, "VILLEURBANNE", "69100", 45.8330, 4.8300),  # ~8 km au nord
    (4, "CALUIRE", "69300", 45.9000, 4.8300),  # ~15,6 km : hors du rayon de 10 km
    (5, "LYON", "69002", 45.7650, 4.8300),     # ~0,5 km, prix incohérent (masqué)
    (6, "SAINT-ÉTIENNE", "42000", 45.4397, 4.3872),
    (7, "SAINT-DENIS", "93200", 48.9362, 2.3574),
    (8, "SAINT-DENIS", "97400", -20.88, 55.45),
]
DAY = "2026-10-17"


def make_db(path=":memory:"):
    c = duckdb.connect(str(path))
    c.execute("CREATE TABLE station(station_id BIGINT, address VARCHAR, city VARCHAR, postal_code VARCHAR, "
              "latitude DOUBLE, longitude DOUBLE, on_map BOOLEAN)")
    c.executemany("INSERT INTO station VALUES (?, 'rue X', ?, ?, ?, ?, true)", STATIONS)
    c.execute("CREATE TABLE price_daily(day DATE, station_id BIGINT, fuel VARCHAR, price DECIMAL(6,3), "
              "price_updated_at TIMESTAMP, age_days INTEGER)")
    gazole = {1: 2.00, 2: 1.90, 3: 1.95, 4: 1.80, 5: 1.10}  # médiane du jour = 1.95 ; la station 5 est à 0,56 x
    for sid, price in gazole.items():
        c.execute("INSERT INTO price_daily VALUES (?, ?, 'Gazole', ?, TIMESTAMP '2026-10-16 08:00:00', 1)",
                  [DAY, sid, price])
    # un jour ANTÉRIEUR : ne doit jamais apparaître dans `latest`
    c.execute("INSERT INTO price_daily VALUES ('2026-10-16', 1, 'Gazole', 0.500, TIMESTAMP '2026-10-15 08:00:00', 1)")
    # E85 : un prix à 1.6 x la médiane est NORMAL pour ce carburant (pas de drapeau haut)
    for sid, price in {1: 0.80, 2: 1.28}.items():
        c.execute("INSERT INTO price_daily VALUES (?, ?, 'E85', ?, TIMESTAMP '2026-10-16 08:00:00', 1)",
                  [DAY, sid, price])
    # gazole à 3,3 (> 1,5 x médiane) : drapeau haut pour un carburant classique
    c.execute("INSERT INTO price_daily VALUES (?, 6, 'Gazole', 3.30, TIMESTAMP '2026-10-16 08:00:00', 1)", [DAY])
    c.execute("CREATE TABLE daily_stats(day DATE, fuel VARCHAR, median_price DOUBLE)")
    c.execute("INSERT INTO daily_stats VALUES (?, 'Gazole', 1.95), (?, 'E85', 0.80), ('2026-10-16', 'Gazole', 1.95)",
              [DAY, DAY])
    return c


@pytest.fixture
def con():
    c = make_db()
    build_latest(c)
    return c


def test_cli_end_to_end(tmp_path, capsys):
    """La commande complète sur un fichier DuckDB SANS table `latest` (comme ta vraie base)."""
    db = tmp_path / "t.duckdb"
    make_db(db).close()
    assert main(["--db", str(db), "--commune", "lyon", "--fuel", "Gazole", "--radius", "10"]) == 0
    out = capsys.readouterr().out
    assert "Centre : LYON (69)" in out
    assert "1.900 EUR/L" in out and "1.100" not in out      # le prix incohérent est masqué
    assert "1 station(s) masquée(s)" in out
    assert main(["--db", str(db), "--commune", "saint-denis"]) == 1   # ambigu -> demande --cp
    assert "--cp" in capsys.readouterr().out
    assert main(["--db", str(db)]) == 1                               # ni commune ni coordonnées


def test_latest_uses_last_day_only_and_flags_outliers(con):
    rows = dict(con.execute("SELECT station_id, is_outlier FROM latest WHERE fuel = 'Gazole'").fetchall())
    assert rows == {1: False, 2: False, 3: False, 4: False, 5: True, 6: True}   # 5 trop bas, 6 trop haut
    assert con.execute("SELECT count(*) FROM latest WHERE price < 1").fetchone()[0] == 1  # le 0,50 d'hier est exclu


def test_e85_high_ratio_is_not_flagged(con):
    # 1.28 / 0.80 = 1.6 : normal pour le E85
    assert con.execute("SELECT is_outlier FROM latest WHERE fuel='E85' AND station_id = 2").fetchone()[0] is False


def test_cheapest_near_orders_by_price_and_respects_radius(con):
    rows, masked = cheapest_near(con, 45.7600, 4.8300, "Gazole", radius_km=10)
    assert [r[0] for r in rows] == [2, 3, 1]          # 1.90, 1.95, 2.00 ; la station 4 est hors rayon
    assert masked == 1                                 # la station 5 (1.10) est masquée mais comptée
    distances = {r[0]: r[4] for r in rows}
    assert distances[1] == 0.0
    assert 2.8 < distances[2] < 3.2                    # 0,027 degré de latitude ~ 3,0 km
    assert 8.0 < distances[3] < 8.6


def test_outliers_can_be_included(con):
    rows, _ = cheapest_near(con, 45.7600, 4.8300, "Gazole", radius_km=10, include_outliers=True)
    assert rows[0][0] == 5 and rows[0][6] is True      # le prix à 1.10 devient le premier


def test_distance_paris_lyon_is_about_392_km(con):
    # distance à vol d'oiseau Paris -> Lyon : environ 392 km (valeur de référence connue)
    rows, _ = cheapest_near(con, 48.8566, 2.3522, "Gazole", radius_km=500, include_outliers=True)
    lyon = [r for r in rows if r[0] == 1][0]
    assert 389 < lyon[4] < 395


def test_limit_applies(con):
    rows, _ = cheapest_near(con, 45.7600, 4.8300, "Gazole", radius_km=100, limit=2)
    assert len(rows) == 2


def test_find_commune_ignores_accents_and_case(con):
    found = find_commune(con, "saint-etienne")
    assert [(c, d, n) for c, d, n, *_ in found] == [("SAINT-ÉTIENNE", "42", 1)]


def test_find_commune_prefers_exact_match_and_lists_ambiguities(con):
    found = find_commune(con, "lyon")                  # "LYON" exact ; pas "VILLEURBANNE"
    assert [c for c, *_ in found] == ["LYON"]
    assert found[0][2] == 3                            # 3 stations à Lyon
    two = find_commune(con, "Saint-Denis")             # même nom dans 2 départements
    assert sorted(d for _, d, *_ in two) == ["93", "97"]
    assert [d for _, d, *_ in find_commune(con, "Saint-Denis", postal_prefix="93")] == ["93"]


def test_find_commune_unknown(con):
    assert find_commune(con, "Atlantide") == []