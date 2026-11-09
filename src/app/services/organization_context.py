"""Reusable ownership checks for EMS services, including background work."""

from dataclasses import dataclass
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthorizationError, NotFoundError
from app.models import Branch, Organization, User
from app.models.base import Base
from app.services.organization import OrganizationService

Record = TypeVar("Record", bound=Base)


@dataclass(frozen=True)
class OrganizationContext:
    organization_id: int
    membership_id: int
    user_id: int

    @classmethod
    async def resolve(cls, db: AsyncSession, user_id: int, organization_id: int):
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
        return cls(organization.id, member.id, user.id)

    def query(self, model: type[Record]):
        """Use this scoped select for reads, joins, aggregates and exports."""
        return select(model).where(model.organization_id == self.organization_id)

    async def get(
        self, db: AsyncSession, model: type[Record], record_id: int
    ) -> Record:
        record = await db.scalar(self.query(model).where(model.id == record_id))
        if record is None:
            raise NotFoundError()
        return record

    async def shop(self, db: AsyncSession, shop_id: int) -> Branch:
        shop = await self.get(db, Branch, shop_id)
        if not shop.is_active:
            raise AuthorizationError()
        return shop

    def new(self, model: type[Record], **values) -> Record:
        """Assign ownership from verified context, ignoring no supplied overrides."""
        if "organization_id" in values:
            raise ValueError("Organization ownership must come from context")
        return model(organization_id=self.organization_id, **values)

    def key(self, namespace: str, identifier: str) -> str:
        """Namespace cache/retry keys; persist tenant uniqueness in the DB too."""
        return f"organization:{self.organization_id}:{namespace}:{identifier}"

    def audit_values(self) -> dict[str, int]:
        return {
            "organization_id": self.organization_id,
            "membership_id": self.membership_id,
            "user_id": self.user_id,
        }
