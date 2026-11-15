"""Cash report response contracts."""

from datetime import datetime

from pydantic import Field

from app.schemas.base.model import Model
from app.schemas.base.response import BaseResponseModel


class CashBalanceData(Model):
    cashbox_id: int
    currency_id: int
    amount: str
    book_afn: str


class CashReportData(Model):
    cutoff: datetime
    balances: list[CashBalanceData]


class CashReportResponse(BaseResponseModel[CashReportData]):
    pass


class ReportRowData(Model):
    entry_id: int
    posted_at: datetime
    user_id: int
    action: str
    account_id: int
    account_code: str
    kind: str
    currency_id: int
    currency: str
    cashbox_id: int | None
    contact_id: int | None = None
    role: str | None = None
    direction: str | None = None
    amount: str
    book_afn: str
    conversion_rate: str | None = None


class HawalaStatusData(Model):
    hawala_id: int
    status: str
    refund_currency: str | None = None
    refund_remaining: str = "0"


class AuditReportData(Model):
    id: int
    created_at: datetime
    user_id: int
    action: str
    record_type: str
    record_id: int
    reason: str | None


class FinancialReportData(Model):
    shop_name: str
    generated_at: datetime
    cutoff: datetime
    start: datetime | None
    valuation_basis: str = (
        "Posted historical AFN book values; weighted-average cash cost"
    )
    filters: dict[str, str | int | None]
    rows: list[ReportRowData]
    totals: dict[str, str]
    hawalas: list[HawalaStatusData] = Field(default_factory=list)
    audit_events: list[AuditReportData] = Field(default_factory=list)


class FinancialReportResponse(BaseResponseModel[FinancialReportData]):
    pass
