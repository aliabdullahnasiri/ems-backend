"""Hawala and agent settlement HTTP contracts, authorization and history exports."""

import pytest
from sqlalchemy import select

from app.models import Role
from app.services.organization import OrganizationService
from app.services.role import RoleService
from tests.api.test_organizations import create
from tests.api.test_tenant_resources import select_org


@pytest.mark.asyncio
async def test_hawala_http_lifecycle_settlement_history_permissions_and_isolation(
    client, identity, db_session
):
    user, _ = identity
    org = await create(client, "hawala-http")
    await select_org(client, org)
    response = await client.post("/api/v1/shops", json={"name": "صرافی"})
    assert response.status_code == 201, response.text
    shop = response.json()["data"]
    params = {"shop_id": shop["id"]}
    response = await client.post(
        "/api/v1/currencies", json={"code": "AFN", "precision": 2}
    )
    assert response.status_code == 201, response.text
    afn = response.json()["data"]["id"]
    response = await client.post(
        "/api/v1/cashboxes", params=params, json={"name": "Till"}
    )
    assert response.status_code == 201, response.text
    box = response.json()["data"]["id"]
    response = await client.post(
        "/api/v1/contacts",
        json={"shop_uuid": shop["uuid"], "name": "نماینده", "is_agent": True},
    )
    assert response.status_code == 201, response.text
    agent = response.json()["data"]
    response = await client.post(
        f"/api/v1/contacts/{agent['uuid']}/opening-balances",
        headers={"Idempotency-Key": "customer-opening"},
        json={
            "role": "customer",
            "direction": "receivable",
            "currency_id": afn,
            "amount": "100",
            "book_afn": "100",
            "owner_approved": True,
            "reason": "Separate customer balance",
        },
    )
    assert response.status_code == 201, response.text
    payload = {
        "direction": "outgoing",
        "agent_id": agent["id"],
        "collection_currency_id": afn,
        "payout_currency_id": afn,
        "cashbox_id": box,
        "sender_name": "احمد",
        "recipient_name": "محمود",
        "destination": "کابل",
        "collection_amount": "1000",
        "payout_amount": "1000",
        "rate": "1",
        "customer_fee": "100",
        "agent_fee": "20",
    }
    response = await client.post("/api/v1/hawalas", params=params, json=payload)
    assert response.status_code == 201, response.text
    record = response.json()["data"]
    assert record["state"] == "draft"
    url = f"/api/v1/hawalas/{record['uuid']}"
    response = await client.patch(url, params=params, json={"destination": "هرات"})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["draft"]["sender_name"] == "احمد"
    response = await client.get(url + "/receipt", params=params)
    assert response.status_code == 409, response.text
    response = await client.post(
        url + "/confirm", params=params, json={"reason": "Confirm"}
    )
    assert response.status_code == 422, response.text
    response = await client.post(
        url + "/confirm",
        params=params,
        headers={"Idempotency-Key": "confirm"},
        json={"reason": "Confirm"},
    )
    assert response.status_code == 200, response.text
    confirmed = response.json()["data"]
    response = await client.post(
        url + "/confirm",
        params=params,
        headers={"Idempotency-Key": "confirm"},
        json={"reason": "Confirm"},
    )
    assert response.status_code == 200 and response.json()["data"] == confirmed
    response = await client.patch(
        url, params=params, json={"destination": "New destination"}
    )
    assert response.status_code == 409, response.text
    for _ in range(2):
        response = await client.get(url + "/receipt", params=params)
        assert (
            response.status_code == 200
            and response.json()["data"] == confirmed["receipt"]
        )
    response = await client.get(
        "/api/v1/hawalas",
        params={**params, "reference": record["reference"], "agent_id": agent["id"]},
    )
    assert response.status_code == 200 and len(response.json()["data"]) == 1
    response = await client.get(f"/api/v1/contacts/{agent['uuid']}/agent-outstanding")
    assert response.status_code == 200, response.text
    owed = next(o for o in response.json()["data"] if o["kind"] == "principal")
    settlement = {
        "agent_id": agent["id"],
        "currency_id": afn,
        "cashbox_id": box,
        "direction": "pay",
        "method": "cash",
        "amount": "600",
        "external_reference": "Receipt 1",
        "evidence_reference": "Receipt 1",
        "reason": "Partial settlement",
        "allocations": [{"obligation_id": owed["id"], "amount": "600"}],
    }
    response = await client.post(
        "/api/v1/agent-settlements",
        params=params,
        headers={"Idempotency-Key": "settle"},
        json=settlement,
    )
    assert response.status_code == 201, response.text
    posted = response.json()["data"]
    response = await client.get(
        f"/api/v1/agent-settlements/{posted['uuid']}", params=params
    )
    assert response.status_code == 200 and response.json()["data"] == posted
    response = await client.get("/api/v1/agent-settlements", params=params)
    assert response.status_code == 200 and len(response.json()["data"]) == 1
    response = await client.get(
        "/api/v1/agent-settlements/statement", params={"agent_uuid": agent["uuid"]}
    )
    assert response.status_code == 200, response.text
    assert {m["role"] for m in response.json()["data"]["movements"]} == {"agent"}
    response = await client.get(
        "/api/v1/agent-settlements/statement",
        params={"agent_uuid": agent["uuid"], "format": "csv"},
    )
    assert (
        response.status_code == 200 and "text/csv" in response.headers["content-type"]
    )
    assert ",customer," not in response.text
    response = await client.post(
        f"/api/v1/agent-settlements/{posted['uuid']}/reverse",
        params=params,
        headers={"Idempotency-Key": "reverse"},
        json={"owner_approved": True, "reason": "Correction"},
    )
    assert response.status_code == 201, response.text
    # Cashier can record manual payout, but cannot confirm/cancel or settle agents.
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
    for action in ("confirm", "cancel"):
        response = await client.post(
            url + "/" + action,
            params=params,
            headers={"Idempotency-Key": action},
            json={"reason": "Denied"},
        )
        assert response.status_code == 403, response.text
    response = await client.post(
        "/api/v1/agent-settlements",
        params=params,
        headers={"Idempotency-Key": "denied"},
        json=settlement,
    )
    assert response.status_code == 403, response.text
    payout = {
        "mode": "partner",
        "beneficiary_verified": True,
        "verification_reference": "Verified recipient",
        "evidence_reference": "Manual partner receipt",
        "reason": "Partner paid",
    }
    response = await client.post(
        url + "/pay", params=params, headers={"Idempotency-Key": "pay"}, json=payout
    )
    assert response.status_code == 200 and response.json()["data"]["state"] == "paid", (
        response.text
    )
    response = await client.post(
        url + "/pay",
        params=params,
        headers={"Idempotency-Key": "duplicate"},
        json=payout,
    )
    assert response.status_code == 409, response.text
    response = await client.get(
        "/api/v1/agent-settlements/statement",
        params={"agent_uuid": agent["uuid"], "format": "csv"},
    )
    assert response.status_code == 403, response.text
    other = await create(client, "hawala-other")
    await select_org(client, other)
    for path in (
        url,
        url + "/receipt",
        "/api/v1/hawalas",
        "/api/v1/agent-settlements",
        f"/api/v1/agent-settlements/{posted['uuid']}",
    ):
        response = await client.get(path, params=params)
        assert response.status_code == 404, response.text
    response = await client.get(f"/api/v1/contacts/{agent['uuid']}/agent-outstanding")
    assert response.status_code == 404, response.text
    client.headers.pop("Authorization")
    response = await client.get(url, params=params)
    assert response.status_code == 401, response.text
