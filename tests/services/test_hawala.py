"""Hawala acceptance, money movement, cancellation, fee and settlement invariants."""

from decimal import Decimal
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import AppValidationError, ConflictError, NotFoundError
from app.models.finance import JournalEntry, LedgerAccount
from app.models.hawala import Hawala, HawalaEvent
from app.schemas.contact.request import ContactCreateRequest, ContactUpdateRequest
from app.schemas.hawala.request import (
    AgentSettlementCreateRequest,
    HawalaCancelRequest,
    HawalaConfirmRequest,
    HawalaCreateRequest,
    HawalaPayRequest,
    HawalaRefundRequest,
    HawalaReviewRequest,
    HawalaUpdateRequest,
)
from app.schemas.payment.request import PaymentReverseRequest
from app.services.agent_settlement import AgentSettlementService
from app.services.contact import ContactService
from app.services.contact_ledger import ContactLedgerService
from app.services.finance import LedgerService
from app.services.hawala import HawalaService
from tests.services.test_exchange import cash, seed
from tests.services.test_finance import finance as finance  # noqa: PLC0414
from tests.services.test_payment import assert_balanced
from tests.services.test_tenant_operations import tenants as tenants  # noqa: PLC0414


@pytest_asyncio.fixture
async def hawala_setup(db_session, finance):
    ca, cb, shop, other_shop, currencies, box = finance
    await seed(db_session, ca, shop, currencies, box)
    agent = await ContactService.create(
        db_session,
        ca,
        ContactCreateRequest(
            shop_uuid=shop.uuid, name="نماینده پښتون", is_agent=True, is_customer=False
        ),
    )
    return ca, cb, shop, other_shop, currencies, box, agent


def draft(currencies, box, agent, direction="outgoing", **changes):
    return HawalaCreateRequest(
        **{
            "direction": direction,
            "agent_id": agent.id,
            "collection_currency_id": currencies["AFN"].id,
            "payout_currency_id": currencies["AFN"].id,
            "cashbox_id": box.id,
            "sender_name": "احمد",
            "recipient_name": "محمود",
            "destination": "کابل",
            "collection_amount": "1000",
            "payout_amount": "1000",
            "rate": "1",
            "customer_fee": "100",
            "agent_fee": "20",
            **changes,
        }
    )


def pay(box=None, mode="partner"):
    return HawalaPayRequest(
        mode=mode,
        cashbox_id=box.id if box else None,
        beneficiary_verified=True,
        verification_reference="Verified recipient",
        evidence_reference="Partner payout receipt",
        reason="Payout",
    )


async def confirm(db, context, shop, record, key="confirm"):
    return await HawalaService.transition(
        db,
        context,
        shop.id,
        record.uuid,
        key,
        "confirm",
        HawalaConfirmRequest(
            reason="Reviewed instruction",
            instruction_verified=True,
            instruction_reference="Partner instruction 1",
        ),
    )


def settle(
    agent, currency, obligations, amount, box=None, direction="pay", method="cash"
):
    return AgentSettlementCreateRequest(
        agent_id=agent.id,
        currency_id=currency.id,
        cashbox_id=box.id if box else None,
        direction=direction,
        method=method,
        amount=amount,
        external_reference="Manual transfer 1",
        evidence_reference="Bank/cash receipt",
        allocations=obligations,
        reason="Agent settlement",
    )


