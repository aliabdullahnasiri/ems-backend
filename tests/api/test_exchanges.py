"""HTTP acceptance and permission coverage for exchange endpoints."""

import pytest
from sqlalchemy import select

from app.models import Role
from app.services.organization import OrganizationService
from app.services.role import RoleService
from tests.api.test_organizations import create
from tests.api.test_tenant_resources import select_org


@pytest.mark.asyncio
async def test_exchange_http_workflow(client, identity, db_session):
    user, _ = identity
    org = await create(client, "exchange-http")
    await select_org(client, org)
    response = await client.post("/api/v1/shops", json={"name": "صرافی"})
    assert response.status_code == 201, response.text
    shop = response.json()["data"]
    params = {"shop_id": shop["id"]}
    currencies = {}
    for code in ("AFN", "USD"):
        response = await client.post(
            "/api/v1/currencies", json={"code": code, "precision": 2}
        )
        assert response.status_code == 201, response.text
        currencies[code] = response.json()["data"]["id"]
    response = await client.post(
        "/api/v1/cashboxes", params=params, json={"name": "صندوق"}
    )
    assert response.status_code == 201, response.text
    box = response.json()["data"]["id"]
    response = await client.post(
        "/api/v1/opening-balances",
        params=params,
        headers={"Idempotency-Key": "opening"},
        json={
            "cashbox_id": box,
            "currency_id": currencies["AFN"],
            "amount": "10000",
            "book_afn": "10000",
            "owner_approved": True,
            "reason": "opening",
        },
    )
    assert response.status_code == 201, response.text
    pair = {
        "base_currency_id": currencies["USD"],
        "quote_currency_id": currencies["AFN"],
    }
    rate = {**pair, "buying_rate": "70", "selling_rate": "71"}
    response = await client.post("/api/v1/rates", params=params, json=rate)
    assert response.status_code == 201, response.text
    assert response.json()["data"]["direction"] == "quote_per_base"
    response = await client.get("/api/v1/rates", params=params)
    assert response.status_code == 200 and len(response.json()["data"]) == 1
    quote = {**pair, "cashbox_id": box, "side": "buy", "amount": "100", "rate": "70"}
    response = await client.post("/api/v1/exchanges/quote", params=params, json=quote)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["outgoing_amount"] == "7000.00"
    assert (
        len((await client.get("/api/v1/ledger/entries", params=params)).json()["data"])
        == 1
    )
    for invalid in (1.0, "1e2", "-1", "0", "1.001"):
        response = await client.post(
            "/api/v1/exchanges/quote", params=params, json={**quote, "amount": invalid}
        )
        assert response.status_code == 422, response.text
    payload = {**quote, "reason": "تبادله"}
    response = await client.post("/api/v1/exchanges", params=params, json=payload)
    assert response.status_code == 422, response.text
    headers = {"Idempotency-Key": "buy"}
    response = await client.post(
        "/api/v1/exchanges", params=params, headers=headers, json=payload
    )
    assert response.status_code == 201, response.text
    exchange = response.json()["data"]
    response = await client.post(
        "/api/v1/exchanges", params=params, headers=headers, json=payload
    )
    assert response.status_code == 201 and response.json()["data"] == exchange
    response = await client.post(
        "/api/v1/exchanges",
        params=params,
        headers=headers,
        json={**payload, "rate": "71"},
    )
    assert response.status_code == 409, response.text
    path = f"/api/v1/exchanges/{exchange['uuid']}"
    for _ in range(2):
        response = await client.get(path + "/receipt", params=params)
        assert (
            response.status_code == 200
            and response.json()["data"] == exchange["receipt"]
        )
    response = await client.get(
        "/api/v1/exchanges",
        params={
            **params,
            "reference": exchange["reference"],
            "cashier_id": user.id,
            "currency_id": currencies["USD"],
        },
    )
    assert response.status_code == 200 and len(response.json()["data"]) == 1
    reverse = {"reason": "اصلاح", "owner_approved": True}
    response = await client.post(
        path + "/reverse",
        params=params,
        headers={"Idempotency-Key": "reverse"},
        json=reverse,
    )
    assert response.status_code == 201, response.text
    reversal = response.json()["data"]
    response = await client.get(path, params=params)
    assert response.json()["data"]["reversal_entry_id"] == reversal["id"]
    assert response.json()["data"]["receipt"] == exchange["receipt"]
    response = await client.post(
        path + "/reverse",
        params=params,
        headers={"Idempotency-Key": "again"},
        json=reverse,
    )
    assert response.status_code == 409, response.text
    # A cashier can quote/post/read, but cannot set rates or approve a reversal.
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
    response = await client.post("/api/v1/rates", params=params, json=rate)
    assert response.status_code == 403, response.text
    response = await client.post(
        path + "/reverse",
        params=params,
        headers={"Idempotency-Key": "cashier"},
        json=reverse,
    )
    assert response.status_code == 403, response.text
    response = await client.post("/api/v1/exchanges/quote", params=params, json=quote)
    assert response.status_code == 200, response.text
    response = await client.post(
        "/api/v1/exchanges",
        params=params,
        headers={"Idempotency-Key": "cashier-buy"},
        json=payload,
    )
    assert response.status_code == 201, response.text
    other = await create(client, "exchange-other")
    await select_org(client, other)
    for url in (path, path + "/receipt", "/api/v1/exchanges", "/api/v1/rates"):
        response = await client.get(url, params=params)
        assert response.status_code == 404, response.text
    client.headers.pop("Authorization")
    response = await client.get(path + "/receipt", params=params)
    assert response.status_code == 401, response.text
