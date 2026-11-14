"""Hawala drafts, immutable lifecycle history, agent debts and settlement records."""

from typing import Any

from sqlalchemy import JSON, CheckConstraint, Index, String, UniqueConstraint, event
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


class Hawala(ShopOwned, Base):
    __tablename__ = "hawalas"
    agent_id: Mapped[int] = mapped_column(nullable=False)
    collection_currency_id: Mapped[int] = mapped_column(nullable=False)
    payout_currency_id: Mapped[int] = mapped_column(nullable=False)
    cashbox_id: Mapped[int | None] = mapped_column()
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    reference: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__: Any = (
        *shop_constraints("hawalas"),
        shop_reference("fk_hawala_agent", "agent_id", "contacts"),
        shop_reference("fk_hawala_cashbox", "cashbox_id", "cashboxes"),
        owned_reference("fk_hawala_collection", "collection_currency_id", "currencies"),
        owned_reference("fk_hawala_payout", "payout_currency_id", "currencies"),
        CheckConstraint(
            "direction IN ('incoming', 'outgoing')", name="ck_hawala_direction"
        ),
        Index("ix_hawala_history", "organization_id", "shop_id", "created_at"),
    )


class HawalaEvent(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "hawala_events"
    hawala_id: Mapped[int] = mapped_column(nullable=False)
    membership_id: Mapped[int] = mapped_column(nullable=False)
    user_id: Mapped[int] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    transition_key: Mapped[str | None] = mapped_column(String(20))
    entry_id: Mapped[int | None] = mapped_column()
    details: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__: Any = (
        *shop_constraints("hawala_events"),
        actor_constraint("fk_hawala_event_actor"),
        shop_reference("fk_hawala_event_transfer", "hawala_id", "hawalas"),
        shop_reference("fk_hawala_event_entry", "entry_id", "journal_entries"),
        UniqueConstraint("hawala_id", "transition_key", name="uq_hawala_transition"),
        CheckConstraint(
            "kind IN ('created','updated','confirmed','paid','cancelled','refunded','reviewed')",
            name="ck_hawala_event_kind",
        ),
    )


class HawalaRefund(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "hawala_refunds"
    hawala_id: Mapped[int] = mapped_column(nullable=False)
    entry_id: Mapped[int] = mapped_column(nullable=False)
    amount: Mapped[str] = mapped_column(String(40), nullable=False)
    book_afn: Mapped[str] = mapped_column(String(40), nullable=False)
    __table_args__: Any = (
        *shop_constraints("hawala_refunds"),
        shop_reference("fk_refund_hawala", "hawala_id", "hawalas"),
        shop_reference("fk_refund_entry", "entry_id", "journal_entries"),
        UniqueConstraint("entry_id", name="uq_hawala_refund_entry"),
    )


class AgentObligation(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "agent_obligations"
    agent_id: Mapped[int] = mapped_column(nullable=False)
    hawala_id: Mapped[int | None] = mapped_column()
    source_entry_id: Mapped[int] = mapped_column(nullable=False)
    account_id: Mapped[int] = mapped_column(nullable=False)
    currency_id: Mapped[int] = mapped_column(nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[str] = mapped_column(String(40), nullable=False)
    book_afn: Mapped[str] = mapped_column(String(40), nullable=False)
    __table_args__: Any = (
        *shop_constraints("agent_obligations"),
        shop_reference("fk_agent_obligation_contact", "agent_id", "contacts"),
        shop_reference("fk_agent_obligation_transfer", "hawala_id", "hawalas"),
        shop_reference(
            "fk_agent_obligation_source", "source_entry_id", "journal_entries"
        ),
        shop_reference("fk_agent_obligation_account", "account_id", "ledger_accounts"),
        owned_reference("fk_agent_obligation_currency", "currency_id", "currencies"),
        UniqueConstraint(
            "source_entry_id", "account_id", "kind", name="uq_agent_obligation_source"
        ),
        CheckConstraint(
            "direction IN ('receivable','payable')",
            name="ck_agent_obligation_direction",
        ),
        CheckConstraint(
            "kind IN ('principal','customer_fee','agent_fee','opening')",
            name="ck_agent_obligation_kind",
        ),
        Index("ix_agent_obligation_contact", "organization_id", "shop_id", "agent_id"),
    )


class AgentObligationVoid(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "agent_obligation_voids"
    obligation_id: Mapped[int] = mapped_column(nullable=False, unique=True)
    entry_id: Mapped[int] = mapped_column(nullable=False)
    __table_args__: Any = (
        *shop_constraints("agent_obligation_voids"),
        shop_reference(
            "fk_agent_void_obligation", "obligation_id", "agent_obligations"
        ),
        shop_reference("fk_agent_void_entry", "entry_id", "journal_entries"),
    )


class AgentSettlement(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "agent_settlements"
    membership_id: Mapped[int] = mapped_column(nullable=False)
    user_id: Mapped[int] = mapped_column(nullable=False)
    agent_id: Mapped[int] = mapped_column(nullable=False)
    currency_id: Mapped[int] = mapped_column(nullable=False)
    cashbox_id: Mapped[int | None] = mapped_column()
    entry_id: Mapped[int] = mapped_column(nullable=False, unique=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[str] = mapped_column(String(40), nullable=False)
    reference: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__: Any = (
        *shop_constraints("agent_settlements"),
        actor_constraint("fk_settlement_actor"),
        shop_reference("fk_agent_settlement_contact", "agent_id", "contacts"),
        owned_reference("fk_agent_settlement_currency", "currency_id", "currencies"),
        shop_reference("fk_agent_settlement_cashbox", "cashbox_id", "cashboxes"),
        shop_reference("fk_agent_settlement_entry", "entry_id", "journal_entries"),
        CheckConstraint(
            "direction IN ('receive','pay')", name="ck_agent_settlement_direction"
        ),
        CheckConstraint(
            "(method = 'cash' AND cashbox_id IS NOT NULL) OR (method IN ('bank','other') AND cashbox_id IS NULL)",
            name="ck_agent_settlement_method",
        ),
        Index(
            "ix_agent_settlement_history", "organization_id", "shop_id", "created_at"
        ),
    )


class AgentAllocation(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "agent_allocations"
    settlement_id: Mapped[int] = mapped_column(nullable=False)
    obligation_id: Mapped[int] = mapped_column(nullable=False)
    amount: Mapped[str] = mapped_column(String(40), nullable=False)
    book_afn: Mapped[str] = mapped_column(String(40), nullable=False)
    __table_args__: Any = (
        *shop_constraints("agent_allocations"),
        shop_reference(
            "fk_agent_allocation_settlement", "settlement_id", "agent_settlements"
        ),
        shop_reference(
            "fk_agent_allocation_obligation", "obligation_id", "agent_obligations"
        ),
        UniqueConstraint("settlement_id", "obligation_id", name="uq_agent_allocation"),
        Index("ix_agent_allocation_obligation", "organization_id", "obligation_id"),
    )


class AgentSettlementReversal(ImmutableHistory, ShopOwned, Base):
    __tablename__ = "agent_settlement_reversals"
    settlement_id: Mapped[int] = mapped_column(nullable=False, unique=True)
    entry_id: Mapped[int] = mapped_column(nullable=False, unique=True)
    __table_args__: Any = (
        *shop_constraints("agent_settlement_reversals"),
        shop_reference(
            "fk_agent_reversal_settlement", "settlement_id", "agent_settlements"
        ),
        shop_reference("fk_agent_reversal_entry", "entry_id", "journal_entries"),
    )


def protect_hawala_drafts(target, connection, **kwargs):
    """Transfers may be edited only until confirmation; no history may be deleted."""
    condition = "EXISTS (SELECT 1 FROM hawala_events WHERE hawala_id = OLD.id AND kind IN ('confirmed','cancelled'))"
    dialect = connection.dialect.name
    if dialect == "sqlite":
        connection.exec_driver_sql(
            f"CREATE TRIGGER hawalas_frozen BEFORE UPDATE ON hawalas WHEN {condition} BEGIN SELECT RAISE(ABORT, 'Transfer is frozen'); END"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER hawalas_retained BEFORE DELETE ON hawalas BEGIN SELECT RAISE(ABORT, 'Transfer is retained'); END"
        )
    elif dialect == "postgresql":
        connection.exec_driver_sql(
            f"CREATE OR REPLACE FUNCTION hawalas_frozen() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF TG_OP = 'DELETE' OR {condition} THEN RAISE EXCEPTION 'Transfer is frozen'; END IF; RETURN NEW; END; $$"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER hawalas_frozen BEFORE UPDATE OR DELETE ON hawalas FOR EACH ROW EXECUTE FUNCTION hawalas_frozen()"
        )
    elif dialect == "mysql":
        connection.exec_driver_sql(
            f"CREATE TRIGGER hawalas_frozen BEFORE UPDATE ON hawalas FOR EACH ROW BEGIN IF {condition} THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Transfer is frozen'; END IF; END"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER hawalas_retained BEFORE DELETE ON hawalas FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Transfer is retained'"
        )


event.listen(HawalaEvent.__table__, "after_create", protect_hawala_drafts)
