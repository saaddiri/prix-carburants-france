"""Tests des statistiques hebdomadaires (exemples calculables à la main)."""
from datetime import date, timedelta

import duckdb
import pytest

from fuel.trends import build_weekly, main


def make_stats(days_by_week_start: dict[date, tuple[int, float]], fuel="Gazole"):
    """{lundi: (nombre de jours présents, médiane)} -> table daily_stats."""
    con = duckdb.connect()
    con.execute("CREATE TABLE daily_stats(day DATE, fuel VARCHAR, median_price DOUBLE, n_stations BIGINT)")
    for monday, (n_days, price) in days_by_week_start.items():
        for i in range(n_days):
            con.execute("INSERT INTO daily_stats VALUES (?, ?, ?, 1000)", [monday + timedelta(days=i), fuel, price])
    return con


MON1, MON2, MON3, MON4 = date(2026, 10, 5), date(2026, 10, 12), date(2026, 10, 19), date(2026, 10, 26)
assert MON1.weekday() == 0  # le 5 octobre 2026 est bien un lundi


def test_weekly_median_delta_in_euros_and_percent():
    con = make_stats({MON1: (7, 2.00), MON2: (7, 2.10)})
    build_weekly(con)
    rows = con.execute("SELECT week_start, median_price, delta_eur, delta_pct FROM weekly_stats ORDER BY 1").fetchall()
    assert rows[0] == (MON1, 2.0, None, None)           # pas de semaine précédente
    assert rows[1] == (MON2, 2.1, 0.1, 5.0)              # +0,10 EUR = +5 %


def test_incomplete_week_is_dropped():
    con = make_stats({MON1: (7, 2.00), MON2: (3, 2.50)})
    assert build_weekly(con) == 1
    assert con.execute("SELECT week_start FROM weekly_stats").fetchone()[0] == MON1


def test_no_delta_when_previous_week_is_missing():
    con = make_stats({MON1: (7, 2.00), MON2: (4, 9.0), MON3: (7, 2.20)})   # la semaine du 12/10 est incomplète
    build_weekly(con)
    last = con.execute("SELECT delta_eur, delta_pct FROM weekly_stats WHERE week_start = ?", [MON3]).fetchone()
    assert last == (None, None)                          # on ne compare pas avec une semaine non adjacente


def test_weekly_value_is_mean_of_daily_medians():
    con = duckdb.connect()
    con.execute("CREATE TABLE daily_stats(day DATE, fuel VARCHAR, median_price DOUBLE, n_stations BIGINT)")
    for i, price in enumerate([2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.7]):
        con.execute("INSERT INTO daily_stats VALUES (?, 'E10', ?, 900)", [MON1 + timedelta(days=i), price])
    build_weekly(con)
    assert con.execute("SELECT median_price, min_daily_median, max_daily_median FROM weekly_stats").fetchone() == (2.1, 2.0, 2.7)


def test_fuels_are_not_mixed():
    con = make_stats({MON1: (7, 2.00), MON2: (7, 2.10)}, fuel="Gazole")
    for i in range(7):
        con.execute("INSERT INTO daily_stats VALUES (?, 'E85', 0.9, 500)", [MON2 + timedelta(days=i)])
    build_weekly(con)
    assert con.execute("SELECT count(*) FROM weekly_stats WHERE fuel = 'E85'").fetchone()[0] == 1
    assert con.execute("SELECT delta_eur FROM weekly_stats WHERE fuel = 'E85'").fetchone()[0] is None


def test_cli_writes_csv(tmp_path, capsys):
    db = tmp_path / "t.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE daily_stats(day DATE, fuel VARCHAR, median_price DOUBLE, n_stations BIGINT)")
    for monday, price in ((MON1, 2.0), (MON2, 2.1)):
        for i in range(7):
            con.execute("INSERT INTO daily_stats VALUES (?, 'Gazole', ?, 1000)", [monday + timedelta(days=i), price])
    con.close()
    out = tmp_path / "w.csv"
    assert main(["--db", str(db), "--out", str(out)]) == 0
    text = out.read_text()
    assert text.splitlines()[0].startswith("week_start,fuel,n_days,median_price")
    assert len(text.splitlines()) == 3
    assert "+0.100 EUR (+5.00 %)" in capsys.readouterr().out