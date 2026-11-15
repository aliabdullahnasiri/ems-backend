"""Exact, posted-ledger cash reporting."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select

from app.models.finance import JournalEntry, JournalLine, JournalPosting, LedgerAccount
from app.schemas.reports.response import CashBalanceData, CashReportData
from app.services.tenant_operations import TenantOperations


class ReportService:
    """Read financial reports within the authorized shop."""

    @staticmethod
    async def cash(
        db, context, shop_id, cutoff=None, currency_id=None, cashbox_id=None
    ):
        """Sum posted cash in Decimal, retaining historical AFN book values."""
        await TenantOperations.authorize(db, context, "report:read")
        await context.shop(db, shop_id)
        cutoff = cutoff or datetime.now(UTC)
        statement = (
            select(JournalLine, LedgerAccount)
            .join(LedgerAccount, JournalLine.account_id == LedgerAccount.id)
            .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
            .join(JournalPosting, JournalPosting.entry_id == JournalEntry.id)
            .where(
                JournalLine.organization_id == context.organization_id,
                JournalLine.shop_id == shop_id,
                LedgerAccount.kind == "cash",
                JournalPosting.created_at <= cutoff,
            )
        )
        if currency_id is not None:
            statement = statement.where(LedgerAccount.currency_id == currency_id)
        if cashbox_id is not None:
            statement = statement.where(LedgerAccount.cashbox_id == cashbox_id)
        totals = {}
        for line, account in (await db.execute(statement)).all():
            key = (account.cashbox_id, account.currency_id)
            amount, book = totals.get(key, (Decimal(0), Decimal(0)))
            totals[key] = (amount + Decimal(line.amount), book + Decimal(line.book_afn))
        return CashReportData(
            cutoff=cutoff,
            balances=[
                CashBalanceData(
                    cashbox_id=box,
                    currency_id=currency,
                    amount=str(amount),
                    book_afn=str(book),
                )
                for (box, currency), (amount, book) in sorted(totals.items())
            ],
        )

    @staticmethod
    async def report(db, context, shop_id, kind, filters):
        """Produce reproducible posted-ledger detail and exact currency totals."""
        from decimal import localcontext

        from app.core.exceptions import AppValidationError
        from app.models.contact import ContactAccount
        from app.models.exchange import Exchange
        from app.models.finance import Currency
        from app.schemas.reports.response import FinancialReportData, ReportRowData

        await TenantOperations.authorize(db, context, "report:read")
        if kind == "audit":
            await TenantOperations.authorize(db, context, "audit:read")
        shop = await context.shop(db, shop_id)
        cutoff = filters.get("cutoff") or datetime.now(UTC)
        if filters.get("closing_id") is not None:
            from app.models.daily import DayClosing

            closing = await context.reference(
                db, DayClosing, filters["closing_id"], shop_id=shop_id
            )
            cutoff = (
                closing.cutoff.replace(tzinfo=UTC)
                if closing.cutoff.tzinfo is None
                else closing.cutoff
            )
        start = filters.get("start")
        if cutoff.tzinfo is None or (
            start and (start.tzinfo is None or start > cutoff)
        ):
            raise AppValidationError()
        query = (
            select(
                JournalLine,
                LedgerAccount,
                JournalEntry,
                JournalPosting,
                Currency,
                ContactAccount,
            )
            .join(LedgerAccount, JournalLine.account_id == LedgerAccount.id)
            .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
            .join(JournalPosting, JournalPosting.entry_id == JournalEntry.id)
            .join(Currency, LedgerAccount.currency_id == Currency.id)
            .outerjoin(ContactAccount, ContactAccount.account_id == LedgerAccount.id)
            .outerjoin(Exchange, Exchange.entry_id == JournalEntry.id)
            .where(
                JournalLine.organization_id == context.organization_id,
                JournalLine.shop_id == shop_id,
                JournalPosting.created_at <= cutoff,
            )
            .order_by(JournalPosting.id, JournalLine.id)
        )
        # Outstanding is a balance as of cutoff; start applies only to activity.
        if start and kind not in ("outstanding", "dashboard"):
            query = query.where(JournalPosting.created_at >= start)
        for name, column in (
            ("currency_id", LedgerAccount.currency_id),
            ("cashbox_id", LedgerAccount.cashbox_id),
            ("cashier_id", JournalEntry.user_id),
            (
                "contact_id",
                func.coalesce(ContactAccount.contact_id, Exchange.contact_id),
            ),
        ):
            if filters.get(name) is not None:
                query = query.where(column == filters[name])
        if kind == "cash":
            query = query.where(LedgerAccount.kind == "cash")
        elif kind == "profit":
            query = query.where(LedgerAccount.kind.in_(("income", "expense")))
        elif kind == "outstanding":
            query = query.where(LedgerAccount.kind.in_(("receivable", "payable")))
        elif kind == "hawala":
            query = query.where(JournalEntry.action.like("%hawala%"))
        rows, totals = [], {"net_profit_afn": Decimal(0)}
        with localcontext() as precision:
            precision.prec = 60
            for line, account, entry, posting, currency, contact in (
                await db.execute(query)
            ).all():
                amount, book = Decimal(line.amount), Decimal(line.book_afn)
                rows.append(
                    ReportRowData(
                        entry_id=entry.id,
                        posted_at=posting.created_at,
                        user_id=entry.user_id,
                        action=entry.action,
                        account_id=account.id,
                        account_code=account.code,
                        kind=account.kind,
                        currency_id=currency.id,
                        currency=currency.code,
                        cashbox_id=account.cashbox_id,
                        contact_id=contact.contact_id if contact else None,
                        role=contact.role if contact else None,
                        direction=contact.direction if contact else None,
                        amount=line.amount,
                        book_afn=line.book_afn,
                        conversion_rate=format(book / amount, "f") if amount else None,
                    )
                )
                in_period = (
                    start is None
                    or (
                        posting.created_at.replace(tzinfo=UTC)
                        if posting.created_at.tzinfo is None
                        else posting.created_at
                    )
                    >= start
                )
                if (
                    in_period
                    and entry.action == "exchange:create"
                    and account.kind == "cash"
                ):
                    volume_key = f"exchange_{'received' if amount > 0 else 'paid'}:{currency.code}"
                    totals[volume_key] = totals.get(volume_key, Decimal(0)) + abs(
                        amount
                    )
                key = f"{account.kind}:{contact.role if contact else 'shop'}:{currency.code}"
                sign = -1 if account.kind in ("income", "payable") else 1
                if account.kind not in ("income", "expense") or in_period:
                    totals[key] = totals.get(key, Decimal(0)) + sign * amount
                if account.kind == "cash":
                    balance_key = f"cashbox:{account.cashbox_id}:{currency.code}"
                    totals[balance_key] = totals.get(balance_key, Decimal(0)) + amount
                if contact:
                    balance_key = f"{contact.role}:{contact.contact_id}:{contact.direction}:{currency.code}"
                    totals[balance_key] = (
                        totals.get(balance_key, Decimal(0)) + sign * amount
                    )
                if account.kind in ("income", "expense") and in_period:
                    totals["net_profit_afn"] = (
                        totals.get("net_profit_afn", Decimal(0)) - book
                    )
                    bucket = f"{account.code}:afn"
                    totals[bucket] = totals.get(bucket, Decimal(0)) - book
        hawalas = []
        if kind in ("hawala", "dashboard"):
            from app.models.hawala import Hawala, HawalaEvent, HawalaRefund
            from app.schemas.reports.response import HawalaStatusData

            transfers = await db.scalars(
                context.query(Hawala).where(
                    Hawala.shop_id == shop_id, Hawala.created_at <= cutoff
                )
            )
            for transfer in transfers:
                if (
                    filters.get("contact_id") is not None
                    and transfer.agent_id != filters["contact_id"]
                ):
                    continue
                if filters.get("currency_id") is not None and filters[
                    "currency_id"
                ] not in (transfer.collection_currency_id, transfer.payout_currency_id):
                    continue
                events = list(
                    await db.scalars(
                        context.query(HawalaEvent)
                        .where(
                            HawalaEvent.hawala_id == transfer.id,
                            HawalaEvent.created_at <= cutoff,
                        )
                        .order_by(HawalaEvent.id)
                    )
                )
                status = "draft"
                remaining, unit = Decimal(0), None
                for event in events:
                    if event.kind in ("confirmed", "paid", "cancelled"):
                        status = event.kind
                    if event.kind == "cancelled":
                        remaining = Decimal(event.details.get("refund_due", "0"))
                        unit = event.details.get("refund_currency")
                refunds = await db.scalars(
                    context.query(HawalaRefund).where(
                        HawalaRefund.hawala_id == transfer.id,
                        HawalaRefund.created_at <= cutoff,
                    )
                )
                remaining -= sum(
                    (Decimal(refund.amount) for refund in refunds), Decimal(0)
                )
                hawalas.append(
                    HawalaStatusData(
                        hawala_id=transfer.id,
                        status=status,
                        refund_currency=unit,
                        refund_remaining=str(remaining),
                    )
                )
                if unit:
                    key = f"refund_due:{unit}"
                    totals[key] = totals.get(key, Decimal(0)) + remaining
                if status == "confirmed":
                    totals["pending_hawala_count"] = (
                        totals.get("pending_hawala_count", Decimal(0)) + 1
                    )
        audits = []
        if kind == "audit":
            from app.models.tenant_operations import AuditEvent
            from app.schemas.reports.response import AuditReportData

            # Journal audit events have an explicit shop through their immutable entry.
            audit_query = (
                context.query(AuditEvent)
                .join(
                    JournalEntry,
                    (AuditEvent.record_id == JournalEntry.id)
                    & (AuditEvent.record_type == "journal_entry"),
                )
                .where(JournalEntry.shop_id == shop_id, AuditEvent.created_at <= cutoff)
            )
            if start:
                audit_query = audit_query.where(AuditEvent.created_at >= start)
            if filters.get("cashier_id") is not None:
                audit_query = audit_query.where(
                    AuditEvent.user_id == filters["cashier_id"]
                )
            audits = [
                AuditReportData.model_validate(row)
                for row in await db.scalars(audit_query.order_by(AuditEvent.id))
            ]
        return FinancialReportData(
            hawalas=hawalas,
            audit_events=audits,
            shop_name=shop.name,
            generated_at=datetime.now(UTC),
            cutoff=cutoff,
            start=start,
            filters={
                k: str(v) if isinstance(v, datetime) else v for k, v in filters.items()
            },
            rows=rows,
            totals={k: format(v, "f") for k, v in totals.items()},
        )

    @staticmethod
    def csv(report):
        """Export metadata and original currency detail with safe spreadsheet cells."""
        import csv
        import io

        stream = io.StringIO()
        writer = csv.writer(stream)

        def safe(value):
            value = str(value) if value is not None else ""
            return (
                "'" + value
                if value.startswith(("=", "+", "-", "@", "\t", "\r"))
                else value
            )

        for key in (
            "shop_name",
            "generated_at",
            "cutoff",
            "valuation_basis",
            "filters",
            "totals",
        ):
            writer.writerow([key, safe(getattr(report, key))])
        from app.schemas.reports.response import ReportRowData

        writer.writerow(ReportRowData.model_fields)
        for row in report.rows:
            writer.writerow([safe(value) for value in row.model_dump().values()])
        for section in ("hawalas", "audit_events"):
            writer.writerow([section])
            for item in getattr(report, section):
                writer.writerow([safe(value) for value in item.model_dump().values()])
        return stream.getvalue()
