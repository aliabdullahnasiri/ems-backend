"""Phase 9 acceptance: partial cash, obligations, allocations and linked corrections."""

from decimal import Decimal
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.exceptions import (
    AppValidationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
)
from app.models.finance import JournalLine, LedgerAccount
from app.models.payment import Payment, PaymentAllocation
from app.schemas.contact.request import (
    ContactCreateRequest,
    ContactOpeningRequest,
    ContactUpdateRequest,
)
from app.schemas.exchange.request import ExchangeReverseRequest
from app.schemas.payment.request import PaymentCreateRequest, PaymentReverseRequest
from app.services.contact import ContactService
from app.services.contact_ledger import ContactLedgerService
from app.services.exchange import ExchangeService
from app.services.obligation import ObligationService
from app.services.payment import PaymentService
from tests.services.test_exchange import cash, seed, trade
from tests.services.test_finance import finance as finance  # noqa: PLC0414
from tests.services.test_tenant_operations import tenants as tenants  # noqa: PLC0414


@pytest_asyncio.fixture
async def credit(db_session, finance):
    ca, cb, shop, other_shop, currencies, box = finance
    await seed(db_session, ca, shop, currencies, box)
    customer = await ContactService.create(
        db_session, ca, ContactCreateRequest(shop_uuid=shop.uuid, name="احمد پښتون")
    )
    return ca, cb, shop, other_shop, currencies, box, customer


async def opening_debt(
    db,
    context,
    customer,
    currency,
    amount="10000",
    book="10000",
    direction="receivable",
    key="debt",
):
    return await ContactLedgerService.opening(
        db,
        context,
        customer.uuid,
        key,
        ContactOpeningRequest(
            role="customer",
            direction=direction,
            currency_id=currency.id,
            amount=amount,
            book_afn=book,
            reason="reviewed opening",
            owner_approved=True,
        ),
    )


def payment(
    customer, box, currency, obligation, amount="6000", direction="receive", **changes
):
    return PaymentCreateRequest(
        **{
            "contact_id": customer.id,
            "cashbox_id": box.id,
            "currency_id": currency.id,
            "direction": direction,
            "amount": amount,
            "allocations": [{"obligation_id": obligation.id, "amount": amount}],
            "reason": "تصفیه",
            **changes,
        }
    )


async def assert_balanced(db, entry_id):
    book, amounts = Decimal(0), {}
    for line in await db.scalars(
        select(JournalLine).where(JournalLine.entry_id == entry_id)
    ):
        account = await db.get(LedgerAccount, line.account_id)
        amounts[account.currency_id] = amounts.get(
            account.currency_id, Decimal(0)
        ) + Decimal(line.amount)
        book += Decimal(line.book_afn)
    assert book == 0 and not any(amounts.values())


