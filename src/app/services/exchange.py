"""Cash exchange workflows. Callers own commits; all financial effects are atomic."""

from datetime import UTC, date
from decimal import Decimal, localcontext
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppValidationError, ConflictError, NotFoundError
from app.models import User
from app.models.contact import Contact
from app.models.exchange import (
    Exchange,
    ExchangePaymentLink,
    ExchangeRate,
    ExchangeReversal,
)
from app.models.finance import (
    Cashbox,
    Currency,
    JournalEntry,
    JournalLine,
    JournalPosting,
    LedgerAccount,
    ShopSettings,
)
from app.schemas.exchange.data import ExchangeData, QuoteData, RateData, ReceiptData
from app.schemas.exchange.request import (
    ExchangeCreateRequest,
    ExchangeQuoteRequest,
    ExchangeReverseRequest,
    RateCreateRequest,
)
from app.schemas.finance.data import SettingsData
from app.services.finance import FinanceService, LedgerService
from app.services.financial_values import day_cutoffs, decimal_text, exact
from app.services.organization_context import OrganizationContext
from app.services.tenant_operations import TenantOperations


class ExchangeService:
    """Explicit quote-per-base rates and shop-perspective buy/sell cash postings."""

    @staticmethod
    async def pair(db, context, base_id, quote_id):
        """Validate two distinct active organization currencies."""
        if base_id == quote_id:
            raise AppValidationError()
        return (
            await context.reference(db, Currency, base_id),
            await context.reference(db, Currency, quote_id),
        )

    @staticmethod
    async def rate_data(db, context, rate):
        """Resolve historical currency codes without requiring active currencies."""
        base = await context.get(db, Currency, rate.base_currency_id)
        quote = await context.get(db, Currency, rate.quote_currency_id)
        return RateData.model_validate(
            {
                **rate.to_dict(),
                "created_at": rate.created_at.replace(tzinfo=UTC)
                if rate.created_at.tzinfo is None
                else rate.created_at,
                "base_currency": base.code,
                "quote_currency": quote.code,
            }
        )

    @staticmethod
    async def create_rate(
        db: AsyncSession,
        context: OrganizationContext,
        shop_id: int,
        data: RateCreateRequest,
    ):
        """Append an attributed rate; settings editors alone can set shop rates."""
        await TenantOperations.authorize(db, context, "settings:update")
        await LedgerService.lock(db, context)
        await context.shop(db, shop_id)
        await ExchangeService.pair(
            db, context, data.base_currency_id, data.quote_currency_id
        )
        buying, selling = (
            decimal_text(data.buying_rate, 8),
            decimal_text(data.selling_rate, 8),
        )
        if buying <= 0 or selling <= 0:
            raise AppValidationError()
        rate = ExchangeRate(
            **context.audit_values(),
            shop_id=shop_id,
            base_currency_id=data.base_currency_id,
            quote_currency_id=data.quote_currency_id,
            buying_rate=exact(buying, 8),
            selling_rate=exact(selling, 8),
        )
        db.add(rate)
        await db.flush()
        await TenantOperations.audit(
            db, context, "rate:create", "exchange_rate", rate.id
        )
        return await ExchangeService.rate_data(db, context, rate)

    @staticmethod
    def dates(query, model, date_from, date_to):
        """Apply inclusive Kabul business dates with UTC half-open boundaries."""
        if date_from and date_to and date_from > date_to:
            raise AppValidationError()
        if date_from:
            query = query.where(model.created_at >= day_cutoffs(date_from)[0])
        if date_to:
            query = query.where(model.created_at < day_cutoffs(date_to)[1])
        return query

    @staticmethod
    async def rates(
        db,
        context,
        shop_id,
        offset=0,
        limit=50,
        date_from: date | None = None,
        date_to: date | None = None,
        base_currency_id=None,
        quote_currency_id=None,
    ):
        """Return append-only rate history, newest first, with actor and direction."""
        await TenantOperations.authorize(db, context, "exchange:read")
        await context.shop(db, shop_id)
        FinanceService.page(offset, limit)
        query = context.query(ExchangeRate).where(ExchangeRate.shop_id == shop_id)
        for field, value in (
            (ExchangeRate.base_currency_id, base_currency_id),
            (ExchangeRate.quote_currency_id, quote_currency_id),
        ):
            if value is not None:
                await context.get(db, Currency, value)
                query = query.where(field == value)
        query = ExchangeService.dates(query, ExchangeRate, date_from, date_to)
        return [
            await ExchangeService.rate_data(db, context, row)
            for row in await db.scalars(
                query.order_by(ExchangeRate.id.desc()).offset(offset).limit(limit)
            )
        ]

    @staticmethod
    async def quote(
        db: AsyncSession,
        context: OrganizationContext,
        shop_id: int,
        data: ExchangeQuoteRequest,
    ) -> QuoteData:
        """Calculate a nonbinding quote without creating accounts or posting money."""
        await TenantOperations.authorize(db, context, "exchange:create")
        await context.shop(db, shop_id)
        await context.reference(db, Cashbox, data.cashbox_id, shop_id=shop_id)
        if data.contact_id is not None:
            contact = await context.reference(
                db, Contact, data.contact_id, shop_id=shop_id
            )
            if not contact.is_customer:
                raise AppValidationError()
        base, quote = await ExchangeService.pair(
            db, context, data.base_currency_id, data.quote_currency_id
        )
        amount, rate = (
            decimal_text(data.amount, base.precision),
            decimal_text(data.rate, 8),
        )
        if amount <= 0 or rate <= 0:
            raise AppValidationError()
        with localcontext() as ctx:
            ctx.prec = 60
            raw = amount * rate
            quoted = decimal_text(exact(raw, quote.precision), quote.precision)
            if quoted <= 0:
                raise AppValidationError()
            incoming, outgoing = (base, quote) if data.side == "buy" else (quote, base)
            received, paid = (
                (amount, quoted) if data.side == "buy" else (quoted, amount)
            )
            cash_in = (
                received
                if data.incoming_cash_amount is None
                else decimal_text(data.incoming_cash_amount, incoming.precision)
            )
            cash_out = (
                paid
                if data.outgoing_cash_amount is None
                else decimal_text(data.outgoing_cash_amount, outgoing.precision)
            )
            if cash_in > received or cash_out > paid:
                raise AppValidationError()
            if (cash_in < received or cash_out < paid) and data.contact_id is None:
                raise AppValidationError()
            account = await db.scalar(
                context.query(LedgerAccount).where(
                    LedgerAccount.cashbox_id == data.cashbox_id,
                    LedgerAccount.currency_id == outgoing.id,
                )
            )
            available, book = (
                await LedgerService.balance(db, context, account.id)
                if account
                else (Decimal(0), Decimal(0))
            )
            if cash_out > available:
                raise ConflictError()
            cash_cost = (
                Decimal(0)
                if not cash_out
                else (
                    book
                    if cash_out == available
                    else Decimal(exact(book * cash_out / available, 2))
                )
            )
            unpaid_cost = Decimal(0)
            outgoing_valuation = None
            if cash_out < paid:
                if outgoing.code == "AFN":
                    unpaid_cost = paid - cash_out
                else:
                    outgoing_valuation = decimal_text(data.outgoing_afn_rate, 8)
                    if outgoing_valuation <= 0:
                        raise AppValidationError()
                    unpaid_cost = Decimal(
                        exact((paid - cash_out) * outgoing_valuation, 2)
                    )
            if outgoing_valuation is None and data.outgoing_afn_rate is not None:
                raise AppValidationError()
            payout_cost = cash_cost + unpaid_cost
            valuation = None
            if incoming.code == "AFN":
                incoming_cost = received
            elif outgoing.code == "AFN":
                incoming_cost = paid
            else:
                valuation = decimal_text(data.incoming_afn_rate, 8)
                if valuation <= 0:
                    raise AppValidationError()
                incoming_cost = Decimal(exact(received * valuation, 2))
            if valuation is None and data.incoming_afn_rate is not None:
                raise AppValidationError()
            if incoming_cost <= 0:
                raise AppValidationError()
            # Validate calculated values against the same persisted money bounds.
            decimal_text(exact(incoming_cost, 2), 2)
            decimal_text(exact(payout_cost, 2), 2)
            return QuoteData(
                side=data.side,
                base_currency=base.code,
                quote_currency=quote.code,
                amount=exact(amount, base.precision),
                rate=exact(rate, 8),
                incoming_currency=incoming.code,
                outgoing_currency=outgoing.code,
                incoming_amount=exact(received, incoming.precision),
                outgoing_amount=exact(paid, outgoing.precision),
                unrounded_quote_amount=format(raw, "f"),
                rounding_adjustment=format(quoted - raw, "f"),
                incoming_afn_rate=exact(valuation, 8)
                if valuation is not None
                else None,
                incoming_book_afn=exact(incoming_cost, 2),
                outgoing_book_afn=exact(payout_cost, 2),
                profit_afn=exact(incoming_cost - payout_cost, 2),
                incoming_cash_amount=exact(cash_in, incoming.precision),
                outgoing_cash_amount=exact(cash_out, outgoing.precision),
                incoming_unpaid_amount=exact(received - cash_in, incoming.precision),
                outgoing_unpaid_amount=exact(paid - cash_out, outgoing.precision),
                incoming_cash_book_afn=exact(
                    incoming_cost
                    if cash_in == received
                    else incoming_cost * cash_in / received,
                    2,
                ),
                outgoing_cash_book_afn=exact(cash_cost, 2),
                outgoing_afn_rate=exact(outgoing_valuation, 8)
                if outgoing_valuation is not None
                else None,
            )

    @staticmethod
    async def create(
        db: AsyncSession,
        context: OrganizationContext,
        shop_id: int,
        retry_key: str,
        data: ExchangeCreateRequest,
    ) -> dict:
        """Recalculate under the tenant lock; post costs, cash, profit and receipt once."""

        async def execute():
            values = await ExchangeService.quote(db, context, shop_id, data)
            incoming_id, outgoing_id = (
                (data.base_currency_id, data.quote_currency_id)
                if data.side == "buy"
                else (data.quote_currency_id, data.base_currency_id)
            )
            lines = []

            async def leg(currency_id, code, kind, amount, book, cashbox_id=None):
                if Decimal(amount) == 0:
                    return
                account = await LedgerService.account(
                    db, context, shop_id, currency_id, code, kind, cashbox_id
                )
                lines.append(
                    {"account_id": account.id, "amount": amount, "book_afn": book}
                )

            await leg(
                incoming_id,
                "cash",
                "cash",
                values.incoming_cash_amount,
                values.incoming_cash_book_afn,
                data.cashbox_id,
            )
            await leg(
                incoming_id,
                "exchange_clearing",
                "clearing",
                "-" + values.incoming_amount,
                "-" + values.incoming_book_afn,
            )
            await leg(
                outgoing_id,
                "cash",
                "cash",
                "-" + (values.outgoing_cash_amount or "0.00"),
                "-" + (values.outgoing_cash_book_afn or "0.00"),
                data.cashbox_id,
            )
            await leg(
                outgoing_id,
                "exchange_clearing",
                "clearing",
                values.outgoing_amount,
                values.outgoing_book_afn,
            )
            # Unpaid portions are contact ledger legs, never physical cash.
            from app.services.contact_ledger import ContactLedgerService

            obligations = []
            for currency_id, direction, unpaid, total_book, cash_book in (
                (
                    incoming_id,
                    "receivable",
                    values.incoming_unpaid_amount,
                    values.incoming_book_afn,
                    values.incoming_cash_book_afn,
                ),
                (
                    outgoing_id,
                    "payable",
                    values.outgoing_unpaid_amount,
                    values.outgoing_book_afn,
                    values.outgoing_cash_book_afn,
                ),
            ):
                if Decimal(unpaid or "0"):
                    if data.contact_id is None:
                        raise AppValidationError()
                    customer = await context.reference(
                        db, Contact, data.contact_id, shop_id=shop_id
                    )
                    account_id = await ContactLedgerService.account(
                        db, context, customer, "customer", direction, currency_id
                    )
                    with localcontext() as ctx:
                        ctx.prec = 60
                        cost = Decimal(total_book) - Decimal(cash_book or "0")
                    sign = 1 if direction == "receivable" else -1
                    currency = await context.get(db, Currency, currency_id)
                    lines.append(
                        {
                            "account_id": account_id,
                            "amount": exact(
                                Decimal(unpaid or "0") * sign, currency.precision
                            ),
                            "book_afn": exact(cost * sign, 2),
                        }
                    )
                    obligations.append(
                        (account_id, currency_id, direction, unpaid, exact(cost, 2))
                    )
            profit = Decimal(values.profit_afn)
            if profit:
                afn = await db.scalar(
                    context.query(Currency).where(Currency.code == "AFN")
                )
                if afn is None:
                    raise AppValidationError()
                await context.reference(db, Currency, afn.id)
                await leg(
                    afn.id,
                    "exchange_clearing",
                    "clearing",
                    exact(profit, 2),
                    exact(profit, 2),
                )
                await leg(
                    afn.id,
                    "exchange_profit" if profit > 0 else "exchange_loss",
                    "income" if profit > 0 else "expense",
                    exact(-profit, 2),
                    exact(-profit, 2),
                )
            entry = await LedgerService._write(
                db, context, shop_id, "exchange:create", data.reason, lines
            )
            shop = await context.shop(db, shop_id)
            settings = await db.scalar(
                context.query(ShopSettings).where(ShopSettings.shop_id == shop_id)
            )
            cashier = (
                await db.execute(select(User).where(User.id == context.user_id))
            ).scalar_one()
            customer = (
                await context.get(db, Contact, data.contact_id)
                if data.contact_id
                else None
            )
            reference = "EX-" + uuid4().hex.upper()
            snapshot = ReceiptData(
                reference=reference,
                created_at=entry["created_at"],
                shop=SettingsData(
                    shop_id=shop_id,
                    name=shop.name,
                    address=shop.address,
                    phone=settings.phone if settings else None,
                    logo_reference=settings.logo_reference if settings else None,
                    preferred_language=settings.preferred_language
                    if settings
                    else "fa",
                    receipt_footer=settings.receipt_footer if settings else None,
                ),
                cashier_id=context.user_id,
                cashier_name=cashier.user_name,
                customer_id=data.contact_id,
                customer_name=customer.name if customer else None,
                cashbox_id=data.cashbox_id,
                values=values,
            )
            exchange = Exchange(
                **context.audit_values(),
                shop_id=shop_id,
                contact_id=data.contact_id,
                cashbox_id=data.cashbox_id,
                base_currency_id=data.base_currency_id,
                quote_currency_id=data.quote_currency_id,
                entry_id=entry["id"],
                reference=reference,
                details=snapshot.model_dump(mode="json"),
            )
            db.add(exchange)
            await db.flush()
            from app.services.obligation import ObligationService

            for account_id, currency_id, direction, unpaid, cost in obligations:
                await ObligationService.record(
                    db,
                    context,
                    shop_id,
                    data.contact_id,
                    entry["id"],
                    account_id,
                    currency_id,
                    direction,
                    unpaid,
                    cost,
                    exchange_id=exchange.id,
                )
            return (
                await ExchangeService.exchange_data(db, context, exchange)
            ).model_dump(mode="json")

        # Preserve hashes of Phase 8 retries when the new credit fields are omitted.
        payload = data.model_dump()
        for field in (
            "incoming_cash_amount",
            "outgoing_cash_amount",
            "outgoing_afn_rate",
        ):
            if payload[field] is None:
                payload.pop(field)
        return await TenantOperations.execute_once(
            db,
            context,
            "exchange:create",
            retry_key,
            {"shop_id": shop_id, **payload},
            "exchange:create",
            execute,
        )

    @staticmethod
    async def exchange_data(db, context, exchange):
        """Expose original snapshot and separately derived reversal status."""
        reversal = await db.scalar(
            context.query(ExchangeReversal).where(
                ExchangeReversal.exchange_id == exchange.id
            )
        )
        from app.models.payment import CustomerObligation
        from app.services.obligation import ObligationService

        obligations = [
            await ObligationService.data(db, context, row)
            for row in await db.scalars(
                context.query(CustomerObligation)
                .where(CustomerObligation.exchange_id == exchange.id)
                .order_by(CustomerObligation.id)
            )
        ]
        return ExchangeData(
            obligations=obligations,
            id=exchange.id,
            uuid=exchange.uuid,
            shop_id=exchange.shop_id,
            entry_id=exchange.entry_id,
            reference=exchange.reference,
            receipt=ReceiptData.model_validate(exchange.details),
            reversal_entry_id=reversal.entry_id if reversal else None,
        )

    @staticmethod
    async def get(db, context, shop_id, exchange_id: int | UUID):
        """Read only exchanges inside the authorized shop and organization."""
        await TenantOperations.authorize(db, context, "exchange:read")
        await context.shop(db, shop_id)
        exchange = await context.get(db, Exchange, exchange_id)
        if exchange.shop_id != shop_id:
            raise NotFoundError()
        return await ExchangeService.exchange_data(db, context, exchange)

    @staticmethod
    async def list(
        db,
        context,
        shop_id,
        offset=0,
        limit=50,
        date_from=None,
        date_to=None,
        reference=None,
        cashier_id=None,
        contact_id=None,
        currency_id=None,
    ):
        """Search receipts by reference, dates, actor, customer and either currency."""
        await TenantOperations.authorize(db, context, "exchange:read")
        await context.shop(db, shop_id)
        FinanceService.page(offset, limit)
        query = context.query(Exchange).where(Exchange.shop_id == shop_id)
        query = ExchangeService.dates(query, Exchange, date_from, date_to)
        if reference:
            query = query.where(Exchange.reference.contains(reference, autoescape=True))
        if cashier_id is not None:
            query = query.where(Exchange.user_id == cashier_id)
        if contact_id is not None:
            contact = await context.get(db, Contact, contact_id)
            if contact.shop_id != shop_id:
                raise NotFoundError()
            query = query.where(Exchange.contact_id == contact_id)
        if currency_id is not None:
            await context.get(db, Currency, currency_id)
            query = query.where(
                or_(
                    Exchange.base_currency_id == currency_id,
                    Exchange.quote_currency_id == currency_id,
                )
            )
        return [
            await ExchangeService.exchange_data(db, context, row)
            for row in await db.scalars(
                query.order_by(Exchange.id.desc()).offset(offset).limit(limit)
            )
        ]

    @staticmethod
    async def reverse(
        db: AsyncSession,
        context: OrganizationContext,
        shop_id: int,
        exchange_id: UUID,
        retry_key: str,
        data: ExchangeReverseRequest,
    ):
        """Owner approval plus reverse permission; negate exact original legs once."""

        async def execute():
            await LedgerService.owner(db, context)
            if data.owner_approved is not True:
                raise AppValidationError()
            await context.shop(db, shop_id)
            exchange = await context.get(db, Exchange, exchange_id)
            if exchange.shop_id != shop_id:
                raise NotFoundError()
            if await db.scalar(
                context.query(ExchangeReversal).where(
                    ExchangeReversal.exchange_id == exchange.id
                )
            ):
                raise ConflictError()
            # Payment postings register every allocated exchange here.
            for link in await db.scalars(
                context.query(ExchangePaymentLink).where(
                    ExchangePaymentLink.exchange_id == exchange.id
                )
            ):
                if not await db.scalar(
                    context.query(JournalEntry, JournalPosting)
                    .join(JournalPosting, JournalPosting.entry_id == JournalEntry.id)
                    .where(
                        JournalEntry.reversal_of_id == link.entry_id,
                        JournalEntry.shop_id == shop_id,
                        JournalPosting.shop_id == shop_id,
                    )
                ):
                    raise ConflictError()
            lines = list(
                await db.scalars(
                    context.query(JournalLine).where(
                        JournalLine.entry_id == exchange.entry_id
                    )
                )
            )
            with localcontext() as ctx:
                ctx.prec = 60
                opposite = [
                    {
                        "account_id": line.account_id,
                        "amount": format(-Decimal(line.amount), "f"),
                        "book_afn": format(-Decimal(line.book_afn), "f"),
                    }
                    for line in lines
                ]
            entry = await LedgerService._write(
                db,
                context,
                shop_id,
                "exchange:reverse",
                data.reason,
                opposite,
                reversal_of_id=exchange.entry_id,
            )
            db.add(
                context.new(
                    ExchangeReversal,
                    shop_id=shop_id,
                    exchange_id=exchange.id,
                    entry_id=entry["id"],
                )
            )
            await db.flush()
            return entry

        return await TenantOperations.execute_once(
            db,
            context,
            "exchange:reverse",
            retry_key,
            {"shop_id": shop_id, "exchange_id": str(exchange_id), **data.model_dump()},
            "exchange:reverse",
            execute,
        )
