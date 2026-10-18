from datetime import datetime
from decimal import Decimal

from fuel.clean import Price, Reject, Station, clean_price, clean_station
from fuel.parse import RawPrice, RawStation

NOW = datetime(2026, 10, 17, 12, 0, 0)


def station(**kw):
    base = dict(station_id="1000005", latitude="4620000", longitude="520000",
                postal_code="01000", road_type="R", address="1 rue  Test ", city="Bourg")
    return RawStation(**{**base, **kw})


def price(**kw):
    base = dict(station_id="1000005", fuel="Gazole", updated_at="2026-10-16 08:00:00", value="2.369")
    return RawPrice(**{**base, **kw})


# ---------------------------------------------------------------- stations --
def test_normal_station_is_on_map():
    s = clean_station(station())
    assert isinstance(s, Station)
    assert (s.station_id, s.latitude, s.longitude, s.on_map) == (1000005, 46.2, 5.2, True)
    assert s.address == "1 rue Test"  # espaces en trop retirés


def test_zero_coordinates_mean_not_on_map():
    s = clean_station(station(latitude="0", longitude="0"))
    assert (s.latitude, s.longitude, s.on_map) == (None, None, False)


def test_one_missing_coordinate_drops_both():
    s = clean_station(station(latitude=None))
    assert (s.latitude, s.longitude, s.on_map) == (None, None, False)


def test_station_outside_france_is_kept_but_not_on_map():
    s = clean_station(station(latitude="-2094971", longitude="5529526"))
    assert s.on_map is False
    assert s.latitude is not None  # la valeur est conservée


def test_bad_station_id_is_rejected():
    r = clean_station(station(station_id="abc"))
    assert isinstance(r, Reject) and r.reason == "station_id_invalide"


def test_bad_postal_code_becomes_none():
    assert clean_station(station(postal_code="7500")).postal_code is None


# ------------------------------------------------------------------- prix --
def test_normal_price():
    p = clean_price(price(), NOW)
    assert isinstance(p, Price)
    assert (p.station_id, p.fuel, p.price, p.is_suspect) == (1000005, "Gazole", Decimal("2.369"), False)
    assert p.updated_at == datetime(2026, 10, 16, 8, 0, 0)


def test_comma_decimal_is_accepted():
    assert clean_price(price(value="2,369"), NOW).price == Decimal("2.369")


def test_empty_node_is_rejected_as_prix_vide():
    r = clean_price(RawPrice("1000005", None, None, None), NOW)
    assert isinstance(r, Reject) and r.reason == "prix_vide"


def test_reject_reasons():
    cases = {
        "carburant_inconnu": price(fuel="Diesel"),
        "date_illisible": price(updated_at="2026/10/16"),
        "date_future": price(updated_at="2027-01-01 00:00:00"),
        "prix_illisible": price(value="abc"),
        "prix_hors_format": price(value="0"),
        "station_id_invalide": price(station_id=None),
    }
    for reason, raw in cases.items():
        r = clean_price(raw, NOW)
        assert isinstance(r, Reject) and r.reason == reason, reason


def test_nan_and_infinity_are_unreadable():
    for bad in ("NaN", "Infinity", "-Infinity"):
        assert clean_price(price(value=bad), NOW).reason == "prix_illisible"


def test_too_big_price_is_rejected():
    assert clean_price(price(value="1000"), NOW).reason == "prix_hors_format"


def test_absurd_price_is_kept_but_suspect():
    p = clean_price(price(fuel="E85", value="9.919"), NOW)
    assert isinstance(p, Price) and p.is_suspect is True


def test_suspect_boundaries():
    assert clean_price(price(value="3.5"), NOW).is_suspect is False
    assert clean_price(price(value="3.51"), NOW).is_suspect is True
    assert clean_price(price(value="0.9"), NOW).is_suspect is False
    assert clean_price(price(value="0.89"), NOW).is_suspect is True