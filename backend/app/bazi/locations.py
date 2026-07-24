"""Versioned district coordinate lookup for true-solar-time correction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..schemas import Birthplace


DATA_PATH = Path(__file__).with_name("data") / "district_longitudes.json"


class BirthplaceCoordinateError(ValueError):
    """Raised when a birthplace cannot be resolved to a coordinate record."""


@dataclass(frozen=True)
class DistrictCoordinate:
    longitude: float
    latitude: float | None
    reference_meridian: float
    precision: str
    coordinate_match: str
    fallback: bool
    source: str
    source_name: str


@lru_cache(maxsize=1)
def _load_coordinate_data() -> dict[str, Any]:
    try:
        with DATA_PATH.open(encoding="utf-8") as data_file:
            payload = json.load(data_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise BirthplaceCoordinateError("行政区经度数据不可用") from exc

    records = payload.get("records")
    if not isinstance(records, dict):
        raise BirthplaceCoordinateError("行政区经度数据格式无效")
    return payload


def get_district_coordinate(birthplace: Birthplace) -> DistrictCoordinate:
    payload = _load_coordinate_data()
    record = payload["records"].get(birthplace.district_code)
    if not isinstance(record, dict):
        raise BirthplaceCoordinateError(
            f"暂不支持该出生地区：{birthplace.district_name}（{birthplace.district_code}）"
        )

    expected_birthplace = {
        "province_code": birthplace.province_code,
        "province_name": birthplace.province_name,
        "city_code": birthplace.city_code,
        "city_name": birthplace.city_name,
        "district_code": birthplace.district_code,
        "district_name": birthplace.district_name,
    }
    if any(record.get(field) != value for field, value in expected_birthplace.items()):
        raise BirthplaceCoordinateError("出生地名称或行政层级与官方区划不一致")

    try:
        longitude = float(record["longitude"])
        latitude_value = record.get("latitude")
        latitude = float(latitude_value) if latitude_value is not None else None
        reference_meridian = float(payload["standard_meridian_longitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BirthplaceCoordinateError("行政区经度记录格式无效") from exc

    if not -180 <= longitude <= 180 or (latitude is not None and not -90 <= latitude <= 90):
        raise BirthplaceCoordinateError("区县经纬度超出有效范围")
    if not -180 <= reference_meridian <= 180:
        raise BirthplaceCoordinateError("标准经线超出有效范围")

    coordinate_match = str(record.get("coordinate_match", ""))
    fallback = bool(record.get("fallback", False))
    if fallback or coordinate_match.endswith("_fallback"):
        raise BirthplaceCoordinateError(
            f"该出生地区缺少独立坐标，系统禁止使用回退坐标："
            f"{birthplace.district_name}（{birthplace.district_code}）"
        )

    source = str(record.get("coordinate_source", "")).strip()
    if not source:
        raise BirthplaceCoordinateError("行政区坐标来源缺失")

    return DistrictCoordinate(
        longitude=longitude,
        latitude=latitude,
        reference_meridian=reference_meridian,
        precision=str(record.get("precision", "district_center")),
        coordinate_match=coordinate_match,
        fallback=fallback,
        source=source,
        source_name=str(record.get("source_name", birthplace.district_name)),
    )
