"""Tests des données de la carte + test de fumée de l'application Streamlit."""
from datetime import date
from pathlib import Path

import pytest

from fuel.mapdata import color_range, day_bounds, day_prices, fuels, with_distance
from test_nearby import make_db

APP = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"


@pytest.fixture
def con():
    return make_db()


def test_bounds_and_fuels(con):
    assert day_bounds(con) == (date(2026, 10, 16), date(2026, 10, 17))
    assert fuels(con) == ["E85", "Gazole"]


def test_day_prices_masks_outliers_and_counts_them(con):
    df, masked = day_prices(con, date(2026, 10, 17), "Gazole")
    assert masked == 2                                   # station 5 (trop bas) et station 6 (trop haut)
    assert sorted(df["station_id"]) == [1, 2, 3, 4]
    df_all, _ = day_prices(con, date(2026, 10, 17), "Gazole", show_outliers=True)
    assert len(df_all) == 6


def test_day_prices_uses_requested_day_only(con):
    df, _ = day_prices(con, date(2026, 10, 16), "Gazole", show_outliers=True)
    assert df["station_id"].tolist() == [1] and df["price"].iloc[0] == 0.5


def test_color_range_uses_percentiles_not_extremes(con):
    df, _ = day_prices(con, date(2026, 10, 17), "Gazole", show_outliers=True)
    lo, hi = color_range(df["price"])
    assert df["price"].min() < lo and hi < df["price"].max()     # le 1,10 et le 3,30 sont écartés


def test_with_distance_matches_known_distance(con):
    df, _ = day_prices(con, date(2026, 10, 17), "Gazole")
    out = with_distance(df, 45.7600, 4.8300)
    d = dict(zip(out["station_id"], out["distance_km"]))
    assert d[1] == pytest.approx(0.0, abs=1e-6)
    assert 2.8 < d[2] < 3.2


def test_streamlit_app_runs_without_error(tmp_path, monkeypatch):
    """Test de fumée : l'app démarre sur une petite base, avec et sans recherche de commune."""
    st_testing = pytest.importorskip("streamlit.testing.v1")
    db = tmp_path / "t.duckdb"
    make_db(db).close()
    monkeypatch.setenv("FUEL_DB", str(db))
    at = st_testing.AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception
    assert [m.label for m in at.metric][0] == "Stations"
    at.sidebar.text_input[0].set_value("Lyon").run()
    assert not at.exception
    assert at.dataframe                                   # le tableau des moins chères est affiché