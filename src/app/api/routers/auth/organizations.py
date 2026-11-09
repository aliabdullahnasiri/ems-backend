from uuid import UUID

from fastapi import APIRouter

from app.api.dependencies.auth import (
    CurrentActiveSession,
    CurrentMembership,
    CurrentOrganization,
    CurrentUserPermissions,
)
from app.api.dependencies.db import DBSession
from app.schemas.organization.data.organization import OrganizationData
from app.schemas.organization.response.organization import OrganizationResponse
from app.services.organization import OrganizationService

router = APIRouter()


@router.post(
    "/organizations/{organization_uuid}/select", response_model=OrganizationResponse
)
async def select_organization(
    organization_uuid: UUID, session: CurrentActiveSession, db: DBSession
) -> OrganizationResponse:
    """Select an active organization for subsequent requests on this session."""
    organization = await OrganizationService.select(db, session, organization_uuid)
    return OrganizationResponse(data=OrganizationData.model_validate(organization))


@router.get("/organization", response_model=OrganizationResponse)
async def current_organization(
    organization: CurrentOrganization,
) -> OrganizationResponse:
    """Return the selected organization after checking its current membership."""
    return OrganizationResponse(data=OrganizationData.model_validate(organization))


@router.get("/context")
async def current_context(
    organization: CurrentOrganization,
    membership: CurrentMembership,
    permissions: CurrentUserPermissions,
) -> dict:
    """Verified tenant identity and effective permissions for this session.

    Requires an active user, session, organization and membership. Client headers
    and query parameters cannot override the selected organization.
    """
    return {
        "organization_id": organization.id,
        "membership_id": membership.id,
        "user_id": membership.user_id,
        "permissions": sorted(permissions),
    }
