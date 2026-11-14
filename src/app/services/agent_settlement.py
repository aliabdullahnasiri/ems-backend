"""Agent debts, explicit allocations, manual settlement and protected reversals."""

from datetime import UTC
from decimal import Decimal, localcontext
from uuid import uuid4

from app.core.exceptions import AppValidationError, ConflictError, NotFoundError
from app.models.contact import Contact
from app.models.finance import Cashbox, Currency
from app.models.hawala import (
    AgentAllocation,
    AgentObligation,
    AgentObligationVoid,
    AgentSettlement,
    AgentSettlementReversal,
)
from app.schemas.hawala.data import AgentObligationData, AgentSettlementData
from app.schemas.hawala.request import AgentSettlementCreateRequest
from app.schemas.payment.request import PaymentReverseRequest
from app.services.contact import ContactService
from app.services.finance import FinanceService, LedgerService
from app.services.financial_values import decimal_text, exact
from app.services.hawala_posting import HawalaPostingService as Posting
from app.services.tenant_operations import TenantOperations


class AgentSettlementService:
    """Gross receivables/payables stay separate by agent, currency and obligation."""

    @staticmethod
    async def record(
        db,
        context,
        shop_id,
        agent_id,
        entry_id,
        account_id,
        currency_id,
        direction,
        amount,
        book_afn,
        hawala_id=None,
        kind="opening",
    ):
        """Create an obligation in the source journal's posting savepoint."""
        obligation = context.new(
            AgentObligation,
            shop_id=shop_id,
            agent_id=agent_id,
            source_entry_id=entry_id,
            account_id=account_id,
            currency_id=currency_id,
            direction=direction,
            amount=amount,
            book_afn=book_afn,
            hawala_id=hawala_id,
            kind=kind,
        )
        db.add(obligation)
        await db.flush()
        return obligation

    @staticmethod
    async def obligation_data(db, context, obligation):
        """Count only live settlement allocations; retain cancellations and reversal history."""
        currency = await context.get(db, Currency, obligation.currency_id)
        cancelled = bool(
            await db.scalar(
                context.query(AgentObligationVoid).where(
                    AgentObligationVoid.obligation_id == obligation.id
                )
            )
        )
        rows = (
            await db.execute(
                context.query(AgentAllocation, AgentSettlement)
                .add_columns(AgentSettlement)
                .join(
                    AgentSettlement, AgentSettlement.id == AgentAllocation.settlement_id
                )
                .where(AgentAllocation.obligation_id == obligation.id)
                .order_by(AgentSettlement.id)
            )
        ).all()
        with localcontext() as ctx:
            ctx.prec = 60
            settled, book, history = Decimal(0), Decimal(0), []
            for allocation, settlement in rows:
                reverse = await db.scalar(
                    context.query(AgentSettlementReversal).where(
                        AgentSettlementReversal.settlement_id == settlement.id
                    )
                )
                if reverse is None:
                    settled += Decimal(allocation.amount)
                    book += Decimal(allocation.book_afn)
                instant = settlement.created_at
                history.append(
                    {
                        "settlement_id": settlement.id,
                        "reference": settlement.reference,
                        "amount": allocation.amount,
                        "book_afn": allocation.book_afn,
                        "created_at": (
                            instant.replace(tzinfo=UTC)
                            if instant.tzinfo is None
                            else instant
                        ).isoformat(),
                        "reversal_entry_id": reverse.entry_id if reverse else None,
                    }
                )
            remaining = Decimal(obligation.amount) - settled
            cost = Decimal(obligation.book_afn) - book
            if remaining < 0 or cost < 0:
                raise ConflictError()
            return AgentObligationData(
                id=obligation.id,
                uuid=obligation.uuid,
                agent_id=obligation.agent_id,
                hawala_id=obligation.hawala_id,
                source_entry_id=obligation.source_entry_id,
                currency_id=currency.id,
                currency=currency.code,
                direction=obligation.direction,
                kind=obligation.kind,
                original_amount=obligation.amount,
                settled_amount=exact(settled, currency.precision),
                remaining_amount=exact(
                    Decimal(0) if cancelled else remaining, currency.precision
                ),
                remaining_book_afn=exact(Decimal(0) if cancelled else cost, 2),
                is_cancelled=cancelled,
                settlements=history,
            )

    @staticmethod
    async def outstanding(
        db, context, agent_uuid, offset=0, limit=50, include_settled=False
    ):
        """Read agent obligations, including retained openings, with bounded pagination."""
        await TenantOperations.authorize(db, context, "contact:read")
        agent = await ContactService.get(db, context, agent_uuid)
        if not agent.is_agent:
            raise AppValidationError()
        FinanceService.page(offset, limit)
        result = []
        for obligation in await db.scalars(
            context.query(AgentObligation)
            .where(
                AgentObligation.agent_id == agent.id,
                AgentObligation.shop_id == agent.shop_id,
            )
            .order_by(AgentObligation.id)
        ):
            data = await AgentSettlementService.obligation_data(db, context, obligation)
            if include_settled or Decimal(data.remaining_amount):
                result.append(data)
        return result[offset : offset + limit]

    @staticmethod
    async def data(db, context, settlement):
        """Expose original evidence/receipt and current reversal status separately."""
        reverse = await db.scalar(
            context.query(AgentSettlementReversal).where(
                AgentSettlementReversal.settlement_id == settlement.id
            )
        )
        return AgentSettlementData(
            id=settlement.id,
            uuid=settlement.uuid,
            shop_id=settlement.shop_id,
            entry_id=settlement.entry_id,
            reference=settlement.reference,
            details=settlement.details,
            reversal_entry_id=reverse.entry_id if reverse else None,
        )

    @staticmethod
    async def create(
        db, context, shop_id, retry_key, data: AgentSettlementCreateRequest
    ):
        """Allocate exact same-currency payments; bank/other never imply local cash movement."""

        async def execute():
            await context.shop(db, shop_id)
            agent = await context.reference(db, Contact, data.agent_id, shop_id=shop_id)
            if not agent.is_agent:
                raise AppValidationError()
            currency = await context.reference(db, Currency, data.currency_id)
            amount = decimal_text(data.amount, currency.precision)
            if amount <= 0 or len({a.obligation_id for a in data.allocations}) != len(
                data.allocations
            ):
                raise AppValidationError()
            if any(
                not value.strip()
                for value in (data.external_reference, data.evidence_reference)
            ):
                raise AppValidationError()
            if data.method == "cash":
                if data.cashbox_id is None:
                    raise AppValidationError()
                await context.reference(db, Cashbox, data.cashbox_id, shop_id=shop_id)
            elif data.cashbox_id is not None:
                raise AppValidationError()
            prepared = []
            with localcontext() as ctx:
                ctx.prec = 60
                for item in data.allocations:
                    obligation = await context.get(
                        db, AgentObligation, item.obligation_id
                    )
                    if obligation.agent_id != agent.id or obligation.shop_id != shop_id:
                        raise NotFoundError()
                    if (
                        obligation.currency_id != currency.id
                        or obligation.direction
                        != ("receivable" if data.direction == "receive" else "payable")
                    ):
                        raise AppValidationError()
                    state = await AgentSettlementService.obligation_data(
                        db, context, obligation
                    )
                    allocated = decimal_text(item.amount, currency.precision)
                    if allocated <= 0:
                        raise AppValidationError()
                    if state.is_cancelled or allocated > Decimal(
                        state.remaining_amount
                    ):
                        raise ConflictError()
                    cost = (
                        Decimal(state.remaining_book_afn)
                        if allocated == Decimal(state.remaining_amount)
                        else Decimal(
                            exact(
                                Decimal(state.remaining_book_afn)
                                * allocated
                                / Decimal(state.remaining_amount),
                                2,
                            )
                        )
                    )
                    prepared.append((obligation, allocated, cost))
                if sum((a for _, a, _ in prepared), Decimal(0)) != amount:
                    raise AppValidationError()
                released = sum((b for _, _, b in prepared), Decimal(0))
                cost = (
                    await Posting.cash_cost(
                        db, context, data.cashbox_id, currency.id, amount
                    )
                    if data.method == "cash" and data.direction == "pay"
                    else released
                )
                sign = 1 if data.direction == "receive" else -1
                lines = []
                await Posting.line(
                    db,
                    context,
                    shop_id,
                    lines,
                    currency.id,
                    amount * sign,
                    cost * sign,
                    "cash"
                    if data.method == "cash"
                    else f"agent_external:{data.method}",
                    "cash" if data.method == "cash" else "clearing",
                    data.cashbox_id,
                )
                for obligation, allocated, book in prepared:
                    await Posting.line(
                        db,
                        context,
                        shop_id,
                        lines,
                        currency.id,
                        -allocated * sign,
                        -book * sign,
                        account_id=obligation.account_id,
                    )
                profit = await Posting.balance_books(
                    db, context, shop_id, lines, currency.id
                )
            entry = await LedgerService._write(
                db, context, shop_id, "agent_settlement:create", data.reason, lines
            )
            reference = "AS-" + uuid4().hex.upper()
            details = {
                **data.model_dump(),
                "amount": exact(amount, currency.precision),
                "currency": currency.code,
                "reference": reference,
                "created_at": entry["created_at"],
                "operator_id": context.user_id,
                "agent_name": agent.name,
                "book_afn": exact(cost, 2),
                "profit_afn": exact(profit, 2),
                "shop": (await Posting.shop_snapshot(db, context, shop_id)).model_dump(
                    mode="json"
                ),
            }
            settlement = AgentSettlement(
                **context.audit_values(),
                shop_id=shop_id,
                agent_id=agent.id,
                currency_id=currency.id,
                cashbox_id=data.cashbox_id,
                entry_id=entry["id"],
                direction=data.direction,
                method=data.method,
                amount=exact(amount, currency.precision),
                reference=reference,
                details=details,
            )
            db.add(settlement)
            await db.flush()
            for obligation, allocated, book in prepared:
                db.add(
                    context.new(
                        AgentAllocation,
                        shop_id=shop_id,
                        settlement_id=settlement.id,
                        obligation_id=obligation.id,
                        amount=exact(allocated, currency.precision),
                        book_afn=exact(book, 2),
                    )
                )
            await db.flush()
            return (
                await AgentSettlementService.data(db, context, settlement)
            ).model_dump(mode="json")

        return await TenantOperations.execute_once(
            db,
            context,
            "agent_settlement:create",
            retry_key,
            {"shop_id": shop_id, **data.model_dump()},
            "agent_settlement:create",
            execute,
        )

    @staticmethod
    async def get(db, context, shop_id, settlement_id):
        """Read settlement evidence within the verified shop."""
        await TenantOperations.authorize(db, context, "contact:read")
        await context.shop(db, shop_id)
        settlement = await context.get(db, AgentSettlement, settlement_id)
        if settlement.shop_id != shop_id:
            raise NotFoundError()
        return await AgentSettlementService.data(db, context, settlement)

    @staticmethod
    async def list(
        db,
        context,
        shop_id,
        offset=0,
        limit=50,
        agent_id=None,
        date_from=None,
        date_to=None,
    ):
        """Read settlement history with Kabul dates and explicit agent ownership checks."""
        from app.services.exchange import ExchangeService

        await TenantOperations.authorize(db, context, "contact:read")
        await context.shop(db, shop_id)
        FinanceService.page(offset, limit)
        query = context.query(AgentSettlement).where(AgentSettlement.shop_id == shop_id)
        if agent_id is not None:
            agent = await context.get(db, Contact, agent_id)
            if agent.shop_id != shop_id:
                raise NotFoundError()
            query = query.where(AgentSettlement.agent_id == agent_id)
        query = ExchangeService.dates(query, AgentSettlement, date_from, date_to)
        return [
            await AgentSettlementService.data(db, context, row)
            for row in await db.scalars(
                query.order_by(AgentSettlement.id.desc()).offset(offset).limit(limit)
            )
        ]

    @staticmethod
    async def reverse(
        db, context, shop_id, settlement_id, retry_key, data: PaymentReverseRequest
    ):
        """Owner-reviewed linked reversal restores allocations; spent cash blocks correction."""

        async def execute():
            await LedgerService.owner(db, context)
            if data.owner_approved is not True:
                raise AppValidationError()
            await context.shop(db, shop_id)
            settlement = await context.get(db, AgentSettlement, settlement_id)
            if settlement.shop_id != shop_id:
                raise NotFoundError()
            if await db.scalar(
                context.query(AgentSettlementReversal).where(
                    AgentSettlementReversal.settlement_id == settlement.id
                )
            ):
                raise ConflictError()
            lines = await Posting.opposite(db, context, settlement.entry_id)
            entry = await LedgerService._write(
                db,
                context,
                shop_id,
                "agent_settlement:reverse",
                data.reason,
                lines,
                reversal_of_id=settlement.entry_id,
            )
            db.add(
                context.new(
                    AgentSettlementReversal,
                    shop_id=shop_id,
                    settlement_id=settlement.id,
                    entry_id=entry["id"],
                )
            )
            await db.flush()
            return entry

        return await TenantOperations.execute_once(
            db,
            context,
            "agent_settlement:reverse",
            retry_key,
            {
                "shop_id": shop_id,
                "settlement_id": str(settlement_id),
                **data.model_dump(),
            },
            "agent_settlement:reverse",
            execute,
        )
