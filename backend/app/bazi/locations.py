"""Versioned static location lookup for true-solar-time correction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DATA_DIR = Path(__file__).with_name("data")
MAINLAND_DATA_PATH = DATA_DIR / "district_longitudes.json"
SPECIAL_REGION_DATA_PATH = DATA_DIR / "special_region_locations.json"
SUPPORTED_COORDINATE_MATCHES = frozenset(
    {
        "direct_code",
        "geonames_adm1_direct_id",
        "official_mca_api",
        "official_had_service_point",
        "official_boundary_derived_centroid",
    }
)


class LocationDataError(RuntimeError):
    """Raised when the bundled location snapshot is missing or invalid."""


class BirthplaceCoordinateError(ValueError):
    """Raised when a requested location ID is absent from a valid snapshot."""


@dataclass(frozen=True)
class DivisionPathItem:
    code: str
    name: str
    type: str


@dataclass(frozen=True)
class LocationRecord:
    location_id: str
    region_code: str
    timezone: str
    division_path: tuple[DivisionPathItem, ...]
    longitude: float
    latitude: float | None
    precision: str
    coordinate_match: str
    fallback: bool
    coordinate_source: str

    @property
    def display_name(self) -> str:
        return self.division_path[-1].name


def _read_payload(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as data_file:
            payload = json.load(data_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise LocationDataError(f"地点数据不可用：{path.name}") from exc
    if not isinstance(payload, dict):
        raise LocationDataError(f"地点数据格式无效：{path.name}")
    return payload


def _required_text(record: dict[str, Any], field: str, *, location_id: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise LocationDataError(f"地点记录 {location_id} 缺少有效字段：{field}")
    return value.strip()


def _coordinates(record: dict[str, Any], *, location_id: str) -> tuple[float, float | None]:
    try:
        longitude = float(record["longitude"])
        latitude_value = record.get("latitude")
        latitude = float(latitude_value) if latitude_value is not None else None
    except (KeyError, TypeError, ValueError) as exc:
        raise LocationDataError(f"地点记录 {location_id} 的坐标格式无效") from exc
    if not -180 <= longitude <= 180 or (latitude is not None and not -90 <= latitude <= 90):
        raise LocationDataError(f"地点记录 {location_id} 的坐标超出有效范围")
    return longitude, latitude


def _validate_timezone(timezone: str, *, location_id: str) -> None:
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise LocationDataError(
            f"地点记录 {location_id} 使用了无效的 IANA 时区：{timezone}"
        ) from exc


def _common_record_fields(
    record: dict[str, Any],
    *,
    location_id: str,
) -> tuple[float, float | None, str, str, bool, str]:
    longitude, latitude = _coordinates(record, location_id=location_id)
    precision = _required_text(record, "precision", location_id=location_id)
    coordinate_match = _required_text(record, "coordinate_match", location_id=location_id)
    coordinate_source = _required_text(record, "coordinate_source", location_id=location_id)
    if coordinate_match not in SUPPORTED_COORDINATE_MATCHES:
        raise LocationDataError(
            f"地点记录 {location_id} 使用了不受支持的坐标匹配方式：{coordinate_match}"
        )
    fallback = record.get("fallback")
    if not isinstance(fallback, bool):
        raise LocationDataError(f"地点记录 {location_id} 的 fallback 字段无效")
    if fallback or coordinate_match.endswith("_fallback"):
        raise LocationDataError(
            f"地点 {location_id} 缺少独立坐标，系统禁止加载回退坐标"
        )
    return (
        longitude,
        latitude,
        precision,
        coordinate_match,
        fallback,
        coordinate_source,
    )


def _mainland_records(payload: dict[str, Any]) -> dict[str, LocationRecord]:
    raw_records = payload.get("records")
    if not isinstance(raw_records, dict):
        raise LocationDataError("大陆行政区经纬度数据格式无效")

    locations: dict[str, LocationRecord] = {}
    for district_key, raw_record in raw_records.items():
        if not isinstance(raw_record, dict):
            raise LocationDataError(f"大陆行政区记录格式无效：{district_key}")
        district_code = _required_text(
            raw_record, "district_code", location_id=f"CN:{district_key}"
        )
        if str(district_key) != district_code:
            raise LocationDataError(f"大陆行政区记录键与代码不一致：{district_key}")
        location_id = f"CN:{district_code}"
        province_code = _required_text(raw_record, "province_code", location_id=location_id)
        province_name = _required_text(raw_record, "province_name", location_id=location_id)
        district_name = _required_text(raw_record, "district_name", location_id=location_id)
        division_path = [DivisionPathItem(province_code, province_name, "province")]

        city_code = raw_record.get("city_code")
        city_name = raw_record.get("city_name")
        if (city_code is None) != (city_name is None):
            raise LocationDataError(f"大陆行政区记录 {location_id} 的城市层级不完整")
        if city_code is not None:
            if (
                not isinstance(city_code, str)
                or not isinstance(city_name, str)
                or not city_name.strip()
            ):
                raise LocationDataError(f"大陆行政区记录 {location_id} 的城市层级无效")
            division_path.append(DivisionPathItem(city_code, city_name.strip(), "city"))
        division_path.append(DivisionPathItem(district_code, district_name, "district"))

        common = _common_record_fields(raw_record, location_id=location_id)
        locations[location_id] = LocationRecord(
            location_id=location_id,
            region_code="CN",
            timezone="Asia/Shanghai",
            division_path=tuple(division_path),
            longitude=common[0],
            latitude=common[1],
            precision=common[2],
            coordinate_match=common[3],
            fallback=common[4],
            coordinate_source=common[5],
        )
    return locations


def _iter_special_records(payload: dict[str, Any]) -> list[tuple[str | None, dict[str, Any]]]:
    raw_records = payload.get("records")
    if isinstance(raw_records, dict):
        records: list[tuple[str | None, dict[str, Any]]] = []
        for key, value in raw_records.items():
            if not isinstance(value, dict):
                raise LocationDataError(f"特别地区地点记录格式无效：{key}")
            records.append((str(key), value))
        return records
    if isinstance(raw_records, list):
        if not all(isinstance(value, dict) for value in raw_records):
            raise LocationDataError("特别地区地点记录列表格式无效")
        return [(None, value) for value in raw_records]
    raise LocationDataError("特别地区地点数据格式无效")


def _special_records(payload: dict[str, Any]) -> dict[str, LocationRecord]:
    locations: dict[str, LocationRecord] = {}
    validated_timezones: set[str] = set()
    for record_key, raw_record in _iter_special_records(payload):
        location_id = _required_text(raw_record, "location_id", location_id=record_key or "unknown")
        if record_key is not None and record_key != location_id:
            raise LocationDataError(
                f"特别地区地点记录键与 location_id 不一致：{record_key}"
            )
        if location_id in locations:
            raise LocationDataError(f"特别地区存在重复地点标识：{location_id}")
        region_code = _required_text(raw_record, "region_code", location_id=location_id)
        timezone = _required_text(raw_record, "timezone", location_id=location_id)
        if timezone not in validated_timezones:
            _validate_timezone(timezone, location_id=location_id)
            validated_timezones.add(timezone)

        raw_path = raw_record.get("division_path")
        if not isinstance(raw_path, list) or not raw_path:
            raise LocationDataError(f"地点记录 {location_id} 缺少有效 division_path")
        division_path: list[DivisionPathItem] = []
        for index, raw_item in enumerate(raw_path):
            if not isinstance(raw_item, dict):
                raise LocationDataError(
                    f"地点记录 {location_id} 的 division_path[{index}] 格式无效"
                )
            item_label = f"{location_id}.division_path[{index}]"
            division_path.append(
                DivisionPathItem(
                    code=_required_text(raw_item, "code", location_id=item_label),
                    name=_required_text(raw_item, "name", location_id=item_label),
                    type=_required_text(raw_item, "type", location_id=item_label),
                )
            )
        if division_path[0].code != region_code:
            raise LocationDataError(
                f"地点记录 {location_id} 的 division_path 根节点与 region_code 不一致"
            )
        if division_path[-1].code != location_id:
            raise LocationDataError(
                f"地点记录 {location_id} 的 division_path 末级代码与 location_id 不一致"
            )

        common = _common_record_fields(raw_record, location_id=location_id)
        locations[location_id] = LocationRecord(
            location_id=location_id,
            region_code=region_code,
            timezone=timezone,
            division_path=tuple(division_path),
            longitude=common[0],
            latitude=common[1],
            precision=common[2],
            coordinate_match=common[3],
            fallback=common[4],
            coordinate_source=common[5],
        )
    return locations


@lru_cache(maxsize=1)
def _load_location_data() -> dict[str, LocationRecord]:
    mainland_payload = _read_payload(MAINLAND_DATA_PATH)
    locations = _mainland_records(mainland_payload)

    special_payload = _read_payload(SPECIAL_REGION_DATA_PATH)
    for location_id, record in _special_records(special_payload).items():
        if location_id in locations:
            raise LocationDataError(f"地点标识重复：{location_id}")
        locations[location_id] = record
    return locations


def get_location(location_id: str) -> LocationRecord:
    record = _load_location_data().get(location_id)
    if record is None:
        raise BirthplaceCoordinateError(f"暂不支持该出生地点：{location_id}")
    if record.fallback or record.coordinate_match.endswith("_fallback"):
        raise LocationDataError(
            f"地点 {record.display_name}（{location_id}）缺少独立坐标，系统禁止使用回退坐标"
        )
    return record


def validate_location_data() -> int:
    """Load and validate the complete static index for readiness checks."""

    return len(_load_location_data())
