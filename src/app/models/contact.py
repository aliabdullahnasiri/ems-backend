"""Shop-owned counterparties and retained private evidence links."""

from typing import Any, ClassVar

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.ownership import ImmutableHistory, ShopOwned, shop_reference


class Contact(ShopOwned, Base):
    __tablename__ = "contacts"
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50))
    normalized_phone: Mapped[str | None] = mapped_column(String(50))
    search_name: Mapped[str] = mapped_column(String(150), nullable=False)
    location: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(String(2000))
    is_customer: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_agent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    identification_encrypted: Mapped[str | None] = mapped_column(Text)
    __hidden_fields__: ClassVar[set[str]] = {"identification_encrypted"}
    __table_args__: Any = (
        CheckConstraint("is_customer OR is_agent", name="ck_contact_role"),
        ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["branches.organization_id", "branches.id"],
            ondelete="RESTRICT",
            name="fk_contacts_shop_organization",
        ),
        UniqueConstraint("organization_id", "id", name="uq_contacts_organization_id"),
        UniqueConstraint(
            "organization_id", "shop_id", "id", name="uq_contacts_shop_id"
        ),
        Index("ix_contacts_search", "organization_id", "shop_id", "search_name"),
        Index("ix_contacts_phone", "organization_id", "shop_id", "normalized_phone"),
    )


class ContactAttachment(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "contact_attachments"
    contact_id: Mapped[int] = mapped_column(nullable=False)
    attachment_id: Mapped[int] = mapped_column(nullable=False)
    __table_args__: Any = (
        shop_reference("fk_contact_attachment_contact", "contact_id", "contacts"),
        shop_reference(
            "fk_contact_attachment_file", "attachment_id", "private_attachments"
        ),
        UniqueConstraint("attachment_id", name="uq_contact_attachment_file"),
        UniqueConstraint(
            "organization_id", "id", name="uq_contact_attachments_organization_id"
        ),
        UniqueConstraint(
            "organization_id", "shop_id", "id", name="uq_contact_attachments_shop_id"
        ),
        ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["branches.organization_id", "branches.id"],
            ondelete="RESTRICT",
            name="fk_contact_attachments_shop_organization",
        ),
    )


class ContactAccount(ImmutableHistory, ShopOwned, Base):
    """Each role/direction/unit maps to one shared ledger account."""

    __tablename__ = "contact_accounts"
    contact_id: Mapped[int] = mapped_column(nullable=False)
    account_id: Mapped[int] = mapped_column(nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    currency_id: Mapped[int] = mapped_column(nullable=False)
    __table_args__: Any = (
        shop_reference("fk_contact_account_contact", "contact_id", "contacts"),
        shop_reference("fk_contact_account_ledger", "account_id", "ledger_accounts"),
        ForeignKeyConstraint(
            ["organization_id", "currency_id"],
            ["currencies.organization_id", "currencies.id"],
            ondelete="RESTRICT",
            name="fk_contact_account_currency",
        ),
        ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["branches.organization_id", "branches.id"],
            ondelete="RESTRICT",
            name="fk_contact_accounts_shop_organization",
        ),
        UniqueConstraint("account_id", name="uq_contact_account_ledger"),
        UniqueConstraint(
            "organization_id",
            "contact_id",
            "role",
            "direction",
            "currency_id",
            name="uq_contact_account_dimension",
        ),
        UniqueConstraint(
            "organization_id", "id", name="uq_contact_accounts_organization_id"
        ),
        UniqueConstraint(
            "organization_id", "shop_id", "id", name="uq_contact_accounts_shop_id"
        ),
        CheckConstraint(
            "role IN ('customer', 'agent')", name="ck_contact_account_role"
        ),
        CheckConstraint(
            "direction IN ('receivable', 'payable')",
            name="ck_contact_account_direction",
        ),
    )
