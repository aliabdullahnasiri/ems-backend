from app.core.exceptions import OrganizationOwnershipError

"""Reusable ownership checks for EMS services, including background work."""

import hashlib
import json
from dataclasses import dataclass
from typing import TypeVar, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthorizationError, NotFoundError
from app.models import Branch, Organization, User
from app.models.base import Base
from app.models.ownership import OrganizationOwned
from app.services.organization import OrganizationService

Record = TypeVar("Record", bound=Base)


@dataclass(frozen=True)
class OrganizationContext:
    organization_id: int
    membership_id: int
    user_id: int
    membership_uuid: str | None = None

    @classmethod
    async def resolve(
        cls,
        db: AsyncSession,
        user_id: int,
        organization_id: int,
        *,
        membership_id: int | None = None,
        membership_uuid: str | None = None,
    ):
        """Revalidate persisted job context; never trust a queued tenant ID alone."""
        user = await db.get(User, user_id, populate_existing=True)
        organization = await db.get(
            Organization, organization_id, populate_existing=True
        )
        if (
            user is None
            or not user.is_active
            or organization is None
            or not organization.is_active
        ):
            raise AuthorizationError()
        member = await OrganizationService.validate_membership(db, user, organization)
        if membership_id is not None and member.id != membership_id:
            raise AuthorizationError()
        if membership_uuid is not None and str(member.uuid) != membership_uuid:
            raise AuthorizationError()
        return cls(organization.id, member.id, user.id, str(member.uuid))

    def query(self, model: type[Record], *joined_models):
        """Use this scoped select for reads, joins, aggregates and exports."""
        return select(model).where(
            *(
                cast(type[OrganizationOwned], m).organization_id == self.organization_id
                for m in (model, *joined_models)
            )
        )

    async def get(
        self, db: AsyncSession, model: type[Record], record_id: int | UUID
    ) -> Record:
        record = await db.scalar(
            self.query(model)
            .where(
                (model.uuid if isinstance(record_id, UUID) else model.id) == record_id
            )
            .execution_options(populate_existing=True)
        )
        if record is None:
            raise NotFoundError()
        return record

    async def shop(self, db: AsyncSession, shop_id: int | UUID) -> Branch:
        shop = await self.get(db, Branch, shop_id)
        if not shop.is_active:
            raise AuthorizationError()
        return shop

    def new(self, model: type[Record], **values) -> Record:
        """Assign ownership from verified context; reject supplied overrides."""
        if "organization_id" in values:
            raise OrganizationOwnershipError()
        return model(organization_id=self.organization_id, **values)

    def key(self, namespace: str, identifier: str) -> str:
        """Namespace cache/retry keys; persist tenant uniqueness in the DB too."""
        suffix = hashlib.sha256(
            json.dumps([namespace, identifier], ensure_ascii=False).encode()
        ).hexdigest()
        return f"organization:{self.organization_id}:{suffix}"

    async def reference(
        self,
        db: AsyncSession,
        model: type[Record],
        record_id: int | UUID,
        *,
        shop_id: int | None = None,
    ) -> Record:
        """One validator for contacts, currencies, cashboxes and other owned records."""
        record = await self.get(db, model, record_id)
        if shop_id is not None:
            await self.shop(db, shop_id)
            record_shop = (
                record.id
                if isinstance(record, Branch)
                else getattr(record, "shop_id", None)
            )
            if record_shop != shop_id:
                raise NotFoundError()
        if getattr(record, "is_active", True) is False:
            raise AuthorizationError()
        return record

    def job_payload(self) -> dict:
        if self.membership_uuid is None:
            raise AuthorizationError()
        return {**self.audit_values(), "membership_uuid": self.membership_uuid}

    @classmethod
    async def from_job(cls, db: AsyncSession, payload: dict):
        if set(payload) != {
            "organization_id",
            "membership_id",
            "user_id",
            "membership_uuid",
        }:
            raise AuthorizationError()
        if any(
            type(payload[k]) is not int or payload[k] < 1
            for k in ("organization_id", "membership_id", "user_id")
        ) or not isinstance(payload["membership_uuid"], str):
            raise AuthorizationError()
        return await cls.resolve(
            db,
            payload["user_id"],
            payload["organization_id"],
            membership_id=payload["membership_id"],
            membership_uuid=payload["membership_uuid"],
        )

    def audit_values(self) -> dict[str, int]:
        return {
            "organization_id": self.organization_id,
            "membership_id": self.membership_id,
            "user_id": self.user_id,
        }
