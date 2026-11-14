"""Customer cash settlement and allocation with exact, reversible journal effects."""

from decimal import Decimal, localcontext
from uuid import uuid4

from sqlalchemy import select

from app.core.exceptions import AppValidationError, ConflictError, NotFoundError
from app.models import User
from app.models.contact import Contact
from app.models.exchange import Exchange, ExchangePaymentLink, ExchangeReversal
from app.models.finance import Cashbox, Currency, JournalLine, ShopSettings
from app.models.payment import (
    CustomerObligation,
    Payment,
    PaymentAllocation,
    PaymentReversal,
)
from app.schemas.exchange.data import ReceiptData
from app.schemas.finance.data import SettingsData
from app.schemas.payment.data import (
    AllocationData,
    PaymentData,
    PaymentReceiptData,
    PaymentResultData,
)
from app.schemas.payment.request import PaymentCreateRequest, PaymentReverseRequest
from app.services.contact_ledger import ContactLedgerService
from app.services.finance import FinanceService, LedgerService
from app.services.financial_values import decimal_text, exact
from app.services.obligation import ObligationService
from app.services.tenant_operations import TenantOperations


class PaymentService:
    """Serialize payments against both cash and obligation balances, with retained history."""

    @staticmethod
    async def allocation(
        db, context, shop_id, contact_id, obligation_id, amount, direction
    ):
        """Resolve one active obligation and release a proportional remaining book value."""
        obligation = await context.get(db, CustomerObligation, obligation_id)
        if obligation.shop_id != shop_id or obligation.contact_id != contact_id:
            raise NotFoundError()
        if obligation.direction != direction:
            raise AppValidationError()
        currency = await context.reference(db, Currency, obligation.currency_id)
        amount = decimal_text(amount, currency.precision)
        state = await ObligationService.data(db, context, obligation)
        remaining = Decimal(state.remaining_amount)
        if amount <= 0:
            raise AppValidationError()
        if state.is_reversed or amount > remaining:
            raise ConflictError()
        with localcontext() as ctx:
            ctx.prec = 60
            book = (
                Decimal(state.remaining_book_afn)
                if amount == remaining
                else Decimal(
                    exact(Decimal(state.remaining_book_afn) * amount / remaining, 2)
                )
            )
        return obligation, currency, amount, book

    @staticmethod
    async def conversion(db, context, shop_id, data, amount, prepared):
        """Use both unpaid legs of an explicitly linked exchange as a conversion bridge.

        Cash settles the bridge's payment-currency leg. Its other leg is netted
        against the target obligations. All three allocations share one journal.
        """
        exchange = await context.get(db, Exchange, data.settlement_exchange_id)
        if exchange.shop_id != shop_id or exchange.contact_id != data.contact_id:
            raise NotFoundError()
        if await db.scalar(
            context.query(ExchangeReversal).where(
                ExchangeReversal.exchange_id == exchange.id
            )
        ):
            raise ConflictError()
        receipt = ReceiptData.model_validate(exchange.details)
        if (
            Decimal(
                receipt.values.incoming_cash_amount or receipt.values.incoming_amount
            )
            != 0
            or Decimal(
                receipt.values.outgoing_cash_amount or receipt.values.outgoing_amount
            )
            != 0
        ):
            raise AppValidationError()
        bridge = list(
            await db.scalars(
                context.query(CustomerObligation).where(
                    CustomerObligation.exchange_id == exchange.id
                )
            )
        )
        direction = "receivable" if data.direction == "receive" else "payable"
        other_direction = "payable" if direction == "receivable" else "receivable"
        cash_leg = next(
            (
                o
                for o in bridge
                if o.currency_id == data.currency_id and o.direction == direction
            ),
            None,
        )
        other_leg = next((o for o in bridge if o.direction == other_direction), None)
        if cash_leg is None or other_leg is None or len(bridge) != 2:
            raise AppValidationError()
        if any(
            o.currency_id != other_leg.currency_id or o.exchange_id == exchange.id
            for o, _, _, _ in prepared
        ):
            raise AppValidationError()
        target_currency = prepared[0][1]
        cash_state = await ObligationService.data(db, context, cash_leg)
        other_state = await ObligationService.data(db, context, other_leg)
        with localcontext() as ctx:
            ctx.prec = 60
            # Cumulative rounding ensures full settlement removes every final unit.
            expected_prior = Decimal(
                exact(
                    Decimal(cash_state.paid_amount)
                    * Decimal(other_leg.amount)
                    / Decimal(cash_leg.amount),
                    target_currency.precision,
                )
            )
            if expected_prior != Decimal(other_state.paid_amount):
                raise ConflictError()
            converted = (
                Decimal(
                    exact(
                        (Decimal(cash_state.paid_amount) + amount)
                        * Decimal(other_leg.amount)
                        / Decimal(cash_leg.amount),
                        target_currency.precision,
                    )
                )
                - expected_prior
            )
            if (
                converted <= 0
                or sum((a for _, _, a, _ in prepared), Decimal(0)) != converted
            ):
                raise AppValidationError()
        prepared.append(
            await PaymentService.allocation(
                db,
                context,
                shop_id,
                data.contact_id,
                cash_leg.id,
                data.amount,
                direction,
            )
        )
        prepared.append(
            await PaymentService.allocation(
                db,
                context,
                shop_id,
                data.contact_id,
                other_leg.id,
                exact(converted, target_currency.precision),
                other_direction,
            )
        )
        return receipt

    @staticmethod
    async def data(db, context, payment):
        """Return immutable receipt separately from derived current reversal status."""
        reversal = await db.scalar(
            context.query(PaymentReversal).where(
                PaymentReversal.payment_id == payment.id
            )
        )
        return PaymentData(
            id=payment.id,
            uuid=payment.uuid,
            shop_id=payment.shop_id,
            entry_id=payment.entry_id,
            reference=payment.reference,
            receipt=PaymentReceiptData.model_validate(payment.receipt),
            reversal_entry_id=reversal.entry_id if reversal else None,
        )

    @staticmethod
    async def result(db, context, payment):
        """Return the payment plus ledger-derived balances and touched obligation states."""
        contact = await context.get(db, Contact, payment.contact_id)
        rows = await db.scalars(
            context.query(CustomerObligation, PaymentAllocation)
            .join(
                PaymentAllocation,
                PaymentAllocation.obligation_id == CustomerObligation.id,
            )
            .where(PaymentAllocation.payment_id == payment.id)
            .order_by(CustomerObligation.id)
        )
        return PaymentResultData(
            payment=await PaymentService.data(db, context, payment),
            balances=await ContactLedgerService.balances(db, context, contact.uuid),
            obligations=[
                await ObligationService.data(db, context, row) for row in rows
            ],
        )

    @staticmethod
    async def create(
        db, context, shop_id: int, retry_key: str, data: PaymentCreateRequest
    ):
        """Post one fully allocated cash receipt/payout, all obligations and retry atomically."""

        async def execute():
            shop = await context.shop(db, shop_id)
            contact = await context.reference(
                db, Contact, data.contact_id, shop_id=shop_id
            )
            if not contact.is_customer:
                raise AppValidationError()
            await context.reference(db, Cashbox, data.cashbox_id, shop_id=shop_id)
            currency = await context.reference(db, Currency, data.currency_id)
            amount = decimal_text(data.amount, currency.precision)
            if amount <= 0 or len({a.obligation_id for a in data.allocations}) != len(
                data.allocations
            ):
                raise AppValidationError()
            direction = "receivable" if data.direction == "receive" else "payable"
            prepared = [
                await PaymentService.allocation(
                    db,
                    context,
                    shop_id,
                    contact.id,
                    item.obligation_id,
                    item.amount,
                    direction,
                )
                for item in data.allocations
            ]
            conversion = None
            if data.settlement_exchange_id is None:
                if any(c.id != currency.id for _, c, _, _ in prepared):
                    raise AppValidationError()
                with localcontext() as ctx:
                    ctx.prec = 60
                    if sum((a for _, _, a, _ in prepared), Decimal(0)) != amount:
                        raise AppValidationError()
            else:
                conversion = await PaymentService.conversion(
                    db, context, shop_id, data, amount, prepared
                )
            cash = await LedgerService.account(
                db, context, shop_id, currency.id, "cash", "cash", data.cashbox_id
            )
            with localcontext() as ctx:
                ctx.prec = 60
                # The cash-currency obligation fixes received acquisition cost.
                received_cost = sum(
                    (b for _, c, _, b in prepared if c.id == currency.id), Decimal(0)
                )
                if data.direction == "pay":
                    available, carrying = await LedgerService.balance(
                        db, context, cash.id
                    )
                    if amount > available:
                        raise ConflictError()
                    cash_cost = (
                        carrying
                        if amount == available
                        else Decimal(exact(carrying * amount / available, 2))
                    )
                else:
                    cash_cost = received_cost
                sign = 1 if data.direction == "receive" else -1
                lines = [
                    {
                        "account_id": cash.id,
                        "amount": exact(amount * sign, currency.precision),
                        "book_afn": exact(cash_cost * sign, 2),
                    }
                ]
                for obligation, unit, allocated, book in prepared:
                    debt_sign = -1 if obligation.direction == "receivable" else 1
                    lines.append(
                        {
                            "account_id": obligation.account_id,
                            "amount": exact(allocated * debt_sign, unit.precision),
                            "book_afn": exact(book * debt_sign, 2),
                        }
                    )
                profit = sum((Decimal(line["book_afn"]) for line in lines), Decimal(0))
                if profit:
                    afn = await db.scalar(
                        context.query(Currency).where(Currency.code == "AFN")
                    )
                    if afn is None:
                        raise AppValidationError()
                    account = await LedgerService.account(
                        db,
                        context,
                        shop_id,
                        afn.id,
                        "payment_profit" if profit > 0 else "payment_loss",
                        "income" if profit > 0 else "expense",
                    )
                    lines.append(
                        {
                            "account_id": account.id,
                            "amount": exact(-profit, 2),
                            "book_afn": exact(-profit, 2),
                        }
                    )
                    # Exchange-rate gains/losses change AFN book value without
                    # inventing physical foreign-currency movements.
                    clearing = await LedgerService.account(
                        db, context, shop_id, afn.id, "payment_fx_clearing", "clearing"
                    )
                    lines.append(
                        {
                            "account_id": clearing.id,
                            "amount": exact(profit, 2),
                            "book_afn": exact(profit, 2),
                        }
                    )
                    foreign = (
                        currency
                        if currency.code != "AFN"
                        else next(
                            (c for _, c, _, _ in prepared if c.code != "AFN"), None
                        )
                    )
                    if foreign is None:
                        raise AppValidationError()
                    book_account = await LedgerService.account(
                        db, context, shop_id, foreign.id, "payment_fx_book", "clearing"
                    )
                    lines.append(
                        {
                            "account_id": book_account.id,
                            "amount": exact(Decimal(0), foreign.precision),
                            "book_afn": exact(-profit, 2),
                        }
                    )
            entry = await LedgerService._write(
                db, context, shop_id, "payment:create", data.reason, lines
            )
            settings = await db.scalar(
                context.query(ShopSettings).where(ShopSettings.shop_id == shop_id)
            )
            cashier = (
                await db.execute(select(User).where(User.id == context.user_id))
            ).scalar_one()
            reference = "PAY-" + uuid4().hex.upper()
            receipt = PaymentReceiptData(
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
                customer_id=contact.id,
                customer_name=contact.name,
                cashbox_id=data.cashbox_id,
                direction=data.direction,
                currency=currency.code,
                amount=exact(amount, currency.precision),
                book_afn=exact(cash_cost, 2),
                profit_afn=exact(profit, 2),
                reason=data.reason,
                settlement_exchange=conversion,
                allocations=[
                    AllocationData(
                        obligation_id=o.id,
                        amount=exact(a, c.precision),
                        currency=c.code,
                        direction=o.direction,
                        book_afn=exact(b, 2),
                    )
                    for o, c, a, b in prepared
                ],
            )
            payment = Payment(
                **context.audit_values(),
                shop_id=shop_id,
                contact_id=contact.id,
                cashbox_id=data.cashbox_id,
                currency_id=currency.id,
                direction=data.direction,
                amount=exact(amount, currency.precision),
                entry_id=entry["id"],
                settlement_exchange_id=data.settlement_exchange_id,
                reference=reference,
                receipt=receipt.model_dump(mode="json"),
            )
            db.add(payment)
            await db.flush()
            linked = set()
            for obligation, unit, allocated, book in prepared:
                db.add(
                    context.new(
                        PaymentAllocation,
                        shop_id=shop_id,
                        payment_id=payment.id,
                        obligation_id=obligation.id,
                        amount=exact(allocated, unit.precision),
                        book_afn=exact(book, 2),
                    )
                )
                if obligation.exchange_id is not None:
                    linked.add(obligation.exchange_id)
            for exchange_id in linked:
                db.add(
                    context.new(
                        ExchangePaymentLink,
                        shop_id=shop_id,
                        exchange_id=exchange_id,
                        entry_id=entry["id"],
                    )
                )
            await db.flush()
            return (await PaymentService.result(db, context, payment)).model_dump(
                mode="json"
            )

        return await TenantOperations.execute_once(
            db,
            context,
            "payment:create",
            retry_key,
            {"shop_id": shop_id, **data.model_dump()},
            "payment:create",
            execute,
        )

    @staticmethod
    async def get(db, context, shop_id, payment_id):
        """Read authorized shop history even if the customer/currency was deactivated."""
        await TenantOperations.authorize(db, context, "payment:read")
        await context.shop(db, shop_id)
        payment = await context.get(db, Payment, payment_id)
        if payment.shop_id != shop_id:
            raise NotFoundError()
        return await PaymentService.data(db, context, payment)

    @staticmethod
    async def list(
        db,
        context,
        shop_id,
        offset=0,
        limit=50,
        date_from=None,
        date_to=None,
        contact_id=None,
        currency_id=None,
        direction=None,
        reference=None,
    ):
        """Return paginated payment history using Kabul business dates."""
        from app.services.exchange import ExchangeService

        await TenantOperations.authorize(db, context, "payment:read")
        await context.shop(db, shop_id)
        FinanceService.page(offset, limit)
        query = context.query(Payment).where(Payment.shop_id == shop_id)
        query = ExchangeService.dates(query, Payment, date_from, date_to)
        if contact_id is not None:
            contact = await context.get(db, Contact, contact_id)
            if contact.shop_id != shop_id:
                raise NotFoundError()
            query = query.where(Payment.contact_id == contact_id)
        if currency_id is not None:
            await context.get(db, Currency, currency_id)
            query = query.where(Payment.currency_id == currency_id)
        if direction is not None:
            if direction not in ("receive", "pay"):
                raise AppValidationError()
            query = query.where(Payment.direction == direction)
        if reference:
            query = query.where(Payment.reference.contains(reference, autoescape=True))
        return [
            await PaymentService.data(db, context, payment)
            for payment in await db.scalars(
                query.order_by(Payment.id.desc()).offset(offset).limit(limit)
            )
        ]

    @staticmethod
    async def reverse(
        db, context, shop_id, payment_id, retry_key, data: PaymentReverseRequest
    ):
        """Owner-approved exact reversal restores allocations without editing history."""

        async def execute():
            await LedgerService.owner(db, context)
            if data.owner_approved is not True:
                raise AppValidationError()
            await context.shop(db, shop_id)
            payment = await context.get(db, Payment, payment_id)
            if payment.shop_id != shop_id:
                raise NotFoundError()
            if await db.scalar(
                context.query(PaymentReversal).where(
                    PaymentReversal.payment_id == payment.id
                )
            ):
                raise ConflictError()
            # Later conversions can depend on prior cumulative rounding. Undo those
            # payments first to keep both bridge legs synchronized.
            if payment.settlement_exchange_id is not None:
                later = await db.scalars(
                    context.query(Payment).where(
                        Payment.settlement_exchange_id
                        == payment.settlement_exchange_id,
                        Payment.id > payment.id,
                    )
                )
                for item in later:
                    if not await db.scalar(
                        context.query(PaymentReversal).where(
                            PaymentReversal.payment_id == item.id
                        )
                    ):
                        raise ConflictError()
            rows = await db.scalars(
                context.query(JournalLine).where(
                    JournalLine.entry_id == payment.entry_id
                )
            )
            with localcontext() as ctx:
                ctx.prec = 60
                lines = [
                    {
                        "account_id": line.account_id,
                        "amount": format(-Decimal(line.amount), "f"),
                        "book_afn": format(-Decimal(line.book_afn), "f"),
                    }
                    for line in rows
                ]
            entry = await LedgerService._write(
                db,
                context,
                shop_id,
                "payment:reverse",
                data.reason,
                lines,
                reversal_of_id=payment.entry_id,
            )
            db.add(
                context.new(
                    PaymentReversal,
                    shop_id=shop_id,
                    payment_id=payment.id,
                    entry_id=entry["id"],
                )
            )
            await db.flush()
            result = (await PaymentService.result(db, context, payment)).model_dump(
                mode="json"
            )
            return {"entry": entry, **result}

        return await TenantOperations.execute_once(
            db,
            context,
            "payment:reverse",
            retry_key,
            {"shop_id": shop_id, "payment_id": str(payment_id), **data.model_dump()},
            "payment:reverse",
            execute,
        )
