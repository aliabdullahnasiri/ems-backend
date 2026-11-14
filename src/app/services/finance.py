"""Shop configuration and reusable, atomic double-entry posting services.

Methods flush only. Request middleware or the calling job owns the commit.
"""

from datetime import UTC
from decimal import Decimal, localcontext
from typing import ClassVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppValidationError, AuthorizationError, ConflictError
from app.models import Organization
from app.models.finance import (
    Cashbox,
    Currency,
    JournalEntry,
    JournalLine,
    JournalPosting,
    LedgerAccount,
    ShopSettings,
)
from app.schemas.finance.data import CashboxData, CurrencyData, EntryData, SettingsData
from app.services.financial_values import decimal_text, exact
from app.services.organization_context import OrganizationContext
from app.services.tenant_operations import TenantOperations


class FinanceService:
    """Configuration, scoped queries, and ledger-derived cash balances."""

    @staticmethod
    async def settings(
        db: AsyncSession, context: OrganizationContext, shop_id: int
    ) -> SettingsData:
        """Read Unicode shop settings; omitted preferences use Dari by default."""
        await TenantOperations.authorize(db, context, "settings:read")
        shop = await context.shop(db, shop_id)
        settings = await db.scalar(
            context.query(ShopSettings).where(ShopSettings.shop_id == shop_id)
        )
        return SettingsData(
            shop_id=shop.id,
            name=shop.name,
            address=shop.address,
            phone=settings.phone if settings else None,
            logo_reference=settings.logo_reference if settings else None,
            preferred_language=settings.preferred_language if settings else "fa",
            receipt_footer=settings.receipt_footer if settings else None,
        )

    @staticmethod
    async def update_settings(
        db: AsyncSession, context: OrganizationContext, shop_id: int, data
    ) -> SettingsData:
        """Update the verified shop's settings and retain the actor in audit history."""
        await TenantOperations.authorize(db, context, "settings:update")
        await LedgerService.lock(db, context)
        shop = await context.shop(db, shop_id)
        changes = data.model_dump(exclude_unset=True)
        if "name" in changes and (
            changes["name"] is None or not changes["name"].strip()
        ):
            raise AppValidationError()
        if "preferred_language" in changes and changes["preferred_language"] is None:
            raise AppValidationError()
        for key in ("name", "address"):
            if key in changes:
                setattr(shop, key, changes.pop(key))
        settings = await db.scalar(
            context.query(ShopSettings).where(ShopSettings.shop_id == shop_id)
        )
        if settings is None:
            settings = context.new(ShopSettings, shop_id=shop_id)
            db.add(settings)
        for key, value in changes.items():
            setattr(settings, key, value)
        await db.flush()
        await TenantOperations.audit(db, context, "settings:update", "shop", shop_id)
        return await FinanceService.settings(db, context, shop_id)

    @staticmethod
    async def currencies(
        db: AsyncSession, context: OrganizationContext, offset=0, limit=50
    ):
        """List configured currencies, including deactivated historical units."""
        await TenantOperations.authorize(db, context, "settings:read")
        FinanceService.page(offset, limit)
        return [
            CurrencyData.model_validate(c)
            for c in await db.scalars(
                context.query(Currency)
                .order_by(Currency.id)
                .offset(offset)
                .limit(limit)
            )
        ]

    @staticmethod
    async def create_currency(db: AsyncSession, context: OrganizationContext, data):
        """Configure an explicit currency code and immutable decimal precision."""
        await TenantOperations.authorize(db, context, "settings:update")
        await LedgerService.lock(db, context)
        if await db.scalar(context.query(Currency).where(Currency.code == data.code)):
            raise ConflictError()
        if data.code == "AFN" and data.precision != 2:
            raise AppValidationError()
        currency = context.new(
            Currency,
            code=data.code,
            precision=data.precision,
            rial_per_unit={"IRR": 1, "IRT": 10}.get(data.code),
        )
        db.add(currency)
        await db.flush()
        await TenantOperations.audit(
            db, context, "currency:create", "currency", currency.id
        )
        return CurrencyData.model_validate(currency)

    @staticmethod
    async def update_currency(
        db: AsyncSession, context: OrganizationContext, currency_id: int | UUID, data
    ):
        """Deactivate a currency without changing historical code, precision or unit."""
        await TenantOperations.authorize(db, context, "settings:update")
        await LedgerService.lock(db, context)
        currency = await context.get(db, Currency, currency_id)
        currency.is_active = data.is_active
        await db.flush()
        await TenantOperations.audit(
            db, context, "currency:update", "currency", currency.id
        )
        return CurrencyData.model_validate(currency)

    @staticmethod
    async def create_cashbox(
        db: AsyncSession, context: OrganizationContext, shop_id: int, data
    ):
        """Create an empty cashbox; money can only enter through ledger postings."""
        await TenantOperations.authorize(db, context, "cashbox:manage")
        await LedgerService.lock(db, context)
        await context.shop(db, shop_id)
        if not data.name.strip() or await db.scalar(
            context.query(Cashbox).where(
                Cashbox.shop_id == shop_id, Cashbox.name == data.name
            )
        ):
            raise ConflictError()
        box = context.new(Cashbox, shop_id=shop_id, name=data.name)
        db.add(box)
        await db.flush()
        await TenantOperations.audit(db, context, "cashbox:create", "cashbox", box.id)
        return await FinanceService.cashbox_data(db, context, box)

    @staticmethod
    async def update_cashbox(
        db: AsyncSession,
        context: OrganizationContext,
        shop_id: int,
        box_id: int | UUID,
        data,
    ):
        """Rename or deactivate a cashbox, retaining its balance and movements."""
        await TenantOperations.authorize(db, context, "cashbox:manage")
        await LedgerService.lock(db, context)
        await context.shop(db, shop_id)
        box = await context.get(db, Cashbox, box_id)
        if box.shop_id != shop_id:
            raise AuthorizationError()
        changes = data.model_dump(exclude_unset=True)
        if any(value is None for value in changes.values()):
            raise AppValidationError()
        if "name" in changes:
            if not changes["name"].strip():
                raise AppValidationError()
            if await db.scalar(
                context.query(Cashbox).where(
                    Cashbox.shop_id == shop_id,
                    Cashbox.name == changes["name"],
                    Cashbox.id != box.id,
                )
            ):
                raise ConflictError()
        for key, value in changes.items():
            setattr(box, key, value)
        await db.flush()
        await TenantOperations.audit(db, context, "cashbox:update", "cashbox", box.id)
        return await FinanceService.cashbox_data(db, context, box)

    @staticmethod
    def page(offset, limit):
        """Bound all ledger/configuration list queries."""
        if offset < 0 or not 1 <= limit <= 100:
            raise AppValidationError()

    @staticmethod
    async def cashbox_data(db, context, box):
        """Reconcile original-currency cash and AFN carrying value from sealed lines."""
        accounts = list(
            await db.scalars(
                context.query(LedgerAccount).where(LedgerAccount.cashbox_id == box.id)
            )
        )
        balances = []
        for account in accounts:
            currency = await context.get(db, Currency, account.currency_id)
            amount, book = await LedgerService.balance(db, context, account.id)
            balances.append(
                {
                    "currency": currency.code,
                    "amount": exact(amount, currency.precision),
                    "book_afn": exact(book, 2),
                }
            )
        return CashboxData(
            id=box.id,
            uuid=box.uuid,
            shop_id=box.shop_id,
            name=box.name,
            is_active=box.is_active,
            balances=balances,
        )

    @staticmethod
    async def cashboxes(db, context, shop_id, offset=0, limit=50):
        """List cashboxes and ledger balances within one verified shop."""
        await TenantOperations.authorize(db, context, "cashbox:read")
        await context.shop(db, shop_id)
        FinanceService.page(offset, limit)
        boxes = await db.scalars(
            context.query(Cashbox)
            .where(Cashbox.shop_id == shop_id)
            .order_by(Cashbox.id)
            .offset(offset)
            .limit(limit)
        )
        return [await FinanceService.cashbox_data(db, context, box) for box in boxes]