@pytest.mark.asyncio
async def test_outgoing_funding_partner_payout_partial_agent_settlement_and_receipt(
    db_session, hawala_setup
):
    ca, _, shop, _, units, box, agent = hawala_setup
    record = await HawalaService.create(
        db_session, ca, shop.id, draft(units, box, agent)
    )
    assert record.state == "draft" and record.receipt is None
    assert len(list(await db_session.scalars(select(JournalEntry)))) == 2
    funded = await confirm(db_session, ca, shop, record)
    assert funded["state"] == "pending" and funded["settlement_status"] == "outstanding"
    assert (await cash(db_session, ca, shop))["AFN"] == ("21100.00", "21100.00")
    assert (await ContactLedgerService.balances(db_session, ca, agent.uuid))[
        0
    ].payable == "1020.00"
    assert await confirm(db_session, ca, shop, record) == funded
    before = await cash(db_session, ca, shop)
    paid = await HawalaService.transition(
        db_session, ca, shop.id, record.uuid, "pay", "pay", pay()
    )
    assert paid["state"] == "paid" and paid["settlement_status"] == "outstanding"
    assert (
        await cash(db_session, ca, shop) == before
    )  # Partner payout never spends our cash.
    with pytest.raises(ConflictError):
        await HawalaService.transition(
            db_session, ca, shop.id, record.uuid, "pay-again", "pay", pay()
        )
    for entry in await db_session.scalars(select(JournalEntry)):
        await assert_balanced(db_session, entry.id)
    due = await AgentSettlementService.outstanding(db_session, ca, agent.uuid)
    principal = next(o for o in due if o.kind == "principal")
    request = settle(
        agent,
        units["AFN"],
        [{"obligation_id": principal.id, "amount": "600"}],
        "600",
        box,
    )
    result = await AgentSettlementService.create(
        db_session, ca, shop.id, "settlement", request
    )
    assert (
        await AgentSettlementService.create(
            db_session, ca, shop.id, "settlement", request
        )
        == result
    )
    assert (await cash(db_session, ca, shop))["AFN"][0] == "20500.00"
    assert (
        await HawalaService.get(db_session, ca, shop.id, record.uuid)
    ).settlement_status == "partial"
    due = await AgentSettlementService.outstanding(db_session, ca, agent.uuid)
    assert sum(Decimal(o.remaining_amount) for o in due) == 420
    rest = await AgentSettlementService.create(
        db_session,
        ca,
        shop.id,
        "rest",
        settle(
            agent,
            units["AFN"],
            [{"obligation_id": o.id, "amount": o.remaining_amount} for o in due],
            "420",
            box,
        ),
    )
    assert (await cash(db_session, ca, shop))["AFN"][0] == "20080.00"
    assert (
        await HawalaService.get(db_session, ca, shop.id, record.uuid)
    ).settlement_status == "settled"
    await AgentSettlementService.reverse(
        db_session,
        ca,
        shop.id,
        UUID(rest["uuid"]),
        "undo-rest",
        PaymentReverseRequest(owner_approved=True, reason="Correction"),
    )
    assert (
        await HawalaService.get(db_session, ca, shop.id, record.uuid)
    ).settlement_status == "partial"
    with pytest.raises(ConflictError):
        await AgentSettlementService.reverse(
            db_session,
            ca,
            shop.id,
            UUID(rest["uuid"]),
            "undo-again",
            PaymentReverseRequest(owner_approved=True, reason="Correction"),
        )
    await ContactService.update(
        db_session, ca, agent.uuid, ContactUpdateRequest(name="Changed name")
    )
    assert (
        await HawalaService.get(db_session, ca, shop.id, record.uuid)
    ).receipt.model_dump(mode="json") == funded["receipt"]


