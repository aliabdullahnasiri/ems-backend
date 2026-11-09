import pytest

from app.core.permissions import EMS_PERMISSIONS
from app.models import OrganizationUser
from tests.api.test_organizations import create


@pytest.mark.asyncio
async def test_context_requires_selection_and_rejects_inactive_state(
    client, identity, db_session
):
    user, _session = identity
    assert (await client.get("/api/v1/auth/context")).status_code == 403
    org = await create(client)
    response = await client.post(f"/api/v1/auth/organizations/{org['uuid']}/select")
    assert response.status_code == 200
    response = await client.get("/api/v1/auth/context", params={"organization_id": 999})
    assert response.status_code == 200, response.text
    assert response.json()["organization_id"] == org["id"]
    assert set(EMS_PERMISSIONS) <= set(response.json()["permissions"])
    member = await db_session.get(OrganizationUser, response.json()["membership_id"])
    member.is_active = False
    await db_session.commit()
    assert (await client.get("/api/v1/auth/context")).status_code == 404
    member.is_active = True
    user.is_active = False
    await db_session.commit()
    assert (await client.get("/api/v1/auth/context")).status_code == 401
