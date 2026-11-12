"""Shop operations always derive their tenant from verified context."""

import csv
import io
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppValidationError
from app.models import Branch
from app.services.organization_context import OrganizationContext
from app.services.tenant_operations import TenantOperations


class ShopService:
    @staticmethod
    async def list(
        db: AsyncSession,
        context: OrganizationContext,
        offset=0,
        limit=50,
        name: str | None = None,
    ):
        await TenantOperations.authorize(db, context, "settings:read")
        if offset < 0 or not 1 <= limit <= 100:
            raise AppValidationError()
        query = context.query(Branch)
        if name is not None:
            query = query.where(Branch.name.contains(name, autoescape=True))
        return list(
            await db.scalars(query.order_by(Branch.id).offset(offset).limit(limit))
        )

    @staticmethod
    async def get(db: AsyncSession, context: OrganizationContext, shop_id: int | UUID):
        await TenantOperations.authorize(db, context, "settings:read")
        return await context.get(db, Branch, shop_id)

    @staticmethod
    async def create(
        db: AsyncSession,
        context: OrganizationContext,
        name: str,
        address: str | None = None,
    ):
        await TenantOperations.authorize(db, context, "settings:update")
        if (
            not name.strip()
            or len(name) > 150
            or (address is not None and len(address) > 500)
        ):
            raise AppValidationError()
        shop = context.new(Branch, name=name, address=address)
        db.add(shop)
        await db.flush()
        await TenantOperations.audit(db, context, "shop:create", "shop", shop.id)
        return shop

    @staticmethod
    async def update(
        db: AsyncSession,
        context: OrganizationContext,
        shop_id: int | UUID,
        changes: dict,
    ):
        await TenantOperations.authorize(db, context, "settings:update")
        shop = await context.get(db, Branch, shop_id)
        if not set(changes) <= {"name", "address", "is_active"}:
            raise AppValidationError()
        if "name" in changes and (
            not isinstance(changes["name"], str)
            or not changes["name"].strip()
            or len(changes["name"]) > 150
        ):
            raise AppValidationError()
        if changes.get("address") is not None and len(changes["address"]) > 500:
            raise AppValidationError()
        if "is_active" in changes and type(changes["is_active"]) is not bool:
            raise AppValidationError()
        for key, value in changes.items():
            setattr(shop, key, value)
        await db.flush()
        await TenantOperations.audit(db, context, "shop:update", "shop", shop.id)
        return shop

    @staticmethod
    async def export(db: AsyncSession, context: OrganizationContext) -> str:
        await TenantOperations.authorize(db, context, "report:read")
        shops = await db.scalars(context.query(Branch).order_by(Branch.id))
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["organization_id", "shop_id", "name", "address", "is_active"])

        def cell(value):
            text = value or ""
            return (
                "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text
            )

        for shop in shops:
            writer.writerow(
                [
                    context.organization_id,
                    shop.id,
                    cell(shop.name),
                    cell(shop.address),
                    shop.is_active,
                ]
            )
        return output.getvalue()

    @staticmethod
    async def count(db: AsyncSession, context: OrganizationContext) -> int:
        await TenantOperations.authorize(db, context, "report:read")
        return (
            await db.execute(
                select(func.count()).select_from(context.query(Branch).subquery())
            )
        ).scalar_one()
