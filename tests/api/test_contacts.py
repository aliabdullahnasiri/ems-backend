import pytest
from app.models.finance import Currency
from sqlalchemy import select

from app.models import Role
from app.services.organization import OrganizationService
from app.services.role import RoleService
from tests.api.test_organizations import create
from tests.api.test_tenant_resources import select_org


async def contact_setup(client, slug):
    org = await create(client, slug)
    await select_org(client, org)
    res = await client.post("/api/v1/shops", json={"name": "صرافی"})
    assert res.status_code == 201, res.text
    shop = res.json()["data"]
    res = await client.post(
        "/api/v1/contacts",
        json={
            "shop_uuid": shop["uuid"],
            "name": "احمد",
            "phone": "۰۷۰۱۲۳",
            "is_agent": True,
            "identification": "private identity",
        },
    )
    assert res.status_code == 201, res.text
    return org, shop, res.json()["data"]


@pytest.mark.asyncio
async def test_contact_crud_search_isolation_files_and_deactivation(client, identity):
    org, shop, contact = await contact_setup(client, "contact-api")
    path = f"/api/v1/contacts/{contact['uuid']}"
    res = await client.get(
        "/api/v1/contacts",
        params={"shop_uuid": shop["uuid"], "search": "070123", "role": "agent"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["data"][0]["uuid"] == contact["uuid"]
    res = await client.post(
        "/api/v1/contacts", json={"shop_uuid": shop["uuid"], "name": "احمد"}
    )
    assert res.status_code == 201, res.text
    assert res.json()["data"]["likely_duplicate_uuids"] == [contact["uuid"]]
    for changes in (
        {"name": None},
        {"name": " "},
        {"is_customer": False, "is_agent": False},
        {"organization_id": 999},
    ):
        res = await client.patch(path, json=changes)
        assert res.status_code == 422, res.text
    res = await client.post(
        "/api/v1/files",
        data={"shop_uuid": shop["uuid"], "contact_uuid": contact["uuid"]},
        files={"file": ("id.pdf", b"%PDF-identity", "application/pdf")},
    )
    assert res.status_code == 201, res.text
    file_path = f"/api/v1/files/{res.json()['data']['uuid']}"
    assert (await client.get(file_path)).content == b"%PDF-identity"
    other = await create(client, "contact-other")
    await select_org(client, other)
    for suffix in ("", "/balances", "/statement", "/statement?format=csv"):
        assert (await client.get(path + suffix)).status_code == 404
    assert (await client.get(file_path)).status_code == 404
    assert (await client.patch(path, json={"name": "spoof"})).status_code == 404
    await select_org(client, org)
    res = await client.patch(
        path, json={"notes": "یادداشت", "phone": None, "is_active": False}
    )
    assert res.status_code == 200, res.text
    assert res.json()["data"]["is_active"] is False
    res = await client.get(path)
    assert res.status_code == 200 and res.json()["data"]["notes"] == "یادداشت"
    assert (await client.get(file_path)).content == b"%PDF-identity"
    res = await client.post(
        "/api/v1/files",
        data={"shop_uuid": shop["uuid"], "contact_uuid": contact["uuid"]},
        files={"file": ("late.pdf", b"%PDF-late", "application/pdf")},
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_opening_balances_statement_retry_and_csv(client, identity, db_session):
    _, _, contact = await contact_setup(client, "contact-financial")
    from app.models import Organization

    org = await db_session.scalar(
        select(Organization).where(Organization.slug == "contact-financial")
    )
    currency = Currency(organization_id=org.id, code="AFN", precision=2)
    db_session.add(currency)
    await db_session.flush()
    path = f"/api/v1/contacts/{contact['uuid']}"
    data = {
        "role": "customer",
        "direction": "receivable",
        "currency_id": currency.id,
        "amount": "۱۰۰۰۰٫۰۰",
        "book_afn": "10000.00",
        "reason": "Reviewed books",
        "owner_approved": True,
    }
    assert (await client.post(path + "/opening-balances", json=data)).status_code == 422
    headers = {"Idempotency-Key": "reviewed-opening"}
    res = await client.post(path + "/opening-balances", json=data, headers=headers)
    assert res.status_code == 201, res.text
    repeated = await client.post(path + "/opening-balances", json=data, headers=headers)
    assert repeated.status_code == 201 and repeated.json() == res.json()
    assert (
        await client.post(
            path + "/opening-balances",
            json={**data, "amount": "9999.00"},
            headers=headers,
        )
    ).status_code == 409
    res = await client.get(path + "/balances")
    assert res.status_code == 200, res.text
    assert res.json()["data"][0]["receivable"] == "10000.00"
    res = await client.get(path + "/statement")
    assert res.status_code == 200, res.text
    assert res.json()["data"]["total_movements"] == 1
    assert res.json()["data"]["movements"][0]["entry_id"] > 0
    csv = await client.get(path + "/statement", params={"format": "csv"})
    assert csv.status_code == 200 and "text/csv" in csv.headers["content-type"]
    assert "احمد" in csv.text and "10000.00" in csv.text
    assert "private identity" not in csv.text
    assert (
        await client.get(path + "/statement?date_from=2026-11-11&date_to=2026-11-01")
    ).status_code == 422


@pytest.mark.asyncio
async def test_cashier_identity_and_financial_permissions(client, identity, db_session):
    user, _ = identity
    org, shop, contact = await contact_setup(client, "contact-cashier")
    res = await client.post(
        "/api/v1/files",
        data={"shop_uuid": shop["uuid"], "contact_uuid": contact["uuid"]},
        files={"file": ("id.pdf", b"%PDF-secret", "application/pdf")},
    )
    assert res.status_code == 201, res.text
    file_uuid = res.json()["data"]["uuid"]
    organization = await OrganizationService.get_by_id(db_session, org["id"])
    member = await OrganizationService.validate_membership(
        db_session, user, organization
    )
    role = await db_session.scalar(
        select(Role).where(
            Role.organization_id == organization.id, Role.name == "cashier"
        )
    )
    await RoleService.set_member_roles(
        db_session, organization.id, member.uuid, [role.uuid], user.id
    )
    organization.owner_id = None
    await db_session.commit()
    path = f"/api/v1/contacts/{contact['uuid']}"
    res = await client.get(path)
    assert res.status_code == 200, res.text
    assert res.json()["data"].get("identification") is None
    assert (
        await client.patch(path, json={"identification": "leak"})
    ).status_code == 403
    assert (await client.get(f"/api/v1/files/{file_uuid}")).status_code == 403
    assert (await client.get(path + "/statement?format=csv")).status_code == 403
    assert (
        await client.post(
            "/api/v1/files",
            data={"shop_uuid": shop["uuid"], "contact_uuid": contact["uuid"]},
            files={"file": ("id.pdf", b"%PDF-new", "application/pdf")},
        )
    ).status_code == 403
    assert (await client.get(path + "/balances")).status_code == 200
