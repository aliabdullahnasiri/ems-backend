"""Cash reports reconcile with posted openings."""

import pytest

from app.services.finance import LedgerService
from app.services.reports import ReportService
from tests.services.test_finance import finance as finance  # noqa: PLC0414
from tests.services.test_finance import opening
from tests.services.test_tenant_operations import tenants as tenants  # noqa: PLC0414


@pytest.mark.asyncio
async def test_cash_report_opening(db_session, finance):
    context, _, shop, _, units, box = finance
    await LedgerService.opening(
        db_session, context, shop.id, "report-opening", opening(box, units["USD"])
    )
    report = await ReportService.cash(db_session, context, shop.id)
    assert len(report.balances) == 1
    assert report.balances[0].amount == "100.00"
    assert report.balances[0].book_afn == "7000.00"
    filtered = await ReportService.cash(db_session, context, shop.id, cashbox_id=-1)
    assert filtered.balances == []


@pytest.mark.asyncio
async def test_profit_cash_cutoff_and_exports(db_session, finance):
    from datetime import UTC, datetime
    from decimal import Decimal
    from uuid import UUID

    from app.services.exchange import ExchangeService
    from tests.services.test_exchange import seed, trade

    ca, _, shop, _, units, box = finance
    await seed(db_session, ca, shop, units, box)
    await ExchangeService.create(
        db_session, ca, shop.id, "buy-report", trade(units, box)
    )
    sold = await ExchangeService.create(
        db_session,
        ca,
        shop.id,
        "sell-report",
        trade(units, box, side="sell", amount="10", rate="71"),
    )
    cutoff = datetime.now(UTC)
    profit = await ReportService.report(
        db_session, ca, shop.id, "profit", {"cutoff": cutoff}
    )
    assert Decimal(profit.totals["net_profit_afn"]) == 10
    assert all(row.kind in ("income", "expense") for row in profit.rows)
    cash = await ReportService.report(
        db_session, ca, shop.id, "cash", {"cutoff": cutoff}
    )
    assert sum(Decimal(row.amount) for row in cash.rows if row.currency == "USD") == 190
    export = ReportService.csv(profit)
    assert "shop_name" in export and "conversion_rate" in export and "AFN" in export
    from app.schemas.exchange.request import ExchangeReverseRequest

    await ExchangeService.reverse(
        db_session,
        ca,
        shop.id,
        UUID(sold["uuid"]),
        "reverse-report",
        ExchangeReverseRequest(reason="Correct", owner_approved=True),
    )
    historical = await ReportService.report(
        db_session, ca, shop.id, "profit", {"cutoff": cutoff}
    )
    assert historical.totals == profit.totals
    current = await ReportService.report(db_session, ca, shop.id, "profit", {})
    assert Decimal(current.totals["net_profit_afn"]) == 0


@pytest.mark.asyncio
async def test_reports_scope_and_naive_cutoff(db_session, finance):
    from datetime import datetime

    from app.core.exceptions import AppValidationError, NotFoundError

    ca, cb, shop, _, _, _ = finance
    with pytest.raises(NotFoundError):
        await ReportService.report(db_session, cb, shop.id, "daily", {})
    with pytest.raises(AppValidationError):
        await ReportService.report(
            db_session,
            ca,
            shop.id,
            "daily",
            {"cutoff": datetime(2026, 1, 1)},  # noqa: DTZ001
        )


@pytest.mark.asyncio
async def test_hawala_fees_pending_and_agent_balances(db_session, finance):
    from decimal import Decimal

    from app.schemas.contact.request import ContactCreateRequest
    from app.services.contact import ContactService
    from app.services.hawala import HawalaService
    from tests.services.test_exchange import seed
    from tests.services.test_hawala import confirm, draft

    ca, _, shop, _, units, box = finance
    await seed(db_session, ca, shop, units, box)
    agent = await ContactService.create(
        db_session,
        ca,
        ContactCreateRequest(
            shop_uuid=shop.uuid, name="Report agent", is_agent=True, is_customer=False
        ),
    )
    transfer = await HawalaService.create(
        db_session,
        ca,
        shop.id,
        draft(
            units,
            box,
            agent,
            customer_fee_earned_at="confirm",
            agent_fee_earned_at="confirm",
        ),
    )
    await confirm(db_session, ca, shop, transfer)
    report = await ReportService.report(db_session, ca, shop.id, "dashboard", {})
    assert report.totals["pending_hawala_count"] == "1"
    assert report.hawalas[0].status == "confirmed"
    assert Decimal(report.totals["net_profit_afn"]) == 80
    assert Decimal(report.totals["hawala_customer_fee:afn"]) == 100
    assert Decimal(report.totals["hawala_agent_fee:afn"]) == -20
    outstanding = await ReportService.report(db_session, ca, shop.id, "outstanding", {})
    assert any(
        row.role == "agent" and row.contact_id == agent.id for row in outstanding.rows
    )
