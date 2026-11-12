"""Contact obligations use Phase 6's sealed, balanced journal exclusively.

Positive statement amounts increase what the contact owes the shop; negative
amounts increase what the shop owes the contact. Receivable and payable accounts
remain separate, even when a net value is displayed. No float or SQL SUM is used.
"""

import csv
import io
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppValidationError, AuthorizationError, ConflictError
from app.models.contact import ContactAccount
from app.models.finance import Currency, JournalEntry, JournalLine, JournalPosting
from app.schemas.contact.data import (
    ContactBalanceData,
    ContactMovementData,
    ContactStatementData,
)
from app.schemas.contact.request import ContactOpeningRequest
from app.services.contact import ContactService
from app.services.finance import LedgerService
from app.services.financial_values import day_cutoffs, decimal_text, exact
from app.services.organization_context import OrganizationContext
from app.services.tenant_operations import TenantOperations


class ContactLedgerService:
    """Role/currency accounting dimensions and reproducible contact statements."""

    @staticmethod
    async def account(
        db: AsyncSession,
        context: OrganizationContext,
        contact,
        role: str,
        direction: str,
        currency_id: int,
    ):
        """Resolve an immutable contact account for an authorized posting service."""
        await TenantOperations.authorize(db, context)
        await LedgerService.lock(db, context)
        await context.reference(db, type(contact), contact.id, shop_id=contact.shop_id)
        if role not in ("customer", "agent") or direction not in (
            "receivable",
            "payable",
        ):
            raise AppValidationError()
        if not getattr(contact, f"is_{role}"):
            raise AppValidationError()
        currency = await context.reference(db, Currency, currency_id)
        link = await db.scalar(
            context.query(ContactAccount).where(
                ContactAccount.contact_id == contact.id,
                ContactAccount.role == role,
                ContactAccount.direction == direction,
                ContactAccount.currency_id == currency.id,
            )
        )
        if link:
            return link.account_id
        ledger = await LedgerService.account(
            db,
            context,
            contact.shop_id,
            currency.id,
            f"contact:{contact.id}:{role}:{direction}",
            direction,
        )
        db.add(
            context.new(
                ContactAccount,
                shop_id=contact.shop_id,
                contact_id=contact.id,
                account_id=ledger.id,
                role=role,
                direction=direction,
                currency_id=currency.id,
            )
        )
        await db.flush()
        return ledger.id

    @staticmethod
    async def opening(
        db: AsyncSession,
        context: OrganizationContext,
        contact_uuid: UUID,
        retry_key: str,
        data: ContactOpeningRequest,
    ) -> dict:
        """Post one reviewed opening per contact/role/direction/currency, idempotently."""

        async def execute():
            await LedgerService.owner(db, context)
            if data.owner_approved is not True:
                raise AuthorizationError()
            contact = await ContactService.get(db, context, contact_uuid)
            if not contact.is_active:
                raise AuthorizationError()
            currency = await context.reference(db, Currency, data.currency_id)
            amount = decimal_text(data.amount, currency.precision)
            cost = decimal_text(data.book_afn, 2)
            if amount <= 0 or cost <= 0 or (currency.code == "AFN" and amount != cost):
                raise AppValidationError()
            account_id = await ContactLedgerService.account(
                db, context, contact, data.role, data.direction, currency.id
            )
            opening_key = (
                f"contact:{contact.id}:{data.role}:{data.direction}:{currency.id}"
            )
            if await db.scalar(
                context.query(JournalEntry).where(
                    JournalEntry.opening_key == opening_key
                )
            ):
                raise ConflictError()
            # Openings precede any activity in this obligation dimension.
            if await db.scalar(
                context.query(JournalLine).where(JournalLine.account_id == account_id)
            ):
                raise ConflictError()
            equity = await LedgerService.account(
                db, context, contact.shop_id, currency.id, "opening_equity", "equity"
            )
            if data.direction == "payable":
                amount, cost = -amount, -cost
            return await LedgerService._write(
                db,
                context,
                contact.shop_id,
                "contact:opening",
                data.reason,
                [
                    {
                        "account_id": account_id,
                        "amount": exact(amount, currency.precision),
                        "book_afn": exact(cost, 2),
                    },
                    {
                        "account_id": equity.id,
                        "amount": exact(-amount, currency.precision),
                        "book_afn": exact(-cost, 2),
                    },
                ],
                opening_key=opening_key,
            )

        return await TenantOperations.execute_once(
            db,
            context,
            "contact:opening",
            retry_key,
            {"contact_uuid": str(contact_uuid), **data.model_dump()},
            "opening_balance:create",
            execute,
        )

    @staticmethod
    async def rows(db: AsyncSession, context: OrganizationContext, contact):
        """Read only sealed contact journal lines with explicit tenant/shop joins."""
        query = (
            select(JournalLine, JournalEntry, ContactAccount, Currency)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .join(JournalPosting, JournalPosting.entry_id == JournalEntry.id)
            .join(ContactAccount, ContactAccount.account_id == JournalLine.account_id)
            .join(Currency, Currency.id == ContactAccount.currency_id)
            .where(
                ContactAccount.contact_id == contact.id,
                *(
                    model.organization_id == context.organization_id
                    for model in (
                        JournalLine,
                        JournalEntry,
                        JournalPosting,
                        ContactAccount,
                        Currency,
                    )
                ),
                *(
                    model.shop_id == contact.shop_id
                    for model in (
                        JournalLine,
                        JournalEntry,
                        JournalPosting,
                        ContactAccount,
                    )
                ),
            )
            .order_by(JournalEntry.created_at, JournalEntry.id, JournalLine.id)
        )
        return (await db.execute(query)).all()

    @staticmethod
    def balance_data(totals: dict, precisions: dict) -> list[ContactBalanceData]:
        """Return gross directions; signed net is receivable minus payable."""
        result = []
        with localcontext() as ctx:
            ctx.prec = 60
            for (role, code), values in sorted(totals.items()):
                receivable, payable = (
                    values.get("receivable", Decimal(0)),
                    -values.get("payable", Decimal(0)),
                )
                result.append(
                    ContactBalanceData(
                        role=role,
                        currency=code,
                        receivable=exact(receivable, precisions[code]),
                        payable=exact(payable, precisions[code]),
                        net_receivable=exact(receivable - payable, precisions[code]),
                    )
                )
        return result

    @staticmethod
    async def statement(
        db: AsyncSession,
        context: OrganizationContext,
        contact_uuid: UUID,
        date_from: date | None = None,
        date_to: date | None = None,
        offset: int = 0,
        limit: int = 100,
        *,
        export: bool = False,
    ) -> ContactStatementData:
        """Inclusive Kabul dates; totals cover the full period despite movement paging."""
        await TenantOperations.authorize(
            db, context, "contact:read", *(("report:read",) if export else ())
        )
        if date_from and date_to and date_from > date_to:
            raise AppValidationError()
        if offset < 0 or not 1 <= limit <= 1000:
            raise AppValidationError()
        contact = await ContactService.get(db, context, contact_uuid)
        shop = await context.shop(db, contact.shop_id)
        start = day_cutoffs(date_from)[0] if date_from else None
        end = day_cutoffs(date_to)[1] if date_to else None
        rows = await ContactLedgerService.rows(db, context, contact)
        totals, opening, precisions, movements = {}, {}, {}, []
        with localcontext() as ctx:
            ctx.prec = 60
            for line, entry, link, currency in rows:
                instant = entry.created_at
                if instant.tzinfo is None:
                    instant = instant.replace(tzinfo=UTC)
                if end and instant >= end:
                    continue
                key = (link.role, currency.code)
                precisions[currency.code] = currency.precision
                values = totals.setdefault(key, {})
                values[link.direction] = values.get(
                    link.direction, Decimal(0)
                ) + Decimal(line.amount)
                if start and instant < start:
                    opening[key] = dict(values)
                    continue
                net = values.get("receivable", Decimal(0)) + values.get(
                    "payable", Decimal(0)
                )
                movements.append(
                    ContactMovementData(
                        entry_id=entry.id,
                        line_id=line.id,
                        created_at=instant.isoformat(),
                        action=entry.action,
                        reason=entry.reason,
                        role=link.role,
                        direction=link.direction,
                        currency=currency.code,
                        amount=line.amount,
                        book_afn=line.book_afn,
                        running_net_receivable=exact(net, currency.precision),
                    )
                )
        # Include explicit zero opening totals for currencies first seen in the period.
        for key in totals:
            opening.setdefault(key, {})
        return ContactStatementData(
            contact_uuid=contact.uuid,
            contact_name=contact.name,
            shop_name=shop.name,
            date_from=date_from.isoformat() if date_from else None,
            date_to=date_to.isoformat() if date_to else None,
            generated_at=datetime.now(UTC).isoformat(),
            opening_balances=ContactLedgerService.balance_data(opening, precisions),
            movements=movements if export else movements[offset : offset + limit],
            closing_balances=ContactLedgerService.balance_data(totals, precisions),
            offset=0 if export else offset,
            limit=len(movements) if export else limit,
            total_movements=len(movements),
        )

    @staticmethod
    async def balances(
        db: AsyncSession, context: OrganizationContext, contact_uuid: UUID
    ) -> list[ContactBalanceData]:
        """Derive all role/currency balances from posted ledger lines only."""
        return (
            await ContactLedgerService.statement(db, context, contact_uuid)
        ).closing_balances

    @staticmethod
    async def export(
        db: AsyncSession,
        context: OrganizationContext,
        contact_uuid: UUID,
        date_from: date | None,
        date_to: date | None,
    ) -> str:
        """Export a complete Unicode CSV statement with safe spreadsheet cells."""
        statement = await ContactLedgerService.statement(
            db, context, contact_uuid, date_from, date_to, export=True
        )
        output = io.StringIO(newline="")
        writer = csv.writer(output)

        def safe(value):
            text = str(value or "")
            return (
                "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text
            )

        writer.writerow(
            [
                "shop",
                safe(statement.shop_name),
                "contact",
                safe(statement.contact_name),
                "contact_uuid",
                str(statement.contact_uuid),
            ]
        )
        writer.writerow(
            [
                "date_from",
                statement.date_from,
                "date_to",
                statement.date_to,
                "timezone",
                statement.timezone,
                "generated_at",
                statement.generated_at,
            ]
        )
        writer.writerow(
            [
                "type",
                "role",
                "currency",
                "receivable",
                "payable",
                "net_receivable",
                "entry_id",
                "line_id",
                "created_at",
                "direction",
                "amount",
                "book_afn",
                "reason",
            ]
        )
        for balance in statement.opening_balances:
            writer.writerow(
                [
                    "opening",
                    balance.role,
                    balance.currency,
                    balance.receivable,
                    balance.payable,
                    balance.net_receivable,
                ]
            )
        for movement in statement.movements:
            writer.writerow(
                [
                    "movement",
                    movement.role,
                    movement.currency,
                    "",
                    "",
                    movement.running_net_receivable,
                    movement.entry_id,
                    movement.line_id,
                    movement.created_at,
                    movement.direction,
                    movement.amount,
                    movement.book_afn,
                    safe(movement.reason),
                ]
            )
        for balance in statement.closing_balances:
            writer.writerow(
                [
                    "closing",
                    balance.role,
                    balance.currency,
                    balance.receivable,
                    balance.payable,
                    balance.net_receivable,
                ]
            )
        return output.getvalue()