@pytest.mark.asyncio
async def test_incoming_instruction_verification_local_cash_payout_and_agent_receivable(
    db_session, hawala_setup
):
    ca, _, shop, _, units, box, agent = hawala_setup
    record = await HawalaService.create(
        db_session, ca, shop.id, draft(units, box, agent, direction="incoming")
    )
    before = await cash(db_session, ca, shop)
    with pytest.raises(AppValidationError):
        await HawalaService.transition(
            db_session,
            ca,
            shop.id,
            record.uuid,
            "unverified",
            "confirm",
            HawalaConfirmRequest(reason="Instruction"),
        )
    funded = await confirm(db_session, ca, shop, record)
    assert await cash(db_session, ca, shop) == before
    balances = (await ContactLedgerService.balances(db_session, ca, agent.uuid))[0]
    assert balances.receivable == "1100.00" and balances.payable == "20.00"
    with pytest.raises(AppValidationError):
        await HawalaService.transition(
            db_session, ca, shop.id, record.uuid, "wrong-mode", "pay", pay()
        )
    paid = await HawalaService.transition(
        db_session, ca, shop.id, record.uuid, "pay", "pay", pay(box, "local")
    )
    assert paid["state"] == "paid" and funded["receipt"] == paid["receipt"]
    assert (await cash(db_session, ca, shop))["AFN"][0] == "19000.00"
    due = await AgentSettlementService.outstanding(db_session, ca, agent.uuid)
    incoming = [o for o in due if o.direction == "receivable"]
    await AgentSettlementService.create(
        db_session,
        ca,
        shop.id,
        "receive-agent",
        settle(
            agent,
            units["AFN"],
            [{"obligation_id": o.id, "amount": o.remaining_amount} for o in incoming],
            "1100",
            box,
            "receive",
        ),
    )
    outgoing = next(o for o in due if o.direction == "payable")
    await AgentSettlementService.create(
        db_session,
        ca,
        shop.id,
        "pay-fee",
        settle(
            agent,
            units["AFN"],
            [{"obligation_id": outgoing.id, "amount": "20"}],
            "20",
            box,
        ),
    )
    assert (await cash(db_session, ca, shop))["AFN"][0] == "20080.00"
    assert not await AgentSettlementService.outstanding(db_session, ca, agent.uuid)
    for entry in await db_session.scalars(select(JournalEntry)):
        await assert_balanced(db_session, entry.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "customer_refund,agent_refund,earned",
    [
        (c, a, e)
        for c in (False, True)
        for a in (False, True)
        for e in ("confirm", "paid")
    ],
)
async def test_cancellation_policy_refund_due_and_actual_partial_refunds(
    db_session, hawala_setup, customer_refund, agent_refund, earned
):
    ca, _, shop, _, units, box, agent = hawala_setup
    record = await HawalaService.create(
        db_session,
        ca,
        shop.id,
        draft(
            units,
            box,
            agent,
            customer_fee_refundable=customer_refund,
            agent_fee_refundable=agent_refund,
            customer_fee_earned_at=earned,
            agent_fee_earned_at=earned,
        ),
    )
    await confirm(db_session, ca, shop, record)
    with pytest.raises(AppValidationError):
        await HawalaService.transition(
            db_session,
            ca,
            shop.id,
            record.uuid,
            "unsafe-cancel",
            "cancel",
            HawalaCancelRequest(reason="Not checked"),
        )
    data = HawalaCancelRequest(
        reason="Partner has not paid",
        partner_not_paid=True,
        partner_confirmation_reference="Partner cancellation 1",
    )
    before = await cash(db_session, ca, shop)
    cancelled = await HawalaService.transition(
        db_session, ca, shop.id, record.uuid, "cancel", "cancel", data
    )
    assert (
        await HawalaService.transition(
            db_session, ca, shop.id, record.uuid, "cancel", "cancel", data
        )
        == cancelled
    )
    assert cancelled["state"] == "cancelled" and cancelled["refund_paid"] == "0.00"
    assert cancelled["refund_due"] == ("1100.00" if customer_refund else "1000.00")
    assert await cash(db_session, ca, shop) == before
    due = await AgentSettlementService.outstanding(db_session, ca, agent.uuid)
    assert sum((Decimal(o.remaining_amount) for o in due), Decimal(0)) == (
        0 if agent_refund else 20
    )
    first = HawalaRefundRequest(
        cashbox_id=box.id,
        amount="600",
        reason="Refund",
        evidence_reference="Beneficiary receipt",
    )
    partial = await HawalaService.transition(
        db_session, ca, shop.id, record.uuid, "refund-first", "refund", first
    )
    assert (
        await HawalaService.transition(
            db_session, ca, shop.id, record.uuid, "refund-first", "refund", first
        )
        == partial
    )
    remaining = partial["refund_remaining"]
    final = await HawalaService.transition(
        db_session,
        ca,
        shop.id,
        record.uuid,
        "refund-rest",
        "refund",
        HawalaRefundRequest(
            cashbox_id=box.id,
            amount=remaining,
            reason="Rest",
            evidence_reference="Receipt 2",
        ),
    )
    assert final["refund_remaining"] == "0.00"
    assert (await cash(db_session, ca, shop))["AFN"][0] == (
        "20000.00" if customer_refund else "20100.00"
    )
    with pytest.raises(ConflictError):
        await HawalaService.transition(
            db_session, ca, shop.id, record.uuid, "over-refund", "refund", first
        )
    for entry in await db_session.scalars(select(JournalEntry)):
        await assert_balanced(db_session, entry.id)


