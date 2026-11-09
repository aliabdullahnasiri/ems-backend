import pytest

from app.core.exceptions import AuthorizationError, NotFoundError
from app.core.permissions import EMS_ROLE_PERMISSIONS
from app.models import Branch, User
from app.services.organization import OrganizationService
from app.services.organization_context import OrganizationContext
from app.services.role import RoleService


@pytest.mark.asyncio
async def test_context_isolates_records_and_revalidates_membership(db_session):
    user = User(
        user_name="context", email="context@example.com", password_hash="unused"
    )
    db_session.add(user)
    await db_session.flush()
    a = await OrganizationService.create(db_session, "A", "context-a", user)
    b = await OrganizationService.create(db_session, "B", "context-b", user)
    ca = await OrganizationContext.resolve(db_session, user.id, a.id)
    cb = await OrganizationContext.resolve(db_session, user.id, b.id)
    shop = ca.new(Branch, name="Shop")
    db_session.add(shop)
    await db_session.commit()
    assert (await ca.shop(db_session, shop.id)).id == shop.id
    with pytest.raises(NotFoundError):
        await cb.shop(db_session, shop.id)
    assert ca.key("retry", "same") != cb.key("retry", "same")
    assert ca.audit_values()["organization_id"] == a.id
    with pytest.raises(ValueError):
        ca.new(Branch, name="Spoof", organization_id=b.id)
    b.is_active = False
    await db_session.commit()
    with pytest.raises(AuthorizationError):
        await OrganizationContext.resolve(db_session, user.id, b.id)
    user.is_active = False
    await db_session.commit()
    with pytest.raises(AuthorizationError):
        await OrganizationContext.resolve(db_session, user.id, a.id)


@pytest.mark.asyncio
async def test_default_roles_are_scoped_and_repeatable(db_session):
    org = await OrganizationService.create(db_session, "Roles", "ems-defaults")
    await RoleService.provision_ems_defaults(db_session, org.id)
    await db_session.commit()
    roles = await RoleService.list(db_session, org.id)
    assert {r.name for r in roles} == set(EMS_ROLE_PERMISSIONS)
    for role in roles:
        names = {
            p.name for p in await RoleService.permissions(db_session, org.id, role.uuid)
        }
        assert names == set(EMS_ROLE_PERMISSIONS[role.name])
    for staff in ("cashier", "accountant"):
        assert not {
            "day_closing:approve",
            "settings:update",
            "hawala:refund",
            "roles:assign",
        } & set(EMS_ROLE_PERMISSIONS[staff])
