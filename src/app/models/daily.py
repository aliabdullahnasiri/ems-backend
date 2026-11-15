"""Retained operating expenses, transfers and cash closing snapshots."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Date, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.finance import shop_constraints
from app.models.ownership import (
    ImmutableHistory,
    ShopOwned,
    owned_reference,
    shop_reference,
)
from app.models.tenant_operations import actor_constraint


class ExpenseCategory(ShopOwned, Base):
    __tablename__ = "expense_categories"
    name: Mapped[str] = mapped_column(String(150))
    is_active: Mapped[bool] = mapped_column(default=True)
    __table_args__: Any = (
        *shop_constraints("expense_categories"),
        UniqueConstraint(
            "organization_id", "shop_id", "name", name="uq_expense_category_name"
        ),
    )


class Expense(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "expenses"
    category_id: Mapped[int] = mapped_column()
    cashbox_id: Mapped[int] = mapped_column()
    currency_id: Mapped[int] = mapped_column()
    entry_id: Mapped[int] = mapped_column(unique=True)
    attachment_id: Mapped[int | None] = mapped_column()
    expense_date: Mapped[date] = mapped_column(Date)
    payee: Mapped[str] = mapped_column(String(150))
    explanation: Mapped[str] = mapped_column(String(500))
    amount: Mapped[str] = mapped_column(String(40))
    book_afn: Mapped[str] = mapped_column(String(40))
    __table_args__: Any = (
        *shop_constraints("expenses"),
        shop_reference("fk_expense_category", "category_id", "expense_categories"),
        shop_reference("fk_expense_cash", "cashbox_id", "cashboxes"),
        owned_reference("fk_expense_currency", "currency_id", "currencies"),
        shop_reference("fk_expense_entry", "entry_id", "journal_entries"),
        shop_reference("fk_expense_evidence", "attachment_id", "private_attachments"),
    )


class CashTransfer(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "cash_transfers"
    source_cashbox_id: Mapped[int] = mapped_column()
    destination_cashbox_id: Mapped[int] = mapped_column()
    currency_id: Mapped[int] = mapped_column()
    entry_id: Mapped[int] = mapped_column(unique=True)
    amount: Mapped[str] = mapped_column(String(40))
    book_afn: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(String(500))
    __table_args__: Any = (
        *shop_constraints("cash_transfers"),
        CheckConstraint(
            "source_cashbox_id <> destination_cashbox_id",
            name="ck_transfer_distinct_cashboxes",
        ),
        shop_reference("fk_transfer_source", "source_cashbox_id", "cashboxes"),
        shop_reference(
            "fk_transfer_destination", "destination_cashbox_id", "cashboxes"
        ),
        owned_reference("fk_transfer_currency", "currency_id", "currencies"),
        shop_reference("fk_transfer_entry", "entry_id", "journal_entries"),
    )


class BusinessDay(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "business_days"
    day: Mapped[date] = mapped_column(Date)
    opening: Mapped[list] = mapped_column(JSON)
    membership_id: Mapped[int] = mapped_column()
    user_id: Mapped[int] = mapped_column()
    __table_args__: Any = (
        *shop_constraints("business_days"),
        actor_constraint("fk_day_actor"),
        UniqueConstraint("organization_id", "shop_id", "day", name="uq_business_day"),
    )


class DayClosing(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "day_closings"
    business_day_id: Mapped[int] = mapped_column(unique=True)
    cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    counts: Mapped[list] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(String(500))
    membership_id: Mapped[int] = mapped_column()
    user_id: Mapped[int] = mapped_column()
    __table_args__: Any = (
        *shop_constraints("day_closings"),
        actor_constraint("fk_closing_actor"),
        shop_reference("fk_closing_day", "business_day_id", "business_days"),
    )


class ClosingConfirmation(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "closing_confirmations"
    closing_id: Mapped[int] = mapped_column(unique=True)
    entry_id: Mapped[int | None] = mapped_column(unique=True)
    membership_id: Mapped[int] = mapped_column()
    user_id: Mapped[int] = mapped_column()
    __table_args__: Any = (
        *shop_constraints("closing_confirmations"),
        actor_constraint("fk_closing_approval_actor"),
        shop_reference("fk_confirmation_closing", "closing_id", "day_closings"),
        shop_reference("fk_confirmation_entry", "entry_id", "journal_entries"),
    )