@pytest.mark.asyncio
async def test_ten_thousand_debt_six_thousand_payment_receipt_and_reversal(
    db_session, credit
):
    ca, _, shop, _, currencies, box, customer = credit
    await opening_debt(db_session, ca, customer, currencies["AFN"])
    obligation = (await ObligationService.outstanding(db_session, ca, customer.uuid))[0]
    before = await cash(db_session, ca, shop)
    data = payment(customer, box, currencies["AFN"], obligation)
    result = await PaymentService.create(db_session, ca, shop.id, "receive", data)
    assert (
        await PaymentService.create(db_session, ca, shop.id, "receive", data) == result
    )
    assert result["obligations"][0]["remaining_amount"] == "4000.00"
    assert result["obligations"][0]["paid_amount"] == "6000.00"
    assert result["balances"][0]["receivable"] == "4000.00"
    assert (await cash(db_session, ca, shop))["AFN"] == ("26000.00", "26000.00")
    posted = result["payment"]
    await assert_balanced(db_session, posted["entry_id"])
    receipt = posted["receipt"]
    await ContactService.update(
        db_session, ca, customer.uuid, ContactUpdateRequest(name="New name")
    )
    assert (
        await PaymentService.get(db_session, ca, shop.id, UUID(posted["uuid"]))
    ).receipt.model_dump(mode="json") == receipt
    reverse = PaymentReverseRequest(reason="correction", owner_approved=True)
    reversed_result = await PaymentService.reverse(
        db_session, ca, shop.id, UUID(posted["uuid"]), "reverse", reverse
    )
    assert (
        reversed_result["payment"]["reversal_entry_id"]
        == reversed_result["entry"]["id"]
    )
    assert reversed_result["balances"][0]["receivable"] == "10000.00"
    assert reversed_result["obligations"][0]["paid_amount"] == "0.00"
    assert (
        reversed_result["obligations"][0]["settlements"][0]["reversal_entry_id"]
        is not None
    )
    assert await cash(db_session, ca, shop) == before
    assert (
        await PaymentService.reverse(
            db_session, ca, shop.id, UUID(posted["uuid"]), "reverse", reverse
        )
        == reversed_result
    )
    with pytest.raises(ConflictError):
        await PaymentService.reverse(
            db_session, ca, shop.id, UUID(posted["uuid"]), "again", reverse
        )
    assert len(list(await db_session.scalars(select(Payment)))) == 1


@pytest.mark.asyncio
async def test_partial_credit_exchange_only_changes_actual_cash_and_restores_allocation(
    db_session, credit
):
    ca, _, shop, _, currencies, box, customer = credit
    data = trade(
        currencies,
        box,
        side="sell",
        amount="100",
        rate="100",
        contact_id=customer.id,
        incoming_cash_amount="4000",
        outgoing_cash_amount="40",
        outgoing_afn_rate="70",
    )
    result = await ExchangeService.create(db_session, ca, shop.id, "credit", data)
    assert await cash(db_session, ca, shop) == {
        "AFN": ("24000.00", "24000.00"),
        "USD": ("60.00", "4200.00"),
    }
    obligations = {o["direction"]: o for o in result["obligations"]}
    assert obligations["receivable"]["original_amount"] == "6000.00"
    assert obligations["payable"]["original_amount"] == "60.00"
    assert result["receipt"]["values"]["incoming_amount"] == "10000.00"
    await assert_balanced(db_session, result["entry_id"])
    owed = (await ObligationService.outstanding(db_session, ca, customer.uuid))[0]
    settled = await PaymentService.create(
        db_session,
        ca,
        shop.id,
        "settle",
        payment(customer, box, currencies["AFN"], owed, amount="6000"),
    )
    assert settled["obligations"][0]["remaining_amount"] == "0.00"
    with pytest.raises(ConflictError):
        await ExchangeService.reverse(
            db_session,
            ca,
            shop.id,
            UUID(result["uuid"]),
            "reverse-credit",
            ExchangeReverseRequest(owner_approved=True, reason="correction"),
        )
    await PaymentService.reverse(
        db_session,
        ca,
        shop.id,
        UUID(settled["payment"]["uuid"]),
        "reverse-settle",
        PaymentReverseRequest(owner_approved=True, reason="correction"),
    )
    await ExchangeService.reverse(
        db_session,
        ca,
        shop.id,
        UUID(result["uuid"]),
        "reverse-credit",
        ExchangeReverseRequest(owner_approved=True, reason="correction"),
    )
    assert not await ObligationService.outstanding(db_session, ca, customer.uuid)
    assert await cash(db_session, ca, shop) == {
        "AFN": ("20000.00", "20000.00"),
        "USD": ("100.00", "7000.00"),
    }


