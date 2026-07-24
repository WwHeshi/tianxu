import json
import re
from pathlib import Path

DATA_PATH = Path(__file__).parents[1] / "app" / "bazi" / "data" / "district_longitudes.json"


def test_coordinate_snapshot_is_internally_consistent() -> None:
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    records = payload["records"]
    coverage = payload["coverage"]

    assert payload["schema_version"] == "tianxu.mainland-district-longitudes.v3"
    assert payload["lookup_key"] == "district_code"
    assert payload["standard_meridian_longitude"] == 120
    assert len(records) == coverage["total"] == 2849
    assert coverage == {
        "total": 2849,
        "direct_code": 2841,
        "official_mca_api": 8,
        "fallback": 0,
    }
    assert sum(value for key, value in coverage.items() if key != "total") == len(records)

    for key, record in records.items():
        assert key == record["district_code"]
        assert record["province_code"][:2] == record["district_code"][:2]
        assert (record["city_code"] is None) is (record["city_name"] is None)
        if record["city_code"] is not None:
            assert record["city_code"][:2] == record["province_code"][:2]
        assert 73 <= record["longitude"] <= 136
        assert 3 <= record["latitude"] <= 54
        assert record["precision"] in {"district_center", "city_center"}
        assert record["coordinate_match"] in {
            "direct_code",
            "official_mca_api",
        }
        assert record["fallback"] is record["coordinate_match"].endswith("_fallback")
        assert record["coordinate_source"].strip()


def test_coordinate_snapshot_contains_no_fallback_records() -> None:
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))

    fallback_records = [
        record["district_code"]
        for record in payload["records"].values()
        if record["fallback"]
    ]

    assert fallback_records == []


def test_official_mca_coordinates_cover_all_non_direct_records() -> None:
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    official_records = {
        district_code: record
        for district_code, record in payload["records"].items()
        if record["coordinate_match"] == "official_mca_api"
    }

    assert set(official_records) == {
        "460302",
        "460303",
        "500157",
        "540481",
        "540581",
        "653228",
        "653229",
        "659012",
    }
    for record in official_records.values():
        assert record["fallback"] is False
        assert record["coordinate_source"].strip()


def test_known_district_coordinate_is_stable() -> None:
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    tianhe = payload["records"]["440106"]

    assert tianhe["district_name"] == "天河区"
    assert tianhe["longitude"] == 113.361597
    assert tianhe["latitude"] == 23.124817
    assert tianhe["precision"] == "district_center"


def test_direct_administered_divisions_have_no_synthetic_city_level() -> None:
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))

    for district_code in ("110105", "469001"):
        record = payload["records"][district_code]
        assert record["city_code"] is None
        assert record["city_name"] is None
        assert record["coordinate_match"] == "direct_code"


def test_statistical_development_zone_is_not_in_official_snapshot() -> None:
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    forbidden_functional_area = re.compile(r"开发区|产业园|管理区|示范区|风景名胜区|生态园|高新区")

    assert "130171" not in payload["records"]
    assert not [
        record["district_name"]
        for record in payload["records"].values()
        if forbidden_functional_area.search(record["district_name"])
    ]
