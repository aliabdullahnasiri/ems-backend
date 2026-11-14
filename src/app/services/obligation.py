"""Outstanding debt is derived from immutable sources and live payment allocations."""

from datetime import UTC
from decimal import Decimal, localcontext

from app.core.exceptions import AppValidationError, ConflictError
from app.models.finance import Currency, JournalEntry, JournalPosting
from app.models.payment import (
    CustomerObligation,
    Payment,
    PaymentAllocation,
    PaymentReversal,
)
from app.schemas.contact.data import ObligationData, SettlementData
from app.services.contact import ContactService
from app.services.financial_values import exact
from app.services.tenant_operations import TenantOperations


class ObligationService:
    """Customer obligations preserve original currency, source and acquisition value."""

    @staticmethod
    async def record(
        db,
        context,
        shop_id,
        contact_id,
        entry_id,
        account_id,
        currency_id,
        direction,
        amount,
        book_afn,
        exchange_id=None,
    ):
        """Append an obligation inside the same posting transaction as its source."""
        record = context.new(
            CustomerObligation,
            shop_id=shop_id,
            contact_id=contact_id,
            source_entry_id=entry_id,
            account_id=account_id,
            currency_id=currency_id,
            direction=direction,
            amount=amount,
            book_afn=book_afn,
            exchange_id=exchange_id,
        )
        db.add(record)
        await db.flush()
        return record

    @staticmethod
    async def data(db, context, obligation):
        """Include all settlement history; reversed payments do not reduce the balance."""
        currency = await context.get(db, Currency, obligation.currency_id)
        reversed_source = bool(
            await db.scalar(
                context.query(JournalEntry, JournalPosting)
                .join(JournalPosting, JournalPosting.entry_id == JournalEntry.id)
                .where(JournalEntry.reversal_of_id == obligation.source_entry_id)
            )
        )
        rows = (
            await db.execute(
                context.query(PaymentAllocation, Payment)
                .add_columns(Payment)
                .join(Payment, Payment.id == PaymentAllocation.payment_id)
                .where(PaymentAllocation.obligation_id == obligation.id)
                .order_by(Payment.id)
            )
        ).all()
        history = []
        with localcontext() as ctx:
            ctx.prec = 60
            paid, cost = Decimal(0), Decimal(0)
            for allocation, payment in rows:
                reversal = await db.scalar(
                    context.query(PaymentReversal).where(
                        PaymentReversal.payment_id == payment.id
                    )
                )
                if not reversal:
                    paid += Decimal(allocation.amount)
                    cost += Decimal(allocation.book_afn)
                instant = payment.created_at
                if instant.tzinfo is None:
                    instant = instant.replace(tzinfo=UTC)
                history.append(
                    SettlementData(
                        payment_id=payment.id,
                        payment_uuid=payment.uuid,
                        reference=payment.reference,
                        entry_id=payment.entry_id,
                        amount=allocation.amount,
                        book_afn=allocation.book_afn,
                        created_at=instant.isoformat(),
                        reversal_entry_id=reversal.entry_id if reversal else None,
                    )
                )
            remaining = Decimal(obligation.amount) - paid
            book = Decimal(obligation.book_afn) - cost
            if remaining < 0 or book < 0:
                raise ConflictError()
            return ObligationData(
                id=obligation.id,
                uuid=obligation.uuid,
                contact_id=obligation.contact_id,
                exchange_id=obligation.exchange_id,
                source_entry_id=obligation.source_entry_id,
                currency_id=currency.id,
                currency=currency.code,
                direction=obligation.direction,
                original_amount=obligation.amount,
                paid_amount=exact(paid, currency.precision),
                remaining_amount=exact(
                    Decimal(0) if reversed_source else remaining, currency.precision
                ),
                remaining_book_afn=exact(Decimal(0) if reversed_source else book, 2),
                is_reversed=reversed_source,
                settlements=history,
            )

    @staticmethod
    async def outstanding(
        db, context, contact_uuid, offset=0, limit=50, include_settled=False
    ):
        """Return paginated customer debts with original, paid and remaining amounts."""
        await TenantOperations.authorize(db, context, "contact:read")
        contact = await ContactService.get(db, context, contact_uuid)
        if offset < 0 or not 1 <= limit <= 100:
            raise AppValidationError()
        # Filter before paging, so settled records cannot leave gaps in the result.
        rows = await db.scalars(
            context.query(CustomerObligation)
            .where(
                CustomerObligation.contact_id == contact.id,
                CustomerObligation.shop_id == contact.shop_id,
            )
            .order_by(CustomerObligation.id)
        )
        result = []
        for row in rows:
            data = await ObligationService.data(db, context, row)
            if include_settled or Decimal(data.remaining_amount) > 0:
                result.append(data)
        return result[offset : offset + limit]
