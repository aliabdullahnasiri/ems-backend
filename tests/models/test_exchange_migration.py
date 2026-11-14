"""Phase 8 schema ownership, retention and uniqueness on a migrated database."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Base
from tests.models.test_initial_migration import migrated_database as migration_fixture

migrated_database = pytest.fixture(migration_fixture.__wrapped__)


def test_exchange_ownership_reversal_and_immutable_receipt_constraints(
    migrated_database,
):
    connection, _ = migrated_database
    tables = Base.metadata.tables
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
            .values(id=ident, name=f"Org {ident}", slug=f"exchange-{ident}")
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
            .values(id=ident, organization_id=ident, code="AFN", precision=2)
        )
        connection.execute(
            tables["cashboxes"]
            .insert()
            .values(id=ident, organization_id=ident, shop_id=ident, name="Till")
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
            )
        )
        connection.execute(
            tables["journal_entries"]
            .insert()
            .values(
                id=ident,
                organization_id=ident,
                shop_id=ident,
                membership_id=ident,
                user_id=ident,
                action="exchange:create",
                reason="exchange",
            )
        )
    connection.execute(
        tables["currencies"]
        .insert()
        .values(id=3, organization_id=1, code="USD", precision=2)
    )
    actor = {"organization_id": 1, "shop_id": 1, "membership_id": 1, "user_id": 1}
    rate = {
        **actor,
        "base_currency_id": 3,
        "quote_currency_id": 1,
        "buying_rate": "70",
        "selling_rate": "71",
    }
    for changes in ({"base_currency_id": 2}, {"membership_id": 2}, {"shop_id": 2}):
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                tables["exchange_rates"].insert().values(**{**rate, **changes})
            )
    connection.execute(tables["exchange_rates"].insert().values(id=1, **rate))
    exchange = {
        **actor,
        "cashbox_id": 1,
        "contact_id": 1,
        "base_currency_id": 3,
        "quote_currency_id": 1,
        "entry_id": 1,
        "reference": "EX-1",
        "details": {"customer_name": "احمد"},
    }
    for changes in (
        {"contact_id": 2},
        {"cashbox_id": 2},
        {"entry_id": 2},
        {"base_currency_id": 2},
    ):
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                tables["exchanges"].insert().values(**{**exchange, **changes})
            )
    connection.execute(tables["exchanges"].insert().values(id=1, **exchange))
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(tables["exchanges"].insert().values(**exchange))
    connection.execute(
        tables["journal_entries"]
        .insert()
        .values(
            id=3,
            **actor,
            action="exchange:reverse",
            reason="correction",
            reversal_of_id=1,
        )
    )
    reversal = {"organization_id": 1, "shop_id": 1, "exchange_id": 1, "entry_id": 3}
    connection.execute(tables["exchange_reversals"].insert().values(**reversal))
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(tables["exchange_reversals"].insert().values(**reversal))
    connection.execute(tables["exchange_payment_links"].insert().values(**reversal))
    for name in (
        "exchange_rates",
        "exchanges",
        "exchange_reversals",
        "exchange_payment_links",
    ):
        for statement in (
            tables[name].update().values(shop_id=2),
            tables[name].delete(),
        ):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(statement)
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            tables["contacts"].delete().where(tables["contacts"].c.id == 1)
        )
    assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
