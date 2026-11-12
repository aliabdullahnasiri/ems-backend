from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from app.models.finance import (
    Currency,
    JournalEntry,
    JournalLine,
    JournalPosting,
    LedgerAccount,
)
from app.services.finance import LedgerService
from sqlalchemy import select

from app.core.exceptions import (
    AppValidationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
)
from app.models.contact import ContactAccount
from app.schemas.contact.request import (
    ContactCreateRequest,
    ContactOpeningRequest,
    ContactUpdateRequest,
)
from app.services.contact import ContactService
from app.services.contact_ledger import ContactLedgerService
from tests.services.test_tenant_operations import tenants as tenant_fixture

tenants = pytest_asyncio.fixture(tenant_fixture.__wrapped__)


@pytest_asyncio.fixture
async def contact_ledger(db_session, tenants):
    _, _, _, _, context, other, shop, _ = tenants
    afn = context.new(Currency, code="AFN", precision=2)
    usd = context.new(Currency, code="USD", precision=2)
    db_session.add_all([afn, usd])
    await db_session.flush()
    contact = await ContactService.create(
        db_session,
        context,
        ContactCreateRequest(shop_uuid=shop.uuid, name="احمد", is_agent=True),
    )
    return context, other, shop, contact, afn, usd


def opening(currency, **kwargs):
    return ContactOpeningRequest(
        currency_id=currency.id,
        role="customer",
        direction="receivable",
        amount="10000.00",
        book_afn="10000.00",
        reason="Reviewed original books",
        owner_approved=True,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_opening_debts_are_separate_and_ledger_traceable(
    db_session, contact_ledger
):
    ctx, other, _shop, contact, afn, usd = contact_ledger
    data = opening(afn)
    first = await ContactLedgerService.opening(
        db_session, ctx, contact.uuid, "debt-1", data
    )
    assert first == await ContactLedgerService.opening(
        db_session, ctx, contact.uuid, "debt-1", data
    )
    with pytest.raises(ConflictError):
        await ContactLedgerService.opening(
            db_session,
            ctx,
            contact.uuid,
            "debt-1",
            data.model_copy(update={"amount": "999.00"}),
        )
    with pytest.raises(ConflictError):
        await ContactLedgerService.opening(
            db_session, ctx, contact.uuid, "debt-2", data
        )
    for key, role, direction, currency, amount, cost in [
        ("customer-payable", "customer", "payable", afn, "3000.00", "3000.00"),
        ("agent-receivable", "agent", "receivable", afn, "500.00", "500.00"),
        ("agent-usd", "agent", "payable", usd, "10.00", "700.00"),
    ]:
        await ContactLedgerService.opening(
            db_session,
            ctx,
            contact.uuid,
            key,
            data.model_copy(
                update={
                    "role": role,
                    "direction": direction,
                    "currency_id": currency.id,
                    "amount": amount,
                    "book_afn": cost,
                }
            ),
        )
    balances = await ContactLedgerService.balances(db_session, ctx, contact.uuid)
    by_key = {(b.role, b.currency): b for b in balances}
    assert by_key["customer", "AFN"].receivable == "10000.00"
    assert by_key["customer", "AFN"].payable == "3000.00"
    assert by_key["customer", "AFN"].net_receivable == "7000.00"
    assert by_key["agent", "AFN"].receivable == "500.00"
    assert by_key["agent", "USD"].payable == "10.00"
    assert by_key["agent", "USD"].net_receivable == "-10.00"
    assert all(
        b.receivable_direction == "contact_owes_shop"
        and b.payable_direction == "shop_owes_contact"
        for b in balances
    )
    accounts = list(await db_session.scalars(select(LedgerAccount)))
    assert not {"cash", "income", "expense"} & {a.kind for a in accounts}
    entries = list(await db_session.scalars(select(JournalEntry)))
    assert len(entries) == 4
    for entry in entries:
        lines = list(
            await db_session.scalars(
                select(JournalLine).where(JournalLine.entry_id == entry.id)
            )
        )
        assert sum(Decimal(line.amount) for line in lines) == 0
        assert sum(Decimal(line.book_afn) for line in lines) == 0
        assert await db_session.scalar(
            select(JournalPosting.id).where(JournalPosting.entry_id == entry.id)
        )
    with pytest.raises(NotFoundError):
        await ContactLedgerService.balances(db_session, other, contact.uuid)
    await ContactService.update(
        db_session, ctx, contact.uuid, ContactUpdateRequest(is_active=False)
    )
    assert (
        await ContactLedgerService.balances(db_session, ctx, contact.uuid) == balances
    )


@pytest.mark.asyncio
async def test_invalid_openings_leave_no_partial_accounts_or_entries(
    db_session, contact_ledger
):
    ctx, _, _, contact, afn, _ = contact_ledger
    for changes, error in [
        ({"owner_approved": False}, AuthorizationError),
        ({"amount": "0.00"}, AppValidationError),
        ({"amount": "1.001"}, AppValidationError),
        ({"amount": "1e4"}, AppValidationError),
        ({"book_afn": "2.00"}, AppValidationError),
    ]:
        with pytest.raises(error):
            await ContactLedgerService.opening(
                db_session,
                ctx,
                contact.uuid,
                "invalid",
                opening(afn).model_copy(update=changes),
            )
        assert await db_session.scalar(select(JournalEntry.id)) is None
        assert await db_session.scalar(select(ContactAccount.id)) is None
        assert await db_session.scalar(select(LedgerAccount.id)) is None


@pytest.mark.asyncio
async def test_dates_pagination_running_balances_and_csv(db_session, contact_ledger):
    ctx, _, _shop, contact, afn, usd = contact_ledger
    # Journal timestamps are fixed at insertion, never edited after posting.
    from unittest.mock import patch

    from app.services import finance

    real_entry = finance.JournalEntry
    times = iter(
        [
            datetime(2026, 11, 9, 19, 29, tzinfo=UTC),
            datetime(2026, 11, 9, 19, 30, tzinfo=UTC),
            datetime(2026, 11, 10, 19, 29, tzinfo=UTC),
            datetime(2026, 11, 10, 19, 30, tzinfo=UTC),
        ]
    )

    def make_entry(**kwargs):
        return real_entry(created_at=next(times), **kwargs)

    with patch.object(finance, "JournalEntry", side_effect=make_entry):
        await ContactLedgerService.opening(
            db_session, ctx, contact.uuid, "before", opening(afn)
        )
        await ContactLedgerService.opening(
            db_session,
            ctx,
            contact.uuid,
            "start",
            opening(afn).model_copy(
                update={
                    "direction": "payable",
                    "amount": "3000.00",
                    "book_afn": "3000.00",
                }
            ),
        )
        await ContactLedgerService.opening(
            db_session,
            ctx,
            contact.uuid,
            "end",
            opening(afn).model_copy(
                update={"currency_id": usd.id, "amount": "10.00", "book_afn": "700.00"}
            ),
        )
        await ContactLedgerService.opening(
            db_session,
            ctx,
            contact.uuid,
            "after",
            opening(afn).model_copy(update={"role": "agent"}),
        )
    statement = await ContactLedgerService.statement(
        db_session,
        ctx,
        contact.uuid,
        date(2026, 11, 10),
        date(2026, 11, 10),
        offset=1,
        limit=1,
    )
    assert statement.total_movements == 2 and len(statement.movements) == 1
    assert statement.movements[0].currency == "USD"
    assert statement.movements[0].running_net_receivable == "10.00"
    assert statement.opening_balances[0].receivable == "10000.00"
    assert statement.closing_balances[0].net_receivable == "7000.00"
    full = await ContactLedgerService.statement(
        db_session, ctx, contact.uuid, date(2026, 11, 10), date(2026, 11, 10)
    )
    assert full.movements[0].running_net_receivable == "7000.00"
    export = await ContactLedgerService.export(
        db_session, ctx, contact.uuid, date(2026, 11, 10), date(2026, 11, 10)
    )
    assert "احمد" in export and "opening,customer,AFN,10000.00" in export
    assert "closing,customer,AFN,10000.00,3000.00,7000.00" in export
    assert export.count("movement,") == 2
    with pytest.raises(AppValidationError):
        await ContactLedgerService.statement(
            db_session, ctx, contact.uuid, date(2026, 11, 11), date(2026, 11, 10)
        )


@pytest.mark.asyncio
async def test_posted_movements_and_linked_reversals_reconcile(
    db_session, contact_ledger
):
    """Later workflows reuse the same contact account without editable balance fields."""
    ctx, _, shop, contact, afn, _ = contact_ledger
    await ContactLedgerService.opening(
        db_session, ctx, contact.uuid, "opening", opening(afn)
    )
    account_id = await ContactLedgerService.account(
        db_session, ctx, contact, "customer", "receivable", afn.id
    )
    offset = await LedgerService.account(
        db_session, ctx, shop.id, afn.id, "test-clearing", "clearing"
    )
    posted = await LedgerService.post(
        db_session,
        ctx,
        shop.id,
        "payment:create",
        "payment",
        "Partial settlement",
        [
            {"account_id": account_id, "amount": "-6000.00", "book_afn": "-6000.00"},
            {"account_id": offset.id, "amount": "6000.00", "book_afn": "6000.00"},
        ],
    )
    balances = await ContactLedgerService.balances(db_session, ctx, contact.uuid)
    assert balances[0].receivable == "4000.00"
    # Test the journal link/sign contract; payment allocation is introduced in Phase 9.
    await LedgerService._write(
        db_session,
        ctx,
        shop.id,
        "payment:reverse",
        "Reviewed correction",
        [
            {"account_id": account_id, "amount": "6000.00", "book_afn": "6000.00"},
            {"account_id": offset.id, "amount": "-6000.00", "book_afn": "-6000.00"},
        ],
        reversal_of_id=posted["id"],
    )
    statement = await ContactLedgerService.statement(db_session, ctx, contact.uuid)
    assert [m.running_net_receivable for m in statement.movements] == [
        "10000.00",
        "4000.00",
        "10000.00",
    ]
    assert statement.closing_balances[0].receivable == "10000.00"


@pytest.mark.asyncio
async def test_concurrent_contact_openings_post_once(tmp_path):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, User
    from app.services.organization import OrganizationService
    from app.services.organization_context import OrganizationContext
    from app.services.shop import ShopService

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'contact-concurrent.sqlite'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as db:
            user = User(
                user_name="contact-concurrent",
                email="contact-concurrent@example.com",
                password_hash="unused",
            )
            db.add(user)
            await db.flush()
            org = await OrganizationService.create(
                db, "Contact concurrency", "contact-concurrent", user
            )
            ctx = await OrganizationContext.resolve(db, user.id, org.id)
            shop = await ShopService.create(db, ctx, "Shop")
            currency = ctx.new(Currency, code="AFN", precision=2)
            db.add(currency)
            await db.flush()
            contact = await ContactService.create(
                db, ctx, ContactCreateRequest(shop_uuid=shop.uuid, name="Concurrent")
            )
            data = opening(currency)
            await db.commit()

        async def post():
            async with sessions() as db:
                result = await ContactLedgerService.opening(
                    db, ctx, contact.uuid, "same-opening", data
                )
                await db.commit()
                return result

        first, second = await asyncio.wait_for(
            asyncio.gather(post(), post()), timeout=15
        )
        assert first == second
        async with sessions() as db:
            assert len(list(await db.scalars(select(JournalEntry)))) == 1
            assert len(list(await db.scalars(select(ContactAccount)))) == 1
            assert (await ContactLedgerService.balances(db, ctx, contact.uuid))[
                0
            ].receivable == "10000.00"
    finally:
        await engine.dispose()