@pytest.mark.asyncio
async def test_draft_editing_freeze_and_paid_correction_review(
    db_session, hawala_setup
):
    ca, cb, shop, other_shop, units, box, agent = hawala_setup
    request = draft(units, box, agent, sender_name=None)
    record = await HawalaService.create(db_session, ca, shop.id, request)
    with pytest.raises(AppValidationError):
        await confirm(db_session, ca, shop, record)
    updated = await HawalaService.update(
        db_session, ca, shop.id, record.uuid, HawalaUpdateRequest(sender_name="احمد")
    )
    assert updated.draft.recipient_name == "محمود"
    funded = await confirm(db_session, ca, shop, updated)
    with pytest.raises(ConflictError):
        await HawalaService.update(
            db_session,
            ca,
            shop.id,
            record.uuid,
            HawalaUpdateRequest(sender_name="Changed"),
        )
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                update(Hawala).where(Hawala.id == record.id).values(details={})
            )
    await HawalaService.transition(
        db_session, ca, shop.id, record.uuid, "pay", "pay", pay()
    )
    with pytest.raises(ConflictError):
        await HawalaService.transition(
            db_session,
            ca,
            shop.id,
            record.uuid,
            "cancel-paid",
            "cancel",
            HawalaCancelRequest(
                reason="Disputed",
                partner_not_paid=True,
                partner_confirmation_reference="Dispute",
            ),
        )
    reviewed = await HawalaService.transition(
        db_session,
        ca,
        shop.id,
        record.uuid,
        "review",
        "review",
        HawalaReviewRequest(
            owner_approved=True,
            reason="Dispute investigated",
            decision="investigate",
            evidence_reference="Case 1",
        ),
    )
    assert reviewed["state"] == "paid" and reviewed["history"][-1]["kind"] == "reviewed"
    assert reviewed["receipt"] == funded["receipt"]
    with pytest.raises(NotFoundError):
        await HawalaService.get(db_session, cb, other_shop.id, record.uuid)
    with pytest.raises(NotFoundError):
        await HawalaService.create(db_session, cb, other_shop.id, request)


@pytest.mark.asyncio
async def test_external_settlement_cancellation_dependency_and_reversal(
    db_session, hawala_setup
):
    ca, _, shop, _, units, box, agent = hawala_setup
    record = await HawalaService.create(
        db_session, ca, shop.id, draft(units, box, agent)
    )
    await confirm(db_session, ca, shop, record)
    principal = next(
        o
        for o in await AgentSettlementService.outstanding(db_session, ca, agent.uuid)
        if o.kind == "principal"
    )
    before = await cash(db_session, ca, shop)
    request = settle(
        agent,
        units["AFN"],
        [{"obligation_id": principal.id, "amount": "600"}],
        "600",
        method="bank",
    )
    result = await AgentSettlementService.create(
        db_session, ca, shop.id, "external", request
    )
    assert await cash(db_session, ca, shop) == before
    with pytest.raises(ConflictError):
        await HawalaService.transition(
            db_session,
            ca,
            shop.id,
            record.uuid,
            "cancel",
            "cancel",
            HawalaCancelRequest(
                reason="Cancel",
                partner_not_paid=True,
                partner_confirmation_reference="Partner",
            ),
        )
    with pytest.raises(ConflictError):
        await AgentSettlementService.create(db_session, ca, shop.id, "excess", request)
    await AgentSettlementService.reverse(
        db_session,
        ca,
        shop.id,
        UUID(result["uuid"]),
        "undo-external",
        PaymentReverseRequest(owner_approved=True, reason="Undo external record"),
    )
    assert await cash(db_session, ca, shop) == before
    await HawalaService.transition(
        db_session,
        ca,
        shop.id,
        record.uuid,
        "cancel",
        "cancel",
        HawalaCancelRequest(
            reason="Cancel",
            partner_not_paid=True,
            partner_confirmation_reference="Partner",
        ),
    )
    assert not await AgentSettlementService.outstanding(db_session, ca, agent.uuid)


