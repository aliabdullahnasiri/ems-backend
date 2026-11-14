"""Append-only shop rate history, cash exchanges and reversal links."""

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


class ExchangeRate(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "exchange_rates"
    membership_id: Mapped[int] = mapped_column(nullable=False)
    user_id: Mapped[int] = mapped_column(nullable=False)
    base_currency_id: Mapped[int] = mapped_column(nullable=False)
    quote_currency_id: Mapped[int] = mapped_column(nullable=False)
    buying_rate: Mapped[str] = mapped_column(String(40), nullable=False)
    selling_rate: Mapped[str] = mapped_column(String(40), nullable=False)
    __table_args__: Any = (
        *shop_constraints("exchange_rates"),
        actor_constraint("fk_rate_actor"),
        owned_reference("fk_rate_base", "base_currency_id", "currencies"),
        owned_reference("fk_rate_quote", "quote_currency_id", "currencies"),
        CheckConstraint("base_currency_id <> quote_currency_id", name="ck_rate_pair"),
        Index("ix_rates_history", "organization_id", "shop_id", "created_at"),
    )


class Exchange(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "exchanges"
    membership_id: Mapped[int] = mapped_column(nullable=False)
    user_id: Mapped[int] = mapped_column(nullable=False)
    contact_id: Mapped[int | None] = mapped_column()
    cashbox_id: Mapped[int] = mapped_column(nullable=False)
    base_currency_id: Mapped[int] = mapped_column(nullable=False)
    quote_currency_id: Mapped[int] = mapped_column(nullable=False)
    entry_id: Mapped[int] = mapped_column(nullable=False)
    reference: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    # Snapshot includes rounded values, cost, actor and Unicode receipt metadata.
    details: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__: Any = (
        *shop_constraints("exchanges"),
        actor_constraint("fk_exchange_actor"),
        shop_reference("fk_exchange_contact", "contact_id", "contacts"),
        shop_reference("fk_exchange_cashbox", "cashbox_id", "cashboxes"),
        shop_reference("fk_exchange_entry", "entry_id", "journal_entries"),
        owned_reference("fk_exchange_base", "base_currency_id", "currencies"),
        owned_reference("fk_exchange_quote", "quote_currency_id", "currencies"),
        UniqueConstraint("entry_id", name="uq_exchange_entry"),
        CheckConstraint(
            "base_currency_id <> quote_currency_id", name="ck_exchange_pair"
        ),
        Index("ix_exchanges_search", "organization_id", "shop_id", "created_at"),
    )


class ExchangeReversal(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "exchange_reversals"
    exchange_id: Mapped[int] = mapped_column(nullable=False)
    entry_id: Mapped[int] = mapped_column(nullable=False)
    __table_args__: Any = (
        *shop_constraints("exchange_reversals"),
        shop_reference("fk_exchange_reversal_original", "exchange_id", "exchanges"),
        shop_reference("fk_exchange_reversal_entry", "entry_id", "journal_entries"),
        UniqueConstraint("exchange_id", name="uq_exchange_reversal_once"),
        UniqueConstraint("entry_id", name="uq_exchange_reversal_entry"),
    )


class ExchangePaymentLink(ImmutableHistory, ShopOwned, Base):
    """Payment services register links atomically; live payments block reversal."""

    __tablename__ = "exchange_payment_links"
    exchange_id: Mapped[int] = mapped_column(nullable=False)
    entry_id: Mapped[int] = mapped_column(nullable=False)
    __table_args__: Any = (
        *shop_constraints("exchange_payment_links"),
        shop_reference("fk_exchange_payment_original", "exchange_id", "exchanges"),
        shop_reference("fk_exchange_payment_entry", "entry_id", "journal_entries"),
        UniqueConstraint("exchange_id", "entry_id", name="uq_exchange_payment_link"),
    )