@pytest.mark.asyncio
async def test_payable_payout_cost_difference_is_realized_and_reversed(
    db_session, credit
):
    ca, _, shop, _, currencies, box, customer = credit
    await opening_debt(
        db_session, ca, customer, currencies["USD"], "10", "710", "payable"
    )
    obligation = (await ObligationService.outstanding(db_session, ca, customer.uuid))[0]
    result = await PaymentService.create(
        db_session,
        ca,
        shop.id,
        "pay",
        payment(customer, box, currencies["USD"], obligation, "10", "pay"),
    )
    assert result["payment"]["receipt"]["profit_afn"] == "10.00"
    assert result["obligations"][0]["remaining_amount"] == "0.00"
    assert (await cash(db_session, ca, shop))["USD"] == ("90.00", "6300.00")
    await assert_balanced(db_session, result["payment"]["entry_id"])
    await PaymentService.reverse(
        db_session,
        ca,
        shop.id,
        UUID(result["payment"]["uuid"]),
        "undo-pay",
        PaymentReverseRequest(owner_approved=True, reason="correction"),
    )
    assert (await cash(db_session, ca, shop))["USD"] == ("100.00", "7000.00")
    assert (await ObligationService.outstanding(db_session, ca, customer.uuid))[
        0
    ].remaining_book_afn == "710.00"


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["receive", "pay"])
async def test_different_currency_uses_explicit_credit_exchange_and_reverses(
    db_session, credit, direction
):
    ca, _, shop, _, currencies, box, customer = credit
    await opening_debt(
        db_session,
        ca,
        customer,
        currencies["AFN"],
        "10000",
        "10000",
        "receivable" if direction == "receive" else "payable",
    )
    target = (await ObligationService.outstanding(db_session, ca, customer.uuid))[0]
    bridge = await ExchangeService.create(
        db_session,
        ca,
        shop.id,
        "bridge",
        trade(
            currencies,
            box,
            side="buy" if direction == "receive" else "sell",
            amount="100",
            rate="70",
            contact_id=customer.id,
            incoming_cash_amount="0",
            outgoing_cash_amount="0",
            **({"outgoing_afn_rate": "70"} if direction == "pay" else {}),
        ),
    )
    before = await cash(db_session, ca, shop)
    data = payment(
        customer,
        box,
        currencies["USD"],
        target,
        "50",
        direction,
        allocations=[{"obligation_id": target.id, "amount": "3500"}],
        settlement_exchange_id=bridge["id"],
    )
    result = await PaymentService.create(db_session, ca, shop.id, "cross", data)
    assert (
        result["payment"]["receipt"]["settlement_exchange"]["reference"]
        == bridge["reference"]
    )
    assert (
        next(o for o in result["obligations"] if o["id"] == target.id)[
            "remaining_amount"
        ]
        == "6500.00"
    )
    assert (await cash(db_session, ca, shop))["AFN"] == before["AFN"]
    assert (await cash(db_session, ca, shop))["USD"][0] == (
        "150.00" if direction == "receive" else "50.00"
    )
    await assert_balanced(db_session, result["payment"]["entry_id"])
    with pytest.raises(ConflictError):
        await ExchangeService.reverse(
            db_session,
            ca,
            shop.id,
            UUID(bridge["uuid"]),
            "undo-bridge",
            ExchangeReverseRequest(owner_approved=True, reason="correction"),
        )
    second = await PaymentService.create(db_session, ca, shop.id, "cross2", data)
    assert (
        next(o for o in second["obligations"] if o["id"] == target.id)[
            "remaining_amount"
        ]
        == "3000.00"
    )
    with pytest.raises(ConflictError):
        await PaymentService.reverse(
            db_session,
            ca,
            shop.id,
            UUID(result["payment"]["uuid"]),
            "undo1",
            PaymentReverseRequest(owner_approved=True, reason="correction"),
        )
    await PaymentService.reverse(
        db_session,
        ca,
        shop.id,
        UUID(second["payment"]["uuid"]),
        "undo2",
        PaymentReverseRequest(owner_approved=True, reason="correction"),
    )
    await PaymentService.reverse(
        db_session,
        ca,
        shop.id,
        UUID(result["payment"]["uuid"]),
        "undo1",
        PaymentReverseRequest(owner_approved=True, reason="correction"),
    )
    await ExchangeService.reverse(
        db_session,
        ca,
        shop.id,
        UUID(bridge["uuid"]),
        "undo-bridge",
        ExchangeReverseRequest(owner_approved=True, reason="correction"),
    )
    assert await cash(db_session, ca, shop) == before
    assert (await ObligationService.outstanding(db_session, ca, customer.uuid))[
        0
    ].remaining_amount == "10000.00"


