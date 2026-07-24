import json
import re
from pathlib import Path

import pytest

from app.bazi import locations

DATA_PATH = Path(__file__).parents[1] / "app" / "bazi" / "data" / "district_longitudes.json"
SPECIAL_DATA_PATH = (
    Path(__file__).parents[1] / "app" / "bazi" / "data" / "special_region_locations.json"
)
FRONTEND_SPECIAL_OPTIONS_PATH = (
    Path(__file__).parents[2] / "frontend" / "lib" / "special-region-options.json"
)


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
        "direct_code": 2839,
        "official_mca_api": 10,
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
        "150204",
        "460302",
        "460303",
        "500157",
        "540481",
        "540581",
        "653221",
        "653228",
        "653229",
        "659012",
    }
    for record in official_records.values():
        assert record["fallback"] is False
        assert record["coordinate_source"].strip()


def test_corrected_mainland_coordinates_are_stable() -> None:
    records = json.loads(DATA_PATH.read_text(encoding="utf-8"))["records"]
    baotou_qingshan = records["150204"]
    hotan_county = records["653221"]

    assert baotou_qingshan["longitude"] == pytest.approx(109.897026602433)
    assert baotou_qingshan["latitude"] == pytest.approx(40.642914138635)
    assert baotou_qingshan["coordinate_match"] == "official_mca_api"
    assert (baotou_qingshan["longitude"], baotou_qingshan["latitude"]) != (
        records["420107"]["longitude"],
        records["420107"]["latitude"],
    )
    assert hotan_county["longitude"] == pytest.approx(79.9340833)
    assert hotan_county["latitude"] == pytest.approx(37.1072882)
    assert hotan_county["coordinate_match"] == "official_mca_api"


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


def test_special_region_snapshot_has_complete_zero_fallback_coverage() -> None:
    payload = json.loads(SPECIAL_DATA_PATH.read_text(encoding="utf-8"))
    records = payload["records"]

    assert payload["schema_version"] == "tianxu.special-region-locations.v1"
    assert len(records) == payload["coverage"]["total"] == 394
    assert payload["coverage"] == {
        "total": 394,
        "hong_kong_district": 18,
        "macau_geographic_area": 8,
        "taiwan_township": 368,
        "fallback": 0,
    }
    assert {record["region_code"] for record in records.values()} == {
        "CN-HK",
        "CN-MO",
        "CN-TW",
    }
    assert {record["timezone"] for record in records.values()} == {
        "Asia/Hong_Kong",
        "Asia/Macau",
        "Asia/Taipei",
    }
    macau_source = payload["sources"]["macau"]
    assert macau_source["license"] == "Creative Commons Attribution 4.0"
    assert macau_source["license_url"] == "https://creativecommons.org/licenses/by/4.0/"
    assert macau_source["url_template"] == (
        "https://sws.geonames.org/{geonamesId}/about.rdf"
    )
    assert macau_source["attribution"].startswith("GeoNames")
    assert len(macau_source["source_sha256"]) == 64
    for location_id, record in records.items():
        assert record["location_id"] == location_id
        assert record["division_path"][-1]["code"] == location_id
        assert record["coordinate_crs"] == "EPSG:4326"
        assert record["coordinate_source"].strip()
        assert record["fallback"] is False
        assert not record["coordinate_match"].endswith("_fallback")


def test_special_region_snapshot_uses_expected_identity_sets() -> None:
    records = json.loads(SPECIAL_DATA_PATH.read_text(encoding="utf-8"))["records"]
    hong_kong = [record for record in records.values() if record["region_code"] == "CN-HK"]
    macau = [record for record in records.values() if record["region_code"] == "CN-MO"]
    taiwan = [record for record in records.values() if record["region_code"] == "CN-TW"]

    assert {record["official_area_id"] for record in hong_kong} == set("ABCDEFGHJKLMNPQRST")
    assert len({record["official_admin_area_id"] for record in hong_kong}) == 18
    assert {
        record["location_id"]: (
            record["geonames_id"],
            record["division_path"][-1]["name"],
            record["division_path"][-1]["type"],
        )
        for record in macau
    } == {
        "CN-MO:AREA:01": (11875154, "花地玛堂区", "traditional_parish"),
        "CN-MO:AREA:02": (11875155, "圣安多尼堂区", "traditional_parish"),
        "CN-MO:AREA:03": (11875157, "大堂区", "traditional_parish"),
        "CN-MO:AREA:04": (11875156, "望德堂区", "traditional_parish"),
        "CN-MO:AREA:05": (11875158, "风顺堂区", "traditional_parish"),
        "CN-MO:AREA:06": (11875159, "嘉模堂区", "traditional_parish"),
        "CN-MO:AREA:07": (11875160, "路氹城", "geographic_area"),
        "CN-MO:AREA:08": (11875161, "圣方济各堂区", "traditional_parish"),
    }
    assert all(record["coordinate_match"] == "geonames_adm1_direct_id" for record in macau)
    assert all(
        record["source_license"] == "Creative Commons Attribution 4.0" for record in macau
    )
    assert len({record["official_town_code"] for record in taiwan}) == 368
    assert len({record["official_county_code"] for record in taiwan}) == 22


def test_restricted_macau_statistical_zone_source_is_not_bundled() -> None:
    source = SPECIAL_DATA_PATH.read_text(encoding="utf-8")

    assert "CN-MO:STAT:" not in source
    assert "webmap.gis.gov.mo" not in source
    assert "official_dsscu_webmap_boundary_centroid" not in source


