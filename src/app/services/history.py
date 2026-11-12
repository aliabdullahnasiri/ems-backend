"""Retain actor identities and tenant history when access is revoked."""

from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Base
from app.models.ownership import OrganizationOwned


class HistoryService:
    @staticmethod
    def owned_models():
        return [
            mapper.class_
            for mapper in Base.registry.mappers
            if issubclass(mapper.class_, OrganizationOwned)
        ]

    @staticmethod
    async def has_organization_history(db: AsyncSession, organization_id: int) -> bool:
        for model in HistoryService.owned_models():
            if (
                await db.scalar(
                    select(cast(type[Base], model).id)
                    .where(model.organization_id == organization_id)
                    .limit(1)
                )
                is not None
            ):
                return True
        return False

    @staticmethod
    async def has_actor_history(
        db: AsyncSession,
        *,
        membership_id: int | None = None,
        user_id: int | None = None,
    ) -> bool:
        for model in HistoryService.owned_models():
            if not hasattr(model, "membership_id") or not hasattr(model, "user_id"):
                continue
            field_name, value = (
                ("membership_id", membership_id)
                if membership_id is not None
                else ("user_id", user_id)
            )
            field = getattr(model, field_name)
            if (
                value is not None
                and await db.scalar(
                    select(cast(type[Base], model).id).where(field == value).limit(1)
                )
                is not None
            ):
                return True
        return False
