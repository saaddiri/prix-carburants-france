"""Lecture du XML "Prix des carburants" : ZIP -> lignes brutes (tout en texte, rien de nettoyé)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class RawStation:
    station_id: str | None
    latitude: str | None
    longitude: str | None
    postal_code: str | None
    road_type: str | None
    address: str | None
    city: str | None


@dataclass(frozen=True)
class RawPrice:
    station_id: str | None
    fuel: str | None
    updated_at: str | None
    value: str | None


def parse_zip(zip_path: str | Path) -> Iterator[RawStation | RawPrice]:
    """Lit le XML en flux. Pour chaque station : un RawStation, puis ses RawPrice."""
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not names:
            raise ValueError(f"Aucun fichier .xml dans {zip_path}")
        with zf.open(names[0]) as f:
            for _, el in ET.iterparse(f, events=("end",)):
                if el.tag != "pdv":
                    continue
                sid = el.get("id")
                yield RawStation(
                    station_id=sid,
                    latitude=el.get("latitude"),
                    longitude=el.get("longitude"),
                    postal_code=el.get("cp"),
                    road_type=el.get("pop"),
                    address=el.findtext("adresse") or None,
                    city=el.findtext("ville") or None,
                )
                for p in el.findall("prix"):
                    yield RawPrice(sid, p.get("nom"), p.get("maj"), p.get("valeur"))
                el.clear()  # libère la mémoire : on ne garde pas toute l'année en RAM


if __name__ == "__main__":
    import sys

    n_stations = n_prices = n_empty = 0
    for item in parse_zip(sys.argv[1]):
        if isinstance(item, RawStation):
            n_stations += 1
        else:
            n_prices += 1
            n_empty += item.value is None
    print(f"stations={n_stations} prix={n_prices} prix_vides={n_empty}")