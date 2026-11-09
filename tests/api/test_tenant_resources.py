import pytest

from app.services.organization import OrganizationService
from tests.api.test_organizations import create


async def select_org(client, org):
    response = await client.post(f"/api/v1/auth/organizations/{org['uuid']}/select")
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_guessed_ids_filters_exports_and_downloads_are_isolated(
    client, identity, db_session
):
    user, _ = identity
    a = await create(client, "resource-a")
    b = await create(client, "resource-b")
    await select_org(client, a)
    response = await client.post("/api/v1/shops", json={"name": "دکان الف"})
    assert response.status_code == 201, response.text
    sa = response.json()["data"]
    response = await client.post(
        "/api/v1/files",
        data={"shop_uuid": sa["uuid"]},
        files={"file": ("proof.pdf", b"%PDF-private-a", "application/pdf")},
    )
    assert response.status_code == 201, response.text
    file_id = response.json()["data"]["uuid"]
    await select_org(client, b)
    sb = (await client.post("/api/v1/shops", json={"name": "Shop B"})).json()["data"]
    for path in (f"/shops/{sa['uuid']}", f"/files/{file_id}"):
        assert (await client.get("/api/v1" + path)).status_code == 404
    assert (
        await client.patch(f"/api/v1/shops/{sa['uuid']}", json={"name": "intrusion"})
    ).status_code == 404
    assert (await client.get("/api/v1/shops", params={"name": "دکان الف"})).json()[
        "data"
    ] == []
    assert (await client.get("/api/v1/shops/summary")).json()["data"]["count"] == 1
    export = await client.get(
        "/api/v1/shops/export", params={"organization_id": a["id"]}
    )
    assert "Shop B" in export.text and "دکان الف" not in export.text
    audit = (await client.get("/api/v1/audit-logs")).json()["data"]
    assert audit and all(e["organization_id"] == b["id"] for e in audit)
    assert (
        await client.post(
            "/api/v1/shops", json={"name": "spoof", "organization_id": a["id"]}
        )
    ).status_code == 422
    assert (
        await client.post(
            "/api/v1/files",
            data={"shop_uuid": sa["uuid"]},
            files={"file": ("proof.pdf", b"%PDF-foreign", "application/pdf")},
        )
    ).status_code == 404
    await select_org(client, a)
    response = await client.get(f"/api/v1/files/{file_id}")
    assert response.content == b"%PDF-private-a"
    assert response.headers["cache-control"] == "no-store"
    # Remove membership in B entirely: a guessed organization cannot be selected.
    member = await OrganizationService.validate_membership(
        db_session, user, await OrganizationService.get_by_id(db_session, b["id"])
    )
    member.is_active = False
    await db_session.commit()
    assert (
        await client.post(f"/api/v1/auth/organizations/{b['uuid']}/select")
    ).status_code == 404
    assert (await client.get(f"/api/v1/shops/{sb['uuid']}")).status_code == 404


@pytest.mark.asyncio
async def test_private_upload_validation_and_revocation(client, identity, db_session):
    user, _session = identity
    org = await create(client, "private-files")
    await select_org(client, org)
    shop = (await client.post("/api/v1/shops", json={"name": "Shop"})).json()["data"]
    for filename, content, content_type in [
        ("bad.html", b"<script>x</script>", "text/html"),
        ("bad.pdf", b"fake", "application/pdf"),
        ("../proof.pdf", b"%PDF-", "application/pdf"),
    ]:
        res = await client.post(
            "/api/v1/files",
            data={"shop_uuid": shop["uuid"]},
            files={"file": (filename, content, content_type)},
        )
        assert res.status_code == 422, res.text
    response = await client.post(
        "/api/v1/files",
        data={"shop_uuid": shop["uuid"]},
        files={"file": ("proof.pdf", b"%PDF-", "application/pdf")},
    )
    file_id = response.json()["data"]["uuid"]
    member = await OrganizationService.validate_membership(
        db_session, user, await OrganizationService.get_by_id(db_session, org["id"])
    )
    member.is_active = False
    await db_session.commit()
    assert (await client.get(f"/api/v1/files/{file_id}")).status_code == 404


@pytest.mark.asyncio
async def test_switching_changes_effective_role_and_sensitive_access(
    client, identity, db_session
):
    from sqlalchemy import select

    from app.models import Role
    from app.services.role import RoleService

    user, _ = identity
    a = await create(client, "cashier-org")
    b = await create(client, "accountant-org")
    for org, role_name in ((a, "cashier"), (b, "accountant")):
        model = await OrganizationService.get_by_id(db_session, org["id"])
        member = await OrganizationService.validate_membership(db_session, user, model)
        role = await db_session.scalar(
            select(Role).where(Role.organization_id == model.id, Role.name == role_name)
        )
        await RoleService.set_member_roles(
            db_session, model.id, member.uuid, [role.uuid], user.id
        )
        # Exercise grants independently of the owner override.
        model.owner_id = None
        await db_session.commit()
    await select_org(client, a)
    cashier = (await client.get("/api/v1/auth/context")).json()["permissions"]
    assert "exchange:create" in cashier and "audit:read" not in cashier
    assert (await client.get("/api/v1/shops/export")).status_code == 403
    assert (await client.get("/api/v1/audit-logs")).status_code == 403
    assert (
        await client.post("/api/v1/shops", json={"name": "Unauthorized"})
    ).status_code == 403
    await select_org(client, b)
    accountant = (await client.get("/api/v1/auth/context")).json()["permissions"]
    assert "audit:read" in accountant and "exchange:create" not in accountant
    assert (await client.get("/api/v1/shops/export")).status_code == 200
    assert (await client.get("/api/v1/audit-logs")).status_code == 200
    assert (
        await client.post("/api/v1/shops", json={"name": "Unauthorized"})
    ).status_code == 403
