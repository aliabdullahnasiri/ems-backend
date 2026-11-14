"""Phase 8 acceptance, accounting, retry, history and security coverage."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    AppValidationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
)
from app.models import User
from app.models.exchange import Exchange, ExchangePaymentLink, ExchangeRate
from app.models.finance import JournalEntry, JournalLine, LedgerAccount
from app.schemas.exchange.request import (
    ExchangeCreateRequest,
    ExchangeQuoteRequest,
    ExchangeReverseRequest,
    RateCreateRequest,
)
from app.schemas.finance.request import (
    CashboxUpdateRequest,
    CurrencyUpdateRequest,
    SettingsUpdateRequest,
)
from app.services.exchange import ExchangeService
from app.services.finance import FinanceService, LedgerService
from tests.services.test_finance import finance as finance  # noqa: PLC0414
from tests.services.test_finance import opening
from tests.services.test_tenant_operations import tenants as tenants  # noqa: PLC0414


def trade(currencies, box, **changes):
    return ExchangeCreateRequest(
        **{
            "cashbox_id": box.id,
            "base_currency_id": currencies["USD"].id,
            "quote_currency_id": currencies["AFN"].id,
            "side": "buy",
            "amount": "100.00",
            "rate": "70",
            "reason": "تبادله",
            **changes,
        }
    )


async def seed(db, context, shop, currencies, box):
    await LedgerService.opening(
        db,
        context,
        shop.id,
        "afn-opening",
        opening(box, currencies["AFN"], "20000.00", "20000.00"),
    )
    await LedgerService.opening(
        db,
        context,
        shop.id,
        "usd-opening",
        opening(box, currencies["USD"], "100.00", "7000.00"),
    )


async def cash(db, context, shop):
    return {
        b.currency: (b.amount, b.book_afn)
        for b in (await FinanceService.cashboxes(db, context, shop.id))[0].balances
    }


@pytest.mark.asyncio
async def test_buy_sell_cost_profit_receipt_retries_and_reversal(db_session, finance):
    ca, _, shop, _, currencies, box = finance
    await seed(db_session, ca, shop, currencies, box)
    data = trade(currencies, box)
    before = await cash(db_session, ca, shop)
    quote = await ExchangeService.quote(db_session, ca, shop.id, data)
    assert quote.incoming_amount == "100.00" and quote.outgoing_amount == "7000.00"
    assert await cash(db_session, ca, shop) == before
    first = await ExchangeService.create(db_session, ca, shop.id, "buy", data)
    assert await ExchangeService.create(db_session, ca, shop.id, "buy", data) == first
    assert await cash(db_session, ca, shop) == {
        "AFN": ("13000.00", "13000.00"),
        "USD": ("200.00", "14000.00"),
    }
    with pytest.raises(ConflictError):
        await ExchangeService.create(
            db_session, ca, shop.id, "buy", trade(currencies, box, rate="71")
        )
    sold = await ExchangeService.create(
        db_session,
        ca,
        shop.id,
        "sell",
        trade(currencies, box, side="sell", amount="10", rate="71"),
    )
    assert sold["receipt"]["values"]["profit_afn"] == "10.00"
    assert await cash(db_session, ca, shop) == {
        "AFN": ("13710.00", "13710.00"),
        "USD": ("190.00", "13300.00"),
    }
    income = await db_session.scalar(
        select(LedgerAccount).where(LedgerAccount.code == "exchange_profit")
    )
    assert await LedgerService.balance(db_session, ca, income.id) == (
        Decimal(-10),
        Decimal(-10),
    )
    # Per-currency original amounts and total book value balance independently.
    totals, book = {}, Decimal(0)
    for line in await db_session.scalars(
        select(JournalLine).where(JournalLine.entry_id == sold["entry_id"])
    ):
        account = await db_session.get(LedgerAccount, line.account_id)
        totals[account.currency_id] = totals.get(
            account.currency_id, Decimal(0)
        ) + Decimal(line.amount)
        book += Decimal(line.book_afn)
    assert not any(totals.values()) and book == 0
    reverse = ExchangeReverseRequest(reason="اصلاح", owner_approved=True)
    reversal = await ExchangeService.reverse(
        db_session, ca, shop.id, UUID(sold["uuid"]), "reverse", reverse
    )
    assert reversal["reversal_of_id"] == sold["entry_id"]
    assert (
        await ExchangeService.reverse(
            db_session, ca, shop.id, UUID(sold["uuid"]), "reverse", reverse
        )
        == reversal
    )
    with pytest.raises(ConflictError):
        await ExchangeService.reverse(
            db_session, ca, shop.id, UUID(sold["uuid"]), "reverse-again", reverse
        )
    assert await LedgerService.balance(db_session, ca, income.id) == (
        Decimal(0),
        Decimal(0),
    )
    assert await cash(db_session, ca, shop) == {
        "AFN": ("13000.00", "13000.00"),
        "USD": ("200.00", "14000.00"),
    }
    receipt = sold["receipt"]
    await FinanceService.update_settings(
        db_session,
        ca,
        shop.id,
        SettingsUpdateRequest(name="نام جدید", receipt_footer="تشکر"),
    )
    await ExchangeService.create_rate(
        db_session,
        ca,
        shop.id,
        RateCreateRequest(
            base_currency_id=currencies["USD"].id,
            quote_currency_id=currencies["AFN"].id,
            buying_rate="80",
            selling_rate="81",
        ),
    )
    cashier = await db_session.get(User, ca.user_id)
    cashier.user_name = "changed-name"
    await db_session.flush()
    for _ in range(2):
        result = await ExchangeService.get(db_session, ca, shop.id, UUID(sold["uuid"]))
        assert result.receipt.model_dump(mode="json") == receipt
        assert result.reversal_entry_id == reversal["id"]
    assert len(list(await db_session.scalars(select(JournalEntry)))) == 5


@pytest.mark.asyncio
async def test_rounding_validation_precision_and_no_partial_posting(
    db_session, finance
):
    ca, _, shop, _, currencies, box = finance
    await seed(db_session, ca, shop, currencies, box)
    data = trade(currencies, box, amount="۰٫۰۱", rate="70.555")
    quote = await ExchangeService.quote(db_session, ca, shop.id, data)
    assert quote.outgoing_amount == "0.71" and quote.rounding_adjustment == "0.00445"
    before = await cash(db_session, ca, shop)
    for changes in (
        {"amount": "0"},
        {"rate": "0"},
        {"amount": "1.001"},
        {"amount": "1e2"},
        {"amount": "-1"},
        {"rate": "NaN"},
        {"rate": "0.00000001", "amount": "0.01"},
        {"base_currency_id": currencies["AFN"].id},
        {"incoming_afn_rate": "70"},
    ):
        with pytest.raises(AppValidationError):
            await ExchangeService.create(
                db_session, ca, shop.id, "bad", trade(currencies, box, **changes)
            )
    with pytest.raises(ConflictError):
        await ExchangeService.create(
            db_session, ca, shop.id, "too-much", trade(currencies, box, amount="1000")
        )
    assert await cash(db_session, ca, shop) == before
    assert len(list(await db_session.scalars(select(Exchange)))) == 0
    assert len(list(await db_session.scalars(select(JournalEntry)))) == 2
    with pytest.raises(AppValidationError):
        ExchangeQuoteRequest(**{**data.model_dump(exclude={"reason"}), "amount": 1.0})
    with pytest.raises(AppValidationError):
        ExchangeReverseRequest(reason="reason", owner_approved=1)


@pytest.mark.asyncio
async def test_cross_pair_explicit_valuation_and_weighted_cost_exhaustion(
    db_session, finance
):
    ca, _, shop, _, currencies, box = finance
    await seed(db_session, ca, shop, currencies, box)
    # Buy PKR by paying USD; AFN valuation is explicit, never guessed from live rates.
    data = trade(
        currencies,
        box,
        base_currency_id=currencies["PKR"].id,
        quote_currency_id=currencies["USD"].id,
        amount="1000",
        rate="0.01",
        incoming_afn_rate="0.71",
    )
    result = await ExchangeService.create(db_session, ca, shop.id, "cross", data)
    assert result["receipt"]["values"]["profit_afn"] == "10.00"
    assert await cash(db_session, ca, shop) == {
        "AFN": ("20000.00", "20000.00"),
        "USD": ("90.00", "6300.00"),
        "PKR": ("1000.00", "710.00"),
    }
    result = await ExchangeService.create(
        db_session,
        ca,
        shop.id,
        "sell-all",
        trade(currencies, box, side="sell", amount="90", rate="69"),
    )
    assert result["receipt"]["values"]["profit_afn"] == "-90.00"
    assert (await cash(db_session, ca, shop))["USD"] == ("0.00", "0.00")
    expense = await db_session.scalar(
        select(LedgerAccount).where(LedgerAccount.code == "exchange_loss")
    )
    assert await LedgerService.balance(db_session, ca, expense.id) == (
        Decimal(90),
        Decimal(90),
    )


@pytest.mark.asyncio
async def test_rates_history_filters_and_immutable_rows(db_session, finance):
    ca, cb, shop, other_shop, currencies, _box = finance
    pair = {
        "base_currency_id": currencies["USD"].id,
        "quote_currency_id": currencies["AFN"].id,
    }
    one = await ExchangeService.create_rate(
        db_session,
        ca,
        shop.id,
        RateCreateRequest(**pair, buying_rate="۷۰", selling_rate="71"),
    )
    two = await ExchangeService.create_rate(
        db_session,
        ca,
        shop.id,
        RateCreateRequest(**pair, buying_rate="72", selling_rate="73"),
    )
    today = datetime.now(UTC).astimezone(ZoneInfo("Asia/Kabul")).date()
    history = await ExchangeService.rates(
        db_session, ca, shop.id, date_from=today, date_to=today
    )
    assert [r.id for r in history] == [two.id, one.id]
    assert (
        one.buying_rate == "70.00000000"
        and one.direction == "quote_per_base"
        and one.user_id == ca.user_id
    )
    assert not await ExchangeService.rates(
        db_session, ca, shop.id, date_to=date(2000, 1, 1)
    )
    with pytest.raises(AppValidationError):
        await ExchangeService.rates(
            db_session, ca, shop.id, date_from=today, date_to=date(2000, 1, 1)
        )
    with pytest.raises(NotFoundError):
        await ExchangeService.create_rate(
            db_session,
            cb,
            other_shop.id,
            RateCreateRequest(**pair, buying_rate="70", selling_rate="71"),
        )
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                update(ExchangeRate)
                .where(ExchangeRate.id == one.id)
                .values(buying_rate="99")
            )


@pytest.mark.asyncio
async def test_ownership_inactive_references_filters_and_insufficient_reversal(
    db_session, finance
):
    ca, cb, shop, other_shop, currencies, box = finance
    await seed(db_session, ca, shop, currencies, box)
    data = trade(currencies, box)
    with pytest.raises(NotFoundError):
        await ExchangeService.create(db_session, cb, other_shop.id, "foreign", data)
    first = await ExchangeService.create(db_session, ca, shop.id, "buy", data)
    with pytest.raises(NotFoundError):
        await ExchangeService.get(db_session, cb, other_shop.id, UUID(first["uuid"]))
    assert (
        len(
            await ExchangeService.list(
                db_session,
                ca,
                shop.id,
                reference=first["reference"],
                cashier_id=ca.user_id,
                currency_id=currencies["USD"].id,
            )
        )
        == 1
    )
    assert not await ExchangeService.list(db_session, ca, shop.id, reference="missing")
    assert not await ExchangeService.list(
        db_session, ca, shop.id, currency_id=currencies["PKR"].id
    )
    # Spend all USD, then the original buy cannot be undone.
    await ExchangeService.create(
        db_session,
        ca,
        shop.id,
        "sell",
        trade(currencies, box, side="sell", amount="200", rate="71"),
    )
    with pytest.raises(ConflictError):
        await ExchangeService.reverse(
            db_session,
            ca,
            shop.id,
            UUID(first["uuid"]),
            "reverse",
            ExchangeReverseRequest(reason="correction", owner_approved=True),
        )
    await FinanceService.update_currency(
        db_session, ca, currencies["USD"].id, CurrencyUpdateRequest(is_active=False)
    )
    with pytest.raises(AuthorizationError):
        await ExchangeService.create(db_session, ca, shop.id, "inactive-unit", data)
    # Historical reads still work with inactive units/contacts/cashboxes.
    await FinanceService.update_cashbox(
        db_session, ca, shop.id, box.id, CashboxUpdateRequest(is_active=False)
    )
    assert (
        await ExchangeService.get(db_session, ca, shop.id, UUID(first["uuid"]))
    ).reference == first["reference"]


@pytest.mark.asyncio
async def test_failure_after_posting_rolls_back_all_effects(
    db_session, finance, monkeypatch
):
    ca, _, shop, _, currencies, box = finance
    await seed(db_session, ca, shop, currencies, box)
    before = await cash(db_session, ca, shop)

    async def fail(*args):
        raise RuntimeError("receipt failure")

    monkeypatch.setattr(ExchangeService, "exchange_data", fail)
    with pytest.raises(RuntimeError, match="receipt failure"):
        await ExchangeService.create(
            db_session, ca, shop.id, "fail", trade(currencies, box)
        )
    assert await cash(db_session, ca, shop) == before
    assert not list(await db_session.scalars(select(Exchange)))
    assert len(list(await db_session.scalars(select(JournalEntry)))) == 2


@pytest.mark.asyncio
async def test_live_linked_payment_blocks_exchange_reversal(db_session, finance):
    ca, _, shop, _, currencies, box = finance
    await seed(db_session, ca, shop, currencies, box)
    first = await ExchangeService.create(
        db_session, ca, shop.id, "buy", trade(currencies, box)
    )
    # Model a later payment journal with the posting kernel, linked atomically by its service.
    cash_account = await db_session.scalar(
        select(LedgerAccount).where(
            LedgerAccount.kind == "cash",
            LedgerAccount.currency_id == currencies["AFN"].id,
        )
    )
    receivable = await LedgerService.account(
        db_session, ca, shop.id, currencies["AFN"].id, "test-receivable", "receivable"
    )
    payment = await LedgerService.post(
        db_session,
        ca,
        shop.id,
        "payment:create",
        "payment",
        "settlement",
        [
            {"account_id": cash_account.id, "amount": "100.00", "book_afn": "100.00"},
            {"account_id": receivable.id, "amount": "-100.00", "book_afn": "-100.00"},
        ],
    )
    db_session.add(
        ca.new(
            ExchangePaymentLink,
            shop_id=shop.id,
            exchange_id=first["id"],
            entry_id=payment["id"],
        )
    )
    await db_session.flush()
    with pytest.raises(ConflictError):
        await ExchangeService.reverse(
            db_session,
            ca,
            shop.id,
            UUID(first["uuid"]),
            "reverse",
            ExchangeReverseRequest(reason="correction", owner_approved=True),
        )

    # Once the payment service posts its own linked reversal, exchange reversal works.
    from app.services.tenant_operations import TenantOperations

    async def reverse_payment():
        return await LedgerService._write(
            db_session,
            ca,
            shop.id,
            "payment:reverse",
            "restore allocation",
            [
                {
                    "account_id": cash_account.id,
                    "amount": "-100.00",
                    "book_afn": "-100.00",
                },
                {"account_id": receivable.id, "amount": "100.00", "book_afn": "100.00"},
            ],
            reversal_of_id=payment["id"],
        )

    await TenantOperations.execute_once(
        db_session,
        ca,
        "payment:reverse",
        "payment-reversal",
        {"payment_id": payment["id"]},
        "payment:reverse",
        reverse_payment,
    )
    result = await ExchangeService.reverse(
        db_session,
        ca,
        shop.id,
        UUID(first["uuid"]),
        "reverse",
        ExchangeReverseRequest(reason="correction", owner_approved=True),
    )
    assert result["reversal_of_id"] == first["entry_id"]


@pytest.mark.asyncio
async def test_customer_snapshot_and_active_customer_ownership(db_session, finance):
    from app.schemas.contact.request import ContactCreateRequest, ContactUpdateRequest
    from app.services.contact import ContactService

    ca, cb, shop, other_shop, currencies, box = finance
    await seed(db_session, ca, shop, currencies, box)
    customer = await ContactService.create(
        db_session, ca, ContactCreateRequest(shop_uuid=shop.uuid, name="احمد پښتون")
    )
    foreign = await ContactService.create(
        db_session, cb, ContactCreateRequest(shop_uuid=other_shop.uuid, name="Other")
    )
    agent = await ContactService.create(
        db_session,
        ca,
        ContactCreateRequest(
            shop_uuid=shop.uuid, name="Agent", is_customer=False, is_agent=True
        ),
    )
    with pytest.raises(NotFoundError):
        await ExchangeService.create(
            db_session,
            ca,
            shop.id,
            "foreign-customer",
            trade(currencies, box, contact_id=foreign.id),
        )
    with pytest.raises(AppValidationError):
        await ExchangeService.create(
            db_session,
            ca,
            shop.id,
            "agent",
            trade(currencies, box, contact_id=agent.id),
        )
    result = await ExchangeService.create(
        db_session,
        ca,
        shop.id,
        "customer",
        trade(currencies, box, contact_id=customer.id),
    )
    assert result["receipt"]["customer_name"] == "احمد پښتون"
    assert (
        len(await ExchangeService.list(db_session, ca, shop.id, contact_id=customer.id))
        == 1
    )
    await ContactService.update(
        db_session,
        ca,
        customer.uuid,
        ContactUpdateRequest(name="New name", is_active=False),
    )
    assert (
        await ExchangeService.get(db_session, ca, shop.id, UUID(result["uuid"]))
    ).receipt.customer_name == "احمد پښتون"
    with pytest.raises(AuthorizationError):
        await ExchangeService.create(
            db_session,
            ca,
            shop.id,
            "inactive-customer",
            trade(currencies, box, contact_id=customer.id),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("same_retry", [False, True])
async def test_exchange_concurrent_spends_and_retries(tmp_path, same_retry):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base
    from app.schemas.finance.request import CashboxCreateRequest, CurrencyCreateRequest
    from app.services.organization import OrganizationService
    from app.services.organization_context import OrganizationContext
    from app.services.shop import ShopService

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'exchanges.sqlite'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as db:
            user = User(
                user_name="owner", email="exchange@example.com", password_hash="unused"
            )
            db.add(user)
            await db.flush()
            org = await OrganizationService.create(
                db, "Concurrent", "exchange-concurrent", user
            )
            context = await OrganizationContext.resolve(db, user.id, org.id)
            shop = await ShopService.create(db, context, "Concurrent shop")
            currencies = {
                code: await FinanceService.create_currency(
                    db, context, CurrencyCreateRequest(code=code, precision=2)
                )
                for code in ("AFN", "USD")
            }
            box = await FinanceService.create_cashbox(
                db, context, shop.id, CashboxCreateRequest(name="Till")
            )
            await LedgerService.opening(
                db,
                context,
                shop.id,
                "opening",
                opening(box, currencies["AFN"], "7000", "7000"),
            )
            payload = trade(currencies, box, amount="60")
            user_id, org_id, shop_id = user.id, org.id, shop.id
            await db.commit()
        ready = asyncio.Event()

        async def spend(second):
            async with sessions() as db:
                context = await OrganizationContext.resolve(db, user_id, org_id)
                if second:
                    ready.set()
                else:
                    await ready.wait()
                try:
                    result = await ExchangeService.create(
                        db,
                        context,
                        shop_id,
                        "same" if same_retry else f"spend-{second}",
                        payload,
                    )
                    await db.commit()
                    return result
                except ConflictError:
                    await db.rollback()
                    return None

        results = await asyncio.wait_for(
            asyncio.gather(spend(False), spend(True)), timeout=20
        )
        if same_retry:
            assert results[0] == results[1] and results[0] is not None
        else:
            assert sum(result is not None for result in results) == 1
        async with sessions() as db:
            context = await OrganizationContext.resolve(db, user_id, org_id)
            assert await cash(db, context, shop) == {
                "AFN": ("2800.00", "2800.00"),
                "USD": ("60.00", "4200.00"),
            }
            assert len(list(await db.scalars(select(Exchange)))) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_weighted_average_after_purchase_at_different_rate(db_session, finance):
    ca, _, shop, _, currencies, box = finance
    await seed(db_session, ca, shop, currencies, box)
    await ExchangeService.create(
        db_session, ca, shop.id, "buy72", trade(currencies, box, rate="72")
    )
    assert (await cash(db_session, ca, shop))["USD"] == ("200.00", "14200.00")
    sold = await ExchangeService.create(
        db_session,
        ca,
        shop.id,
        "sell73",
        trade(currencies, box, side="sell", amount="10", rate="73"),
    )
    assert sold["receipt"]["values"]["outgoing_book_afn"] == "710.00"
    assert sold["receipt"]["values"]["profit_afn"] == "20.00"
    assert (await cash(db_session, ca, shop))["USD"] == ("190.00", "13490.00")
