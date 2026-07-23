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


def valid_payload() -> dict[str, object]:
    return {
        "local_datetime": "1990-01-01T12:00:00",
        "timezone": "Asia/Shanghai",
        "gender": "male",
    }


@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["engine_version"] == version("lunar-python")


@pytest.mark.asyncio
async def test_chart_preview_returns_known_chart_and_derived_data(client: AsyncClient) -> None:
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
    assert pillars["year"]["na_yin"] == "大林木"
    assert [item["symbol"] for item in pillars["year"]["earthly_branch"]["hidden_stems"]] == [
        "丙",
        "庚",
        "戊",
    ]
    assert pillars["year"]["heavenly_stem"]["ten_god"] == "伤官"
    assert data["chart"]["day_master"] == {
        "symbol": "丙",
        "element": "火",
        "polarity": "yang",
        "ten_god": "日主",
    }
    assert sum(data["chart"]["element_distribution"]["visible"].values()) == 8
    assert data["normalized_input"]["utc_datetime"] == "1990-01-01T04:00:00Z"
    assert data["calculation_policy"]["version"] == "v1"


@pytest.mark.asyncio
async def test_invalid_timezone_is_rejected(client: AsyncClient) -> None:
    payload = valid_payload() | {"timezone": "Mars/Olympus_Mons"}

    response = await client.post("/api/v1/charts/preview", json=payload)

    assert response.status_code == 422
    assert "有效的 IANA 时区" in response.text


@pytest.mark.asyncio
async def test_unsupported_policy_is_rejected_instead_of_silently_ignored(
    client: AsyncClient,
) -> None:
    payload = valid_payload() | {"calculation_policy": {"true_solar_time": True}}

    response = await client.post("/api/v1/charts/preview", json=payload)

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
