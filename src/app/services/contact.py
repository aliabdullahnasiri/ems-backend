"""Reusable contact operations; callers own the transaction."""

import unicodedata
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import SecretEncryption
from app.core.exceptions import AppValidationError, AuthorizationError, ConflictError
from app.models import Branch
from app.models.contact import Contact, ContactAttachment
from app.schemas.contact.data import ContactData
from app.schemas.contact.request import ContactCreateRequest, ContactUpdateRequest
from app.services.finance import LedgerService
from app.services.organization import OrganizationService
from app.services.organization_context import OrganizationContext
from app.services.tenant_operations import TenantOperations


class ContactService:
    """Tenant and shop validation for contacts, identity details and evidence."""

    @staticmethod
    def normalize(value: str) -> str:
        """Normalize Unicode text and decimal numeral input for searches."""
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())

    @staticmethod
    def phone(value: str | None) -> str | None:
        """Compare phones independently of punctuation and numeral alphabet."""
        if not value:
            return None
        return (
            "".join(str(unicodedata.decimal(c)) for c in value if c.isdecimal()) or None
        )

    @staticmethod
    async def identity_access(db: AsyncSession, context: OrganizationContext) -> bool:
        """Identity details require the restricted settings:update permission."""
        try:
            await OrganizationService.require_permissions(
                db, context.user_id, context.organization_id, ("settings:update",)
            )
            return True
        except AuthorizationError:
            return False

    @staticmethod
    async def get(
        db: AsyncSession, context: OrganizationContext, contact_uuid: UUID
    ) -> Contact:
        """Read a contact within its verified active shop, including inactive contacts."""
        await TenantOperations.authorize(db, context, "contact:read")
        contact = await context.get(db, Contact, contact_uuid)
        await context.shop(db, contact.shop_id)
        return contact

    @staticmethod
    async def create(
        db: AsyncSession, context: OrganizationContext, data: ContactCreateRequest
    ) -> Contact:
        """Create a customer, agent or dual-role counterparty."""
        await TenantOperations.authorize(db, context, "contact:manage")
        shop = await context.get(db, Branch, data.shop_uuid)
        await context.shop(db, shop.id)
        values = data.model_dump(exclude={"shop_uuid", "identification"})
        if data.identification is not None and not await ContactService.identity_access(
            db, context
        ):
            raise AuthorizationError()
        contact = context.new(
            Contact,
            shop_id=shop.id,
            **values,
            normalized_phone=ContactService.phone(data.phone),
            search_name=ContactService.normalize(data.name),
            identification_encrypted=SecretEncryption.encrypt(data.identification)
            if data.identification
            else None,
        )
        db.add(contact)
        await db.flush()
        await TenantOperations.audit(
            db, context, "contact:create", "contact", contact.id
        )
        return contact

    @staticmethod
    async def update(
        db: AsyncSession,
        context: OrganizationContext,
        contact_uuid: UUID,
        data: ContactUpdateRequest,
    ) -> Contact:
        """Update or deactivate without deleting financial history or evidence."""
        await TenantOperations.authorize(db, context, "contact:manage")
        # Lock before loading fields so concurrent PATCHes cannot derive stale search values.
        await LedgerService.lock(db, context)
        contact = await ContactService.get(db, context, contact_uuid)
        values = data.model_dump(exclude_unset=True)
        for field in ("name", "is_customer", "is_agent", "is_active"):
            if field in values and values[field] is None:
                raise AppValidationError()
        if not (
            values.get("is_customer", contact.is_customer)
            or values.get("is_agent", contact.is_agent)
        ):
            raise AppValidationError()
        # Roles are historical accounting dimensions: deactivation is preferred.
        if (contact.is_customer and values.get("is_customer") is False) or (
            contact.is_agent and values.get("is_agent") is False
        ):
            raise ConflictError()
        if "identification" in values:
            if not await ContactService.identity_access(db, context):
                raise AuthorizationError()
            value = values.pop("identification")
            contact.identification_encrypted = (
                SecretEncryption.encrypt(value) if value else None
            )
        for key, value in values.items():
            setattr(contact, key, value)
        contact.search_name = ContactService.normalize(contact.name)
        contact.normalized_phone = ContactService.phone(contact.phone)
        await db.flush()
        await TenantOperations.audit(
            db, context, "contact:update", "contact", contact.id
        )
        return contact

    @staticmethod
    async def list(
        db: AsyncSession,
        context: OrganizationContext,
        shop_uuid: UUID,
        search: str | None = None,
        role: str | None = None,
        active: bool | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[Contact]:
        """Paginate literal name/phone matches in one authorized shop."""
        await TenantOperations.authorize(db, context, "contact:read")
        shop = await context.get(db, Branch, shop_uuid)
        await context.shop(db, shop.id)
        if (
            offset < 0
            or not 1 <= limit <= 100
            or role not in (None, "customer", "agent")
        ):
            raise AppValidationError()
        query = context.query(Contact).where(Contact.shop_id == shop.id)
        if search:
            conditions = [
                Contact.search_name.contains(
                    ContactService.normalize(search), autoescape=True
                )
            ]
            phone = ContactService.phone(search)
            if phone:
                conditions.append(
                    Contact.normalized_phone.contains(phone, autoescape=True)
                )
            query = query.where(or_(*conditions))
        if role:
            query = query.where(getattr(Contact, f"is_{role}").is_(True))
        if active is not None:
            query = query.where(Contact.is_active == active)
        return list(
            await db.scalars(query.order_by(Contact.id).offset(offset).limit(limit))
        )

    @staticmethod
    async def data(
        db: AsyncSession, context: OrganizationContext, contact: Contact
    ) -> ContactData:
        """Redact identity details and flag same-shop exact normalized duplicates."""
        contact = await ContactService.get(db, context, contact.uuid)
        conditions = [Contact.search_name == contact.search_name]
        if contact.normalized_phone:
            conditions.append(Contact.normalized_phone == contact.normalized_phone)
        duplicates = list(
            await db.scalars(
                select(Contact.uuid)
                .where(
                    Contact.organization_id == context.organization_id,
                    Contact.shop_id == contact.shop_id,
                    Contact.id != contact.id,
                    or_(*conditions),
                )
                .order_by(Contact.id)
                .limit(20)
            )
        )
        values = {
            key: getattr(contact, key)
            for key in ContactData.model_fields
            if key not in ("identification", "likely_duplicate_uuids")
        }
        identity = None
        if contact.identification_encrypted and await ContactService.identity_access(
            db, context
        ):
            identity = SecretEncryption.decrypt(contact.identification_encrypted)
        return ContactData(
            **values, identification=identity, likely_duplicate_uuids=duplicates
        )

    @staticmethod
    async def link_attachment(
        db: AsyncSession, context: OrganizationContext, contact_uuid: UUID, attachment
    ) -> ContactAttachment:
        """Retain evidence using composite organization/shop references."""
        await TenantOperations.authorize(
            db, context, "contact:manage", "settings:update"
        )
        await LedgerService.lock(db, context)
        contact = await ContactService.get(db, context, contact_uuid)
        if not contact.is_active:
            raise AuthorizationError()
        await context.reference(
            db, type(attachment), attachment.id, shop_id=contact.shop_id
        )
        link = context.new(
            ContactAttachment,
            shop_id=contact.shop_id,
            contact_id=contact.id,
            attachment_id=attachment.id,
        )
        db.add(link)
        await db.flush()
        return link

    @staticmethod
    async def authorize_attachment(
        db: AsyncSession, context: OrganizationContext, attachment_id: int
    ):
        """Identity evidence requires contact read and restricted identity access."""
        link = await db.scalar(
            context.query(ContactAttachment).where(
                ContactAttachment.attachment_id == attachment_id
            )
        )
        if link:
            await TenantOperations.authorize(
                db, context, "contact:read", "settings:update"
            )
            contact = await context.get(db, Contact, link.contact_id)
            await context.shop(db, contact.shop_id)
