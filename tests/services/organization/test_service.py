import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthorizationError, ConflictError, NotFoundError
from app.models import User
from app.services.organization import OrganizationService


@pytest_asyncio.fixture
async def owner(db_session):
    user = User(
        user_name="org-owner", email="org-owner@example.com", password_hash="unused"
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.mark.asyncio
async def test_owner_can_require_permissions_without_role_grants(db_session):
    owner = User(user_name="owner", email="owner@example.com", password_hash="unused")
    db_session.add(owner)
    await db_session.commit()
    owned = await OrganizationService.create(db_session, "Owned", "owned", owner=owner)
    other = await OrganizationService.create(db_session, "Other", "other")

    await OrganizationService.require_permissions(
        db_session, owner.id, owned.id, ("stock.read", "stock.write")
    )
    with pytest.raises(AuthorizationError):
        await OrganizationService.require_permissions(
            db_session, owner.id, other.id, ("stock.read",)
        )

    # Ownership changes must take effect on the next check.
    owned.owner_id = None
    await db_session.commit()
    with pytest.raises(AuthorizationError):
        await OrganizationService.require_permissions(
            db_session, owner.id, owned.id, ("stock.read",)
        )


@pytest.mark.asyncio
async def test_create_organization(db_session: AsyncSession, owner):
    name = "Test Pharmacy"
    slug = "test-pharmacy"

    org = await OrganizationService.create(db_session, name, slug, owner=owner)

    assert org.id is not None
    assert org.name == name
    assert org.slug == slug
    assert org.is_active is True


@pytest.mark.asyncio
async def test_create_organization_duplicate_slug(db_session: AsyncSession, owner):
    name1 = "Pharmacy 1"
    slug = "shared-slug"
    await OrganizationService.create(db_session, name1, slug, owner=owner)

    with pytest.raises(ConflictError):
        await OrganizationService.create(db_session, "Pharmacy 2", slug, owner=owner)


@pytest.mark.asyncio
async def test_get_by_id(db_session: AsyncSession, owner):
    org = await OrganizationService.create(db_session, "Test", "test-slug", owner=owner)

    retrieved = await OrganizationService.get_by_id(
        db_session, org.id, actor_id=owner.id
    )
    assert retrieved.id == org.id
    assert retrieved.slug == "test-slug"


@pytest.mark.asyncio
async def test_get_by_id_not_found(db_session: AsyncSession, owner):
    with pytest.raises(NotFoundError):
        await OrganizationService.get_by_id(db_session, 9999, actor_id=owner.id)


@pytest.mark.asyncio
async def test_get_by_uuid(db_session: AsyncSession, owner):
    org = await OrganizationService.create(db_session, "Test", "test-slug", owner=owner)

    retrieved = await OrganizationService.get_by_uuid(
        db_session, org.uuid, actor_id=owner.id
    )
    assert retrieved.id == org.id


@pytest.mark.asyncio
async def test_get_by_slug(db_session: AsyncSession, owner):
    slug = "unique-slug"
    await OrganizationService.create(db_session, "Test", slug, owner=owner)

    retrieved = await OrganizationService.get_by_slug(
        db_session, slug, actor_id=owner.id
    )
    assert retrieved.slug == slug


@pytest.mark.asyncio
async def test_update_organization(db_session: AsyncSession, owner):
    org = await OrganizationService.create(
        db_session, "Old Name", "old-slug", owner=owner
    )

    updated = await OrganizationService.update(
        db_session,
        org,
        name="New Name",
        slug="new-slug",
        is_active=False,
        actor_id=owner.id,
    )

    assert updated.name == "New Name"
    assert updated.slug == "new-slug"
    assert updated.is_active is False


@pytest.mark.asyncio
async def test_update_organization_duplicate_slug(db_session: AsyncSession, owner):
    org1 = await OrganizationService.create(db_session, "Org 1", "slug-1", owner=owner)
    await OrganizationService.create(db_session, "Org 2", "slug-2", owner=owner)

    with pytest.raises(ConflictError):
        await OrganizationService.update(
            db_session, org1, slug="slug-2", actor_id=owner.id
        )


@pytest.mark.asyncio
async def test_delete_organization(db_session: AsyncSession, owner):
    org = await OrganizationService.create(db_session, "Test", "test-slug", owner=owner)

    await OrganizationService.delete(db_session, org, actor_id=owner.id)

    with pytest.raises(NotFoundError):
        await OrganizationService.get_by_id(db_session, org.id, actor_id=owner.id)


@pytest.mark.asyncio
async def test_membership_management(db_session: AsyncSession, owner):
    org = await OrganizationService.create(db_session, "Org", "org-slug", owner=owner)
    user = User(
        first_name="Test",
        last_name="User",
        email="test@ex.com",
        user_name="testu",
        password_hash="fake_hash",
    )
    db_session.add(user)
    await db_session.commit()

    # Add member
    membership = await OrganizationService.add_member(
        db_session, org.id, user.id, actor_id=owner.id
    )
    assert membership.organization_id == org.id
    assert membership.user_id == user.id
    assert membership.is_active is True

    # Add duplicate member (should return existing)
    duplicate = await OrganizationService.add_member(
        db_session, org.id, user.id, actor_id=owner.id
    )
    assert duplicate.id == membership.id

    # Validate membership
    validated = await OrganizationService.validate_membership(db_session, user, org)
    assert validated.id == membership.id

    # Remove member
    await OrganizationService.remove_member(
        db_session, org.id, user.id, actor_id=owner.id
    )
    with pytest.raises(NotFoundError):
        await OrganizationService.validate_membership(db_session, user, org)


@pytest.mark.asyncio
async def test_validate_membership_inactive(db_session: AsyncSession, owner):
    org = await OrganizationService.create(db_session, "Org", "org-slug", owner=owner)
    user = User(
        first_name="Test",
        last_name="User",
        email="test@ex.com",
        user_name="testu",
        password_hash="fake_hash",
    )
    db_session.add(user)
    await db_session.commit()

    membership = await OrganizationService.add_member(
        db_session, org.id, user.id, actor_id=owner.id
    )
    membership.is_active = False
    await db_session.commit()

    with pytest.raises(NotFoundError):
        await OrganizationService.validate_membership(db_session, user, org)


@pytest.mark.asyncio
async def test_branch_management(db_session: AsyncSession, owner):
    org = await OrganizationService.create(db_session, "Org", "org-slug", owner=owner)

    # Create branch
    branch = await OrganizationService.create_branch(
        db_session, org.id, "Main Branch", "123 St", actor_id=owner.id
    )
    assert branch.id is not None
    assert branch.organization_id == org.id
    assert branch.name == "Main Branch"

    # Get branch
    retrieved = await OrganizationService.get_branch_by_id(
        db_session, branch.id, org.id, actor_id=owner.id
    )
    assert retrieved.id == branch.id

    # Update branch
    updated = await OrganizationService.update_branch(
        db_session, branch, org.id, name="New Branch Name", actor_id=owner.id
    )
    assert updated.name == "New Branch Name"

    # Delete branch
    await OrganizationService.delete_branch(
        db_session, branch, org.id, actor_id=owner.id
    )
    with pytest.raises(NotFoundError):
        await OrganizationService.get_branch_by_id(
            db_session, branch.id, org.id, actor_id=owner.id
        )


@pytest.mark.asyncio
async def test_create_branch_invalid_org(db_session: AsyncSession, owner):
    with pytest.raises(NotFoundError):
        await OrganizationService.create_branch(
            db_session, 9999, "Branch", actor_id=owner.id
        )