class LedgerService:
    """Posting kernel shared by later exchange, payment and hawala services."""

    PERMISSIONS: ClassVar[dict[str, str]] = {
        "opening_balance:create": "opening_balance:create",
        "exchange:create": "exchange:create",
        "payment:create": "payment:create",
        "hawala:confirm": "hawala:confirm",
        "hawala:pay": "hawala:pay",
        "hawala:refund": "hawala:refund",
        "agent_settlement:create": "agent_settlement:create",
        "expense:create": "expense:create",
        "cash_transfer:create": "cash_transfer:create",
    }

    @staticmethod
    async def lock(db, context):
        """Serialize configuration against financial posting using the same tenant lock."""
        from sqlalchemy import update

        await db.scalar(
            select(Organization.id)
            .where(Organization.id == context.organization_id)
            .with_for_update()
        )
        if db.bind.dialect.name == "sqlite":
            await db.execute(
                update(Organization)
                .where(Organization.id == context.organization_id)
                .values(rbac_version=Organization.rbac_version)
            )

    @staticmethod
    async def owner(db, context):
        """Opening approval and foundation reversals require the current owner."""
        organization = await db.get(
            Organization, context.organization_id, populate_existing=True
        )
        if organization.owner_id != context.user_id:
            raise AuthorizationError()

    @staticmethod
    async def account(db, context, shop_id, currency_id, code, kind, cashbox_id=None):
        """Resolve/create a scoped immutable account for internal workflow services."""
        await TenantOperations.authorize(db, context)
        await LedgerService.lock(db, context)
        await context.shop(db, shop_id)
        await context.reference(db, Currency, currency_id)
        if kind == "cash":
            await context.reference(db, Cashbox, cashbox_id, shop_id=shop_id)
            code = f"cash:{cashbox_id}"
        elif cashbox_id is not None:
            raise AppValidationError()
        account = await db.scalar(
            context.query(LedgerAccount).where(
                LedgerAccount.shop_id == shop_id,
                LedgerAccount.currency_id == currency_id,
                LedgerAccount.code == code,
            )
        )
        if account:
            if account.kind != kind or account.cashbox_id != cashbox_id:
                raise ConflictError()
            return account
        account = context.new(
            LedgerAccount,
            shop_id=shop_id,
            currency_id=currency_id,
            code=code,
            kind=kind,
            cashbox_id=cashbox_id,
        )
        db.add(account)
        await db.flush()
        return account

    @staticmethod
    async def balance(db, context, account_id):
        """Sum exact decimal text in a high-precision context; unsealed drafts do not count."""
        rows = (
            await db.execute(
                context.query(JournalLine, JournalPosting)
                .join(JournalPosting, JournalPosting.entry_id == JournalLine.entry_id)
                .where(JournalLine.account_id == account_id)
            )
        ).scalars()
        with localcontext() as ctx:
            ctx.prec = 60
            amount, book = Decimal(0), Decimal(0)
            for line in rows:
                amount += Decimal(line.amount)
                book += Decimal(line.book_afn)
            return amount, book

    @staticmethod
    async def entry_data(db, context, entry):
        """Return immutable financial details with explicit original currency codes."""
        rows = await db.scalars(
            context.query(JournalLine)
            .where(JournalLine.entry_id == entry.id)
            .order_by(JournalLine.id)
        )
        lines = []
        for line in rows:
            account = await context.get(db, LedgerAccount, line.account_id)
            currency = await context.get(db, Currency, account.currency_id)
            lines.append(
                {
                    "account_id": account.id,
                    "account_code": account.code,
                    "kind": account.kind,
                    "currency": currency.code,
                    "amount": line.amount,
                    "book_afn": line.book_afn,
                }
            )
        instant = entry.created_at
        if instant.tzinfo is None:  # SQLite loses the UTC offset on retrieval.
            instant = instant.replace(tzinfo=UTC)
        return EntryData(
            id=entry.id,
            uuid=entry.uuid,
            shop_id=entry.shop_id,
            action=entry.action,
            reason=entry.reason,
            user_id=entry.user_id,
            membership_id=entry.membership_id,
            created_at=instant.isoformat(),
            reversal_of_id=entry.reversal_of_id,
            lines=lines,
        )

    @staticmethod
    async def _write(
        db,
        context,
        shop_id,
        action,
        reason,
        lines,
        *,
        opening_key=None,
        reversal_of_id=None,
    ):
        """Validate every leg before inserting and sealing one balanced journal."""
        if db.bind.dialect.name in {"postgresql", "mysql"}:
            connection = await db.connection()
            if await connection.get_isolation_level() != "READ COMMITTED":
                # A pre-lock snapshot must never authorize a stale cash balance.
                raise ConflictError()
        await context.shop(db, shop_id)
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 500
            or not 2 <= len(lines) <= 200
        ):
            raise AppValidationError()
        amounts, book_total, prepared, cash_changes = {}, Decimal(0), [], {}
        with localcontext() as ctx:
            ctx.prec = 60
            for item in lines:
                if set(item) != {"account_id", "amount", "book_afn"}:
                    raise AppValidationError()
                account = await context.reference(
                    db, LedgerAccount, item["account_id"], shop_id=shop_id
                )
                currency = await context.reference(db, Currency, account.currency_id)
                amount = decimal_text(item["amount"], currency.precision, signed=True)
                book = decimal_text(item["book_afn"], 2, signed=True)
                if (
                    (
                        amount == 0
                        and not (
                            account.kind == "clearing"
                            and currency.code != "AFN"
                            and book != 0
                        )
                    )
                    or (amount > 0 and book < 0)
                    or (amount < 0 and book > 0)
                ):
                    raise AppValidationError()
                if currency.code == "AFN" and amount != book:
                    raise AppValidationError()
                amounts[currency.id] = amounts.get(currency.id, Decimal(0)) + amount
                book_total += book
                prepared.append(
                    (account, exact(amount, currency.precision), exact(book, 2))
                )
                if account.kind == "cash":
                    await context.reference(
                        db, Cashbox, account.cashbox_id, shop_id=shop_id
                    )
                    da, dbk = cash_changes.get(account.id, (Decimal(0), Decimal(0)))
                    cash_changes[account.id] = da + amount, dbk + book
            if any(amounts.values()) or book_total != 0:
                raise AppValidationError()
            for account_id, (amount, book) in cash_changes.items():
                current, cost = await LedgerService.balance(db, context, account_id)
                if (
                    current + amount < 0
                    or cost + book < 0
                    or (current + amount == 0 and cost + book != 0)
                ):
                    raise ConflictError()
        entry = JournalEntry(
            **context.audit_values(),
            shop_id=shop_id,
            action=action,
            reason=reason,
            opening_key=opening_key,
            reversal_of_id=reversal_of_id,
        )
        db.add(entry)
        await db.flush()
        for account, amount, book in prepared:
            db.add(
                context.new(
                    JournalLine,
                    shop_id=shop_id,
                    entry_id=entry.id,
                    account_id=account.id,
                    amount=amount,
                    book_afn=book,
                )
            )
        await db.flush()
        db.add(context.new(JournalPosting, shop_id=shop_id, entry_id=entry.id))
        await db.flush()
        await TenantOperations.audit(
            db, context, action, "journal_entry", entry.id, reason
        )
        return (await LedgerService.entry_data(db, context, entry)).model_dump(
            mode="json"
        )

    @staticmethod
    async def post(db, context, shop_id, action, retry_key, reason, lines):
        """Post all workflow legs atomically, requiring its explicit action permission."""
        if (
            action not in LedgerService.PERMISSIONS
            or action == "opening_balance:create"
        ):
            raise AppValidationError()

        async def execute():
            return await LedgerService._write(
                db, context, shop_id, action, reason, lines
            )

        return await TenantOperations.execute_once(
            db,
            context,
            action,
            retry_key,
            {"shop_id": shop_id, "reason": reason, "lines": lines},
            LedgerService.PERMISSIONS[action],
            execute,
        )

    @staticmethod
    async def opening(db, context, shop_id, retry_key, data):
        """Post one owner-reviewed opening per cashbox/unit against equity, never income."""

        async def execute():
            await LedgerService.owner(db, context)
            if data.owner_approved is not True:
                raise AuthorizationError()
            currency = await context.reference(db, Currency, data.currency_id)
            await context.reference(db, Cashbox, data.cashbox_id, shop_id=shop_id)
            amount = decimal_text(data.amount, currency.precision)
            cost = decimal_text(data.book_afn, 2)
            if amount <= 0 or cost <= 0 or (currency.code == "AFN" and amount != cost):
                raise AppValidationError()
            opening_key = f"{data.cashbox_id}:{currency.id}"
            if await db.scalar(
                context.query(JournalEntry).where(
                    JournalEntry.opening_key == opening_key
                )
            ):
                raise ConflictError()
            # Initial holdings must be reviewed before daily activity in this box/unit.
            cash = await LedgerService.account(
                db, context, shop_id, currency.id, "cash", "cash", data.cashbox_id
            )
            if await db.scalar(
                context.query(JournalLine).where(JournalLine.account_id == cash.id)
            ):
                raise ConflictError()
            equity = await LedgerService.account(
                db, context, shop_id, currency.id, "opening_equity", "equity"
            )
            return await LedgerService._write(
                db,
                context,
                shop_id,
                "opening_balance:create",
                data.reason,
                [
                    {
                        "account_id": cash.id,
                        "amount": exact(amount, currency.precision),
                        "book_afn": exact(cost, 2),
                    },
                    {
                        "account_id": equity.id,
                        "amount": "-" + exact(amount, currency.precision),
                        "book_afn": "-" + exact(cost, 2),
                    },
                ],
                opening_key=opening_key,
            )

        return await TenantOperations.execute_once(
            db,
            context,
            "opening_balance:create",
            retry_key,
            {"shop_id": shop_id, **data.model_dump()},
            "opening_balance:create",
            execute,
        )

    @staticmethod
    async def reverse(db, context, shop_id, entry_id, retry_key, reason):
        """Owner correction with linked opposite legs; no edits or duplicate reversals."""

        async def execute():
            await LedgerService.owner(db, context)
            entry = await context.reference(db, JournalEntry, entry_id, shop_id=shop_id)
            if entry.reversal_of_id is not None or not await db.scalar(
                context.query(JournalPosting).where(JournalPosting.entry_id == entry.id)
            ):
                raise ConflictError()
            if await db.scalar(
                context.query(JournalEntry).where(
                    JournalEntry.reversal_of_id == entry.id
                )
            ):
                raise ConflictError()
            # Later workflow reversals must restore allocations/state in their own service.
            if entry.action != "opening_balance:create":
                raise ConflictError()
            lines = list(
                await db.scalars(
                    context.query(JournalLine).where(JournalLine.entry_id == entry.id)
                )
            )
            values = [
                {
                    "account_id": line.account_id,
                    "amount": str(-Decimal(line.amount)),
                    "book_afn": str(-Decimal(line.book_afn)),
                }
                for line in lines
            ]
            return await LedgerService._write(
                db,
                context,
                shop_id,
                "opening_balance:reverse",
                reason,
                values,
                reversal_of_id=entry.id,
            )

        return await TenantOperations.execute_once(
            db,
            context,
            "opening_balance:reverse",
            retry_key,
            {"shop_id": shop_id, "entry_id": str(entry_id), "reason": reason},
            "opening_balance:create",
            execute,
        )

    @staticmethod
    async def entries(
        db,
        context,
        shop_id,
        offset=0,
        limit=50,
        cashbox_id=None,
        currency_id=None,
        start=None,
        end=None,
    ):
        """Paginated posted entries; UTC cutoffs are supplied by the Kabul date adapter."""
        await TenantOperations.authorize(
            db, context, "cashbox:read" if cashbox_id is not None else "report:read"
        )
        await context.shop(db, shop_id)
        FinanceService.page(offset, limit)
        query = context.query(JournalEntry).where(
            JournalEntry.shop_id == shop_id,
            JournalEntry.id.in_(
                select(JournalPosting.entry_id).where(
                    JournalPosting.organization_id == context.organization_id
                )
            ),
        )
        if start is not None:
            query = query.where(JournalEntry.created_at >= start)
        if end is not None:
            query = query.where(JournalEntry.created_at < end)
        if cashbox_id is not None or currency_id is not None:
            accounts = (
                context.query(LedgerAccount)
                .with_only_columns(LedgerAccount.id)
                .where(LedgerAccount.shop_id == shop_id)
            )
            if cashbox_id is not None:
                box = await context.get(db, Cashbox, cashbox_id)
                if box.shop_id != shop_id:
                    raise AuthorizationError()
                accounts = accounts.where(LedgerAccount.cashbox_id == box.id)
            if currency_id is not None:
                await context.get(db, Currency, currency_id)
                accounts = accounts.where(LedgerAccount.currency_id == currency_id)
            query = query.where(
                JournalEntry.id.in_(
                    context.query(JournalLine)
                    .with_only_columns(JournalLine.entry_id)
                    .where(JournalLine.account_id.in_(accounts))
                )
            )
        return [
            await LedgerService.entry_data(db, context, entry)
            for entry in await db.scalars(
                query.order_by(JournalEntry.id).offset(offset).limit(limit)
            )
        ]
