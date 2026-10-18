import zipfile

from fuel.parse import RawPrice, RawStation, parse_zip

XML = """<?xml version="1.0" encoding="ISO-8859-1" standalone="yes"?>
<pdv_liste>
<pdv id="1000005" latitude="4620000" longitude="520000" cp="01000" pop="R">
<adresse>1 rue Test</adresse><ville>Béziers</ville>
<prix nom="Gazole" id="1" maj="2026-10-16 08:00:00" valeur="2.369"/>
<prix/>
</pdv>
<pdv id="1000006" latitude="0" longitude="0" cp="01000" pop="A">
<adresse></adresse><ville>Bourg</ville>
<prix nom="E10" id="5" maj="2026-10-16 09:00:00" valeur="2.182"/>
</pdv>
</pdv_liste>
"""


def make_zip(tmp_path):
    path = tmp_path / "test.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("data.xml", XML.encode("latin-1"))
    return path


def test_counts_and_order(tmp_path):
    items = list(parse_zip(make_zip(tmp_path)))
    assert sum(isinstance(i, RawStation) for i in items) == 2
    assert sum(isinstance(i, RawPrice) for i in items) == 3
    assert isinstance(items[0], RawStation)  # une station vient avant ses prix


def test_values_are_kept_as_text(tmp_path):
    items = list(parse_zip(make_zip(tmp_path)))
    assert items[0].station_id == "1000005"
    assert items[0].latitude == "4620000"
    assert items[0].city == "Béziers"  # accent ISO-8859-1 bien lu
    assert items[1] == RawPrice("1000005", "Gazole", "2026-10-16 08:00:00", "2.369")


def test_empty_price_node_is_kept_with_none(tmp_path):
    prices = [i for i in parse_zip(make_zip(tmp_path)) if isinstance(i, RawPrice)]
    assert prices[1] == RawPrice("1000005", None, None, None)


def test_empty_address_becomes_none(tmp_path):
    stations = [i for i in parse_zip(make_zip(tmp_path)) if isinstance(i, RawStation)]
    assert stations[1].address is None