@pytest.mark.asyncio
async def test_validation_limits_ownership_and_no_partial_writes(db_session, credit):
    ca, cb, shop, other_shop, currencies, box, customer = credit
    await opening_debt(db_session, ca, customer, currencies["AFN"])
    obligation = (await ObligationService.outstanding(db_session, ca, customer.uuid))[0]
    before = await cash(db_session, ca, shop)
    base = payment(customer, box, currencies["AFN"], obligation)
    for changes in (
        {"amount": "6001"},
        {"direction": "pay"},
        {"currency_id": currencies["USD"].id},
        {"amount": "0"},
        {"amount": "1e2"},
        {"amount": "-1"},
        {"amount": "1.001"},
        {"allocations": [{"obligation_id": obligation.id, "amount": "6000"}] * 2},
        {"allocations": [{"obligation_id": obligation.id, "amount": "0"}]},
    ):
        with pytest.raises(AppValidationError):
            await PaymentService.create(
                db_session,
                ca,
                shop.id,
                "bad",
                PaymentCreateRequest(**{**base.model_dump(), **changes}),
            )
    with pytest.raises(ConflictError):
        await PaymentService.create(
            db_session,
            ca,
            shop.id,
            "excess",
            payment(customer, box, currencies["AFN"], obligation, "10001"),
        )
    with pytest.raises(NotFoundError):
        await PaymentService.create(db_session, cb, other_shop.id, "foreign", base)
    assert await cash(db_session, ca, shop) == before
    assert not list(await db_session.scalars(select(Payment)))
    assert not list(await db_session.scalars(select(PaymentAllocation)))
    # Credit requires a customer, exact paid amounts and a valuation for foreign payables.
    for changes in (
        {"contact_id": None, "incoming_cash_amount": "0"},
        {"incoming_cash_amount": "7001"},
        {"outgoing_cash_amount": "7001"},
        {"incoming_cash_amount": "-1"},
        {"incoming_cash_amount": "1.001"},
    ):
        with pytest.raises(AppValidationError):
            await ExchangeService.create(
                db_session,
                ca,
                shop.id,
                "bad-credit",
                trade(currencies, box, **{"contact_id": customer.id, **changes}),
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("same_retry", [False, True])
async def test_concurrent_allocations_cannot_overpay_and_retries_do_not_duplicate(
    tmp_path, same_retry
):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, User
    from app.schemas.finance.request import CashboxCreateRequest, CurrencyCreateRequest
    from app.services.finance import FinanceService
    from app.services.organization import OrganizationService
    from app.services.organization_context import OrganizationContext
    from app.services.shop import ShopService

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'payments.sqlite'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as db:
            user = User(
                user_name="owner", email="payment@example.com", password_hash="unused"
            )
            db.add(user)
            await db.flush()
            org = await OrganizationService.create(
                db, "Payments", "payment-concurrent", user
            )
            context = await OrganizationContext.resolve(db, user.id, org.id)
            shop = await ShopService.create(db, context, "Till shop")
            currency = await FinanceService.create_currency(
                db, context, CurrencyCreateRequest(code="AFN", precision=2)
            )
            box = await FinanceService.create_cashbox(
                db, context, shop.id, CashboxCreateRequest(name="Till")
            )
            customer = await ContactService.create(
                db, context, ContactCreateRequest(shop_uuid=shop.uuid, name="Customer")
            )
            await opening_debt(db, context, customer, currency)
            obligation = (
                await ObligationService.outstanding(db, context, customer.uuid)
            )[0]
            payload = payment(customer, box, currency, obligation)
            user_id, org_id, shop_id = user.id, org.id, shop.id
            await db.commit()
        ready = asyncio.Event()

        async def settle(second):
            async with sessions() as db:
                context = await OrganizationContext.resolve(db, user_id, org_id)
                if second:
                    ready.set()
                else:
                    await ready.wait()
                try:
                    result = await PaymentService.create(
                        db,
                        context,
                        shop_id,
                        "same" if same_retry else f"settle-{second}",
                        payload,
                    )
                    await db.commit()
                    return result
                except ConflictError:
                    await db.rollback()
                    return None

        results = await asyncio.wait_for(
            asyncio.gather(settle(False), settle(True)), timeout=20
        )
        if same_retry:
            assert results[0] == results[1] and results[0] is not None
        else:
            assert sum(result is not None for result in results) == 1
        async with sessions() as db:
            context = await OrganizationContext.resolve(db, user_id, org_id)
            state = (await ObligationService.outstanding(db, context, customer.uuid))[0]
            assert state.remaining_amount == "4000.00" and len(state.settlements) == 1
            assert len(list(await db.scalars(select(Payment)))) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_multi_obligation_allocation_and_rounding_exhausts_book_values(
    db_session, credit
):
    ca, _, shop, _, currencies, box, customer = credit
    await opening_debt(db_session, ca, customer, currencies["USD"], "3", "1")
    first = (await ObligationService.outstanding(db_session, ca, customer.uuid))[0]
    for index, expected_book in enumerate(("0.33", "0.34", "0.33")):
        result = await PaymentService.create(
            db_session,
            ca,
            shop.id,
            f"round-{index}",
            payment(customer, box, currencies["USD"], first, "1"),
        )
        assert result["payment"]["receipt"]["book_afn"] == expected_book
    assert not await ObligationService.outstanding(db_session, ca, customer.uuid)
    history = (
        await ObligationService.outstanding(
            db_session, ca, customer.uuid, include_settled=True
        )
    )[0]
    assert history.remaining_book_afn == "0.00" and len(history.settlements) == 3
    with pytest.raises(ConflictError):
        await PaymentService.create(
            db_session,
            ca,
            shop.id,
            "overpaid",
            payment(customer, box, currencies["USD"], first, "1"),
        )
    # An additional credit exchange creates another separate original obligation.
    await opening_debt(
        db_session, ca, customer, currencies["AFN"], "100", "100", key="afn-debt"
    )
    exchange = await ExchangeService.create(
        db_session,
        ca,
        shop.id,
        "new-credit",
        trade(
            currencies,
            box,
            side="sell",
            amount="1",
            rate="100",
            contact_id=customer.id,
            incoming_cash_amount="0",
        ),
    )
    owed = await ObligationService.outstanding(db_session, ca, customer.uuid)
    result = await PaymentService.create(
        db_session,
        ca,
        shop.id,
        "allocate-two",
        payment(
            customer,
            box,
            currencies["AFN"],
            owed[0],
            "150",
            allocations=[{"obligation_id": o.id, "amount": "75"} for o in owed],
        ),
    )
    assert len(result["obligations"]) == 2
    assert all(o["remaining_amount"] == "25.00" for o in result["obligations"])
    assert any(o["exchange_id"] == exchange["id"] for o in result["obligations"])


@pytest.mark.asyncio
async def test_cross_currency_cumulative_rounding_and_rate_snapshot(db_session, credit):
    ca, _, shop, _, currencies, box, customer = credit
    await opening_debt(db_session, ca, customer, currencies["AFN"], "10", "10")
    target = (await ObligationService.outstanding(db_session, ca, customer.uuid))[0]
    bridge = await ExchangeService.create(
        db_session,
        ca,
        shop.id,
        "rounding-bridge",
        trade(
            currencies,
            box,
            amount="3",
            rate="3.33333333",
            contact_id=customer.id,
            incoming_cash_amount="0",
            outgoing_cash_amount="0",
        ),
    )
    for index, amount in enumerate(("3.33", "3.34", "3.33")):
        result = await PaymentService.create(
            db_session,
            ca,
            shop.id,
            f"cross-round-{index}",
            payment(
                customer,
                box,
                currencies["USD"],
                target,
                "1",
                settlement_exchange_id=bridge["id"],
                allocations=[{"obligation_id": target.id, "amount": amount}],
            ),
        )
        assert (
            result["payment"]["receipt"]["settlement_exchange"]["values"]["rate"]
            == "3.33333333"
        )
        await assert_balanced(db_session, result["payment"]["entry_id"])
    assert not await ObligationService.outstanding(db_session, ca, customer.uuid)


@pytest.mark.asyncio
async def test_payment_failure_rolls_back_and_spent_receipt_blocks_reversal(
    db_session, credit, monkeypatch
):
    from app.services.finance import LedgerService

    ca, _, shop, _, currencies, box, customer = credit
    await opening_debt(db_session, ca, customer, currencies["AFN"])
    obligation = (await ObligationService.outstanding(db_session, ca, customer.uuid))[0]
    payload = payment(customer, box, currencies["AFN"], obligation)
    before = await cash(db_session, ca, shop)
    original = PaymentService.result

    async def fail(*args):
        raise RuntimeError("response failed")

    monkeypatch.setattr(PaymentService, "result", fail)
    with pytest.raises(RuntimeError, match="response failed"):
        await PaymentService.create(db_session, ca, shop.id, "failure", payload)
    assert await cash(db_session, ca, shop) == before
    assert not list(await db_session.scalars(select(Payment)))
    assert not list(await db_session.scalars(select(PaymentAllocation)))
    monkeypatch.setattr(PaymentService, "result", original)
    result = await PaymentService.create(db_session, ca, shop.id, "failure", payload)
    account = await db_session.scalar(
        select(LedgerAccount).where(
            LedgerAccount.cashbox_id == box.id,
            LedgerAccount.currency_id == currencies["AFN"].id,
        )
    )
    expense = await LedgerService.account(
        db_session, ca, shop.id, currencies["AFN"].id, "test-spending", "expense"
    )
    await LedgerService.post(
        db_session,
        ca,
        shop.id,
        "expense:create",
        "spend-all",
        "spent",
        [
            {"account_id": account.id, "amount": "-26000.00", "book_afn": "-26000.00"},
            {"account_id": expense.id, "amount": "26000.00", "book_afn": "26000.00"},
        ],
    )
    with pytest.raises(ConflictError):
        await PaymentService.reverse(
            db_session,
            ca,
            shop.id,
            UUID(result["payment"]["uuid"]),
            "cannot-undo",
            PaymentReverseRequest(owner_approved=True, reason="correction"),
        )
    assert (await ObligationService.outstanding(db_session, ca, customer.uuid))[
        0
    ].remaining_amount == "4000.00"


@pytest.mark.asyncio
async def test_payment_customer_scope_deactivation_and_conversion_validation(
    db_session, credit
):
    from app.schemas.finance.request import CashboxUpdateRequest
    from app.services.finance import FinanceService

    ca, _, shop, _, currencies, box, customer = credit
    await opening_debt(db_session, ca, customer, currencies["AFN"])
    obligation = (await ObligationService.outstanding(db_session, ca, customer.uuid))[0]
    other = await ContactService.create(
        db_session, ca, ContactCreateRequest(shop_uuid=shop.uuid, name="Other customer")
    )
    with pytest.raises(NotFoundError):
        await PaymentService.create(
            db_session,
            ca,
            shop.id,
            "wrong-customer",
            payment(other, box, currencies["AFN"], obligation),
        )
    bridge = await ExchangeService.create(
        db_session,
        ca,
        shop.id,
        "bridge",
        trade(
            currencies,
            box,
            contact_id=customer.id,
            incoming_cash_amount="0",
            outgoing_cash_amount="0",
        ),
    )
    with pytest.raises(AppValidationError):
        await PaymentService.create(
            db_session,
            ca,
            shop.id,
            "wrong-rate",
            payment(
                customer,
                box,
                currencies["USD"],
                obligation,
                "10",
                settlement_exchange_id=bridge["id"],
                allocations=[{"obligation_id": obligation.id, "amount": "699"}],
            ),
        )
    with pytest.raises(NotFoundError):
        await PaymentService.create(
            db_session,
            ca,
            shop.id,
            "wrong-bridge-customer",
            payment(
                other,
                box,
                currencies["USD"],
                obligation,
                "10",
                settlement_exchange_id=bridge["id"],
                allocations=[{"obligation_id": obligation.id, "amount": "700"}],
            ),
        )
    result = await PaymentService.create(
        db_session,
        ca,
        shop.id,
        "receive",
        payment(customer, box, currencies["AFN"], obligation),
    )
    await ContactService.update(
        db_session, ca, customer.uuid, ContactUpdateRequest(is_active=False)
    )
    with pytest.raises(AuthorizationError):
        await PaymentService.create(
            db_session,
            ca,
            shop.id,
            "inactive-contact",
            payment(customer, box, currencies["AFN"], obligation, "1000"),
        )
    await FinanceService.update_cashbox(
        db_session, ca, shop.id, box.id, CashboxUpdateRequest(is_active=False)
    )
    assert (
        await PaymentService.get(
            db_session, ca, shop.id, UUID(result["payment"]["uuid"])
        )
    ).receipt.customer_name == "احمد پښتون"


@pytest.mark.asyncio
async def test_cash_exchange_retry_hash_remains_compatible_with_phase8(
    db_session, credit
):
    from app.services.tenant_operations import TenantOperations

    ca, _, shop, _, currencies, box, _ = credit
    data = trade(currencies, box)
    legacy_payload = data.model_dump(
        exclude={"incoming_cash_amount", "outgoing_cash_amount", "outgoing_afn_rate"}
    )
    legacy_result = {"legacy": "saved Phase 8 result"}

    async def execute():
        return legacy_result

    await TenantOperations.execute_once(
        db_session,
        ca,
        "exchange:create",
        "legacy",
        {"shop_id": shop.id, **legacy_payload},
        "exchange:create",
        execute,
    )
    assert (
        await ExchangeService.create(db_session, ca, shop.id, "legacy", data)
        == legacy_result
    )


@pytest.mark.asyncio
async def test_book_only_revaluation_cannot_create_cash_or_afn_amounts(
    db_session, credit
):
    from app.services.finance import LedgerService

    ca, _, shop, _, currencies, box, _ = credit
    cash_account = await db_session.scalar(
        select(LedgerAccount).where(
            LedgerAccount.cashbox_id == box.id,
            LedgerAccount.currency_id == currencies["USD"].id,
        )
    )
    foreign = await LedgerService.account(
        db_session, ca, shop.id, currencies["USD"].id, "test-book-clearing", "clearing"
    )
    afn = await LedgerService.account(
        db_session, ca, shop.id, currencies["AFN"].id, "test-afn-clearing", "clearing"
    )
    for account in (cash_account, afn):
        with pytest.raises(AppValidationError):
            await LedgerService.post(
                db_session,
                ca,
                shop.id,
                "payment:create",
                f"zero-{account.id}",
                "invalid revaluation",
                [
                    {"account_id": account.id, "amount": "0.00", "book_afn": "1.00"},
                    {"account_id": foreign.id, "amount": "0.00", "book_afn": "-1.00"},
                ],
            )
    with pytest.raises(AppValidationError):
        await LedgerService.post(
            db_session,
            ca,
            shop.id,
            "payment:create",
            "zero-noop",
            "empty clearing",
            [
                {"account_id": foreign.id, "amount": "0.00", "book_afn": "0.00"},
                {"account_id": foreign.id, "amount": "0.00", "book_afn": "0.00"},
            ],
        )
