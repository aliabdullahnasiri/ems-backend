"""Report routing, response contracts and CSV negotiation."""

import pytest

from tests.api.test_organizations import create
from tests.api.test_tenant_resources import select_org


@pytest.mark.asyncio
async def test_report_routes(client, identity):
    org = await create(client, "reports-http")
    await select_org(client, org)
    response = await client.post("/api/v1/shops", json={"name": "Report shop"})
    assert response.status_code == 201, response.text
    params = {"shop_id": response.json()["data"]["id"]}
    for path in (
        "dashboard",
        "reports/daily",
        "reports/cash",
        "reports/outstanding",
        "reports/profit",
        "reports/hawala",
        "reports/audit",
    ):
        response = await client.get(f"/api/v1/{path}", params=params)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["shop_name"] == "Report shop"
        response = await client.get(
            f"/api/v1/{path}", params={**params, "format": "csv"}
        )
        assert response.status_code == 200, response.text
        assert "text/csv" in response.headers["content-type"]
    response = await client.get(
        "/api/v1/reports/profit", params={**params, "cutoff": "2026-01-01T00:00:00"}
    )
    assert response.status_code == 422, response.text
