"""Retained customer obligations, cash payments, allocations and reversals."""

from typing import Any

from sqlalchemy import JSON, CheckConstraint, Index, String, UniqueConstraint
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


class CustomerObligation(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "customer_obligations"
    contact_id: Mapped[int] = mapped_column(nullable=False)
    exchange_id: Mapped[int | None] = mapped_column()
    source_entry_id: Mapped[int] = mapped_column(nullable=False)
    account_id: Mapped[int] = mapped_column(nullable=False)
    currency_id: Mapped[int] = mapped_column(nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[str] = mapped_column(String(40), nullable=False)
    book_afn: Mapped[str] = mapped_column(String(40), nullable=False)
    __table_args__: Any = (
        *shop_constraints("customer_obligations"),
        shop_reference("fk_obligation_contact", "contact_id", "contacts"),
        shop_reference("fk_obligation_exchange", "exchange_id", "exchanges"),
        shop_reference("fk_obligation_source", "source_entry_id", "journal_entries"),
        shop_reference("fk_obligation_account", "account_id", "ledger_accounts"),
        owned_reference("fk_obligation_currency", "currency_id", "currencies"),
        UniqueConstraint("source_entry_id", "account_id", name="uq_obligation_source"),
        CheckConstraint(
            "direction IN ('receivable', 'payable')", name="ck_obligation_direction"
        ),
        Index("ix_obligation_contact", "organization_id", "shop_id", "contact_id"),
    )


class Payment(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "payments"
    membership_id: Mapped[int] = mapped_column(nullable=False)
    user_id: Mapped[int] = mapped_column(nullable=False)
    contact_id: Mapped[int] = mapped_column(nullable=False)
    cashbox_id: Mapped[int] = mapped_column(nullable=False)
    currency_id: Mapped[int] = mapped_column(nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[str] = mapped_column(String(40), nullable=False)
    entry_id: Mapped[int] = mapped_column(nullable=False)
    settlement_exchange_id: Mapped[int | None] = mapped_column()
    reference: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    receipt: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__: Any = (
        *shop_constraints("payments"),
        actor_constraint("fk_payment_actor"),
        shop_reference("fk_payment_contact", "contact_id", "contacts"),
        shop_reference("fk_payment_cashbox", "cashbox_id", "cashboxes"),
        shop_reference("fk_payment_entry", "entry_id", "journal_entries"),
        shop_reference("fk_payment_conversion", "settlement_exchange_id", "exchanges"),
        owned_reference("fk_payment_currency", "currency_id", "currencies"),
        UniqueConstraint("entry_id", name="uq_payment_entry"),
        CheckConstraint("direction IN ('receive', 'pay')", name="ck_payment_direction"),
        Index("ix_payment_history", "organization_id", "shop_id", "created_at"),
    )


class PaymentAllocation(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "payment_allocations"
    payment_id: Mapped[int] = mapped_column(nullable=False)
    obligation_id: Mapped[int] = mapped_column(nullable=False)
    amount: Mapped[str] = mapped_column(String(40), nullable=False)
    book_afn: Mapped[str] = mapped_column(String(40), nullable=False)
    __table_args__: Any = (
        *shop_constraints("payment_allocations"),
        shop_reference("fk_allocation_payment", "payment_id", "payments"),
        shop_reference(
            "fk_allocation_obligation", "obligation_id", "customer_obligations"
        ),
        UniqueConstraint("payment_id", "obligation_id", name="uq_payment_allocation"),
        Index("ix_allocation_obligation", "organization_id", "obligation_id"),
    )


class PaymentReversal(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "payment_reversals"
    payment_id: Mapped[int] = mapped_column(nullable=False)
    entry_id: Mapped[int] = mapped_column(nullable=False)
    __table_args__: Any = (
        *shop_constraints("payment_reversals"),
        shop_reference("fk_payment_reversal_payment", "payment_id", "payments"),
        shop_reference("fk_payment_reversal_entry", "entry_id", "journal_entries"),
        UniqueConstraint("payment_id", name="uq_payment_reversal_once"),
        UniqueConstraint("entry_id", name="uq_payment_reversal_entry"),
    )
