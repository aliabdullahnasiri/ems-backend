"""Authorized financial reporting endpoints."""

from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Response

from app.api.dependencies import CurrentOrganizationContext, DBSession
from app.api.routers.finance import ShopID
from app.schemas.reports.response import FinancialReportResponse
from app.services.reports import ReportService

router = APIRouter(tags=["Reports"])


def endpoint(kind):
    """Build a consistently filtered report endpoint."""

    async def report(
        db: DBSession,
        context: CurrentOrganizationContext,
        shop_id: ShopID,
        day: date | None = None,
        closing_id: int | None = None,
        cutoff: datetime | None = None,
        start: datetime | None = None,
        currency_id: int | None = None,
        cashbox_id: int | None = None,
        cashier_id: int | None = None,
        contact_id: int | None = None,
        format: Literal["json", "csv"] = "json",
    ):
        """Read historical posted balances or period activity; export JSON or CSV."""
        if day is not None:
            from app.services.financial_values import day_cutoffs

            start, end = day_cutoffs(day)
            cutoff = end - timedelta(microseconds=1)
        data = await ReportService.report(
            db,
            context,
            shop_id,
            kind,
            {
                "closing_id": closing_id,
                "cutoff": cutoff,
                "start": start,
                "currency_id": currency_id,
                "cashbox_id": cashbox_id,
                "cashier_id": cashier_id,
                "contact_id": contact_id,
            },
        )
        if format == "csv":
            return Response(
                ReportService.csv(data),
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{kind}.csv"'},
            )
        return FinancialReportResponse(data=data)

    return report


for kind in ("daily", "cash", "outstanding", "profit", "hawala", "audit"):
    router.add_api_route(
        f"/reports/{kind}",
        endpoint(kind),
        methods=["GET"],
        response_model=FinancialReportResponse,
        name=f"report_{kind}",
    )
router.add_api_route(
    "/dashboard",
    endpoint("dashboard"),
    methods=["GET"],
    response_model=FinancialReportResponse,
    name="dashboard",
)