@pytest.mark.asyncio
async def test_cross_currency_principal_and_income_are_separate(
    db_session, hawala_setup
):
    ca, _, shop, _, units, box, agent = hawala_setup
    record = await HawalaService.create(
        db_session,
        ca,
        shop.id,
        draft(
            units,
            box,
            agent,
            collection_currency_id=units["USD"].id,
            collection_amount="10",
            payout_amount="710",
            rate="71",
            collection_afn_rate="72",
            customer_fee="1",
            agent_fee="10",
        ),
    )
    funded = await confirm(db_session, ca, shop, record)
    assert (await cash(db_session, ca, shop))["USD"] == ("111.00", "7792.00")
    assert funded["receipt"]["terms"]["rate"] == "71.00000000"
    assert (await ContactLedgerService.balances(db_session, ca, agent.uuid))[
        0
    ].payable == "720.00"
    await HawalaService.transition(
        db_session, ca, shop.id, record.uuid, "pay", "pay", pay()
    )
    for entry in await db_session.scalars(select(JournalEntry)):
        await assert_balanced(db_session, entry.id)
    account = await db_session.scalar(
        select(LedgerAccount).where(LedgerAccount.code == "hawala_profit")
    )
    assert await LedgerService.balance(db_session, ca, account.id) == (
        Decimal(-10),
        Decimal(-10),
    )


@pytest.mark.asyncio
async def test_incoming_cancel_retains_only_nonrefundable_fees_and_never_refunds_local_cash(
    db_session, hawala_setup
):
    ca, _, shop, _, units, box, agent = hawala_setup
    record = await HawalaService.create(
        db_session,
        ca,
        shop.id,
        draft(
            units,
            box,
            agent,
            direction="incoming",
            customer_fee_refundable=False,
            agent_fee_refundable=False,
        ),
    )
    await confirm(db_session, ca, shop, record)
    before = await cash(db_session, ca, shop)
    cancelled = await HawalaService.transition(
        db_session,
        ca,
        shop.id,
        record.uuid,
        "cancel-incoming",
        "cancel",
        HawalaCancelRequest(reason="Partner revoked instruction"),
    )
    assert (
        cancelled["refund_due"] == "0.00" and await cash(db_session, ca, shop) == before
    )
    balance = (await ContactLedgerService.balances(db_session, ca, agent.uuid))[0]
    assert balance.receivable == "100.00" and balance.payable == "20.00"
    with pytest.raises(ConflictError):
        await HawalaService.transition(
            db_session,
            ca,
            shop.id,
            record.uuid,
            "wrong-refund",
            "refund",
            HawalaRefundRequest(
                cashbox_id=box.id,
                amount="1",
                evidence_reference="Receipt",
                reason="Cannot refund money not collected",
            ),
        )


