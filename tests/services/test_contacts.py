from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.exceptions import (
    AppValidationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
)
from app.models import AuditEvent, Role
from app.schemas.contact.request import ContactCreateRequest, ContactUpdateRequest
from app.services.contact import ContactService
from app.services.organization import OrganizationService
from app.services.organization_context import OrganizationContext
from app.services.role import RoleService
from app.services.tenant_operations import TenantOperations
from tests.services.test_tenant_operations import tenants as tenant_fixture

tenants = pytest_asyncio.fixture(tenant_fixture.__wrapped__)


@pytest.mark.asyncio
async def test_roles_search_duplicates_identity_and_isolation(db_session, tenants):
    _, _, _, _, ca, cb, sa, sb = tenants
    contact = await ContactService.create(
        db_session,
        ca,
        ContactCreateRequest(
            shop_uuid=sa.uuid,
            name="احمد",
            phone="۰۷۰ ۱۲۳",
            is_agent=True,
            identification="private ID",
        ),
    )
    assert contact.is_customer and contact.is_agent
    assert "private ID" not in contact.identification_encrypted
    assert "identification_encrypted" not in contact.to_dict()
    duplicate = await ContactService.create(
        db_session,
        ca,
        ContactCreateRequest(shop_uuid=sa.uuid, name="Other", phone="070123"),
    )
    assert (
        await ContactService.data(db_session, ca, contact)
    ).likely_duplicate_uuids == [duplicate.uuid]
    assert (
        await ContactService.data(db_session, ca, contact)
    ).identification == "private ID"
    matches = await ContactService.list(db_session, ca, sa.uuid, "٠٧٠١٢٣")
    assert len(matches) == 2
    assert await ContactService.list(db_session, ca, sa.uuid, "%") == []
    assert await ContactService.list(db_session, ca, sa.uuid, role="agent") == [contact]
    with pytest.raises(NotFoundError):
        await ContactService.get(db_session, cb, contact.uuid)
    with pytest.raises(NotFoundError):
        await ContactService.create(
            db_session, ca, ContactCreateRequest(shop_uuid=sb.uuid, name="Foreign")
        )
    with pytest.raises(NotFoundError):
        await ContactService.get(db_session, ca, uuid4())
    await ContactService.update(
        db_session, ca, contact.uuid, ContactUpdateRequest(is_active=False)
    )
    assert not (await ContactService.get(db_session, ca, contact.uuid)).is_active
    assert await ContactService.list(db_session, ca, sa.uuid, active=True) == [
        duplicate
    ]
    with pytest.raises(ConflictError):
        await ContactService.update(
            db_session, ca, contact.uuid, ContactUpdateRequest(is_agent=False)
        )
    for changes in (
        {"name": None},
        {"is_active": None},
        {"is_customer": False, "is_agent": False},
    ):
        with pytest.raises(AppValidationError):
            await ContactService.update(
                db_session, ca, contact.uuid, ContactUpdateRequest(**changes)
            )
    audit = list(await db_session.scalars(select(AuditEvent)))
    assert all("private ID" not in str(event.to_dict()) for event in audit)


@pytest.mark.asyncio
async def test_identity_files_restricted_and_retained(db_session, tenants):
    _, staff, a, _, ca, cb, sa, _ = tenants
    contact = await ContactService.create(
        db_session,
        ca,
        ContactCreateRequest(
            shop_uuid=sa.uuid, name="Private", identification="secret"
        ),
    )
    attachment = await TenantOperations.upload(
        db_session, ca, sa.id, "id.pdf", "application/pdf", b"%PDF-secret"
    )
    await ContactService.link_attachment(db_session, ca, contact.uuid, attachment)
    assert (
        await TenantOperations.download(db_session, ca, attachment.uuid)
    ).content == b"%PDF-secret"
    with pytest.raises(NotFoundError):
        await TenantOperations.download(db_session, cb, attachment.uuid)
    member = await OrganizationService.add_member(db_session, a.id, staff.id)
    cashier = await db_session.scalar(
        select(Role).where(Role.organization_id == a.id, Role.name == "cashier")
    )
    await RoleService.set_member_roles(
        db_session, a.id, member.uuid, [cashier.uuid], ca.user_id
    )
    cs = await OrganizationContext.resolve(db_session, staff.id, a.id)
    assert (
        await ContactService.data(
            db_session, cs, await ContactService.get(db_session, cs, contact.uuid)
        )
    ).identification is None
    with pytest.raises(AuthorizationError):
        await TenantOperations.download(db_session, cs, attachment.uuid)
    with pytest.raises(AuthorizationError):
        await ContactService.update(
            db_session, cs, contact.uuid, ContactUpdateRequest(identification="changed")
        )
    await ContactService.update(
        db_session, ca, contact.uuid, ContactUpdateRequest(is_active=False)
    )
    assert (
        await TenantOperations.download(db_session, ca, attachment.uuid)
    ).content == b"%PDF-secret"


@pytest.mark.asyncio
async def test_evidence_bytes_links_and_contact_survive_direct_deletion(
    db_session, tenants
):
    from sqlalchemy import delete, update
    from sqlalchemy.exc import IntegrityError

    from app.models import PrivateAttachment
    from app.models.contact import ContactAttachment

    _, _, _, _, context, _, shop, _ = tenants
    contact = await ContactService.create(
        db_session, context, ContactCreateRequest(shop_uuid=shop.uuid, name="Retained")
    )
    attachment = await TenantOperations.upload(
        db_session,
        context,
        shop.id,
        "evidence.pdf",
        "application/pdf",
        b"%PDF-original",
    )
    link = await ContactService.link_attachment(
        db_session, context, contact.uuid, attachment
    )
    await db_session.commit()
    for statement in (
        update(PrivateAttachment)
        .where(PrivateAttachment.id == attachment.id)
        .values(content=b"%PDF-replaced"),
        delete(PrivateAttachment).where(PrivateAttachment.id == attachment.id),
        update(ContactAttachment)
        .where(ContactAttachment.id == link.id)
        .values(contact_id=999),
        delete(ContactAttachment).where(ContactAttachment.id == link.id),
    ):
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                await db_session.execute(statement)
    assert (
        await TenantOperations.download(db_session, context, attachment.uuid)
    ).content == b"%PDF-original"
