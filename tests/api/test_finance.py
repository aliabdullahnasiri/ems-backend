"""HTTP contracts for Phase 6, using the selected authenticated organization."""

import pytest
from sqlalchemy import select

from app.models import Role
from app.services.organization import OrganizationService
from app.services.role import RoleService
from tests.api.test_organizations import create
from tests.api.test_tenant_resources import select_org


@pytest.mark.asyncio
async def test_finance_http_workflow_retries_validation_and_isolation(client, identity):
    org = await create(client, "finance-http")
    await select_org(client, org)
    response = await client.post("/api/v1/shops", json={"name": "دکان"})
    assert response.status_code == 201, response.text
    shop = response.json()["data"]
    params = {"shop_id": shop["id"]}
    response = await client.patch(
        "/api/v1/settings",
        params=params,
        json={"preferred_language": "ps", "receipt_footer": "مننه"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["receipt_footer"] == "مننه"
    for code in ("AFN", "USD", "PKR"):
        response = await client.post(
            "/api/v1/currencies", json={"code": code, "precision": 2}
        )
        assert response.status_code == 201, response.text
    currencies = (await client.get("/api/v1/currencies")).json()["data"]
    usd = next(c for c in currencies if c["code"] == "USD")
    response = await client.post(
        "/api/v1/cashboxes", params=params, json={"name": "نقد"}
    )
    assert response.status_code == 201, response.text
    box = response.json()["data"]
    payload = {
        "cashbox_id": box["id"],
        "currency_id": usd["id"],
        "amount": "۱۰۰٫۰۰",
        "book_afn": "۷۰۰۰٫۰۰",
        "owner_approved": True,
        "reason": "بررسی",
    }
    response = await client.post(
        "/api/v1/opening-balances", params=params, json=payload
    )
    assert response.status_code == 422, response.text
    headers = {"Idempotency-Key": "opening-http"}
    response = await client.post(
        "/api/v1/opening-balances", params=params, headers=headers, json=payload
    )
    assert response.status_code == 201, response.text
    entry = response.json()["data"]
    replay = await client.post(
        "/api/v1/opening-balances", params=params, headers=headers, json=payload
    )
    assert replay.status_code == 201 and replay.json()["data"] == entry
    for bad in (100.0, "1e2", "1.001", "-1"):
        invalid = await client.post(
            "/api/v1/opening-balances",
            params=params,
            headers={"Idempotency-Key": "invalid"},
            json={**payload, "amount": bad},
        )
        # Duplicate opening also conflicts before the amount is relevant for that box.
        assert invalid.status_code in (409, 422), invalid.text
    response = await client.get("/api/v1/cashboxes", params=params)
    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["balances"] == [
        {"currency": "USD", "amount": "100.00", "book_afn": "7000.00"}
    ]
    response = await client.get(
        f"/api/v1/cashboxes/{box['uuid']}/movements", params=params
    )
    assert response.status_code == 200 and len(response.json()["data"]) == 1
    response = await client.post(
        f"/api/v1/opening-balances/{entry['uuid']}/reverse",
        params=params,
        headers={"Idempotency-Key": "reverse-http"},
        json={"reason": "اصلاح"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["data"]["reversal_of_id"] == entry["id"]
    assert (
        len((await client.get("/api/v1/ledger/entries", params=params)).json()["data"])
        == 2
    )
    assert (await client.get("/api/v1/audit-logs")).status_code == 200
    response = await client.patch(
        f"/api/v1/currencies/{usd['uuid']}", json={"is_active": False}
    )
    assert response.status_code == 200 and response.json()["data"]["is_active"] is False
    response = await client.patch(
        f"/api/v1/cashboxes/{box['uuid']}", params=params, json={"is_active": False}
    )
    assert response.status_code == 200 and response.json()["data"]["is_active"] is False
    other = await create(client, "finance-other")
    await select_org(client, other)
    for path in (
        "/settings",
        "/cashboxes",
        "/ledger/entries",
        f"/cashboxes/{box['uuid']}/movements",
    ):
        response = await client.get("/api/v1" + path, params=params)
        assert response.status_code == 404, response.text
    assert (await client.get("/api/v1/currencies")).json()["data"] == []


@pytest.mark.asyncio
async def test_cashier_cannot_configure_or_approve_opening(
    client, identity, db_session
):
    user, _ = identity
    org = await create(client, "finance-cashier")
    await select_org(client, org)
    response = await client.post("/api/v1/shops", json={"name": "Shop"})
    shop_id = response.json()["data"]["id"]
    model = await OrganizationService.get_by_id(db_session, org["id"])
    member = await OrganizationService.validate_membership(db_session, user, model)
    role = await db_session.scalar(
        select(Role).where(Role.organization_id == model.id, Role.name == "cashier")
    )
    await RoleService.set_member_roles(
        db_session, model.id, member.uuid, [role.uuid], user.id
    )
    model.owner_id = None
    await db_session.commit()
    response = await client.post(
        "/api/v1/currencies", json={"code": "USD", "precision": 2}
    )
    assert response.status_code == 403, response.text
    response = await client.post(
        "/api/v1/opening-balances",
        params={"shop_id": shop_id},
        headers={"Idempotency-Key": "unauthorized"},
        json={
            "cashbox_id": 1,
            "currency_id": 1,
            "amount": "100",
            "book_afn": "7000",
            "owner_approved": True,
            "reason": "test",
        },
    )
    assert response.status_code == 403, response.text