@pytest.mark.asyncio
async def test_payout_insufficient_cash_and_failure_after_journal_are_atomic(
    db_session, hawala_setup, monkeypatch
):
    from app.schemas.finance.request import CashboxCreateRequest
    from app.services.finance import FinanceService

    ca, _, shop, _, units, box, agent = hawala_setup
    empty = await FinanceService.create_cashbox(
        db_session, ca, shop.id, CashboxCreateRequest(name="Empty")
    )
    record = await HawalaService.create(
        db_session, ca, shop.id, draft(units, box, agent, direction="incoming")
    )
    await confirm(db_session, ca, shop, record)
    with pytest.raises(ConflictError):
        await HawalaService.transition(
            db_session,
            ca,
            shop.id,
            record.uuid,
            "pay-empty",
            "pay",
            pay(empty, "local"),
        )
    before = await cash(db_session, ca, shop)
    original = HawalaService.event

    async def fail(*args, **kwargs):
        raise RuntimeError("Evidence storage failure")

    monkeypatch.setattr(HawalaService, "event", fail)
    with pytest.raises(RuntimeError, match="Evidence storage failure"):
        await HawalaService.transition(
            db_session,
            ca,
            shop.id,
            record.uuid,
            "pay-failure",
            "pay",
            pay(box, "local"),
        )
    assert await cash(db_session, ca, shop) == before
    assert (
        await HawalaService.get(db_session, ca, shop.id, record.uuid)
    ).state == "pending"
    assert len(list(await db_session.scalars(select(JournalEntry)))) == 3
    monkeypatch.setattr(HawalaService, "event", original)
    await HawalaService.transition(
        db_session, ca, shop.id, record.uuid, "pay-failure", "pay", pay(box, "local")
    )
    assert (await cash(db_session, ca, shop))["AFN"][0] == "19000.00"


