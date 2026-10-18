"""Données pour la carte : prix d'un jour et d'un carburant, prêts à afficher."""
from __future__ import annotations

from datetime import date

import duckdb
import numpy as np
import pandas as pd

from fuel.nearby import CLASSIC_FUELS, EARTH_RADIUS_KM, HIGH_RATIO, LOW_RATIO


def day_bounds(con: duckdb.DuckDBPyConnection) -> tuple[date, date]:
    return con.execute("SELECT min(day), max(day) FROM price_daily").fetchone()


def fuels(con: duckdb.DuckDBPyConnection) -> list[str]:
    return [r[0] for r in con.execute("SELECT DISTINCT fuel FROM price_daily ORDER BY fuel").fetchall()]


def day_prices(con: duckdb.DuckDBPyConnection, day: date, fuel: str,
               show_outliers: bool = False) -> tuple[pd.DataFrame, int]:
    """(stations avec leur prix ce jour-là, nombre de stations masquées car prix incohérent).

    Même règle "prix incohérent" que fuel.nearby (rapport à la médiane du jour).
    """
    df = con.execute(
        f"""SELECT p.station_id, s.city, s.address, s.postal_code, s.latitude, s.longitude,
                   p.price::DOUBLE AS price, p.age_days, p.price_updated_at,
                   (p.price::DOUBLE / d.median_price < {LOW_RATIO}
                    OR (p.fuel IN {CLASSIC_FUELS} AND p.price::DOUBLE / d.median_price > {HIGH_RATIO})
                   ) AS is_outlier
            FROM price_daily p
            JOIN station s USING (station_id)
            JOIN daily_stats d USING (day, fuel)
            WHERE p.day = ? AND p.fuel = ?""",
        [day, fuel],
    ).df()
    masked = int(df["is_outlier"].sum())
    if not show_outliers:
        df = df[~df["is_outlier"]]
    return df.reset_index(drop=True), masked


def color_range(prices: pd.Series, low_q: float = 0.02, high_q: float = 0.98) -> tuple[float, float]:
    """Bornes de l'échelle de couleurs : percentiles 2 % et 98 %, pour que quelques prix
    extrêmes n'écrasent pas toutes les couleurs. Les prix au-delà prennent la couleur du bout."""
    return float(prices.quantile(low_q)), float(prices.quantile(high_q))


def with_distance(df: pd.DataFrame, lat: float, lon: float) -> pd.DataFrame:
    """Ajoute la colonne distance_km (à vol d'oiseau, formule de Haversine)."""
    la1, lo1 = np.radians(lat), np.radians(lon)
    la2, lo2 = np.radians(df["latitude"].to_numpy()), np.radians(df["longitude"].to_numpy())
    a = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    out = df.copy()
    out["distance_km"] = 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))
    return out