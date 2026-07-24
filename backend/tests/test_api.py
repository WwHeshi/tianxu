from datetime import datetime
from importlib.metadata import version
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


def guangzhou_birthplace() -> dict[str, str]:
    return {
        "country_code": "CN",
        "province_code": "440000",
        "province_name": "广东省",
        "city_code": "440100",
        "city_name": "广州市",
        "district_code": "440106",
        "district_name": "天河区",
    }


def beijing_chaoyang_birthplace() -> dict[str, str | None]:
    return {
        "country_code": "CN",
        "province_code": "110000",
        "province_name": "北京市",
        "city_code": None,
        "city_name": None,
        "district_code": "110105",
        "district_name": "朝阳区",
    }


def hainan_wuzhishan_birthplace() -> dict[str, str | None]:
    return {
        "country_code": "CN",
        "province_code": "460000",
        "province_name": "海南省",
        "city_code": None,
        "city_name": None,
        "district_code": "469001",
        "district_name": "五指山市",
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
    assert data["normalized_input"]["birthplace"] == guangzhou_birthplace()
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
    assert data["engine"]["solar_time_note"]


@pytest.mark.asyncio
async def test_beijing_district_without_city_level_is_supported(client: AsyncClient) -> None:
    birthplace = beijing_chaoyang_birthplace()
    response = await client.post(
        "/api/v1/charts/preview",
        json=valid_payload() | {"birthplace": birthplace},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["normalized_input"]["birthplace"] == birthplace
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
    assert data["normalized_input"]["birthplace"] == birthplace
    assert data["solar_time_adjustment"]["longitude_degrees"] == pytest.approx(109.516784)
    assert data["solar_time_adjustment"]["coordinate_match"] == "direct_code"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "birthplace",
    [
        beijing_chaoyang_birthplace() | {"district_name": "伪造朝阳区"},
        beijing_chaoyang_birthplace() | {"city_code": "110100", "city_name": "北京市"},
    ],
    ids=["forged-name", "forged-city-hierarchy"],
)
async def test_forged_birthplace_name_or_hierarchy_is_rejected(
    client: AsyncClient,
    birthplace: dict[str, str | None],
) -> None:
    response = await client.post(
        "/api/v1/charts/preview",
        json=valid_payload() | {"birthplace": birthplace},
    )

    assert response.status_code == 422
    assert "官方区划不一致" in response.text


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
    unknown_birthplace = guangzhou_birthplace() | {
        "district_code": "440999",
        "district_name": "不存在区",
    }
    response = await client.post(
        "/api/v1/charts/preview",
        json=valid_payload() | {"birthplace": unknown_birthplace},
    )

    assert response.status_code == 422
    assert "暂不支持该出生地区" in response.text
    assert "440999" in response.text


@pytest.mark.asyncio
async def test_policy_cannot_disable_true_solar_time(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/charts/preview",
        json=valid_payload() | {"calculation_policy": {"true_solar_time": False}},
    )

    assert response.status_code == 422


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
