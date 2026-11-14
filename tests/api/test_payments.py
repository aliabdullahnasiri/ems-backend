"""Credit exchange and payment HTTP contracts with selected organization permissions."""

import pytest
from sqlalchemy import select

from app.models import Role
from app.services.organization import OrganizationService
from app.services.role import RoleService
from tests.api.test_organizations import create
from tests.api.test_tenant_resources import select_org


@pytest.mark.asyncio
async def test_credit_exchange_payment_receipt_outstanding_reversal_and_permissions(
    client, identity, db_session
):
    user, _ = identity
    org = await create(client, "payments-http")
    await select_org(client, org)
    response = await client.post("/api/v1/shops", json={"name": "صرافی"})
    assert response.status_code == 201, response.text
    shop = response.json()["data"]
    params = {"shop_id": shop["id"]}
    units = {}
    for code in ("AFN", "USD"):
        response = await client.post(
            "/api/v1/currencies", json={"code": code, "precision": 2}
        )
        assert response.status_code == 201, response.text
        units[code] = response.json()["data"]["id"]
    response = await client.post(
        "/api/v1/cashboxes", params=params, json={"name": "Till"}
    )
    assert response.status_code == 201, response.text
    box = response.json()["data"]["id"]
    response = await client.post(
        "/api/v1/contacts", json={"shop_uuid": shop["uuid"], "name": "احمد"}
    )
    assert response.status_code == 201, response.text
    customer = response.json()["data"]
    # Fully unpaid exchange creates debts, even without available physical cash.
    response = await client.post(
        "/api/v1/exchanges",
        params=params,
        headers={"Idempotency-Key": "credit"},
        json={
            "cashbox_id": box,
            "contact_id": customer["id"],
            "base_currency_id": units["USD"],
            "quote_currency_id": units["AFN"],
            "side": "sell",
            "amount": "100",
            "rate": "100",
            "incoming_cash_amount": "0",
            "outgoing_cash_amount": "0",
            "outgoing_afn_rate": "100",
            "reason": "credit",
        },
    )
    assert response.status_code == 201, response.text
    exchange = response.json()["data"]
    obligation = next(o for o in exchange["obligations"] if o["currency"] == "AFN")
    outstanding_url = f"/api/v1/contacts/{customer['uuid']}/outstanding"
    response = await client.get(outstanding_url)
    assert response.status_code == 200 and len(response.json()["data"]) == 2
    payload = {
        "cashbox_id": box,
        "contact_id": customer["id"],
        "currency_id": units["AFN"],
        "direction": "receive",
        "amount": "6000",
        "allocations": [{"obligation_id": obligation["id"], "amount": "6000"}],
        "reason": "تصفیه",
    }
    response = await client.post("/api/v1/payments", params=params, json=payload)
    assert response.status_code == 422, response.text
    for bad in (1.0, "1e2", "-1", "0", "1.001"):
        response = await client.post(
            "/api/v1/payments",
            params=params,
            headers={"Idempotency-Key": "bad"},
            json={**payload, "amount": bad},
        )
        assert response.status_code == 422, response.text
    response = await client.post(
        "/api/v1/payments",
        params=params,
        headers={"Idempotency-Key": "receive"},
        json=payload,
    )
    assert response.status_code == 201, response.text
    result = response.json()["data"]
    assert result["obligations"][0]["remaining_amount"] == "4000.00"
    assert (
        next(b for b in result["balances"] if b["currency"] == "AFN")["receivable"]
        == "4000.00"
    )
    response = await client.post(
        "/api/v1/payments",
        params=params,
        headers={"Idempotency-Key": "receive"},
        json=payload,
    )
    assert response.status_code == 201 and response.json()["data"] == result
    response = await client.post(
        "/api/v1/payments",
        params=params,
        headers={"Idempotency-Key": "receive"},
        json={**payload, "reason": "changed"},
    )
    assert response.status_code == 409, response.text
    payment = result["payment"]
    url = f"/api/v1/payments/{payment['uuid']}"
    for path in (url, url + "/receipt"):
        response = await client.get(path, params=params)
        assert response.status_code == 200, response.text
    for _ in range(2):
        response = await client.get(url + "/receipt", params=params)
        assert response.json()["data"] == payment["receipt"]
    response = await client.get(
        "/api/v1/payments",
        params={
            **params,
            "contact_id": customer["id"],
            "direction": "receive",
            "reference": payment["reference"],
        },
    )
    assert response.status_code == 200 and len(response.json()["data"]) == 1
    reverse = {"reason": "correction", "owner_approved": True}
    response = await client.post(
        url + "/reverse",
        params=params,
        headers={"Idempotency-Key": "reverse"},
        json=reverse,
    )
    assert response.status_code == 201, response.text
    assert response.json()["data"]["obligations"][0]["remaining_amount"] == "10000.00"
    response = await client.get(url, params=params)
    assert response.json()["data"]["reversal_entry_id"] is not None
    response = await client.post(
        url + "/reverse",
        params=params,
        headers={"Idempotency-Key": "twice"},
        json=reverse,
    )
    assert response.status_code == 409, response.text
    # Accountants may inspect payments but cannot post or reverse them.
    model = await OrganizationService.get_by_id(db_session, org["id"])
    member = await OrganizationService.validate_membership(db_session, user, model)
    role = await db_session.scalar(
        select(Role).where(Role.organization_id == model.id, Role.name == "accountant")
    )
    await RoleService.set_member_roles(
        db_session, model.id, member.uuid, [role.uuid], user.id
    )
    model.owner_id = None
    await db_session.commit()
    response = await client.get(url + "/receipt", params=params)
    assert response.status_code == 200, response.text
    response = await client.post(
        "/api/v1/payments",
        params=params,
        headers={"Idempotency-Key": "no-permission"},
        json=payload,
    )
    assert response.status_code == 403, response.text
    response = await client.post(
        url + "/reverse",
        params=params,
        headers={"Idempotency-Key": "no-reverse"},
        json=reverse,
    )
    assert response.status_code == 403, response.text
    other = await create(client, "payments-other")
    await select_org(client, other)
    for path in (url, url + "/receipt", "/api/v1/payments"):
        response = await client.get(path, params=params)
        assert response.status_code == 404, response.text
    response = await client.get(outstanding_url)
    assert response.status_code == 404, response.text
    client.headers.pop("Authorization")
    response = await client.get(url + "/receipt", params=params)
    assert response.status_code == 401, response.text
