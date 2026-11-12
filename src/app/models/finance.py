"""Exact-money ledger; a posting seal closes each immutable journal."""

from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.ownership import (
    ImmutableHistory,
    OrganizationOwned,
    ShopOwned,
    owned_reference,
    shop_reference,
)
from app.models.tenant_operations import actor_constraint


def shop_constraints(table):
    return (
        UniqueConstraint("organization_id", "id", name=f"uq_{table}_organization_id"),
        UniqueConstraint(
            "organization_id", "shop_id", "id", name=f"uq_{table}_shop_id"
        ),
        ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["branches.organization_id", "branches.id"],
            ondelete="RESTRICT",
        ),
    )


class ShopSettings(ShopOwned, Base):
    __tablename__ = "shop_settings"
    phone: Mapped[str | None] = mapped_column(String(50))
    logo_reference: Mapped[str | None] = mapped_column(String(500))
    preferred_language: Mapped[str] = mapped_column(
        String(10), default="fa", nullable=False
    )
    receipt_footer: Mapped[str | None] = mapped_column(String(1000))
    __table_args__: Any = (
        UniqueConstraint("organization_id", "shop_id", name="uq_shop_settings_shop"),
        *shop_constraints("shop_settings"),
    )


class Currency(OrganizationOwned, Base):
    __tablename__ = "currencies"
    code: Mapped[str] = mapped_column(String(3), nullable=False)
    precision: Mapped[int] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # IRR is rial; IRT is an explicit internal toman unit, fixed at ten rial.
    rial_per_unit: Mapped[int | None] = mapped_column()
    __table_args__: Any = (
        UniqueConstraint("organization_id", "id", name="uq_currencies_organization_id"),
        UniqueConstraint("organization_id", "code", name="uq_currency_code"),
        CheckConstraint(
            "precision >= 0 AND precision <= 8", name="ck_currency_precision"
        ),
        CheckConstraint(
            "(code = 'IRR' AND rial_per_unit IS NOT NULL AND rial_per_unit = 1) OR (code = 'IRT' AND rial_per_unit IS NOT NULL AND rial_per_unit = 10) OR (code NOT IN ('IRR', 'IRT') AND rial_per_unit IS NULL)",
            name="ck_currency_iran_unit",
        ),
    )


class Cashbox(ShopOwned, Base):
    __tablename__ = "cashboxes"
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__: Any = (
        *shop_constraints("cashboxes"),
        UniqueConstraint("organization_id", "shop_id", "name", name="uq_cashbox_name"),
    )


class LedgerAccount(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "ledger_accounts"
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    currency_id: Mapped[int] = mapped_column(nullable=False)
    cashbox_id: Mapped[int | None] = mapped_column()
    __table_args__: Any = (
        *shop_constraints("ledger_accounts"),
        UniqueConstraint(
            "organization_id",
            "shop_id",
            "code",
            "currency_id",
            name="uq_ledger_account",
        ),
        owned_reference("fk_account_currency", "currency_id", "currencies"),
        shop_reference("fk_account_cashbox", "cashbox_id", "cashboxes"),
        CheckConstraint(
            "kind IN ('cash', 'equity', 'receivable', 'payable', 'income', 'expense', 'clearing')",
            name="ck_account_kind",
        ),
        CheckConstraint(
            "(kind = 'cash' AND cashbox_id IS NOT NULL) OR (kind <> 'cash' AND cashbox_id IS NULL)",
            name="ck_account_cashbox",
        ),
    )


class JournalEntry(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "journal_entries"
    membership_id: Mapped[int] = mapped_column(nullable=False)
    user_id: Mapped[int] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    reversal_of_id: Mapped[int | None] = mapped_column()
    # Unique only on openings: one reviewed opening per cashbox/currency forever.
    opening_key: Mapped[str | None] = mapped_column(String(100))
    __table_args__: Any = (
        *shop_constraints("journal_entries"),
        actor_constraint("fk_journal_actor"),
        shop_reference("fk_journal_reversal", "reversal_of_id", "journal_entries"),
        UniqueConstraint(
            "organization_id", "reversal_of_id", name="uq_journal_reversal"
        ),
        UniqueConstraint("organization_id", "opening_key", name="uq_opening_once"),
    )


class JournalLine(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "journal_lines"
    entry_id: Mapped[int] = mapped_column(nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(nullable=False, index=True)
    # Canonical decimal strings avoid SQLite's binary floating point coercion.
    # Debit is positive; credit is negative. AFN book values use two decimals.
    amount: Mapped[str] = mapped_column(String(40), nullable=False)
    book_afn: Mapped[str] = mapped_column(String(40), nullable=False)
    __table_args__: Any = (
        *shop_constraints("journal_lines"),
        shop_reference("fk_line_entry", "entry_id", "journal_entries"),
        shop_reference("fk_line_account", "account_id", "ledger_accounts"),
    )


class JournalPosting(ImmutableHistory, ShopOwned, Base):
    """Presence means all lines were validated; insertion closes the journal."""

    __tablename__ = "journal_postings"
    entry_id: Mapped[int] = mapped_column(nullable=False)
    __table_args__: Any = (
        *shop_constraints("journal_postings"),
        shop_reference("fk_posting_entry", "entry_id", "journal_entries"),
        UniqueConstraint("entry_id", name="uq_posting_entry"),
    )


def protect_sealed_lines(target, connection, **kwargs):
    """No additional lines may be appended after posting, even via raw SQL."""
    dialect = connection.dialect.name
    condition = "EXISTS (SELECT 1 FROM journal_postings WHERE entry_id = NEW.entry_id)"
    if dialect == "sqlite":
        connection.exec_driver_sql(
            f"CREATE TRIGGER journal_lines_sealed BEFORE INSERT ON journal_lines WHEN {condition} BEGIN SELECT RAISE(ABORT, 'Journal is posted'); END"
        )
    elif dialect == "postgresql":
        connection.exec_driver_sql(
            f"CREATE OR REPLACE FUNCTION journal_lines_sealed() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF {condition} THEN RAISE EXCEPTION 'Journal is posted'; END IF; RETURN NEW; END; $$"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER journal_lines_sealed BEFORE INSERT ON journal_lines FOR EACH ROW EXECUTE FUNCTION journal_lines_sealed()"
        )
    elif dialect == "mysql":
        connection.exec_driver_sql(
            f"CREATE TRIGGER journal_lines_sealed BEFORE INSERT ON journal_lines FOR EACH ROW BEGIN IF {condition} THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Journal is posted'; END IF; END"
        )


event.listen(JournalLine.__table__, "after_create", protect_sealed_lines)