@pytest.mark.parametrize(
    ("region_code", "longitude_bounds", "latitude_bounds", "path_length"),
    [
        ("CN-HK", (113.8, 114.5), (22.1, 22.6), 2),
        ("CN-MO", (113.4, 113.7), (22.0, 22.3), 2),
        ("CN-TW", (118.0, 123.0), (21.8, 26.5), 3),
    ],
)
def test_special_region_coordinates_and_hierarchy_are_valid(
    region_code: str,
    longitude_bounds: tuple[float, float],
    latitude_bounds: tuple[float, float],
    path_length: int,
) -> None:
    records = json.loads(SPECIAL_DATA_PATH.read_text(encoding="utf-8"))["records"]
    matching = [record for record in records.values() if record["region_code"] == region_code]

    assert matching
    for record in matching:
        assert longitude_bounds[0] <= record["longitude"] <= longitude_bounds[1]
        assert latitude_bounds[0] <= record["latitude"] <= latitude_bounds[1]
        assert len(record["division_path"]) == path_length
        assert record["division_path"][0]["code"] == region_code


def test_known_special_region_coordinates_are_stable() -> None:
    records = json.loads(SPECIAL_DATA_PATH.read_text(encoding="utf-8"))["records"]

    assert records["CN-HK:DCD:A"]["longitude"] == pytest.approx(114.15491485)
    assert records["CN-HK:DCD:A"]["coordinate_source_objectid"] == 19
    assert records["CN-MO:AREA:01"]["division_path"][-1]["name"] == "花地玛堂区"
    assert records["CN-MO:AREA:01"]["longitude"] == pytest.approx(113.54537)
    assert records["CN-MO:AREA:01"]["coordinate_match"] == "geonames_adm1_direct_id"
    assert records["CN-TW:TOWN:63000050"]["division_path"][-1]["name"] == "中正区"
    assert records["CN-TW:TOWN:63000050"]["longitude"] == pytest.approx(121.51984015)


def test_frontend_special_region_options_match_backend_location_ids() -> None:
    backend_records = json.loads(SPECIAL_DATA_PATH.read_text(encoding="utf-8"))["records"]
    options = json.loads(FRONTEND_SPECIAL_OPTIONS_PATH.read_text(encoding="utf-8"))

    def terminal_ids(nodes: list[dict[str, object]]) -> set[str]:
        result: set[str] = set()
        for node in nodes:
            location_id = node["location_id"]
            children = node["children"]
            assert isinstance(children, list)
            if location_id is not None:
                assert isinstance(location_id, str)
                assert children == []
                result.add(location_id)
            result.update(terminal_ids(children))
        return result

    assert options["schema_version"] == "tianxu.special-region-options.v1"
    assert terminal_ids(options["provinces"]) == set(backend_records)


def test_runtime_location_index_contains_every_static_record() -> None:
    runtime_records = locations._load_location_data()

    assert len(runtime_records) == 3243
    assert sum(record.region_code == "CN" for record in runtime_records.values()) == 2849
    assert sum(record.region_code != "CN" for record in runtime_records.values()) == 394
    assert all(record.fallback is False for record in runtime_records.values())


def special_location_record() -> dict[str, object]:
    return {
        "location_id": "CN-HK:DCD:A",
        "region_code": "CN-HK",
        "timezone": "Asia/Hong_Kong",
        "division_path": [
            {"code": "CN-HK", "name": "香港特别行政区", "type": "region"},
            {"code": "CN-HK:DCD:A", "name": "中西区", "type": "district"},
        ],
        "longitude": 114.15,
        "latitude": 22.28,
        "precision": "administrative_area_centroid",
        "coordinate_match": "official_had_service_point",
        "fallback": False,
        "coordinate_source": "test-official-source",
    }


@pytest.mark.parametrize("container", ["dict", "list"])
def test_special_location_loader_accepts_versioned_dict_and_list_records(container: str) -> None:
    record = special_location_record()
    records: object = {record["location_id"]: record} if container == "dict" else [record]

    loaded = locations._special_records({"records": records})

    location = loaded["CN-HK:DCD:A"]
    assert location.region_code == "CN-HK"
    assert location.timezone == "Asia/Hong_Kong"
    assert [part.name for part in location.division_path] == ["香港特别行政区", "中西区"]
    assert location.fallback is False


@pytest.mark.parametrize(
    ("coordinate_match", "fallback"),
    [
        ("regional_center_fallback", True),
        ("parent_center", False),
    ],
)
def test_special_location_loader_rejects_fallback_coordinates(
    coordinate_match: str,
    fallback: bool,
) -> None:
    record = special_location_record() | {
        "coordinate_match": coordinate_match,
        "fallback": fallback,
    }

    with pytest.raises(locations.LocationDataError, match=r"回退|不受支持"):
        locations._special_records({"records": {"CN-HK:DCD:A": record}})


@pytest.mark.parametrize(
    "division_path",
    [
        [
            {"code": "wrong-region", "name": "香港特别行政区", "type": "region"},
            {"code": "CN-HK:DCD:A", "name": "中西区", "type": "district"},
        ],
        [
            {"code": "CN-HK", "name": "香港特别行政区", "type": "region"},
            {"code": "wrong-location", "name": "中西区", "type": "district"},
        ],
    ],
)
def test_special_location_loader_rejects_mismatched_division_path(
    division_path: list[dict[str, str]],
) -> None:
    record = special_location_record() | {"division_path": division_path}

    with pytest.raises(locations.LocationDataError, match=r"不一致"):
        locations._special_records({"records": {"CN-HK:DCD:A": record}})


def test_missing_static_location_snapshot_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(locations.LocationDataError, match=r"地点数据不可用"):
        locations._read_payload(tmp_path / "missing.json")
