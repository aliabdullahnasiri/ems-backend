"""Selected-organization shop, export, audit and private-file endpoints."""

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, Response, UploadFile
from pydantic import ConfigDict, Field

from app.api.dependencies import CurrentOrganizationContext, DBSession
from app.schemas.base.model import Model
from app.services.shop import ShopService
from app.services.tenant_operations import TenantOperations

router = APIRouter(tags=["Organization resources"])


class ShopCreate(Model):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=150)
    address: str | None = Field(default=None, max_length=500)


class ShopUpdate(Model):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=150)
    address: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


def shop_data(shop):
    return {
        "id": shop.id,
        "uuid": str(shop.uuid),
        "organization_id": shop.organization_id,
        "name": shop.name,
        "address": shop.address,
        "is_active": shop.is_active,
    }


@router.post("/shops", status_code=201)
async def create_shop(
    data: ShopCreate, db: DBSession, context: CurrentOrganizationContext
):
    """Create a shop in the selected organization (settings:update)."""
    return {
        "data": shop_data(
            await ShopService.create(db, context, data.name, data.address)
        )
    }


@router.get("/shops")
async def list_shops(
    db: DBSession,
    context: CurrentOrganizationContext,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    name: str | None = Query(None, max_length=150),
):
    """Paginated, literal-name-filtered shops (settings:read)."""
    return {
        "data": [
            shop_data(s)
            for s in await ShopService.list(db, context, offset, limit, name)
        ]
    }


@router.get("/shops/export")
async def export_shops(db: DBSession, context: CurrentOrganizationContext):
    """Export only selected-organization shops as Unicode CSV (report:read)."""
    return Response(
        await ShopService.export(db, context),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="shops.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/shops/summary")
async def shop_summary(db: DBSession, context: CurrentOrganizationContext):
    """Tenant-scoped shop count (report:read)."""
    return {"data": {"count": await ShopService.count(db, context)}}


@router.get("/shops/{shop_uuid}")
async def get_shop(shop_uuid: UUID, db: DBSession, context: CurrentOrganizationContext):
    """Look up a shop without exposing foreign organization IDs (settings:read)."""
    return {"data": shop_data(await ShopService.get(db, context, shop_uuid))}


@router.patch("/shops/{shop_uuid}")
async def update_shop(
    shop_uuid: UUID,
    data: ShopUpdate,
    db: DBSession,
    context: CurrentOrganizationContext,
):
    """Edit or deactivate a shop; history is retained (settings:update)."""
    return {
        "data": shop_data(
            await ShopService.update(
                db, context, shop_uuid, data.model_dump(exclude_unset=True)
            )
        )
    }


@router.post("/files", status_code=201)
async def upload_file(
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_uuid: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
    contact_uuid: Annotated[UUID | None, Form()] = None,
):
    """Private shop attachment, max 5 MiB, PDF/PNG/JPEG (attachment:manage).

    Ownership comes from session context. Files are stored privately in the DB,
    never under a public directory. Contact identity links require contact_uuid,
    contact:manage and settings:update; links cannot be reassigned or deleted.
    """
    from app.services.contact import ContactService

    if contact_uuid is not None:
        await TenantOperations.authorize(db, context, "contact:manage", "settings:update")
        contact = await ContactService.get(db, context, contact_uuid)
        shop = await context.shop(db, shop_uuid)
        await context.reference(db, type(contact), contact.id, shop_id=shop.id)
    content = await file.read(5 * 1024 * 1024 + 1)
    async with db.begin_nested():
        attachment = await TenantOperations.upload(
            db, context, shop_uuid, file.filename or "", file.content_type or "", content
        )
        if contact_uuid is not None:
            await ContactService.link_attachment(db, context, contact_uuid, attachment)
    return {
        "data": {
            "id": attachment.id,
            "uuid": str(attachment.uuid),
            "organization_id": attachment.organization_id,
            "shop_id": attachment.shop_id,
            "file_name": attachment.file_name,
        }
    }


@router.get("/files/{attachment_uuid}")
async def download_file(
    attachment_uuid: UUID, db: DBSession, context: CurrentOrganizationContext
):
    """Revalidate membership and shop before downloading (attachment:read)."""
    attachment = await TenantOperations.download(db, context, attachment_uuid)
    return Response(
        attachment.content,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(attachment.file_name, safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.get("/audit-logs")
async def audit_logs(
    db: DBSession,
    context: CurrentOrganizationContext,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """Read append-only tenant audit history (audit:read)."""
    events = await TenantOperations.audit_log(db, context, offset, limit)
    return {
        "data": [
            {
                "id": e.id,
                "organization_id": e.organization_id,
                "membership_id": e.membership_id,
                "user_id": e.user_id,
                "action": e.action,
                "record_type": e.record_type,
                "record_id": e.record_id,
                "reason": e.reason,
                "created_at": e.created_at,
            }
            for e in events
        ]
    }


# Customers and agents extend the existing selected-organization resources.
from app.api.routers.contacts import router as contacts_router

router.include_router(contacts_router)
