"""Reusable exact-money legs for hawala and agent settlement workflows."""

from decimal import Decimal, localcontext

from app.core.exceptions import AppValidationError, ConflictError
from app.models.finance import Currency, JournalLine, LedgerAccount, ShopSettings
from app.schemas.finance.data import SettingsData
from app.services.finance import LedgerService
from app.services.financial_values import decimal_text, exact


class HawalaPostingService:
    """Build balanced legs under the calling workflow's lock and posting savepoint."""

    @staticmethod
    async def line(
        db,
        context,
        shop_id,
        lines,
        currency_id,
        amount,
        book,
        code=None,
        kind=None,
        cashbox_id=None,
        account_id=None,
    ):
        """Append one validated canonical leg; skip empty zero/zero movements."""
        amount, book = Decimal(amount), Decimal(book)
        if not amount and not book:
            return
        currency = await context.reference(db, Currency, currency_id)
        account = (
            await context.reference(db, LedgerAccount, account_id, shop_id=shop_id)
            if account_id is not None
            else await LedgerService.account(
                db, context, shop_id, currency.id, code, kind, cashbox_id
            )
        )
        lines.append(
            {
                "account_id": account.id,
                "amount": exact(amount, currency.precision),
                "book_afn": exact(book, 2),
            }
        )

    @staticmethod
    async def cash_cost(db, context, cashbox_id, currency_id, amount):
        """Weighted-average cost for cash actually leaving, with full-exhaustion rounding."""
        account = await db.scalar(
            context.query(LedgerAccount).where(
                LedgerAccount.cashbox_id == cashbox_id,
                LedgerAccount.currency_id == currency_id,
            )
        )
        available, book = (
            await LedgerService.balance(db, context, account.id)
            if account
            else (Decimal(0), Decimal(0))
        )
        if amount <= 0 or amount > available:
            raise ConflictError()
        with localcontext() as ctx:
            ctx.prec = 60
            return (
                book
                if amount == available
                else Decimal(exact(book * amount / available, 2))
            )

    @staticmethod
    async def spread(db, context, shop_id, lines, profit):
        """Recognize exchange spread without treating transfer principal as income."""
        if not profit:
            return
        afn = await db.scalar(context.query(Currency).where(Currency.code == "AFN"))
        if afn is None:
            raise AppValidationError()
        await HawalaPostingService.line(
            db,
            context,
            shop_id,
            lines,
            afn.id,
            profit,
            profit,
            "hawala_clearing",
            "clearing",
        )
        await HawalaPostingService.line(
            db,
            context,
            shop_id,
            lines,
            afn.id,
            -profit,
            -profit,
            "hawala_profit" if profit > 0 else "hawala_loss",
            "income" if profit > 0 else "expense",
        )

    @staticmethod
    async def balance_books(db, context, shop_id, lines, foreign_currency_id):
        """Realize payout/settlement cost differences with zero foreign physical units."""
        with localcontext() as ctx:
            ctx.prec = 60
            profit = sum((Decimal(line["book_afn"]) for line in lines), Decimal(0))
            if profit:
                foreign = await context.reference(db, Currency, foreign_currency_id)
                if foreign.code == "AFN":
                    raise AppValidationError()
                await HawalaPostingService.spread(db, context, shop_id, lines, profit)
                await HawalaPostingService.line(
                    db,
                    context,
                    shop_id,
                    lines,
                    foreign.id,
                    Decimal(0),
                    -profit,
                    "hawala_book_clearing",
                    "clearing",
                )
            return profit

    @staticmethod
    async def opposite(db, context, entry_id):
        """Negate original legs exactly; the caller supplies the linked reversal header."""
        rows = await db.scalars(
            context.query(JournalLine).where(JournalLine.entry_id == entry_id)
        )
        with localcontext() as ctx:
            ctx.prec = 60
            return [
                {
                    "account_id": row.account_id,
                    "amount": format(-Decimal(row.amount), "f"),
                    "book_afn": format(-Decimal(row.book_afn), "f"),
                }
                for row in rows
            ]

    @staticmethod
    async def shop_snapshot(db, context, shop_id):
        """Snapshot receipt details without granting configuration permissions."""
        shop = await context.shop(db, shop_id)
        settings = await db.scalar(
            context.query(ShopSettings).where(ShopSettings.shop_id == shop_id)
        )
        return SettingsData(
            shop_id=shop_id,
            name=shop.name,
            address=shop.address,
            phone=settings.phone if settings else None,
            logo_reference=settings.logo_reference if settings else None,
            preferred_language=settings.preferred_language if settings else "fa",
            receipt_footer=settings.receipt_footer if settings else None,
        )

    @staticmethod
    async def valuation(db, context, currency_id, rate):
        """AFN values itself at one; other currency values are agreed explicitly."""
        currency = await context.reference(db, Currency, currency_id)
        if currency.code == "AFN":
            if rate is not None and decimal_text(rate, 8) != 1:
                raise AppValidationError()
            return Decimal(1)
        result = decimal_text(rate, 8)
        if result <= 0:
            raise AppValidationError()
        return result
