"""Règles de nettoyage : ligne brute -> ligne propre, ou rejet avec une raison."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from fuel.parse import RawPrice, RawStation

FUELS = {"Gazole", "SP95", "SP98", "E10", "E85", "GPLc"}

# Bornes PROVISOIRES en €/L, à recalibrer plus tard sur les données.
# En dehors : le prix est GARDÉ mais marqué "suspect" (exclu des statistiques).
PLAUSIBLE = {  # en Decimal : comparer un Decimal à un float donne des faux résultats
    "Gazole": (Decimal("0.9"), Decimal("3.5")),
    "SP95": (Decimal("0.9"), Decimal("3.5")),
    "SP98": (Decimal("0.9"), Decimal("3.5")),
    "E10": (Decimal("0.9"), Decimal("3.5")),
    "E85": (Decimal("0.4"), Decimal("1.6")),
    "GPLc": (Decimal("0.6"), Decimal("1.8")),
}
LAT_RANGE, LON_RANGE = (41.0, 51.5), (-5.5, 10.0)  # France métropolitaine + Corse
COORD_DIVISOR = 100_000  # les coordonnées du fichier sont en degrés x 100 000


@dataclass(frozen=True)
class Station:
    station_id: int
    address: str | None
    city: str | None
    postal_code: str | None
    latitude: float | None
    longitude: float | None
    road_type: str | None
    on_map: bool


@dataclass(frozen=True)
class Price:
    station_id: int
    fuel: str
    updated_at: datetime
    price: Decimal
    is_suspect: bool


@dataclass(frozen=True)
class Reject:
    station_id: str | None
    fuel: str | None
    raw_value: str | None
    raw_updated_at: str | None
    reason: str


# ------------------------------------------------------------- utilitaires --
def _text(s: str | None) -> str | None:
    s = " ".join(s.split()) if s else ""
    return s or None


def _to_int(s: str | None) -> int | None:
    return int(s.strip()) if s and s.strip().isdigit() else None


def _coord(s: str | None) -> float | None:
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return None if v == 0 else v / COORD_DIVISOR


def _parse_date(s: str | None) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime((s or "")[:19], fmt)
        except ValueError:
            pass
    return None


def _parse_price(s: str | None) -> Decimal | None:
    try:
        v = Decimal((s or "").strip().replace(",", "."))
    except InvalidOperation:
        return None
    return v if v.is_finite() else None  # refuse NaN et Infinity


# ------------------------------------------------------------------ règles --
def clean_station(raw: RawStation) -> Station | Reject:
    sid = _to_int(raw.station_id)
    if sid is None:
        return Reject(raw.station_id, None, None, None, "station_id_invalide")

    lat, lon = _coord(raw.latitude), _coord(raw.longitude)
    if lat is None or lon is None:  # absentes ou à zéro : on n'en garde aucune
        lat = lon = None
    on_map = (
        lat is not None
        and LAT_RANGE[0] <= lat <= LAT_RANGE[1]
        and LON_RANGE[0] <= lon <= LON_RANGE[1]
    )
    cp = (raw.postal_code or "").strip()
    return Station(
        station_id=sid,
        address=_text(raw.address),
        city=_text(raw.city),
        postal_code=cp if re.fullmatch(r"\d{5}", cp) else None,
        latitude=lat,
        longitude=lon,
        road_type=_text(raw.road_type),
        on_map=on_map,
    )


def clean_price(raw: RawPrice, now: datetime | None = None) -> Price | Reject:
    now = now or datetime.now()

    def reject(reason: str) -> Reject:
        return Reject(raw.station_id, raw.fuel, raw.value, raw.updated_at, reason)

    if raw.fuel is None and raw.updated_at is None and raw.value is None:
        return reject("prix_vide")
    sid = _to_int(raw.station_id)
    if sid is None:
        return reject("station_id_invalide")
    if raw.fuel not in FUELS:
        return reject("carburant_inconnu")
    updated_at = _parse_date(raw.updated_at)
    if updated_at is None:
        return reject("date_illisible")
    if updated_at > now + timedelta(days=1):
        return reject("date_future")
    value = _parse_price(raw.value)
    if value is None:
        return reject("prix_illisible")
    if not (0 < value < 1000):  # 0 ou négatif, ou trop gros pour la base
        return reject("prix_hors_format")

    low, high = PLAUSIBLE[raw.fuel]
    return Price(sid, raw.fuel, updated_at, value, is_suspect=not (low <= value <= high))


# --------------------------------------------- bilan sur un fichier complet --
if __name__ == "__main__":
    import sys
    from collections import Counter

    from fuel.parse import parse_zip

    c: Counter = Counter()
    reasons: Counter = Counter()
    for item in parse_zip(sys.argv[1]):
        if isinstance(item, RawStation):
            r = clean_station(item)
            if isinstance(r, Reject):
                c["stations_rejetees"] += 1
                reasons[r.reason] += 1
            else:
                c["stations_ok"] += 1
                c["stations_sur_la_carte" if r.on_map else "stations_hors_carte"] += 1
        else:
            r = clean_price(item)
            if isinstance(r, Reject):
                c["prix_rejetes"] += 1
                reasons[r.reason] += 1
            else:
                c["prix_ok"] += 1
                c["prix_suspects"] += r.is_suspect
    for k, v in c.items():
        print(f"{k:<24}{v}")
    print("raisons de rejet :", dict(reasons))