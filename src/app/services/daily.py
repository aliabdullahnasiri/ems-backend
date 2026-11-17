"""Daily operations share the tenant lock, savepoint, ledger and retry boundary."""

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal, localcontext

from app.core.exceptions import AppValidationError, AuthorizationError, ConflictError
from app.models import PrivateAttachment
from app.models.daily import (
    BusinessDay,
    CashTransfer,
    ClosingConfirmation,
    DayClosing,
    Expense,
    ExpenseCategory,
)
from app.models.finance import (
    Cashbox,
    Currency,
    JournalEntry,
    JournalLine,
    LedgerAccount,
)
from app.schemas.daily.data import CategoryData, ClosingData, DayData, OperationData
from app.services.finance import FinanceService, LedgerService
from app.services.financial_values import business_day, decimal_text, exact
from app.services.hawala_posting import HawalaPostingService as Posting
from app.services.tenant_operations import TenantOperations


class DailyService:
    """Expenses and same-currency internal movements; caller owns commit."""

    @staticmethod
    async def categories(db, context, shop_id, offset=0, limit=50):
        """List retained categories with bounded pagination."""
        await TenantOperations.authorize(db, context, "report:read")
        await context.shop(db, shop_id)
        FinanceService.page(offset, limit)
        return [
            CategoryData.model_validate(x)
            for x in await db.scalars(
                context.query(ExpenseCategory)
                .where(ExpenseCategory.shop_id == shop_id)
                .order_by(ExpenseCategory.id)
                .offset(offset)
                .limit(limit)
            )
        ]

    @staticmethod
    async def category(db, context, shop_id, data, category_id=None):
        """Create or rename/deactivate a category under the posting lock."""
        await TenantOperations.authorize(db, context, "settings:update")
        await LedgerService.lock(db, context)
        await context.shop(db, shop_id)
        row = (
            await context.get(db, ExpenseCategory, category_id)
            if category_id
            else context.new(ExpenseCategory, shop_id=shop_id)
        )
        if row.shop_id != shop_id:
            raise AuthorizationError()
        changes = data.model_dump(exclude_unset=category_id is not None)
        if any(value is None for value in changes.values()):
            raise AppValidationError()
        name = changes.get("name", row.name)
        if not name or not name.strip():
            raise AppValidationError()
        name = name.strip()
        existing = await db.scalar(
            context.query(ExpenseCategory).where(
                ExpenseCategory.shop_id == shop_id, ExpenseCategory.name == name
            )
        )
        if existing and existing.id != row.id:
            raise ConflictError()
        row.name = name
        if "is_active" in changes:
            row.is_active = changes["is_active"]
        db.add(row)
        await db.flush()
        await TenantOperations.audit(
            db, context, "expense_category:update", "expense_category", row.id
        )
        return CategoryData.model_validate(row)

    @staticmethod
    async def operation_data(db, context, row):
        """Include the linked reversal without mutating original history."""
        currency = await context.get(db, Currency, row.currency_id)
        result = OperationData.model_validate(
            {**row.to_dict(), "currency": currency.code}
        )
        result.reversal_entry_id = await db.scalar(
            context.query(JournalEntry)
            .with_only_columns(JournalEntry.id)
            .where(JournalEntry.reversal_of_id == row.entry_id)
        )
        return result

    @staticmethod
    async def operations(db, context, shop_id, model, offset=0, limit=50):
        """Read scoped expense or transfer history."""
        await TenantOperations.authorize(db, context, "report:read")
        await context.shop(db, shop_id)
        FinanceService.page(offset, limit)
        return [
            await DailyService.operation_data(db, context, row)
            for row in await db.scalars(
                context.query(model)
                .where(model.shop_id == shop_id)
                .order_by(model.id)
                .offset(offset)
                .limit(limit)
            )
        ]

    @staticmethod
    async def create(db, context, shop_id, key, data, *, transfer=False):
        """Calculate outgoing cost under lock and post both legs atomically."""
        action = "cash_transfer:create" if transfer else "expense:create"

        async def execute():
            await ClosingService.guard(db, context, shop_id, datetime.now(UTC))
            currency = await context.reference(db, Currency, data.currency_id)
            source = data.source_cashbox_id if transfer else data.cashbox_id
            await context.reference(db, Cashbox, source, shop_id=shop_id)
            amount = decimal_text(data.amount, currency.precision)
            if amount <= 0:
                raise AppValidationError()
            reason = data.reason if transfer else data.explanation
            if not reason.strip():
                raise AppValidationError()
            if transfer:
                if source == data.destination_cashbox_id:
                    raise AppValidationError()
                await context.reference(
                    db, Cashbox, data.destination_cashbox_id, shop_id=shop_id
                )
            else:
                await context.reference(
                    db, ExpenseCategory, data.category_id, shop_id=shop_id
                )
                if (
                    data.expense_date != business_day(datetime.now(UTC))
                    or not data.payee.strip()
                ):
                    raise AppValidationError()
                if data.attachment_id:
                    await TenantOperations.authorize(db, context, "attachment:manage")
                    await context.reference(
                        db, PrivateAttachment, data.attachment_id, shop_id=shop_id
                    )
                    # Identity documents cannot be repurposed as expense evidence.
                    from app.models.contact import ContactAttachment

                    if await db.scalar(
                        context.query(ContactAttachment).where(
                            ContactAttachment.attachment_id == data.attachment_id
                        )
                    ):
                        raise AuthorizationError()
            book = await Posting.cash_cost(db, context, source, currency.id, amount)
            lines = []
            await Posting.line(
                db,
                context,
                shop_id,
                lines,
                currency.id,
                -amount,
                -book,
                cashbox_id=source,
                kind="cash",
            )
            await Posting.line(
                db,
                context,
                shop_id,
                lines,
                currency.id,
                amount,
                book,
                cashbox_id=data.destination_cashbox_id if transfer else None,
                code=None if transfer else f"expense:{data.category_id}",
                kind="cash" if transfer else "expense",
            )
            entry = await LedgerService._write(
                db, context, shop_id, action, reason, lines
            )
            values = data.model_dump()
            values.update(
                amount=exact(amount, currency.precision),
                book_afn=exact(book, 2),
                entry_id=entry["id"],
            )
            row = context.new(
                CashTransfer if transfer else Expense, shop_id=shop_id, **values
            )
            db.add(row)
            await db.flush()
            return (await DailyService.operation_data(db, context, row)).model_dump(
                mode="json"
            )

        return await TenantOperations.execute_once(
            db,
            context,
            action,
            key,
            {"shop_id": shop_id, **data.model_dump(mode="json")},
            action,
            execute,
        )

    @staticmethod
    async def reverse(db, context, shop_id, record_id, key, reason, *, transfer=False):
        """Owner-authorized linked reversal in the currently open period."""
        action = "cash_transfer:reverse" if transfer else "expense:reverse"

        async def execute():
            await LedgerService.owner(db, context)
            row = await context.reference(
                db, CashTransfer if transfer else Expense, record_id, shop_id=shop_id
            )
            if await db.scalar(
                context.query(JournalEntry).where(
                    JournalEntry.reversal_of_id == row.entry_id
                )
            ):
                raise ConflictError()
            lines = [
                {
                    "account_id": x.account_id,
                    "amount": str(-Decimal(x.amount)),
                    "book_afn": str(-Decimal(x.book_afn)),
                }
                for x in await db.scalars(
                    context.query(JournalLine).where(
                        JournalLine.entry_id == row.entry_id
                    )
                )
            ]
            await LedgerService._write(
                db, context, shop_id, action, reason, lines, reversal_of_id=row.entry_id
            )
            return (await DailyService.operation_data(db, context, row)).model_dump(
                mode="json"
            )

        return await TenantOperations.execute_once(
            db,
            context,
            action,
            key,
            {"shop_id": shop_id, "id": str(record_id), "reason": reason},
            action,
            execute,
        )


