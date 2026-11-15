"""Persisted cash, retries, owner review and closed-period regression checks."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    AppValidationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
)
from app.models import JournalEntry
from app.models.daily import CashTransfer, DayClosing, Expense
from app.schemas.daily.request import (
    CategoryRequest,
    ClosingRequest,
    ConfirmClosingRequest,
    CountRequest,
    ExpenseRequest,
    OpenDayRequest,
    TransferRequest,
)
from app.schemas.finance.request import CashboxCreateRequest
from app.services.daily import ClosingService, DailyService
from app.services.finance import FinanceService, LedgerService
from app.services.financial_values import business_day
from tests.services.test_finance import finance as finance  # noqa: PLC0414
from tests.services.test_finance import opening
from tests.services.test_tenant_operations import tenants as tenants  # noqa: PLC0414


@pytest.mark.asyncio
async def test_expense_transfer_retry_reversal_and_scope(db_session, finance):
    ca, cb, shop, other, units, box = finance
    await LedgerService.opening(
        db_session, ca, shop.id, "initial", opening(box, units["USD"])
    )
    second = await FinanceService.create_cashbox(
        db_session, ca, shop.id, CashboxCreateRequest(name="Second")
    )
    data = TransferRequest(
        source_cashbox_id=box.id,
        destination_cashbox_id=second.id,
        currency_id=units["USD"].id,
        amount="30.00",
        reason="Move cash",
    )
    result = await DailyService.create(
        db_session, ca, shop.id, "move", data, transfer=True
    )
    assert result["book_afn"] == "2100.00"
    assert (
        await DailyService.create(db_session, ca, shop.id, "move", data, transfer=True)
        == result
    )
    balances = await FinanceService.cashboxes(db_session, ca, shop.id)
    assert sum(Decimal(x.balances[0].amount) for x in balances) == 100
    assert sum(Decimal(x.balances[0].book_afn) for x in balances) == 7000
    with pytest.raises(ConflictError):
        await DailyService.create(
            db_session,
            ca,
            shop.id,
            "move",
            data.model_copy(update={"amount": "31.00"}),
            transfer=True,
        )
    with pytest.raises(NotFoundError):
        await DailyService.create(
            db_session, cb, other.id, "foreign", data, transfer=True
        )
    reversed_data = await DailyService.reverse(
        db_session, ca, shop.id, result["id"], "undo", "Correction", transfer=True
    )
    assert reversed_data["reversal_entry_id"]
    with pytest.raises(ConflictError):
        await DailyService.reverse(
            db_session,
            ca,
            shop.id,
            result["id"],
            "undo-again",
            "Correction",
            transfer=True,
        )
    category = await DailyService.category(
        db_session, ca, shop.id, CategoryRequest(name="Salary")
    )
    expense = ExpenseRequest(
        category_id=category.id,
        cashbox_id=box.id,
        currency_id=units["USD"].id,
        amount="10.00",
        expense_date=business_day(datetime.now(UTC)),
        payee="کارمند",
        explanation="Monthly salary",
    )
    paid = await DailyService.create(db_session, ca, shop.id, "salary", expense)
    assert paid["book_afn"] == "700.00"
    assert await DailyService.create(db_session, ca, shop.id, "salary", expense) == paid
    await DailyService.reverse(
        db_session, ca, shop.id, paid["id"], "salary-undo", "Correction"
    )
    assert (await FinanceService.cashboxes(db_session, ca, shop.id))[0].balances[
        0
    ].amount == "100.00"
    for model in (Expense, CashTransfer):
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                await db_session.execute(update(model).values(amount="999.00"))


@pytest.mark.asyncio
async def test_closing_adjustment_freeze_next_day_and_retries(db_session, finance):
    ca, cb, shop, other, units, box = finance
    await LedgerService.opening(
        db_session, ca, shop.id, "initial", opening(box, units["USD"])
    )
    today = business_day(datetime.now(UTC))
    request = OpenDayRequest(day=today)
    day = await ClosingService.open(db_session, ca, shop.id, "open", request)
    assert day["opening"][0]["expected"] == "100.00"
    assert await ClosingService.open(db_session, ca, shop.id, "open", request) == day
    assert len(list(await db_session.scalars(select(JournalEntry)))) == 1
    count = CountRequest(
        cashbox_id=box.id, currency_id=units["USD"].id, counted="99.00"
    )
    with pytest.raises(AppValidationError):
        await ClosingService.create(
            db_session, ca, shop.id, "unexplained", ClosingRequest(counts=[count])
        )
    draft = await ClosingService.create(
        db_session,
        ca,
        shop.id,
        "close",
        ClosingRequest(counts=[count], reason="Counted shortage"),
    )
    assert draft["counts"][0]["difference"] == "-1.00"
    with pytest.raises(ConflictError):
        await LedgerService.reverse(db_session, ca, shop.id, 1, "frozen", "Correction")
    with pytest.raises(AuthorizationError):
        await ClosingService.confirm(
            db_session,
            ca,
            shop.id,
            draft["id"],
            "not-approved",
            ConfirmClosingRequest(owner_approved=False),
        )
    with pytest.raises(NotFoundError):
        await ClosingService.get(db_session, cb, other.id, draft["id"])
    result = await ClosingService.confirm(
        db_session,
        ca,
        shop.id,
        draft["id"],
        "approve",
        ConfirmClosingRequest(owner_approved=True),
    )
    assert result["confirmed"] and result["adjustment_entry_id"]
    assert (
        await ClosingService.confirm(
            db_session,
            ca,
            shop.id,
            draft["id"],
            "approve",
            ConfirmClosingRequest(owner_approved=True),
        )
        == result
    )
    assert (await FinanceService.cashboxes(db_session, ca, shop.id))[0].balances[
        0
    ].amount == "99.00"
    assert "-1.00" in await ClosingService.export(db_session, ca, shop.id, draft["id"])
    with pytest.raises(ConflictError):
        await ClosingService.open(db_session, ca, shop.id, "same-day", request)
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    with patch("app.services.daily.datetime") as clock:
        clock.now.return_value = tomorrow
        new_day = await ClosingService.open(
            db_session,
            ca,
            shop.id,
            "tomorrow",
            OpenDayRequest(day=business_day(tomorrow)),
        )
    assert new_day["opening"][0]["expected"] == "99.00"
    assert len(list(await db_session.scalars(select(JournalEntry)))) == 2
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(update(DayClosing).values(reason="changed"))


@pytest.mark.asyncio
async def test_overage_and_invalid_counts(db_session, finance):
    ca, _, shop, _, units, box = finance
    await ClosingService.open(
        db_session,
        ca,
        shop.id,
        "open",
        OpenDayRequest(day=business_day(datetime.now(UTC))),
    )
    count = CountRequest(cashbox_id=box.id, currency_id=units["USD"].id, counted="2.00")
    with pytest.raises(AppValidationError):
        await ClosingService.create(
            db_session,
            ca,
            shop.id,
            "no-cost",
            ClosingRequest(counts=[count], reason="Found cash"),
        )
    count.overage_book_afn = "140.00"
    with pytest.raises(AppValidationError):
        await ClosingService.create(
            db_session,
            ca,
            shop.id,
            "duplicate",
            ClosingRequest(counts=[count, count], reason="Found cash"),
        )
    draft = await ClosingService.create(
        db_session,
        ca,
        shop.id,
        "valid",
        ClosingRequest(counts=[count], reason="Found cash"),
    )
    await ClosingService.confirm(
        db_session,
        ca,
        shop.id,
        draft["id"],
        "confirm",
        ConfirmClosingRequest(owner_approved=True),
    )
    balance = (await FinanceService.cashboxes(db_session, ca, shop.id))[0].balances[0]
    assert balance.amount == "2.00" and balance.book_afn == "140.00"


@pytest.mark.asyncio
async def test_failed_expenses_and_count_validation_leave_no_effects(
    db_session, finance, monkeypatch
):
    from unittest.mock import AsyncMock

    from app.models import FinancialRetry, Organization
    from app.services.tenant_operations import TenantOperations

    ca, _, shop, _, units, box = finance
    await LedgerService.opening(
        db_session, ca, shop.id, "initial", opening(box, units["USD"])
    )
    category = await DailyService.category(
        db_session, ca, shop.id, CategoryRequest(name="Costs")
    )
    data = ExpenseRequest(
        category_id=category.id,
        cashbox_id=box.id,
        currency_id=units["USD"].id,
        amount="101.00",
        expense_date=business_day(datetime.now(UTC)),
        payee="Payee",
        explanation="Cost",
    )
    with pytest.raises(ConflictError):
        await DailyService.create(db_session, ca, shop.id, "insufficient", data)
    for amount in ("0.00", "-1.00", "1.001", "NaN"):
        with pytest.raises(AppValidationError):
            await DailyService.create(
                db_session,
                ca,
                shop.id,
                "invalid",
                data.model_copy(update={"amount": amount}),
            )
    with pytest.raises(AppValidationError):
        await DailyService.create(
            db_session,
            ca,
            shop.id,
            "backdate",
            data.model_copy(
                update={"expense_date": data.expense_date - timedelta(days=1)}
            ),
        )
    original_audit = TenantOperations.audit
    monkeypatch.setattr(
        TenantOperations, "audit", AsyncMock(side_effect=AppValidationError())
    )
    with pytest.raises(AppValidationError):
        await DailyService.create(
            db_session,
            ca,
            shop.id,
            "failed-audit",
            data.model_copy(update={"amount": "10.00"}),
        )
    monkeypatch.setattr(TenantOperations, "audit", original_audit)
    assert list(await db_session.scalars(select(Expense))) == []
    assert len(list(await db_session.scalars(select(JournalEntry)))) == 1
    assert len(list(await db_session.scalars(select(FinancialRetry)))) == 1
    await ClosingService.open(
        db_session, ca, shop.id, "day", OpenDayRequest(day=data.expense_date)
    )
    with pytest.raises(AppValidationError):
        await ClosingService.create(
            db_session, ca, shop.id, "missing-count", ClosingRequest(counts=[])
        )
    count = CountRequest(
        cashbox_id=box.id, currency_id=units["USD"].id, counted="100.00"
    )
    draft = await ClosingService.create(
        db_session, ca, shop.id, "close", ClosingRequest(counts=[count])
    )
    org = await db_session.get(Organization, ca.organization_id)
    owner = org.owner_id
    org.owner_id = None
    await db_session.flush()
    with pytest.raises(AuthorizationError):
        await ClosingService.confirm(
            db_session,
            ca,
            shop.id,
            draft["id"],
            "not-owner",
            ConfirmClosingRequest(owner_approved=True),
        )
    org.owner_id = owner
    await db_session.flush()
    await ClosingService.confirm(
        db_session,
        ca,
        shop.id,
        draft["id"],
        "owner",
        ConfirmClosingRequest(owner_approved=True),
    )
    assert len(list(await db_session.scalars(select(JournalEntry)))) == 1
    with pytest.raises(ConflictError):
        await DailyService.create(
            db_session,
            ca,
            shop.id,
            "closed-expense",
            data.model_copy(update={"amount": "10.00"}),
        )


@pytest.mark.asyncio
async def test_closing_serializes_against_a_cashier(tmp_path):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, User
    from app.schemas.finance.request import CurrencyCreateRequest
    from app.services.organization import OrganizationService
    from app.services.organization_context import OrganizationContext
    from app.services.shop import ShopService

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'closing.sqlite'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sessions() as db:
            user = User(
                user_name="closing-owner",
                email="closing@example.com",
                password_hash="unused",
            )
            db.add(user)
            await db.flush()
            org = await OrganizationService.create(db, "Closing", "closing", user)
            context = await OrganizationContext.resolve(db, user.id, org.id)
            shop = await ShopService.create(db, context, "Till")
            currency = await FinanceService.create_currency(
                db, context, CurrencyCreateRequest(code="AFN", precision=2)
            )
            box = await FinanceService.create_cashbox(
                db, context, shop.id, CashboxCreateRequest(name="Till")
            )
            await LedgerService.opening(
                db,
                context,
                shop.id,
                "initial",
                opening(box, currency, "100.00", "100.00"),
            )
            category = await DailyService.category(
                db, context, shop.id, CategoryRequest(name="Cost")
            )
            await ClosingService.open(
                db,
                context,
                shop.id,
                "day",
                OpenDayRequest(day=business_day(datetime.now(UTC))),
            )
            user_id, org_id, shop_id, box_id, currency_id, category_id = (
                user.id,
                org.id,
                shop.id,
                box.id,
                currency.id,
                category.id,
            )
            await db.commit()
        ready = asyncio.Event()

        async def action(close):
            async with sessions() as db:
                context = await OrganizationContext.resolve(db, user_id, org_id)
                if close:
                    ready.set()
                else:
                    await ready.wait()
                try:
                    if close:
                        result = await ClosingService.create(
                            db,
                            context,
                            shop_id,
                            "close",
                            ClosingRequest(
                                counts=[
                                    CountRequest(
                                        cashbox_id=box_id,
                                        currency_id=currency_id,
                                        counted="100.00",
                                    )
                                ],
                                reason="Concurrent count",
                            ),
                        )
                    else:
                        result = await DailyService.create(
                            db,
                            context,
                            shop_id,
                            "expense",
                            ExpenseRequest(
                                category_id=category_id,
                                cashbox_id=box_id,
                                currency_id=currency_id,
                                amount="10.00",
                                expense_date=business_day(datetime.now(UTC)),
                                payee="Payee",
                                explanation="Cost",
                            ),
                        )
                    await db.commit()
                    return result
                except ConflictError:
                    await db.rollback()
                    return None

        expense, closing = await asyncio.wait_for(
            asyncio.gather(action(False), action(True)), timeout=20
        )
        assert closing is not None
        assert closing["counts"][0]["expected"] == ("90.00" if expense else "100.00")
        async with sessions() as db:
            context = await OrganizationContext.resolve(db, user_id, org_id)
            balance = (
                (await FinanceService.cashboxes(db, context, shop_id))[0]
                .balances[0]
                .amount
            )
            assert balance == closing["counts"][0]["expected"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_evidence_scope_and_next_day_correction(db_session, finance):
    from app.models import PrivateAttachment
    from app.schemas.daily.request import CategoryUpdateRequest
    from app.services.tenant_operations import TenantOperations

    ca, cb, shop, other, units, box = finance
    await LedgerService.opening(
        db_session, ca, shop.id, "initial", opening(box, units["USD"])
    )
    evidence = await TenantOperations.upload(
        db_session, ca, shop.id, "receipt.pdf", "application/pdf", b"%PDF-evidence"
    )
    other_evidence = await TenantOperations.upload(
        db_session, cb, other.id, "other.pdf", "application/pdf", b"%PDF-other"
    )
    category = await DailyService.category(
        db_session, ca, shop.id, CategoryRequest(name="Rent")
    )
    today = business_day(datetime.now(UTC))
    await ClosingService.open(
        db_session, ca, shop.id, "open", OpenDayRequest(day=today)
    )
    data = ExpenseRequest(
        category_id=category.id,
        cashbox_id=box.id,
        currency_id=units["USD"].id,
        amount="10.00",
        expense_date=today,
        payee="Landlord",
        explanation="Rent",
        attachment_id=other_evidence.id,
    )
    with pytest.raises(NotFoundError):
        await DailyService.create(db_session, ca, shop.id, "foreign-evidence", data)
    data.attachment_id = evidence.id
    expense = await DailyService.create(db_session, ca, shop.id, "expense", data)
    assert (
        await TenantOperations.download(db_session, ca, evidence.id)
    ).content == b"%PDF-evidence"
    with pytest.raises(NotFoundError):
        await TenantOperations.download(db_session, cb, evidence.id)
    category_result = await DailyService.category(
        db_session, ca, shop.id, CategoryUpdateRequest(is_active=False), category.uuid
    )
    assert category_result.name == "Rent" and category_result.is_active is False
    draft = await ClosingService.create(
        db_session,
        ca,
        shop.id,
        "closing",
        ClosingRequest(
            counts=[
                CountRequest(
                    cashbox_id=box.id, currency_id=units["USD"].id, counted="90.00"
                )
            ]
        ),
    )
    await ClosingService.confirm(
        db_session,
        ca,
        shop.id,
        draft["id"],
        "confirm",
        ConfirmClosingRequest(owner_approved=True),
    )
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    with (
        patch("app.services.daily.datetime") as clock,
        patch("app.services.finance.datetime") as ledger_clock,
    ):
        clock.now.return_value = tomorrow
        ledger_clock.now.return_value = tomorrow
        await ClosingService.open(
            db_session, ca, shop.id, "next", OpenDayRequest(day=business_day(tomorrow))
        )
        reversal = await DailyService.reverse(
            db_session, ca, shop.id, expense["id"], "correction", "Correct rent"
        )
    entry = await db_session.get(JournalEntry, reversal["reversal_entry_id"])
    assert entry.reversal_of_id == expense["entry_id"]
    assert business_day(entry.created_at.replace(tzinfo=UTC)) == business_day(tomorrow)
    assert (await ClosingService.get(db_session, ca, shop.id, draft["id"])).counts[
        0
    ].expected == "90.00"
    assert (await FinanceService.cashboxes(db_session, ca, shop.id))[0].balances[
        0
    ].amount == "100.00"
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                update(PrivateAttachment)
                .where(PrivateAttachment.id == evidence.id)
                .values(content=b"changed")
            )
