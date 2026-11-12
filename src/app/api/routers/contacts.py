"""Selected-organization customers and agents."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Response, status

from app.api.dependencies import CurrentOrganizationContext, DBSession
from app.schemas.contact.request import (
    ContactCreateRequest,
    ContactOpeningRequest,
    ContactUpdateRequest,
)
from app.schemas.contact.response import (
    ContactBalanceResponse,
    ContactListResponse,
    ContactOpeningResponse,
    ContactResponse,
    ContactStatementResponse,
)
from app.services.contact import ContactService
from app.services.contact_ledger import ContactLedgerService

router = APIRouter(prefix="/contacts", tags=["Customers and agents"])


@router.post("", response_model=ContactResponse, status_code=status.HTTP_201_CREATED)
async def create_contact(
    data: ContactCreateRequest, db: DBSession, context: CurrentOrganizationContext
):
    """Create a customer/agent (contact:manage); identity needs settings:update."""
    contact = await ContactService.create(db, context, data)
    return ContactResponse(data=await ContactService.data(db, context, contact))


@router.get("", response_model=ContactListResponse)
async def list_contacts(
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_uuid: UUID,
    search: str | None = Query(None, max_length=150),
    role: str | None = Query(None, pattern="^(customer|agent)$"),
    active: bool | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """Search a shop's contacts by literal name or normalized phone (contact:read)."""
    contacts = await ContactService.list(
        db, context, shop_uuid, search, role, active, offset, limit
    )
    return ContactListResponse(
        data=[await ContactService.data(db, context, c) for c in contacts]
    )


@router.get("/{contact_uuid}", response_model=ContactResponse)
async def get_contact(
    contact_uuid: UUID, db: DBSession, context: CurrentOrganizationContext
):
    """Read a contact, with identity redacted for ordinary staff (contact:read)."""
    return ContactResponse(
        data=await ContactService.data(
            db, context, await ContactService.get(db, context, contact_uuid)
        )
    )


@router.patch("/{contact_uuid}", response_model=ContactResponse)
async def update_contact(
    contact_uuid: UUID,
    data: ContactUpdateRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
):
    """Update/deactivate while retaining history (contact:manage)."""
    return ContactResponse(
        data=await ContactService.data(
            db, context, await ContactService.update(db, context, contact_uuid, data)
        )
    )


@router.post(
    "/{contact_uuid}/opening-balances",
    response_model=ContactOpeningResponse,
    status_code=status.HTTP_201_CREATED,
)
async def opening_balance(
    contact_uuid: UUID,
    data: ContactOpeningRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
):
    """Owner-reviewed opening debt against equity; identical retries return the original entry."""
    return ContactOpeningResponse(
        data=await ContactLedgerService.opening(
            db, context, contact_uuid, idempotency_key, data
        )
    )


@router.get("/{contact_uuid}/balances", response_model=ContactBalanceResponse)
async def contact_balances(
    contact_uuid: UUID, db: DBSession, context: CurrentOrganizationContext
):
    """Gross receivables/payables per role and currency, derived from sealed ledger lines."""
    return ContactBalanceResponse(
        data=await ContactLedgerService.balances(db, context, contact_uuid)
    )


@router.get("/{contact_uuid}/statement", response_model=ContactStatementResponse)
async def contact_statement(
    contact_uuid: UUID,
    db: DBSession,
    context: CurrentOrganizationContext,
    date_from: date | None = None,
    date_to: date | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    format: str = Query("json", pattern="^(json|csv)$"),
):
    """Inclusive Kabul dates and paged movements; CSV requires report:read and exports all rows."""
    if format == "csv":
        return Response(
            await ContactLedgerService.export(
                db, context, contact_uuid, date_from, date_to
            ),
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="contact-statement.csv"',
                "Cache-Control": "no-store",
            },
        )
    return ContactStatementResponse(
        data=await ContactLedgerService.statement(
            db, context, contact_uuid, date_from, date_to, offset, limit
        )
    )