@pytest.mark.asyncio
async def test_draft_cancel_and_invalid_precision_references_and_settlement_allocations(
    db_session, hawala_setup
):
    from app.core.exceptions import AuthorizationError
    from app.schemas.finance.request import CashboxUpdateRequest
    from app.services.finance import FinanceService

    ca, cb, shop, other_shop, units, box, agent = hawala_setup
    record = await HawalaService.create(
        db_session, ca, shop.id, draft(units, box, agent)
    )
    cancelled = await HawalaService.transition(
        db_session,
        ca,
        shop.id,
        record.uuid,
        "draft-cancel",
        "cancel",
        HawalaCancelRequest(reason="Draft abandoned"),
    )
    assert cancelled["state"] == "cancelled" and cancelled["refund_due"] == "0.00"
    assert len(list(await db_session.scalars(select(JournalEntry)))) == 2
    for changes in (
        {"rate": "0"},
        {"collection_amount": "0"},
        {"collection_amount": "1e2"},
        {"collection_amount": "1.001"},
        {"payout_amount": "999"},
        {"rate": "2"},
        {"customer_fee": "-1"},
        {"customer_fee": "0.001"},
        {"agent_fee": "NaN"},
        {"collection_afn_rate": "2"},
    ):
        bad = await HawalaService.create(
            db_session, ca, shop.id, draft(units, box, agent, **changes)
        )
        with pytest.raises(AppValidationError):
            await confirm(db_session, ca, shop, bad, f"invalid-{bad.id}")
    foreign = await ContactService.create(
        db_session,
        cb,
        ContactCreateRequest(
            shop_uuid=other_shop.uuid, name="Other agent", is_agent=True
        ),
    )
    with pytest.raises(NotFoundError):
        await HawalaService.create(db_session, ca, shop.id, draft(units, box, foreign))
    record = await HawalaService.create(
        db_session, ca, shop.id, draft(units, box, agent)
    )
    await confirm(db_session, ca, shop, record)
    obligation = (await AgentSettlementService.outstanding(db_session, ca, agent.uuid))[
        0
    ]
    base = settle(
        agent,
        units["AFN"],
        [{"obligation_id": obligation.id, "amount": "100"}],
        "100",
        box,
    )
    for changes in (
        {"amount": "101"},
        {"currency_id": units["USD"].id},
        {"direction": "receive"},
        {"method": "bank"},
        {"allocations": [{"obligation_id": obligation.id, "amount": "50"}] * 2},
    ):
        with pytest.raises(AppValidationError):
            await AgentSettlementService.create(
                db_session,
                ca,
                shop.id,
                "bad",
                AgentSettlementCreateRequest(**{**base.model_dump(), **changes}),
            )
    with pytest.raises(NotFoundError):
        await AgentSettlementService.create(
            db_session, cb, other_shop.id, "foreign", base
        )
    await FinanceService.update_cashbox(
        db_session, ca, shop.id, box.id, CashboxUpdateRequest(is_active=False)
    )
    with pytest.raises(AuthorizationError):
        await AgentSettlementService.create(
            db_session, ca, shop.id, "inactive-box", base
        )
    assert (
        await HawalaService.get(db_session, ca, shop.id, record.uuid)
    ).state == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "race", ["duplicate", "retry", "cancel", "settlement", "refund"]
)
async def test_concurrent_hawala_payouts_and_cancellation_are_serialized(
    tmp_path, race
):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, User
    from app.schemas.finance.request import CashboxCreateRequest, CurrencyCreateRequest
    from app.services.finance import FinanceService
    from app.services.organization import OrganizationService
    from app.services.organization_context import OrganizationContext
    from app.services.shop import ShopService

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'hawala-race.sqlite'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as db:
            user = User(
                user_name="owner",
                email="hawala-race@example.com",
                password_hash="unused",
            )
            db.add(user)
            await db.flush()
            org = await OrganizationService.create(db, "Hawala", "hawala-race", user)
            context = await OrganizationContext.resolve(db, user.id, org.id)
            shop = await ShopService.create(db, context, "Shop")
            unit = await FinanceService.create_currency(
                db, context, CurrencyCreateRequest(code="AFN", precision=2)
            )
            box = await FinanceService.create_cashbox(
                db, context, shop.id, CashboxCreateRequest(name="Till")
            )
            agent = await ContactService.create(
                db,
                context,
                ContactCreateRequest(shop_uuid=shop.uuid, name="Agent", is_agent=True),
            )
            transfer = await HawalaService.create(
                db,
                context,
                shop.id,
                draft(
                    {"AFN": unit},
                    box,
                    agent,
                    direction="outgoing" if race == "refund" else "incoming",
                    customer_fee="0",
                    agent_fee="0",
                ),
            )
            await confirm(db, context, shop, transfer)
            # Cash for exactly one local payout, using the reviewed opening route.
            from tests.services.test_finance import opening

            if race == "refund":
                await HawalaService.transition(
                    db,
                    context,
                    shop.id,
                    transfer.uuid,
                    "cancel-before-refund",
                    "cancel",
                    HawalaCancelRequest(
                        reason="Partner cancellation",
                        partner_not_paid=True,
                        partner_confirmation_reference="Confirmed unpaid",
                    ),
                )
            else:
                await LedgerService.opening(
                    db,
                    context,
                    shop.id,
                    "cash-opening",
                    opening(box, unit, "1000", "1000"),
                )
            obligation = (
                (await AgentSettlementService.outstanding(db, context, agent.uuid))[0]
                if race == "settlement"
                else None
            )
            user_id, org_id, shop_id = user.id, org.id, shop.id
            await db.commit()
        ready = asyncio.Event()

        async def attempt(second):
            async with sessions() as db:
                context = await OrganizationContext.resolve(db, user_id, org_id)
                if second:
                    ready.set()
                else:
                    await ready.wait()
                try:
                    action = "cancel" if second and race == "cancel" else "pay"
                    body = (
                        HawalaCancelRequest(reason="Partner revoked instruction")
                        if action == "cancel"
                        else pay(box, "local")
                    )
                    if race == "settlement":
                        result = await AgentSettlementService.create(
                            db,
                            context,
                            shop_id,
                            f"settle-{second}",
                            settle(
                                agent,
                                unit,
                                [{"obligation_id": obligation.id, "amount": "600"}],
                                "600",
                                box,
                                "receive",
                            ),
                        )
                    elif race == "refund":
                        result = await HawalaService.transition(
                            db,
                            context,
                            shop_id,
                            transfer.uuid,
                            f"refund-{second}",
                            "refund",
                            HawalaRefundRequest(
                                cashbox_id=box.id,
                                amount="600",
                                evidence_reference="Refund receipt",
                                reason="Refund",
                            ),
                        )
                    else:
                        result = await HawalaService.transition(
                            db,
                            context,
                            shop_id,
                            transfer.uuid,
                            "same" if race == "retry" else f"race-{second}",
                            action,
                            body,
                        )

                    await db.commit()
                    return result
                except ConflictError:
                    await db.rollback()
                    return None

        results = await asyncio.wait_for(
            asyncio.gather(attempt(False), attempt(True)), timeout=20
        )
        if race == "retry":
            assert results[0] == results[1] and results[0] is not None
        else:
            assert sum(result is not None for result in results) == 1
        async with sessions() as db:
            context = await OrganizationContext.resolve(db, user_id, org_id)
            status = await HawalaService.get(db, context, shop_id, transfer.uuid)
            balances = (await FinanceService.cashboxes(db, context, shop_id))[
                0
            ].balances
            assert balances[0].amount == (
                "1600.00"
                if race == "settlement"
                else "400.00"
                if race == "refund"
                else "0.00"
                if status.state == "paid"
                else "1000.00"
            )
            if race == "settlement":
                due = await AgentSettlementService.outstanding(db, context, agent.uuid)
                assert due[0].remaining_amount == "400.00"
            if race == "refund":
                assert status.refund_remaining == "400.00"
            events = list(
                await db.scalars(
                    select(HawalaEvent).where(
                        HawalaEvent.kind.in_(("paid", "cancelled"))
                    )
                )
            )
            assert len(events) == (0 if race == "settlement" else 1)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_foreign_local_payout_and_opening_settlement_release_exact_books(
    db_session, hawala_setup
):
    from app.schemas.contact.request import ContactOpeningRequest

    ca, _, shop, _, units, box, agent = hawala_setup
    record = await HawalaService.create(
        db_session,
        ca,
        shop.id,
        draft(
            units,
            box,
            agent,
            direction="incoming",
            payout_currency_id=units["USD"].id,
            collection_amount="1000",
            payout_amount="10",
            rate="0.01",
            payout_afn_rate="72",
            customer_fee="0",
            agent_fee="0",
        ),
    )
    await confirm(db_session, ca, shop, record)
    await HawalaService.transition(
        db_session, ca, shop.id, record.uuid, "foreign-pay", "pay", pay(box, "local")
    )
    assert (await cash(db_session, ca, shop))["USD"] == ("90.00", "6300.00")
    account = await db_session.scalar(
        select(LedgerAccount).where(LedgerAccount.code == "hawala_profit")
    )
    assert await LedgerService.balance(db_session, ca, account.id) == (
        Decimal(-20),
        Decimal(-20),
    )
    # A new agent's reviewed foreign opening is independently allocatable.
    other = await ContactService.create(
        db_session,
        ca,
        ContactCreateRequest(shop_uuid=shop.uuid, name="Opening agent", is_agent=True),
    )
    await ContactLedgerService.opening(
        db_session,
        ca,
        other.uuid,
        "agent-opening",
        ContactOpeningRequest(
            role="agent",
            direction="payable",
            currency_id=units["USD"].id,
            amount="3",
            book_afn="213",
            owner_approved=True,
            reason="Reviewed opening",
        ),
    )
    debt = (await AgentSettlementService.outstanding(db_session, ca, other.uuid))[0]
    result = await AgentSettlementService.create(
        db_session,
        ca,
        shop.id,
        "settle-opening",
        settle(
            other, units["USD"], [{"obligation_id": debt.id, "amount": "3"}], "3", box
        ),
    )
    assert result["details"]["profit_afn"] == "3.00"
    assert not await AgentSettlementService.outstanding(db_session, ca, other.uuid)
    for entry in await db_session.scalars(select(JournalEntry)):
        await assert_balanced(db_session, entry.id)
