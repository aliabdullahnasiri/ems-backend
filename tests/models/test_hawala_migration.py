"""Populated Phase 10 migration, opening backfill and database history protection."""

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.exc import IntegrityError

from app.models import Base


def test_hawala_migration_backfills_agent_openings_and_freezes_posted_history():
    scripts = ScriptDirectory(str(Path(__file__).resolve().parents[2] / "alembic"))
    engine = sa.create_engine("sqlite://")
    tables = Base.metadata.tables
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            with Operations.context(MigrationContext.configure(connection)):
                for rev in reversed(
                    list(scripts.iterate_revisions("f9d004f3a540", "base"))
                ):
                    rev.module.upgrade()
                for ident in (1, 2):
                    for name, values in (
                        (
                            "users",
                            {
                                "user_name": f"owner{ident}",
                                "email": f"owner{ident}@example.com",
                                "password_hash": "unused",
                            },
                        ),
                        (
                            "organizations",
                            {"name": f"Org {ident}", "slug": f"hawala-{ident}"},
                        ),
                        (
                            "organization_users",
                            {"organization_id": ident, "user_id": ident},
                        ),
                        (
                            "branches",
                            {"organization_id": ident, "name": f"Shop {ident}"},
                        ),
                        (
                            "currencies",
                            {"organization_id": ident, "code": "AFN", "precision": 2},
                        ),
                        (
                            "cashboxes",
                            {
                                "organization_id": ident,
                                "shop_id": ident,
                                "name": "Till",
                            },
                        ),
                        (
                            "contacts",
                            {
                                "organization_id": ident,
                                "shop_id": ident,
                                "name": "Agent",
                                "search_name": "agent",
                                "is_agent": True,
                            },
                        ),
                    ):
                        connection.execute(
                            tables[name].insert().values(id=ident, **values)
                        )
                actor = {
                    "organization_id": 1,
                    "shop_id": 1,
                    "membership_id": 1,
                    "user_id": 1,
                }
                connection.execute(
                    tables["ledger_accounts"]
                    .insert()
                    .values(
                        id=1,
                        organization_id=1,
                        shop_id=1,
                        currency_id=1,
                        code="agent-opening",
                        kind="payable",
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
                        account_id=1,
                        role="agent",
                        direction="payable",
                    )
                )
                connection.execute(
                    tables["journal_entries"]
                    .insert()
                    .values(
                        id=1,
                        **actor,
                        action="contact:opening",
                        reason="Reviewed agent opening",
                    )
                )
                connection.execute(
                    tables["journal_lines"]
                    .insert()
                    .values(
                        organization_id=1,
                        shop_id=1,
                        entry_id=1,
                        account_id=1,
                        amount="-10000.00",
                        book_afn="-10000.00",
                    )
                )
                connection.execute(
                    tables["journal_postings"]
                    .insert()
                    .values(organization_id=1, shop_id=1, entry_id=1)
                )
                revision = scripts.get_revision("fae005a4b650").module
                revision.upgrade()
                obligations = (
                    connection.execute(sa.select(tables["agent_obligations"]))
                    .mappings()
                    .all()
                )
                assert len(obligations) == 1
                original = obligations[0]
                assert original["agent_id"] == 1 and original["source_entry_id"] == 1
                assert original["amount"] == original["book_afn"] == "10000.00"
                assert original["kind"] == "opening" and original["hawala_id"] is None
                payload = {
                    "organization_id": 1,
                    "shop_id": 1,
                    "agent_id": 1,
                    "collection_currency_id": 1,
                    "payout_currency_id": 1,
                    "cashbox_id": 1,
                    "direction": "outgoing",
                    "reference": "HW-1",
                    "details": {"sender_name": "احمد"},
                }
                for changes in (
                    {"agent_id": 2},
                    {"cashbox_id": 2},
                    {"payout_currency_id": 2},
                    {"collection_currency_id": 2},
                ):
                    with pytest.raises(IntegrityError), connection.begin_nested():
                        connection.execute(
                            tables["hawalas"].insert().values(**{**payload, **changes})
                        )
                connection.execute(tables["hawalas"].insert().values(id=1, **payload))
                connection.execute(
                    tables["hawalas"]
                    .update()
                    .values(details={"sender_name": "Updated draft"})
                )
                event = {
                    **actor,
                    "hawala_id": 1,
                    "kind": "confirmed",
                    "transition_key": "confirmed",
                    "entry_id": 1,
                    "details": {"receipt": "Retained"},
                }
                connection.execute(tables["hawala_events"].insert().values(**event))
                for statement in (
                    tables["hawalas"].update().values(details={}),
                    tables["hawalas"].delete(),
                ):
                    with pytest.raises(IntegrityError), connection.begin_nested():
                        connection.execute(statement)
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(tables["hawala_events"].insert().values(**event))
                connection.execute(
                    tables["journal_entries"]
                    .insert()
                    .values(
                        id=2,
                        **actor,
                        action="agent_settlement:create",
                        reason="Settlement",
                    )
                )
                settlement = {
                    **actor,
                    "agent_id": 1,
                    "currency_id": 1,
                    "cashbox_id": 1,
                    "entry_id": 2,
                    "direction": "pay",
                    "method": "cash",
                    "amount": "6000.00",
                    "reference": "AS-1",
                    "details": {"reference": "External receipt"},
                }
                for changes in (
                    {"agent_id": 2},
                    {"cashbox_id": 2},
                    {"currency_id": 2},
                    {"method": "bank"},
                ):
                    with pytest.raises(IntegrityError), connection.begin_nested():
                        connection.execute(
                            tables["agent_settlements"]
                            .insert()
                            .values(**{**settlement, **changes})
                        )
                connection.execute(
                    tables["agent_settlements"].insert().values(id=1, **settlement)
                )
                allocation = {
                    "organization_id": 1,
                    "shop_id": 1,
                    "settlement_id": 1,
                    "obligation_id": original["id"],
                    "amount": "6000.00",
                    "book_afn": "6000.00",
                }
                connection.execute(
                    tables["agent_allocations"].insert().values(**allocation)
                )
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(
                        tables["agent_allocations"].insert().values(**allocation)
                    )
                connection.execute(
                    tables["journal_entries"]
                    .insert()
                    .values(
                        id=3,
                        **actor,
                        action="agent_settlement:reverse",
                        reason="Correction",
                        reversal_of_id=2,
                    )
                )
                connection.execute(
                    tables["agent_settlement_reversals"]
                    .insert()
                    .values(organization_id=1, shop_id=1, settlement_id=1, entry_id=3)
                )
                connection.execute(
                    tables["agent_obligation_voids"]
                    .insert()
                    .values(
                        organization_id=1,
                        shop_id=1,
                        obligation_id=original["id"],
                        entry_id=3,
                    )
                )
                connection.execute(
                    tables["hawala_refunds"]
                    .insert()
                    .values(
                        organization_id=1,
                        shop_id=1,
                        hawala_id=1,
                        entry_id=3,
                        amount="1.00",
                        book_afn="1.00",
                    )
                )
                for name in (
                    "hawala_events",
                    "hawala_refunds",
                    "agent_obligations",
                    "agent_obligation_voids",
                    "agent_settlements",
                    "agent_allocations",
                    "agent_settlement_reversals",
                ):
                    for statement in (
                        tables[name].delete(),
                        tables[name].update().values(shop_id=2),
                    ):
                        with pytest.raises(IntegrityError), connection.begin_nested():
                            connection.execute(statement)
                assert (
                    connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
                )
                revision.downgrade()
                revision.upgrade()
                assert (
                    len(
                        connection.execute(sa.select(tables["agent_obligations"])).all()
                    )
                    == 1
                )
    finally:
        engine.dispose()
