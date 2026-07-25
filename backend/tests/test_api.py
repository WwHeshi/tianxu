from datetime import datetime
from importlib.metadata import version
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api import routes
from app.bazi.locations import LocationDataError
from app.main import app


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


def guangzhou_birthplace() -> dict[str, str]:
    return {"location_id": "CN:440106"}


def beijing_chaoyang_birthplace() -> dict[str, str]:
    return {"location_id": "CN:110105"}


def hainan_wuzhishan_birthplace() -> dict[str, str]:
    return {"location_id": "CN:469001"}


def canonical_birthplace(
    location_id: str,
    *path: tuple[str, str, str],
) -> dict[str, object]:
    return {
        "location_id": location_id,
        "region_code": "CN",
        "timezone": "Asia/Shanghai",
        "division_path": [
            {"code": code, "name": name, "type": division_type}
            for code, name, division_type in path
        ],
    }


def valid_payload() -> dict[str, object]:
    return {
        "beijing_datetime": "1990-01-01T12:00:00",
        "birthplace": guangzhou_birthplace(),
        "gender": "male",
    }


@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["engine_version"] == version("lunar-python")


@pytest.mark.asyncio
async def test_health_reports_unavailable_location_data(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> int:
        raise LocationDataError("地点数据不可用：special_region_locations.json")

    monkeypatch.setattr(routes, "validate_location_data", unavailable)

    response = await client.get("/api/v1/health")

    assert response.status_code == 503
    assert "地点数据不可用" in response.text


@pytest.mark.asyncio
async def test_preview_returns_chart_and_true_solar_metadata(client: AsyncClient) -> None:
    response = await client.post("/api/v1/charts/preview", json=valid_payload())

    assert response.status_code == 200
    data = response.json()
    pillars = data["chart"]["pillars"]
    assert {name: pillar["gan_zhi"] for name, pillar in pillars.items()} == {
        "year": "己巳",
        "month": "丙子",
        "day": "丙寅",
        "hour": "甲午",
    }
    assert data["normalized_input"]["beijing_datetime"] == "1990-01-01T12:00:00"
    assert data["normalized_input"]["calendar_type"] == "solar"
    assert data["normalized_input"]["lunar_date"] is None
    assert data["normalized_input"]["birthplace"] == canonical_birthplace(
        "CN:440106",
        ("440000", "广东省", "province"),
        ("440100", "广州市", "city"),
        ("440106", "天河区", "district"),
    )
    assert data["calculation_policy"] == {
        "version": "v1",
        "year_boundary": "lichun",
        "month_boundary": "solar_terms",
        "day_boundary": "midnight",
        "time_basis": "beijing_standard_time",
        "true_solar_time": True,
    }
    adjustment = data["solar_time_adjustment"]
    assert adjustment["longitude_degrees"] == pytest.approx(113.361597)
    assert adjustment["reference_meridian_degrees"] == 120
    assert adjustment["longitude_correction_minutes"] < 0
    assert adjustment["total_correction_minutes"] == pytest.approx(
        adjustment["longitude_correction_minutes"] + adjustment["equation_of_time_minutes"]
    )
    assert datetime.fromisoformat(
        data["normalized_input"]["true_solar_datetime"]
    ) < datetime.fromisoformat(data["normalized_input"]["beijing_datetime"])
    assert data["chart"]["calendar"]["solar_datetime"] == data["normalized_input"][
        "true_solar_datetime"
    ]
    assert data["chart"]["calendar"]["destiny_type"] == "乾造"
    assert data["chart"]["calendar"]["lunar_text"]
    for pillar in pillars.values():
        assert {
            "growth_stage",
            "self_growth_stage",
            "xun_kong",
            "na_yin",
            "shen_sha",
        } <= pillar.keys()
    assert data["engine"]["shen_sha_policy_version"] == "v2"
    assert data["engine"]["solar_time_note"]


@pytest.mark.asyncio
async def test_lunar_calendar_input_returns_normalized_contract(client: AsyncClient) -> None:
    payload = valid_payload() | {
        "beijing_datetime": "2024-02-10T12:00:00",
        "calendar_type": "lunar",
        "lunar_date": {
            "year": 2024,
            "month": 1,
            "day": 1,
            "is_leap_month": False,
        },
    }

    response = await client.post("/api/v1/charts/preview", json=payload)

    assert response.status_code == 200
    normalized = response.json()["normalized_input"]
    assert normalized["beijing_datetime"] == "2024-02-10T12:00:00"
    assert normalized["calendar_type"] == "lunar"
    assert normalized["lunar_date"] == {
        "year": 2024,
        "month": 1,
        "day": 1,
        "is_leap_month": False,
    }


@pytest.mark.asyncio
async def test_leap_lunar_month_is_supported(client: AsyncClient) -> None:
    payload = valid_payload() | {
        "beijing_datetime": "2023-03-22T12:00:00",
        "calendar_type": "lunar",
        "lunar_date": {
            "year": 2023,
            "month": 2,
            "day": 1,
            "is_leap_month": True,
        },
    }

    response = await client.post("/api/v1/charts/preview", json=payload)

    assert response.status_code == 200
    assert response.json()["normalized_input"]["lunar_date"]["is_leap_month"] is True


@pytest.mark.asyncio
async def test_nonexistent_lunar_leap_month_is_rejected(client: AsyncClient) -> None:
    payload = valid_payload() | {
        "beijing_datetime": "2024-03-10T12:00:00",
        "calendar_type": "lunar",
        "lunar_date": {
            "year": 2024,
            "month": 2,
            "day": 1,
            "is_leap_month": True,
        },
    }

    response = await client.post("/api/v1/charts/preview", json=payload)

    assert response.status_code == 422
    assert "不存在所选闰月" in response.text


@pytest.mark.asyncio
async def test_nonexistent_lunar_day_is_rejected(client: AsyncClient) -> None:
    payload = valid_payload() | {
        "beijing_datetime": "2024-03-10T12:00:00",
        "calendar_type": "lunar",
        "lunar_date": {
            "year": 2024,
            "month": 1,
            "day": 30,
            "is_leap_month": False,
        },
    }

    response = await client.post("/api/v1/charts/preview", json=payload)

    assert response.status_code == 422
    assert "无效的农历日期" in response.text


@pytest.mark.asyncio
async def test_lunar_and_converted_solar_dates_must_match(client: AsyncClient) -> None:
    payload = valid_payload() | {
        "beijing_datetime": "2024-02-11T12:00:00",
        "calendar_type": "lunar",
        "lunar_date": {
            "year": 2024,
            "month": 1,
            "day": 1,
            "is_leap_month": False,
        },
    }

    response = await client.post("/api/v1/charts/preview", json=payload)

    assert response.status_code == 422
    assert "2024-02-10" in response.text
    assert "2024-02-11" in response.text


@pytest.mark.asyncio
async def test_solar_calendar_input_cannot_include_lunar_date(client: AsyncClient) -> None:
    payload = valid_payload() | {
        "calendar_type": "solar",
        "lunar_date": {
            "year": 1989,
            "month": 12,
            "day": 5,
            "is_leap_month": False,
        },
    }

    response = await client.post("/api/v1/charts/preview", json=payload)

    assert response.status_code == 422
    assert "公历输入时禁止提供 lunar_date" in response.text


@pytest.mark.asyncio
async def test_lunar_calendar_input_requires_lunar_date(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/charts/preview",
        json=valid_payload() | {"calendar_type": "lunar"},
    )

    assert response.status_code == 422
    assert "农历输入时必须提供 lunar_date" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lunar_date",
    [
        {"year": 2024, "month": 13, "day": 1, "is_leap_month": False},
        {"year": 2024, "month": 1, "day": 31, "is_leap_month": False},
    ],
)
async def test_lunar_date_structure_is_bounded(
    client: AsyncClient,
    lunar_date: dict[str, object],
) -> None:
    response = await client.post(
        "/api/v1/charts/preview",
        json=valid_payload()
        | {
            "calendar_type": "lunar",
            "lunar_date": lunar_date,
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "optional_fields",
    [
        {},
        {"birthplace": None},
        {"birthplace": None, "calculation_policy": {}},
        {"birthplace": None, "calculation_policy": {"true_solar_time": False}},
    ],
    ids=["birthplace-omitted", "birthplace-null", "empty-policy", "explicit-false"],
)
async def test_preview_without_birthplace_uses_beijing_time_directly(
    client: AsyncClient,
    optional_fields: dict[str, object],
) -> None:
    payload = {
        "beijing_datetime": "1990-01-01T12:00:00",
        "gender": "male",
    } | optional_fields

    response = await client.post("/api/v1/charts/preview", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["calculation_policy"]["true_solar_time"] is False
    assert data["normalized_input"]["birthplace"] is None
    assert data["normalized_input"]["beijing_datetime"] == "1990-01-01T12:00:00"
    assert data["normalized_input"]["true_solar_datetime"] == "1990-01-01T12:00:00"
    assert data["solar_time_adjustment"] is None
    assert data["warnings"] == []
    assert "未选择出生地点" in data["engine"]["solar_time_note"]


@pytest.mark.asyncio
async def test_beijing_district_without_city_level_is_supported(client: AsyncClient) -> None:
    birthplace = beijing_chaoyang_birthplace()
    response = await client.post(
        "/api/v1/charts/preview",
        json=valid_payload() | {"birthplace": birthplace},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["normalized_input"]["birthplace"] == canonical_birthplace(
        "CN:110105",
        ("110000", "北京市", "province"),
        ("110105", "朝阳区", "district"),
    )
    assert data["solar_time_adjustment"]["longitude_degrees"] == pytest.approx(116.443136)
    assert data["solar_time_adjustment"]["coordinate_match"] == "direct_code"


@pytest.mark.asyncio
async def test_hainan_direct_administered_county_is_supported(client: AsyncClient) -> None:
    birthplace = hainan_wuzhishan_birthplace()
    response = await client.post(
        "/api/v1/charts/preview",
        json=valid_payload() | {"birthplace": birthplace},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["normalized_input"]["birthplace"] == canonical_birthplace(
        "CN:469001",
        ("460000", "海南省", "province"),
        ("469001", "五指山市", "district"),
    )
    assert data["solar_time_adjustment"]["longitude_degrees"] == pytest.approx(109.516784)
    assert data["solar_time_adjustment"]["coordinate_match"] == "direct_code"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("location_id", "region_code", "timezone"),
    [
        ("CN-HK:DCD:A", "CN-HK", "Asia/Hong_Kong"),
        ("CN-MO:AREA:01", "CN-MO", "Asia/Macau"),
        ("CN-TW:TOWN:10014020", "CN-TW", "Asia/Taipei"),
    ],
)
async def test_special_region_location_can_preview_chart(
    client: AsyncClient,
    location_id: str,
    region_code: str,
    timezone: str,
) -> None:
    response = await client.post(
        "/api/v1/charts/preview",
        json=valid_payload() | {"birthplace": {"location_id": location_id}},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["normalized_input"]["birthplace"]["location_id"] == location_id
    assert data["normalized_input"]["birthplace"]["region_code"] == region_code
    assert data["normalized_input"]["birthplace"]["timezone"] == timezone
    assert data["solar_time_adjustment"]["reference_meridian_degrees"] == 120


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_field", ["district_name", "district_code", "province_code"])
async def test_birthplace_accepts_only_location_id(
    client: AsyncClient, legacy_field: str
) -> None:
    birthplace = beijing_chaoyang_birthplace() | {legacy_field: "伪造值"}
    response = await client.post(
        "/api/v1/charts/preview",
        json=valid_payload() | {"birthplace": birthplace},
    )

    assert response.status_code == 422
    assert legacy_field in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_field", ["local_datetime", "timezone", "longitude"])
async def test_legacy_fields_are_rejected(client: AsyncClient, legacy_field: str) -> None:
    legacy_value: object = {
        "local_datetime": "1990-01-01T12:00:00",
        "timezone": "Asia/Shanghai",
        "longitude": 113.361597,
    }[legacy_field]
    payload = valid_payload() | {legacy_field: legacy_value}

    response = await client.post("/api/v1/charts/preview", json=payload)

    assert response.status_code == 422
    assert legacy_field in response.text


@pytest.mark.asyncio
async def test_beijing_datetime_with_offset_is_rejected(client: AsyncClient) -> None:
    payload = valid_payload() | {"beijing_datetime": "1990-01-01T12:00:00+08:00"}

    response = await client.post("/api/v1/charts/preview", json=payload)

    assert response.status_code == 422
    assert "北京时间" in response.text


@pytest.mark.asyncio
async def test_unknown_district_is_rejected(client: AsyncClient) -> None:
    unknown_birthplace = {"location_id": "CN:440999"}
    response = await client.post(
        "/api/v1/charts/preview",
        json=valid_payload() | {"birthplace": unknown_birthplace},
    )

    assert response.status_code == 422
    assert "暂不支持该出生地点" in response.text
    assert "440999" in response.text


@pytest.mark.asyncio
async def test_removed_macau_statistical_zone_id_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/charts/preview",
        json=valid_payload() | {"birthplace": {"location_id": "CN-MO:STAT:01"}},
    )

    assert response.status_code == 422
    assert "暂不支持该出生地点" in response.text
    assert "CN-MO:STAT:01" in response.text


@pytest.mark.asyncio
async def test_birthplace_cannot_disable_true_solar_time(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/charts/preview",
        json=valid_payload() | {"calculation_policy": {"true_solar_time": False}},
    )

    assert response.status_code == 422
    assert "已选择出生地点" in response.text


@pytest.mark.asyncio
async def test_missing_birthplace_cannot_enable_true_solar_time(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/charts/preview",
        json={
            "beijing_datetime": "1990-01-01T12:00:00",
            "birthplace": None,
            "gender": "male",
            "calculation_policy": {"true_solar_time": True},
        },
    )

    assert response.status_code == 422
    assert "未选择出生地点" in response.text


@pytest.mark.asyncio
async def test_repeated_requests_are_deterministic(client: AsyncClient) -> None:
    first = await client.post("/api/v1/charts/preview", json=valid_payload())
    second = await client.post("/api/v1/charts/preview", json=valid_payload())

    assert first.status_code == second.status_code == 200
    assert first.content == second.content


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["http://localhost:3000", "http://127.0.0.1:3000"])
async def test_local_frontend_origins_are_allowed(client: AsyncClient, origin: str) -> None:
    response = await client.options(
        "/api/v1/charts/preview",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