class ClosingService:
    """An immutable draft freezes the till; confirmation posts explicit differences."""

    @staticmethod
    async def latest(db, context, shop_id):
        """Resolve the most recent business day within a shop."""
        return await db.scalar(
            context.query(BusinessDay)
            .where(BusinessDay.shop_id == shop_id)
            .order_by(BusinessDay.day.desc())
            .limit(1)
        )

    @staticmethod
    async def guard(db, context, shop_id, instant, closing_id=None):
        """Apply to every ledger posting, including corrections and openings."""
        day = await ClosingService.latest(db, context, shop_id)
        if day is None:
            return  # Existing installations opt in by opening their first business day.
        if closing_id is None and business_day(instant) != day.day:
            raise ConflictError()
        closing = await db.scalar(
            context.query(DayClosing).where(DayClosing.business_day_id == day.id)
        )
        if closing:
            if closing.id != closing_id or await db.scalar(
                context.query(ClosingConfirmation).where(
                    ClosingConfirmation.closing_id == closing.id
                )
            ):
                raise ConflictError()
        elif closing_id is not None:
            raise ConflictError()

    @staticmethod
    async def snapshot(db, context, shop_id):
        """Capture all existing cash accounts, including inactive historical tills."""
        result = []
        for account in await db.scalars(
            context.query(LedgerAccount)
            .where(LedgerAccount.shop_id == shop_id, LedgerAccount.kind == "cash")
            .order_by(LedgerAccount.cashbox_id, LedgerAccount.currency_id)
        ):
            currency = await context.get(db, Currency, account.currency_id)
            amount, book = await LedgerService.balance(db, context, account.id)
            result.append(
                {
                    "cashbox_id": account.cashbox_id,
                    "currency_id": currency.id,
                    "currency": currency.code,
                    "expected": exact(amount, currency.precision),
                    "book_afn": exact(book, 2),
                }
            )
        return result

    @staticmethod
    async def day_data(db, context, day):
        """Return retained carried-forward balances and derived state."""
        closing = await db.scalar(
            context.query(DayClosing).where(DayClosing.business_day_id == day.id)
        )
        status = "open"
        if closing:
            status = (
                "closed"
                if await db.scalar(
                    context.query(ClosingConfirmation).where(
                        ClosingConfirmation.closing_id == closing.id
                    )
                )
                else "closing"
            )
        return DayData(
            id=day.id, uuid=day.uuid, day=day.day, opening=day.opening, status=status
        )

    @staticmethod
    async def current(db, context, shop_id):
        """Return the latest business day, including its closed state."""
        await TenantOperations.authorize(db, context, "report:read")
        await context.shop(db, shop_id)
        day = await ClosingService.latest(db, context, shop_id)
        return await ClosingService.day_data(db, context, day) if day else None

    @staticmethod
    async def open(db, context, shop_id, key, data):
        """Carry ledger balances forward without creating any opening journals."""

        async def execute():
            await context.shop(db, shop_id)
            if data.day != business_day(datetime.now(UTC)):
                raise AppValidationError()
            previous = await ClosingService.latest(db, context, shop_id)
            if previous and (
                data.day <= previous.day
                or (await ClosingService.day_data(db, context, previous)).status
                != "closed"
            ):
                raise ConflictError()
            row = BusinessDay(
                **context.audit_values(),
                shop_id=shop_id,
                day=data.day,
                opening=await ClosingService.snapshot(db, context, shop_id),
            )
            db.add(row)
            await db.flush()
            await TenantOperations.audit(
                db, context, "business_day:open", "business_day", row.id
            )
            return (await ClosingService.day_data(db, context, row)).model_dump(
                mode="json"
            )

        return await TenantOperations.execute_once(
            db,
            context,
            "business_day:open",
            key,
            {"shop_id": shop_id, **data.model_dump(mode="json")},
            "day_closing:create",
            execute,
        )

    @staticmethod
    async def closing_data(db, context, row):
        """Return the immutable count snapshot and separate approval record."""
        confirmation = await db.scalar(
            context.query(ClosingConfirmation).where(
                ClosingConfirmation.closing_id == row.id
            )
        )
        return ClosingData(
            id=row.id,
            uuid=row.uuid,
            business_day_id=row.business_day_id,
            cutoff=(
                row.cutoff.replace(tzinfo=UTC)
                if row.cutoff.tzinfo is None
                else row.cutoff
            ),
            counts=row.counts,
            reason=row.reason,
            confirmed=confirmation is not None,
            adjustment_entry_id=confirmation.entry_id if confirmation else None,
        )

    @staticmethod
    async def create(db, context, shop_id, key, data):
        """Freeze expected and counted cash at a server-defined cutoff under lock."""

        async def execute():
            await context.shop(db, shop_id)
            day = await ClosingService.latest(db, context, shop_id)
            if (
                day is None
                or (await ClosingService.day_data(db, context, day)).status != "open"
            ):
                raise ConflictError()
            rows = await ClosingService.snapshot(db, context, shop_id)
            counts = {(x.cashbox_id, x.currency_id): x for x in data.counts}
            if len(counts) != len(data.counts):
                raise AppValidationError()
            # Include explicitly counted currencies with no historical account (overages).
            keys = {(x["cashbox_id"], x["currency_id"]) for x in rows}
            for box_id, currency_id in counts.keys() - keys:
                await context.reference(db, Cashbox, box_id, shop_id=shop_id)
                currency = await context.reference(db, Currency, currency_id)
                rows.append(
                    {
                        "cashbox_id": box_id,
                        "currency_id": currency_id,
                        "currency": currency.code,
                        "expected": exact(Decimal(0), currency.precision),
                        "book_afn": "0.00",
                    }
                )
            if len(rows) > 100 or any(
                (x["cashbox_id"], x["currency_id"]) not in counts for x in rows
            ):
                raise AppValidationError()
            with localcontext() as ctx:
                ctx.prec = 60
                for row in rows:
                    count = counts[row["cashbox_id"], row["currency_id"]]
                    currency = await context.get(db, Currency, row["currency_id"])
                    counted = decimal_text(count.counted, currency.precision)
                    expected = Decimal(row["expected"])
                    difference = counted - expected
                    if difference < 0:
                        book = -(
                            await Posting.cash_cost(
                                db, context, row["cashbox_id"], currency.id, -difference
                            )
                        )
                    elif difference > 0:
                        book = (
                            difference
                            if currency.code == "AFN"
                            else decimal_text(count.overage_book_afn, 2)
                        )
                        if book <= 0:
                            raise AppValidationError()
                    else:
                        book = Decimal(0)
                    if count.overage_book_afn is not None and (
                        difference <= 0
                        or decimal_text(count.overage_book_afn, 2) != book
                    ):
                        raise AppValidationError()
                    row.update(
                        counted=exact(counted, currency.precision),
                        difference=exact(difference, currency.precision),
                        adjustment_book_afn=exact(book, 2),
                    )
            if any(Decimal(x["difference"]) for x in rows) and (
                not data.reason or not data.reason.strip()
            ):
                raise AppValidationError()
            row = DayClosing(
                **context.audit_values(),
                shop_id=shop_id,
                business_day_id=day.id,
                cutoff=datetime.now(UTC),
                counts=rows,
                reason=data.reason,
            )
            db.add(row)
            await db.flush()
            await TenantOperations.audit(
                db, context, "day_closing:create", "day_closing", row.id
            )
            return (await ClosingService.closing_data(db, context, row)).model_dump(
                mode="json"
            )

        return await TenantOperations.execute_once(
            db,
            context,
            "day_closing:create",
            key,
            {"shop_id": shop_id, **data.model_dump(mode="json")},
            "day_closing:create",
            execute,
        )

    @staticmethod
    async def confirm(db, context, shop_id, closing_id, key, data):
        """Require actual owner approval; differences become linked adjustment journals."""

        async def execute():
            row = await context.reference(db, DayClosing, closing_id, shop_id=shop_id)
            await LedgerService.owner(db, context)
            if data.owner_approved is not True:
                raise AuthorizationError()
            if await db.scalar(
                context.query(ClosingConfirmation).where(
                    ClosingConfirmation.closing_id == row.id
                )
            ):
                raise ConflictError()
            lines = []
            for count in row.counts:
                difference, book = (
                    Decimal(count["difference"]),
                    Decimal(count["adjustment_book_afn"]),
                )
                if not difference:
                    continue
                if not row.reason or not row.reason.strip():
                    raise AppValidationError()
                await Posting.line(
                    db,
                    context,
                    shop_id,
                    lines,
                    count["currency_id"],
                    difference,
                    book,
                    cashbox_id=count["cashbox_id"],
                    kind="cash",
                )
                await Posting.line(
                    db,
                    context,
                    shop_id,
                    lines,
                    count["currency_id"],
                    -difference,
                    -book,
                    code="cash_overage" if difference > 0 else "cash_shortage",
                    kind="income" if difference > 0 else "expense",
                )
            entry = (
                await LedgerService._write(
                    db,
                    context,
                    shop_id,
                    "day_closing:adjust",
                    row.reason,
                    lines,
                    closing_id=row.id,
                )
                if lines
                else None
            )
            confirmation = ClosingConfirmation(
                **context.audit_values(),
                shop_id=shop_id,
                closing_id=row.id,
                entry_id=entry["id"] if entry else None,
            )
            db.add(confirmation)
            await db.flush()
            await TenantOperations.audit(
                db, context, "day_closing:confirm", "day_closing", row.id, row.reason
            )
            return (await ClosingService.closing_data(db, context, row)).model_dump(
                mode="json"
            )

        return await TenantOperations.execute_once(
            db,
            context,
            "day_closing:confirm",
            key,
            {"shop_id": shop_id, "id": str(closing_id), **data.model_dump(mode="json")},
            "day_closing:approve",
            execute,
        )

    @staticmethod
    async def get(db, context, shop_id, closing_id):
        """Read one scoped closing report."""
        await TenantOperations.authorize(db, context, "report:read")
        row = await context.reference(db, DayClosing, closing_id, shop_id=shop_id)
        return await ClosingService.closing_data(db, context, row)

    @staticmethod
    async def list(db, context, shop_id, offset=0, limit=50):
        """Read paginated immutable closing reports."""
        await TenantOperations.authorize(db, context, "report:read")
        await context.shop(db, shop_id)
        FinanceService.page(offset, limit)
        return [
            await ClosingService.closing_data(db, context, row)
            for row in await db.scalars(
                context.query(DayClosing)
                .where(DayClosing.shop_id == shop_id)
                .order_by(DayClosing.id)
                .offset(offset)
                .limit(limit)
            )
        ]

    @staticmethod
    async def export(db, context, shop_id, closing_id):
        """Export numeric report data without spreadsheet formula-bearing free text."""
        row = await ClosingService.get(db, context, shop_id, closing_id)
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(
            [
                "cashbox_id",
                "currency",
                "expected",
                "counted",
                "difference",
                "book_afn",
                "adjustment_book_afn",
            ]
        )
        for count in row.counts:
            writer.writerow(
                [
                    count.cashbox_id,
                    count.currency,
                    count.expected,
                    count.counted,
                    count.difference,
                    count.book_afn,
                    count.adjustment_book_afn,
                ]
            )
        return stream.getvalue()
