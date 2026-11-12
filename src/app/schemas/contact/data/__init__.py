from uuid import UUID

from app.schemas.base.model import Model


class ContactData(Model):
    id: int
    uuid: UUID
    organization_id: int
    shop_id: int
    name: str
    phone: str | None
    location: str | None
    notes: str | None
    is_customer: bool
    is_agent: bool
    is_active: bool
    identification: str | None = None
    likely_duplicate_uuids: list[UUID]


class ContactBalanceData(Model):
    role: str
    currency: str
    receivable: str
    payable: str
    net_receivable: str
    receivable_direction: str = "contact_owes_shop"
    payable_direction: str = "shop_owes_contact"


class ContactMovementData(Model):
    entry_id: int
    line_id: int
    created_at: str
    action: str
    reason: str
    role: str
    direction: str
    currency: str
    amount: str
    book_afn: str
    running_net_receivable: str


class ContactStatementData(Model):
    contact_uuid: UUID
    contact_name: str
    shop_name: str
    timezone: str = "Asia/Kabul"
    date_from: str | None
    date_to: str | None
    generated_at: str
    opening_balances: list[ContactBalanceData]
    movements: list[ContactMovementData]
    closing_balances: list[ContactBalanceData]
    offset: int
    limit: int
    total_movements: int
