"""Phase 9 backfills retained customer opening debts and enforces ownership."""

from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.exc import IntegrityError

from app.models import Base


def test_phase9_backfills_customer_openings_and_retains_allocations():
    scripts = ScriptDirectory(str(Path(__file__).resolve().parents[2] / "alembic"))
    engine = sa.create_engine("sqlite://")
    tables = Base.metadata.tables
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            with Operations.context(MigrationContext.configure(connection)):
                for rev in reversed(
                    list(scripts.iterate_revisions("f8c003e2f430", "base"))
                ):
                    rev.module.upgrade()
                for ident in (1, 2):
                    connection.execute(
                        tables["users"]
                        .insert()
                        .values(
                            id=ident,
                            user_name=f"owner{ident}",
                            email=f"owner{ident}@example.com",
                            password_hash="unused",
                        )
                    )
                    connection.execute(
                        tables["organizations"]
                        .insert()
                        .values(id=ident, name=f"Org {ident}", slug=f"pay-{ident}")
                    )
                    connection.execute(
                        tables["organization_users"]
                        .insert()
                        .values(id=ident, organization_id=ident, user_id=ident)
                    )
                    connection.execute(
                        tables["branches"]
                        .insert()
                        .values(id=ident, organization_id=ident, name=f"Shop {ident}")
                    )
                    connection.execute(
                        tables["currencies"]
                        .insert()
                        .values(
                            id=ident, organization_id=ident, code="AFN", precision=2
                        )
                    )
                    connection.execute(
                        tables["cashboxes"]
                        .insert()
                        .values(
                            id=ident, organization_id=ident, shop_id=ident, name="Till"
                        )
                    )
                    connection.execute(
                        tables["contacts"]
                        .insert()
                        .values(
                            id=ident,
                            organization_id=ident,
                            shop_id=ident,
                            name="Customer",
                            search_name="customer",
                            is_agent=True,
                        )
                    )
                for ident, role, direction in (
                    (1, "customer", "receivable"),
                    (2, "customer", "payable"),
                    (3, "agent", "payable"),
                ):
                    connection.execute(
                        tables["ledger_accounts"]
                        .insert()
                        .values(
                            id=ident,
                            organization_id=1,
                            shop_id=1,
                            currency_id=1,
                            code=f"contact-{ident}",
                            kind=direction,
                        )
                    )
                    connection.execute(
                        tables["contact_accounts"]
                        .insert()
                        .values(
                            organization_id=1,
                            shop_id=1,
                            contact_id=1,
                            currency_id=1,
                            account_id=ident,
                            role=role,
                            direction=direction,
                        )
                    )
                    connection.execute(
                        tables["journal_entries"]
                        .insert()
                        .values(
                            id=ident,
                            organization_id=1,
                            shop_id=1,
                            user_id=1,
                            membership_id=1,
                            action="contact:opening",
                            reason="reviewed",
                        )
                    )
                    signed = "10000.00" if direction == "receivable" else "-10000.00"
                    connection.execute(
                        tables["journal_lines"]
                        .insert()
                        .values(
                            organization_id=1,
                            shop_id=1,
                            entry_id=ident,
                            account_id=ident,
                            amount=signed,
                            book_afn=signed,
                        )
                    )
                    connection.execute(
                        tables["journal_postings"]
                        .insert()
                        .values(organization_id=1, shop_id=1, entry_id=ident)
                    )
                revision = scripts.get_revision("f9d004f3a540").module
                revision.upgrade()
                obligations = tables["customer_obligations"]
                rows = (
                    connection.execute(
                        sa.select(obligations).order_by(obligations.c.id)
                    )
                    .mappings()
                    .all()
                )
                assert len(rows) == 2  # Agent openings remain in the agent ledger only.
                assert {r["direction"] for r in rows} == {"receivable", "payable"}
                assert all(r["amount"] == r["book_afn"] == "10000.00" for r in rows)
                assert all(
                    r["exchange_id"] is None and r["contact_id"] == 1 for r in rows
                )
                first = rows[0]
                # A payment cannot reference another organization's contact or box.
                actor = {
                    "organization_id": 1,
                    "shop_id": 1,
                    "user_id": 1,
                    "membership_id": 1,
                }
                connection.execute(
                    tables["journal_entries"]
                    .insert()
                    .values(id=4, **actor, action="payment:create", reason="payment")
                )
                payload = {
                    **actor,
                    "contact_id": 1,
                    "cashbox_id": 1,
                    "currency_id": 1,
                    "direction": "receive",
                    "amount": "6000.00",
                    "entry_id": 4,
                    "reference": "PAY-1",
                    "receipt": {},
                }
                for changes in (
                    {"contact_id": 2},
                    {"cashbox_id": 2},
                    {"currency_id": 2},
                    {"membership_id": 2},
                ):
                    with pytest.raises(IntegrityError), connection.begin_nested():
                        connection.execute(
                            tables["payments"].insert().values(**{**payload, **changes})
                        )
                connection.execute(tables["payments"].insert().values(id=1, **payload))
                allocation = {
                    "organization_id": 1,
                    "shop_id": 1,
                    "payment_id": 1,
                    "obligation_id": first["id"],
                    "amount": "6000.00",
                    "book_afn": "6000.00",
                }
                connection.execute(
                    tables["payment_allocations"].insert().values(**allocation)
                )
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(
                        tables["payment_allocations"].insert().values(**allocation)
                    )
                connection.execute(
                    tables["journal_entries"]
                    .insert()
                    .values(
                        id=5,
                        **actor,
                        action="payment:reverse",
                        reason="correction",
                        reversal_of_id=4,
                    )
                )
                reversal = {
                    "organization_id": 1,
                    "shop_id": 1,
                    "payment_id": 1,
                    "entry_id": 5,
                }
                connection.execute(
                    tables["payment_reversals"].insert().values(**reversal)
                )
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(
                        tables["payment_reversals"].insert().values(**reversal)
                    )
                for name in (
                    "customer_obligations",
                    "payments",
                    "payment_allocations",
                    "payment_reversals",
                ):
                    for statement in (
                        tables[name].delete(),
                        tables[name].update().values(uuid=uuid4()),
                    ):
                        with pytest.raises(IntegrityError), connection.begin_nested():
                            connection.execute(statement)
                assert (
                    connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
                )
                # Re-upgrade recreates obligations from the retained original openings.
                revision.downgrade()
                revision.upgrade()
                assert len(connection.execute(sa.select(obligations)).all()) == 2
    finally:
        engine.dispose()
