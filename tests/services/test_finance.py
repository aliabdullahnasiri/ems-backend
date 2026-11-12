"""Financial invariants using persisted ledger data, not mocked balances."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    AppValidationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
)
from app.models import AuditEvent
from app.models.finance import (
    JournalEntry,
    JournalLine,
    JournalPosting,
    LedgerAccount,
)
from app.schemas.finance.request import (
    CashboxCreateRequest,
    CashboxUpdateRequest,
    CurrencyCreateRequest,
    OpeningBalanceRequest,
    SettingsUpdateRequest,
)
from app.services.finance import FinanceService, LedgerService
from app.services.financial_values import business_day, day_cutoffs, decimal_text, exact
from tests.services.test_tenant_operations import (
    tenants as tenants,  # noqa: PLC0414 -- re-export fixture for pytest discovery
)


@pytest_asyncio.fixture
async def finance(db_session, tenants):
    *_, ca, cb, sa, sb = tenants
    currencies = {}
    for code in ("AFN", "USD", "PKR"):
        currencies[code] = await FinanceService.create_currency(
            db_session, ca, CurrencyCreateRequest(code=code, precision=2)
        )
    box = await FinanceService.create_cashbox(
        db_session, ca, sa.id, CashboxCreateRequest(name="صندوق")
    )
    return ca, cb, sa, sb, currencies, box


def opening(box, currency, amount="100.00", book="7000.00"):
    return OpeningBalanceRequest(
        cashbox_id=box.id,
        currency_id=currency.id,
        amount=amount,
        book_afn=book,
        owner_approved=True,
        reason="بررسی مالک",
    )


@pytest.mark.asyncio
async def test_opening_retries_costs_equity_reversal_and_reconciliation(
    db_session, finance
):
    ca, _, shop, _, currencies, box = finance
    data = opening(box, currencies["USD"], "۱۰۰٫۰۰", "۷۰۰۰٫۰۰")
    first = await LedgerService.opening(db_session, ca, shop.id, "open", data)
    assert await LedgerService.opening(db_session, ca, shop.id, "open", data) == first
    assert len(list(await db_session.scalars(select(JournalEntry)))) == 1
    assert {line["kind"] for line in first["lines"]} == {"cash", "equity"}
    balances = (await FinanceService.cashboxes(db_session, ca, shop.id))[0].balances
    assert balances[0].amount == "100.00" and balances[0].book_afn == "7000.00"
    with pytest.raises(ConflictError):
        await LedgerService.opening(
            db_session, ca, shop.id, "open", opening(box, currencies["USD"], "101.00")
        )
    with pytest.raises(ConflictError):
        await LedgerService.opening(db_session, ca, shop.id, "another", data)
    reversal = await LedgerService.reverse(
        db_session, ca, shop.id, first["id"], "reverse", "تصحیح"
    )
    assert reversal["reversal_of_id"] == first["id"]
    assert (
        await LedgerService.reverse(
            db_session, ca, shop.id, first["id"], "reverse", "تصحیح"
        )
        == reversal
    )
    with pytest.raises(ConflictError):
        await LedgerService.reverse(
            db_session, ca, shop.id, first["id"], "twice", "reason"
        )
    balances = (await FinanceService.cashboxes(db_session, ca, shop.id))[0].balances
    assert balances[0].amount == "0.00" and balances[0].book_afn == "0.00"
    assert (
        len(await LedgerService.entries(db_session, ca, shop.id, cashbox_id=box.id))
        == 2
    )
    assert (
        len(
            list(
                await db_session.scalars(
                    select(AuditEvent).where(AuditEvent.record_type == "journal_entry")
                )
            )
        )
        == 2
    )


@pytest.mark.asyncio
async def test_atomic_balanced_postings_and_insufficient_cash(db_session, finance):
    ca, _, shop, _, currencies, box = finance
    original = await LedgerService.opening(
        db_session, ca, shop.id, "open", opening(box, currencies["USD"])
    )
    cash = await db_session.scalar(
        select(LedgerAccount).where(LedgerAccount.kind == "cash")
    )
    expense = await LedgerService.account(
        db_session, ca, shop.id, currencies["USD"].id, "expense", "expense"
    )

    def legs(amount, book):
        return [
            {"account_id": cash.id, "amount": "-" + amount, "book_afn": "-" + book},
            {"account_id": expense.id, "amount": amount, "book_afn": book},
        ]

    with pytest.raises(ConflictError):
        await LedgerService.post(
            db_session,
            ca,
            shop.id,
            "expense:create",
            "too-much",
            "expense",
            legs("101.00", "7070.00"),
        )
    with pytest.raises(AppValidationError):
        bad = legs("10.00", "700.00")
        bad[1]["book_afn"] = "699.00"
        await LedgerService.post(
            db_session, ca, shop.id, "expense:create", "unbalanced", "expense", bad
        )
    assert len(list(await db_session.scalars(select(JournalEntry)))) == 1
    assert await LedgerService.balance(db_session, ca, cash.id) == (
        Decimal(100),
        Decimal(7000),
    )
    await LedgerService.post(
        db_session,
        ca,
        shop.id,
        "expense:create",
        "valid",
        "expense",
        legs("60.00", "4200.00"),
    )
    assert await LedgerService.balance(db_session, ca, cash.id) == (
        Decimal(40),
        Decimal(2800),
    )
    with pytest.raises(ConflictError):
        await LedgerService.reverse(
            db_session, ca, shop.id, original["id"], "reverse-spent", "reason"
        )


@pytest.mark.asyncio
async def test_scope_deactivation_units_settings_and_large_exact_values(
    db_session, finance
):
    ca, cb, shop, other_shop, currencies, box = finance
    await FinanceService.update_settings(
        db_session,
        ca,
        shop.id,
        SettingsUpdateRequest(
            name="صرافی پښتو", receipt_footer="تشکر", preferred_language="ps"
        ),
    )
    await FinanceService.update_settings(
        db_session, ca, shop.id, SettingsUpdateRequest(phone="۰۷۰")
    )
    settings = await FinanceService.settings(db_session, ca, shop.id)
    assert (
        settings.name == "صرافی پښتو"
        and settings.receipt_footer == "تشکر"
        and settings.preferred_language == "ps"
    )
    with pytest.raises(NotFoundError):
        await FinanceService.settings(db_session, cb, shop.id)
    with pytest.raises(NotFoundError):
        await LedgerService.opening(
            db_session, cb, other_shop.id, "foreign", opening(box, currencies["USD"])
        )
    for code, factor in (("IRR", 1), ("IRT", 10)):
        unit = await FinanceService.create_currency(
            db_session, ca, CurrencyCreateRequest(code=code, precision=0)
        )
        assert unit.rial_per_unit == factor
    huge = "999999999999999999.99"
    await LedgerService.opening(
        db_session, ca, shop.id, "large", opening(box, currencies["AFN"], huge, huge)
    )
    balances = (await FinanceService.cashboxes(db_session, ca, shop.id))[0].balances
    assert balances[0].amount == huge and balances[0].book_afn == huge
    await FinanceService.update_cashbox(
        db_session, ca, shop.id, box.id, CashboxUpdateRequest(is_active=False)
    )
    assert (await FinanceService.cashboxes(db_session, ca, shop.id))[0].balances[
        0
    ].amount == huge
    with pytest.raises(AuthorizationError):
        await LedgerService.opening(
            db_session, ca, shop.id, "inactive", opening(box, currencies["USD"])
        )


@pytest.mark.asyncio
async def test_posted_history_cannot_be_modified_or_extended(db_session, finance):
    ca, _, shop, _, currencies, box = finance
    result = await LedgerService.opening(
        db_session, ca, shop.id, "open", opening(box, currencies["USD"])
    )
    await db_session.commit()
    for model in (JournalEntry, JournalLine, JournalPosting, LedgerAccount):
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                await db_session.execute(
                    update(model).values(updated_at=datetime.now(UTC))
                )
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                ca.new(
                    JournalLine,
                    shop_id=shop.id,
                    entry_id=result["id"],
                    account_id=result["lines"][0]["account_id"],
                    amount="1.00",
                    book_afn="70.00",
                )
            )
            await db_session.flush()


def test_numerals_precision_and_kabul_day():
    assert decimal_text("۱۲۳٫۴۵", 2) == Decimal("123.45")
    assert decimal_text("١٢٣.٤٥", 2) == Decimal("123.45")
    for value in ("1e2", "NaN", "-1", "1,000", "1.001", 1.2, " 1", "١٬٠٠٠"):
        with pytest.raises(AppValidationError):
            decimal_text(value, 2)
    assert exact(Decimal("1.005"), 2) == "1.01"
    instant = datetime(2026, 1, 1, 20, tzinfo=UTC)
    assert business_day(instant) == date(2026, 1, 2)
    start, end = day_cutoffs(date(2026, 1, 2))
    assert start == datetime(2026, 1, 1, 19, 30, tzinfo=UTC)
    assert (end - start).total_seconds() == 86400


@pytest.mark.asyncio
async def test_failure_after_lines_rolls_back_every_effect(
    db_session, finance, monkeypatch
):
    from unittest.mock import AsyncMock

    from app.models import FinancialRetry
    from app.services.tenant_operations import TenantOperations

    ca, _, shop, _, currencies, box = finance
    monkeypatch.setattr(
        TenantOperations, "audit", AsyncMock(side_effect=AppValidationError())
    )
    with pytest.raises(AppValidationError):
        await LedgerService.opening(
            db_session, ca, shop.id, "fail-after-lines", opening(box, currencies["USD"])
        )
    for model in (
        JournalEntry,
        JournalLine,
        JournalPosting,
        LedgerAccount,
        FinancialRetry,
    ):
        assert list(await db_session.scalars(select(model))) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("same_retry", [False, True])
async def test_independent_cashiers_cannot_double_spend_or_double_post(
    tmp_path, same_retry
):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, User
    from app.services.organization import OrganizationService
    from app.services.organization_context import OrganizationContext
    from app.services.shop import ShopService

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cashiers.sqlite'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as db:
            user = User(
                user_name="cashier-owner",
                email="cashier@example.com",
                password_hash="unused",
            )
            db.add(user)
            await db.flush()
            org = await OrganizationService.create(db, "Concurrent", "cashiers", user)
            ctx = await OrganizationContext.resolve(db, user.id, org.id)
            shop = await ShopService.create(db, ctx, "Concurrent cash")
            currency = await FinanceService.create_currency(
                db, ctx, CurrencyCreateRequest(code="USD", precision=2)
            )
            box = await FinanceService.create_cashbox(
                db, ctx, shop.id, CashboxCreateRequest(name="Till")
            )
            initial = await LedgerService.opening(
                db, ctx, shop.id, "initial", opening(box, currency)
            )
            cash_id = initial["lines"][0]["account_id"]
            expense = await LedgerService.account(
                db, ctx, shop.id, currency.id, "expense", "expense"
            )
            expense_id, user_id, org_id, shop_id = expense.id, user.id, org.id, shop.id
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
                    result = await LedgerService.post(
                        db,
                        context,
                        shop_id,
                        "expense:create",
                        "same" if same_retry else f"spend-{second}",
                        "Cashier expense",
                        [
                            {
                                "account_id": cash_id,
                                "amount": "-60.00",
                                "book_afn": "-4200.00",
                            },
                            {
                                "account_id": expense_id,
                                "amount": "60.00",
                                "book_afn": "4200.00",
                            },
                        ],
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
            assert await LedgerService.balance(db, context, cash_id) == (
                Decimal(40),
                Decimal(2800),
            )
            assert len(list(await db.scalars(select(JournalEntry)))) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,counter_kind,cash_sign",
    [
        ("payment:create", "receivable", 1),
        ("hawala:confirm", "payable", 1),
        ("hawala:pay", "receivable", -1),
        ("hawala:refund", "payable", -1),
        ("agent_settlement:create", "payable", -1),
        ("expense:create", "expense", -1),
    ],
)
async def test_workflow_accounting_patterns(
    db_session, finance, action, counter_kind, cash_sign
):
    """Verify foundation legs; workflow state/allocation APIs belong to later phases."""
    ca, _, shop, _, currencies, box = finance
    initial = await LedgerService.opening(
        db_session,
        ca,
        shop.id,
        "initial",
        opening(box, currencies["AFN"], "10000.00", "10000.00"),
    )
    cash_id = initial["lines"][0]["account_id"]
    counter = await LedgerService.account(
        db_session,
        ca,
        shop.id,
        currencies["AFN"].id,
        f"test:{counter_kind}",
        counter_kind,
    )
    value = "100.00" if cash_sign == 1 else "-100.00"
    opposite = "-100.00" if cash_sign == 1 else "100.00"
    result = await LedgerService.post(
        db_session,
        ca,
        shop.id,
        action,
        action,
        "Reviewed example",
        [
            {"account_id": cash_id, "amount": value, "book_afn": value},
            {"account_id": counter.id, "amount": opposite, "book_afn": opposite},
        ],
    )
    assert sum(Decimal(line["amount"]) for line in result["lines"]) == 0
    assert await LedgerService.balance(db_session, ca, cash_id) == (
        Decimal(10000 + cash_sign * 100),
        Decimal(10000 + cash_sign * 100),
    )


@pytest.mark.asyncio
async def test_exchange_and_cash_transfer_accounting_patterns(db_session, finance):
    ca, _, shop, _, currencies, box = finance
    await LedgerService.opening(
        db_session,
        ca,
        shop.id,
        "afn",
        opening(box, currencies["AFN"], "10000.00", "10000.00"),
    )
    initial = await LedgerService.opening(
        db_session, ca, shop.id, "usd", opening(box, currencies["USD"])
    )
    usd_cash = initial["lines"][0]["account_id"]
    afn_cash = await LedgerService.account(
        db_session, ca, shop.id, currencies["AFN"].id, "cash", "cash", box.id
    )
    usd_clear = await LedgerService.account(
        db_session, ca, shop.id, currencies["USD"].id, "fx_clearing", "clearing"
    )
    afn_clear = await LedgerService.account(
        db_session, ca, shop.id, currencies["AFN"].id, "fx_clearing", "clearing"
    )
    income = await LedgerService.account(
        db_session, ca, shop.id, currencies["AFN"].id, "fx_profit", "income"
    )

    def leg(account_id, amount, book):
        return {"account_id": account_id, "amount": amount, "book_afn": book}

    result = await LedgerService.post(
        db_session,
        ca,
        shop.id,
        "exchange:create",
        "fx",
        "Sell USD at illustrative 71 AFN/USD; carrying cost 70",
        [
            leg(usd_cash, "-10.00", "-700.00"),
            leg(usd_clear.id, "10.00", "700.00"),
            leg(afn_cash.id, "710.00", "710.00"),
            leg(afn_clear.id, "-700.00", "-700.00"),
            leg(income.id, "-10.00", "-10.00"),
        ],
    )
    assert sum(Decimal(line["book_afn"]) for line in result["lines"]) == 0
    assert await LedgerService.balance(db_session, ca, usd_cash) == (
        Decimal(90),
        Decimal(6300),
    )
    assert await LedgerService.balance(db_session, ca, afn_cash.id) == (
        Decimal(10710),
        Decimal(10710),
    )
    second = await FinanceService.create_cashbox(
        db_session, ca, shop.id, CashboxCreateRequest(name="Second")
    )
    second_cash = await LedgerService.account(
        db_session, ca, shop.id, currencies["AFN"].id, "cash", "cash", second.id
    )
    await LedgerService.post(
        db_session,
        ca,
        shop.id,
        "cash_transfer:create",
        "transfer",
        "Internal movement",
        [
            leg(afn_cash.id, "-100.00", "-100.00"),
            leg(second_cash.id, "100.00", "100.00"),
        ],
    )
    assert (await LedgerService.balance(db_session, ca, afn_cash.id))[0] + (
        await LedgerService.balance(db_session, ca, second_cash.id)
    )[0] == Decimal(10710)


@pytest.mark.asyncio
async def test_owner_approval_and_inactive_currency_are_enforced(db_session, finance):
    from app.models import Organization
    from app.schemas.finance.request import CurrencyUpdateRequest

    ca, _, shop, _, currencies, box = finance
    data = opening(box, currencies["USD"])
    org = await db_session.get(Organization, ca.organization_id)
    original_owner = org.owner_id
    org.owner_id = (
        None  # Owner-role permissions alone cannot approve reviewed openings.
    )
    await db_session.flush()
    with pytest.raises(AuthorizationError):
        await LedgerService.opening(db_session, ca, shop.id, "not-current-owner", data)
    org.owner_id = original_owner
    await db_session.flush()
    await FinanceService.update_currency(
        db_session, ca, currencies["USD"].id, CurrencyUpdateRequest(is_active=False)
    )
    with pytest.raises(AuthorizationError):
        await LedgerService.opening(db_session, ca, shop.id, "inactive-currency", data)
    for approval in (False, 1, "true"):
        with pytest.raises(AppValidationError):
            OpeningBalanceRequest(**{**data.model_dump(), "owner_approved": approval})
