"""Phase 11 HTTP permissions, schemas, receipts and closed-period behavior."""

from datetime import UTC, datetime

import pytest

from app.services.financial_values import business_day
from tests.api.test_organizations import create
from tests.api.test_tenant_resources import select_org


@pytest.mark.asyncio
async def test_daily_http_workflow(client, identity):
    org = await create(client, "daily-http")
    await select_org(client, org)
    shop_response = await client.post("/api/v1/shops", json={"name": "Daily shop"})
    assert shop_response.status_code == 201, shop_response.text
    params = {"shop_id": shop_response.json()["data"]["id"]}

    async def post(path, data, key=None, expected=201):
        response = await client.post(
            "/api/v1/" + path,
            params=params,
            json=data,
            headers={"Idempotency-Key": key or path},
        )
        assert response.status_code == expected, response.text
        return response.json().get("data")

    currency = await post("currencies", {"code": "AFN", "precision": 2})
    source = await post("cashboxes", {"name": "Till"})
    destination = await post("cashboxes", {"name": "Safe"})
    category = await post("expense-categories", {"name": "Salary"})
    await post(
        "opening-balances",
        {
            "cashbox_id": source["id"],
            "currency_id": currency["id"],
            "amount": "1000.00",
            "book_afn": "1000.00",
            "owner_approved": True,
            "reason": "Initial",
        },
    )
    await post(
        "business-days/open", {"day": business_day(datetime.now(UTC)).isoformat()}
    )
    expense_payload = {
        "category_id": category["id"],
        "cashbox_id": source["id"],
        "currency_id": currency["id"],
        "amount": "100.00",
        "expense_date": business_day(datetime.now(UTC)).isoformat(),
        "payee": "کارمند",
        "explanation": "Salary",
    }
    expense = await post("expenses", expense_payload)
    assert await post("expenses", expense_payload) == expense
    await post(f"expenses/{expense['uuid']}/reverse", {"reason": "Correct salary"})
    transfer = await post(
        "cash-transfers",
        {
            "source_cashbox_id": source["id"],
            "destination_cashbox_id": destination["id"],
            "currency_id": currency["id"],
            "amount": "200.00",
            "reason": "Safe deposit",
        },
    )
    await post(f"cash-transfers/{transfer['uuid']}/reverse", {"reason": "Correct till"})
    for path in (
        "expenses",
        "cash-transfers",
        "expense-categories",
        "business-days/current",
    ):
        response = await client.get("/api/v1/" + path, params=params)
        assert response.status_code == 200, response.text
    counts = [
        {
            "cashbox_id": source["id"],
            "currency_id": currency["id"],
            "counted": "1000.00",
        },
        {
            "cashbox_id": destination["id"],
            "currency_id": currency["id"],
            "counted": "0.00",
        },
    ]
    draft = await post("day-closings", {"counts": counts})
    await post("expenses", expense_payload, key="frozen-expense", expected=409)
    await post(
        f"day-closings/{draft['uuid']}/confirm",
        {"owner_approved": False},
        key="not-approved",
        expected=403,
    )
    await post(f"day-closings/{draft['uuid']}/confirm", {"owner_approved": True})
    for path in (
        "day-closings",
        f"day-closings/{draft['uuid']}",
        f"day-closings/{draft['uuid']}/export",
    ):
        response = await client.get("/api/v1/" + path, params=params)
        assert response.status_code == 200, response.text
    await post(
        "business-days/open",
        {"day": business_day(datetime.now(UTC)).isoformat()},
        key="duplicate-day",
        expected=409,
    )
    response = await client.patch(
        f"/api/v1/expense-categories/{category['uuid']}",
        params=params,
        json={"name": "Salary", "is_active": False},
    )
    assert response.status_code == 200, response.text
    client.headers.pop("Authorization", None)
    response = await client.get("/api/v1/day-closings", params=params)
    assert response.status_code == 401, response.text
