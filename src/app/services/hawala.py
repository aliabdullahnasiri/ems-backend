"""Manual hawala lifecycle; all financial actions use one retained posting boundary."""

from datetime import UTC
from decimal import Decimal, localcontext
from uuid import uuid4

from sqlalchemy import select

from app.core.exceptions import AppValidationError, ConflictError, NotFoundError
from app.models import User
from app.models.contact import Contact
from app.models.finance import Cashbox, Currency, JournalLine, LedgerAccount
from app.models.hawala import (
    AgentObligation,
    AgentObligationVoid,
    Hawala,
    HawalaEvent,
    HawalaRefund,
)
from app.schemas.hawala.data import (
    HawalaData,
    HawalaEventData,
    HawalaReceiptData,
    HawalaTermsData,
)
from app.schemas.hawala.request import HawalaCreateRequest, HawalaUpdateRequest
from app.services.agent_settlement import AgentSettlementService
from app.services.contact_ledger import ContactLedgerService
from app.services.finance import FinanceService, LedgerService
from app.services.financial_values import decimal_text, exact
from app.services.hawala_posting import HawalaPostingService as Posting
from app.services.tenant_operations import TenantOperations


class HawalaService:
    """Separate customer funding, beneficiary payout, refund and agent settlement."""

    @staticmethod
    async def events(db, context, hawala):
        """Read ordered immutable lifecycle history."""
        return list(
            await db.scalars(
                context.query(HawalaEvent)
                .where(HawalaEvent.hawala_id == hawala.id)
                .order_by(HawalaEvent.id)
            )
        )

    @staticmethod
    def state(events):
        """Derive state from the one-time controlled transitions, never a mutable flag."""
        kinds = {e.kind for e in events}
        return (
            "paid"
            if "paid" in kinds
            else "cancelled"
            if "cancelled" in kinds
            else "pending"
            if "confirmed" in kinds
            else "draft"
        )

    @staticmethod
    async def event(db, context, hawala, kind, details, entry_id=None):
        """Record actor, evidence, time and journal link without logging identity payloads."""
        event = HawalaEvent(
            **context.audit_values(),
            shop_id=hawala.shop_id,
            hawala_id=hawala.id,
            kind=kind,
            transition_key=kind if kind in ("confirmed", "paid", "cancelled") else None,
            entry_id=entry_id,
            details=details,
        )
        db.add(event)
        await db.flush()
        await TenantOperations.audit(db, context, f"hawala:{kind}", "hawala", hawala.id)
        return event

    @staticmethod
    async def references(db, context, shop_id, data):
        """Validate draft references before allowing any record to link another shop."""
        await context.shop(db, shop_id)
        agent = await context.reference(db, Contact, data.agent_id, shop_id=shop_id)
        if not agent.is_agent:
            raise AppValidationError()
        await context.reference(db, Currency, data.collection_currency_id)
        await context.reference(db, Currency, data.payout_currency_id)
        if data.cashbox_id is not None:
            await context.reference(db, Cashbox, data.cashbox_id, shop_id=shop_id)
        return agent

    @staticmethod
    async def create(db, context, shop_id, data: HawalaCreateRequest):
        """Create a draft and reference without any journal or money movement."""
        await TenantOperations.authorize(db, context, "hawala:create")
        await LedgerService.lock(db, context)
        await HawalaService.references(db, context, shop_id, data)
        async with db.begin_nested():
            hawala = context.new(
                Hawala,
                shop_id=shop_id,
                agent_id=data.agent_id,
                cashbox_id=data.cashbox_id,
                collection_currency_id=data.collection_currency_id,
                payout_currency_id=data.payout_currency_id,
                direction=data.direction,
                reference="HW-" + uuid4().hex.upper(),
                details=data.model_dump(),
            )
            db.add(hawala)
            await db.flush()
            await HawalaService.event(
                db, context, hawala, "created", {"draft": data.model_dump()}
            )
            return await HawalaService.data(db, context, hawala)

    @staticmethod
    async def update(db, context, shop_id, hawala_id, data: HawalaUpdateRequest):
        """Patch only drafts, preserving every previous version in event history."""
        await TenantOperations.authorize(db, context, "hawala:create")
        await LedgerService.lock(db, context)
        hawala = await HawalaService.record(db, context, shop_id, hawala_id)
        if (
            HawalaService.state(await HawalaService.events(db, context, hawala))
            != "draft"
        ):
            raise ConflictError()
        updated = HawalaCreateRequest.model_validate(
            {**hawala.details, **data.model_dump(exclude_unset=True)}
        )
        await HawalaService.references(db, context, shop_id, updated)
        async with db.begin_nested():
            for field in (
                "agent_id",
                "cashbox_id",
                "collection_currency_id",
                "payout_currency_id",
            ):
                setattr(hawala, field, getattr(updated, field))
            hawala.details = updated.model_dump()
            await db.flush()
            await HawalaService.event(
                db, context, hawala, "updated", {"draft": updated.model_dump()}
            )
            return await HawalaService.data(db, context, hawala)

    @staticmethod
    async def record(db, context, shop_id, hawala_id):
        """Resolve the selected shop's transfer; IDs never replace tenant checks."""
        await context.shop(db, shop_id)
        hawala = await context.get(db, Hawala, hawala_id)
        if hawala.shop_id != shop_id:
            raise NotFoundError()
        return hawala

    @staticmethod
    async def terms(db, context, hawala):
        """Require complete names, amounts, fee policy, explicit units and agreed valuation."""
        data = HawalaCreateRequest.model_validate(hawala.details)
        await HawalaService.references(db, context, hawala.shop_id, data)
        if any(
            value is None or not value.strip()
            for value in (data.sender_name, data.recipient_name, data.destination)
        ):
            raise AppValidationError()
        collection = await context.reference(db, Currency, data.collection_currency_id)
        payout = await context.reference(db, Currency, data.payout_currency_id)
        amount = decimal_text(data.collection_amount, collection.precision)
        outgoing = decimal_text(data.payout_amount, payout.precision)
        rate = decimal_text(data.rate, 8)
        fee, agent_fee = (
            decimal_text(data.customer_fee, collection.precision),
            decimal_text(data.agent_fee, payout.precision),
        )
        if (
            amount <= 0
            or outgoing <= 0
            or rate <= 0
            or (collection.id == payout.id and rate != 1)
        ):
            raise AppValidationError()
        with localcontext() as ctx:
            ctx.prec = 60
            raw = amount * rate
            if Decimal(exact(raw, payout.precision)) != outgoing:
                raise AppValidationError()
            ca = await Posting.valuation(
                db, context, collection.id, data.collection_afn_rate
            )
            pa = await Posting.valuation(db, context, payout.id, data.payout_afn_rate)
            values = data.model_dump()
            values.update(
                collection_amount=exact(amount, collection.precision),
                payout_amount=exact(outgoing, payout.precision),
                rate=exact(rate, 8),
                customer_fee=exact(fee, collection.precision),
                agent_fee=exact(agent_fee, payout.precision),
                collection_afn_rate=exact(ca, 8),
                payout_afn_rate=exact(pa, 8),
                collection_book_afn=exact(amount * ca, 2),
                payout_book_afn=exact(outgoing * pa, 2),
                customer_fee_book_afn=exact(fee * ca, 2),
                agent_fee_book_afn=exact(agent_fee * pa, 2),
                unrounded_payout_amount=format(raw, "f"),
                rounding_adjustment=format(outgoing - raw, "f"),
                collection_currency=collection.code,
                payout_currency=payout.code,
            )
            for key in (
                "collection_book_afn",
                "payout_book_afn",
                "customer_fee_book_afn",
                "agent_fee_book_afn",
            ):
                decimal_text(values[key], 2)
            if (
                Decimal(values["collection_book_afn"]) <= 0
                or Decimal(values["payout_book_afn"]) <= 0
            ):
                raise AppValidationError()
            return HawalaTermsData.model_validate(values)

    @staticmethod
    async def agent_leg(
        db,
        context,
        hawala,
        lines,
        obligations,
        currency_id,
        direction,
        kind,
        amount,
        book,
    ):
        """Post to the existing agent contact ledger and capture the allocatable principal/fee."""
        if not amount:
            return
        agent = await context.reference(
            db, Contact, hawala.agent_id, shop_id=hawala.shop_id
        )
        account_id = await ContactLedgerService.account(
            db, context, agent, "agent", direction, currency_id
        )
        sign = 1 if direction == "receivable" else -1
        await Posting.line(
            db,
            context,
            hawala.shop_id,
            lines,
            currency_id,
            amount * sign,
            book * sign,
            account_id=account_id,
        )
        currency = await context.get(db, Currency, currency_id)
        obligations.append(
            (
                account_id,
                currency_id,
                direction,
                kind,
                exact(amount, currency.precision),
                exact(book, 2),
            )
        )

    @staticmethod
    async def transition(db, context, shop_id, hawala_id, retry_key, action, data):
        """Run a controlled transition with actor-bound retry and one financial savepoint."""
        permissions = {
            "confirm": "hawala:confirm",
            "pay": "hawala:pay",
            "cancel": "hawala:cancel",
            "refund": "hawala:refund",
            "review": "hawala:cancel",
        }
        if action not in permissions:
            raise AppValidationError()

        async def execute():
            hawala = await HawalaService.record(db, context, shop_id, hawala_id)
            events = await HawalaService.events(db, context, hawala)
            if not data.reason.strip():
                raise AppValidationError()
            with localcontext() as ctx:
                ctx.prec = 60
                await getattr(HawalaService, f"_{action}")(
                    db, context, hawala, events, data
                )
            return (await HawalaService.data(db, context, hawala)).model_dump(
                mode="json"
            )

        return await TenantOperations.execute_once(
            db,
            context,
            f"hawala:{action}",
            retry_key,
            {"shop_id": shop_id, "hawala_id": str(hawala_id), **data.model_dump()},
            permissions[action],
            execute,
        )

    @staticmethod
    async def _confirm(db, context, hawala, events, data):
        """Outgoing collects local funds; incoming records a verified partner instruction."""
        if HawalaService.state(events) != "draft":
            raise ConflictError()
        terms = await HawalaService.terms(db, context, hawala)
        if hawala.direction == "incoming" and (
            data.instruction_verified is not True
            or not (data.instruction_reference or "").strip()
        ):
            raise AppValidationError()
        lines, obligations = [], []
        c, p = terms.collection_currency_id, terms.payout_currency_id
        principal, payout = (
            Decimal(terms.collection_amount),
            Decimal(terms.payout_amount),
        )
        cb, pb = Decimal(terms.collection_book_afn), Decimal(terms.payout_book_afn)
        fee, fee_book = (
            Decimal(terms.customer_fee),
            Decimal(terms.customer_fee_book_afn),
        )
        afee, afee_book = Decimal(terms.agent_fee), Decimal(terms.agent_fee_book_afn)
        if hawala.direction == "outgoing":
            if hawala.cashbox_id is None:
                raise AppValidationError()
            await context.reference(
                db, Cashbox, hawala.cashbox_id, shop_id=hawala.shop_id
            )
            await Posting.line(
                db,
                context,
                hawala.shop_id,
                lines,
                c,
                principal + fee,
                cb + fee_book,
                "cash",
                "cash",
                hawala.cashbox_id,
            )
            await Posting.line(
                db,
                context,
                hawala.shop_id,
                lines,
                c,
                -principal,
                -cb,
                "hawala_clearing",
                "clearing",
            )
            await Posting.line(
                db,
                context,
                hawala.shop_id,
                lines,
                p,
                payout,
                pb,
                "hawala_clearing",
                "clearing",
            )
            await HawalaService.agent_leg(
                db,
                context,
                hawala,
                lines,
                obligations,
                p,
                "payable",
                "principal",
                payout,
                pb,
            )
            await Posting.spread(db, context, hawala.shop_id, lines, cb - pb)
        else:
            await HawalaService.agent_leg(
                db,
                context,
                hawala,
                lines,
                obligations,
                p,
                "receivable",
                "principal",
                payout,
                pb,
            )
            await Posting.line(
                db,
                context,
                hawala.shop_id,
                lines,
                p,
                -payout,
                -pb,
                f"hawala:{hawala.id}:beneficiary",
                "payable",
            )
            await HawalaService.agent_leg(
                db,
                context,
                hawala,
                lines,
                obligations,
                c,
                "receivable",
                "customer_fee",
                fee,
                fee_book,
            )
        await Posting.line(
            db,
            context,
            hawala.shop_id,
            lines,
            c,
            -fee,
            -fee_book,
            "hawala_customer_fee"
            if terms.customer_fee_earned_at == "confirm"
            else f"hawala:{hawala.id}:deferred_fee",
            "income" if terms.customer_fee_earned_at == "confirm" else "payable",
        )
        await HawalaService.agent_leg(
            db,
            context,
            hawala,
            lines,
            obligations,
            p,
            "payable",
            "agent_fee",
            afee,
            afee_book,
        )
        await Posting.line(
            db,
            context,
            hawala.shop_id,
            lines,
            p,
            afee,
            afee_book,
            "hawala_agent_fee"
            if terms.agent_fee_earned_at == "confirm"
            else f"hawala:{hawala.id}:prepaid_fee",
            "expense" if terms.agent_fee_earned_at == "confirm" else "clearing",
        )
        entry = await LedgerService._write(
            db, context, hawala.shop_id, "hawala:confirm", data.reason, lines
        )
        for account, currency, direction, kind, amount, book in obligations:
            await AgentSettlementService.record(
                db,
                context,
                hawala.shop_id,
                hawala.agent_id,
                entry["id"],
                account,
                currency,
                direction,
                amount,
                book,
                hawala.id,
                kind,
            )
        user = (
            await db.execute(select(User).where(User.id == context.user_id))
        ).scalar_one()
        agent = await context.get(db, Contact, hawala.agent_id)
        receipt = HawalaReceiptData(
            reference=hawala.reference,
            confirmed_at=entry["created_at"],
            operator_id=user.id,
            operator_name=user.user_name,
            agent_name=agent.name,
            shop=await Posting.shop_snapshot(db, context, hawala.shop_id),
            terms=terms,
        )
        await HawalaService.event(
            db,
            context,
            hawala,
            "confirmed",
            {**data.model_dump(), "receipt": receipt.model_dump(mode="json")},
            entry["id"],
        )

    @staticmethod
    async def _pay(db, context, hawala, events, data):
        """Partner payout is manual evidence only; a local incoming payout spends cash once."""
        if HawalaService.state(events) != "pending":
            raise ConflictError()
        if data.beneficiary_verified is not True or any(
            not v.strip()
            for v in (data.verification_reference, data.evidence_reference)
        ):
            raise AppValidationError()
        await context.reference(db, Contact, hawala.agent_id, shop_id=hawala.shop_id)
        terms = HawalaReceiptData.model_validate(
            next(e.details["receipt"] for e in events if e.kind == "confirmed")
        ).terms
        lines = []
        if hawala.direction == "incoming":
            if data.mode != "local" or data.cashbox_id is None:
                raise AppValidationError()
            await context.reference(
                db, Cashbox, data.cashbox_id, shop_id=hawala.shop_id
            )
            amount, book = Decimal(terms.payout_amount), Decimal(terms.payout_book_afn)
            cost = await Posting.cash_cost(
                db, context, data.cashbox_id, terms.payout_currency_id, amount
            )
            await Posting.line(
                db,
                context,
                hawala.shop_id,
                lines,
                terms.payout_currency_id,
                amount,
                book,
                f"hawala:{hawala.id}:beneficiary",
                "payable",
            )
            await Posting.line(
                db,
                context,
                hawala.shop_id,
                lines,
                terms.payout_currency_id,
                -amount,
                -cost,
                "cash",
                "cash",
                data.cashbox_id,
            )
            await Posting.balance_books(
                db, context, hawala.shop_id, lines, terms.payout_currency_id
            )
        elif data.mode != "partner" or data.cashbox_id is not None:
            raise AppValidationError()
        if terms.customer_fee_earned_at == "paid":
            amount, book = (
                Decimal(terms.customer_fee),
                Decimal(terms.customer_fee_book_afn),
            )
            await Posting.line(
                db,
                context,
                hawala.shop_id,
                lines,
                terms.collection_currency_id,
                amount,
                book,
                f"hawala:{hawala.id}:deferred_fee",
                "payable",
            )
            await Posting.line(
                db,
                context,
                hawala.shop_id,
                lines,
                terms.collection_currency_id,
                -amount,
                -book,
                "hawala_customer_fee",
                "income",
            )
        if terms.agent_fee_earned_at == "paid":
            amount, book = Decimal(terms.agent_fee), Decimal(terms.agent_fee_book_afn)
            await Posting.line(
                db,
                context,
                hawala.shop_id,
                lines,
                terms.payout_currency_id,
                amount,
                book,
                "hawala_agent_fee",
                "expense",
            )
            await Posting.line(
                db,
                context,
                hawala.shop_id,
                lines,
                terms.payout_currency_id,
                -amount,
                -book,
                f"hawala:{hawala.id}:prepaid_fee",
                "clearing",
            )
        entry = (
            await LedgerService._write(
                db, context, hawala.shop_id, "hawala:pay", data.reason, lines
            )
            if lines
            else None
        )
        await HawalaService.event(
            db,
            context,
            hawala,
            "paid",
            data.model_dump(),
            entry["id"] if entry else None,
        )

    @staticmethod
    async def _cancel(db, context, hawala, events, data):
        """Cancel unpaid instruction; establish refund liability without moving cash."""
        state = HawalaService.state(events)
        if state not in ("draft", "pending"):
            raise ConflictError()
        if state == "draft":
            await HawalaService.event(
                db,
                context,
                hawala,
                "cancelled",
                {**data.model_dump(), "refund_due": "0.00", "refund_book_afn": "0.00"},
            )
            return
        if hawala.direction == "outgoing" and (
            data.partner_not_paid is not True
            or not (data.partner_confirmation_reference or "").strip()
        ):
            raise AppValidationError()
        confirmed = next(e for e in events if e.kind == "confirmed")
        terms = HawalaReceiptData.model_validate(confirmed.details["receipt"]).terms
        obligations = list(
            await db.scalars(
                context.query(AgentObligation).where(
                    AgentObligation.hawala_id == hawala.id
                )
            )
        )
        for obligation in obligations:
            if Decimal(
                (
                    await AgentSettlementService.obligation_data(
                        db, context, obligation
                    )
                ).settled_amount
            ):
                raise ConflictError()
        lines = []
        # Undo accounting effects, replacing outgoing cash credit with refund liability.
        rows = await db.scalars(
            context.query(JournalLine).where(JournalLine.entry_id == confirmed.entry_id)
        )
        for row in rows:
            account = await context.get(db, LedgerAccount, row.account_id)
            if account.kind != "cash":
                lines.append(
                    {
                        "account_id": account.id,
                        "amount": format(-Decimal(row.amount), "f"),
                        "book_afn": format(-Decimal(row.book_afn), "f"),
                    }
                )
        refund, refund_book = Decimal(0), Decimal(0)
        c, p = terms.collection_currency_id, terms.payout_currency_id
        fee, fee_book = (
            Decimal(terms.customer_fee),
            Decimal(terms.customer_fee_book_afn),
        )
        afee, afee_book = Decimal(terms.agent_fee), Decimal(terms.agent_fee_book_afn)
        if hawala.direction == "outgoing":
            refund = Decimal(terms.collection_amount) + (
                fee if terms.customer_fee_refundable else 0
            )
            refund_book = Decimal(terms.collection_book_afn) + (
                fee_book if terms.customer_fee_refundable else 0
            )
            await Posting.line(
                db,
                context,
                hawala.shop_id,
                lines,
                c,
                -refund,
                -refund_book,
                f"hawala:{hawala.id}:refund",
                "payable",
            )
        if not terms.customer_fee_refundable:
            if hawala.direction == "incoming":
                agent = await context.reference(
                    db, Contact, hawala.agent_id, shop_id=hawala.shop_id
                )
                account = await ContactLedgerService.account(
                    db, context, agent, "agent", "receivable", c
                )
                await Posting.line(
                    db,
                    context,
                    hawala.shop_id,
                    lines,
                    c,
                    fee,
                    fee_book,
                    account_id=account,
                )
            await Posting.line(
                db,
                context,
                hawala.shop_id,
                lines,
                c,
                -fee,
                -fee_book,
                "hawala_customer_fee",
                "income",
            )
        if not terms.agent_fee_refundable:
            agent = await context.reference(
                db, Contact, hawala.agent_id, shop_id=hawala.shop_id
            )
            account = await ContactLedgerService.account(
                db, context, agent, "agent", "payable", p
            )
            await Posting.line(
                db,
                context,
                hawala.shop_id,
                lines,
                p,
                -afee,
                -afee_book,
                account_id=account,
            )
            await Posting.line(
                db,
                context,
                hawala.shop_id,
                lines,
                p,
                afee,
                afee_book,
                "hawala_agent_fee",
                "expense",
            )
        entry = await LedgerService._write(
            db,
            context,
            hawala.shop_id,
            "hawala:cancel",
            data.reason,
            lines,
            reversal_of_id=confirmed.entry_id,
        )
        for obligation in obligations:
            retained = (
                obligation.kind == "agent_fee" and not terms.agent_fee_refundable
            ) or (
                obligation.kind == "customer_fee" and not terms.customer_fee_refundable
            )
            if not retained:
                db.add(
                    context.new(
                        AgentObligationVoid,
                        shop_id=hawala.shop_id,
                        obligation_id=obligation.id,
                        entry_id=entry["id"],
                    )
                )
        await db.flush()
        currency = await context.get(db, Currency, c)
        await HawalaService.event(
            db,
            context,
            hawala,
            "cancelled",
            {
                **data.model_dump(),
                "refund_due": exact(refund, currency.precision),
                "refund_book_afn": exact(refund_book, 2),
                "refund_currency": currency.code,
            },
            entry["id"],
        )

    @staticmethod
    async def _refund(db, context, hawala, events, data):
        """Pay an actual partial/full refund once, without exceeding the stored liability."""
        if HawalaService.state(events) != "cancelled" or hawala.direction != "outgoing":
            raise ConflictError()
        cancelled = next(e for e in events if e.kind == "cancelled")
        currency = await context.reference(db, Currency, hawala.collection_currency_id)
        await context.reference(db, Cashbox, data.cashbox_id, shop_id=hawala.shop_id)
        if not data.evidence_reference.strip():
            raise AppValidationError()
        previous = list(
            await db.scalars(
                context.query(HawalaRefund).where(HawalaRefund.hawala_id == hawala.id)
            )
        )
        remaining = Decimal(cancelled.details["refund_due"]) - sum(
            (Decimal(r.amount) for r in previous), Decimal(0)
        )
        remaining_book = Decimal(cancelled.details["refund_book_afn"]) - sum(
            (Decimal(r.book_afn) for r in previous), Decimal(0)
        )
        amount = decimal_text(data.amount, currency.precision)
        if amount <= 0:
            raise AppValidationError()
        if amount > remaining:
            raise ConflictError()
        book = (
            remaining_book
            if amount == remaining
            else Decimal(exact(remaining_book * amount / remaining, 2))
        )
        cost = await Posting.cash_cost(
            db, context, data.cashbox_id, currency.id, amount
        )
        lines = []
        await Posting.line(
            db,
            context,
            hawala.shop_id,
            lines,
            currency.id,
            amount,
            book,
            f"hawala:{hawala.id}:refund",
            "payable",
        )
        await Posting.line(
            db,
            context,
            hawala.shop_id,
            lines,
            currency.id,
            -amount,
            -cost,
            "cash",
            "cash",
            data.cashbox_id,
        )
        await Posting.balance_books(db, context, hawala.shop_id, lines, currency.id)
        entry = await LedgerService._write(
            db, context, hawala.shop_id, "hawala:refund", data.reason, lines
        )
        db.add(
            context.new(
                HawalaRefund,
                shop_id=hawala.shop_id,
                hawala_id=hawala.id,
                entry_id=entry["id"],
                amount=exact(amount, currency.precision),
                book_afn=exact(book, 2),
            )
        )
        await db.flush()
        await HawalaService.event(
            db,
            context,
            hawala,
            "refunded",
            {**data.model_dump(), "amount": exact(amount, currency.precision)},
            entry["id"],
        )

    @staticmethod
    async def _review(db, context, hawala, events, data):
        """Retain an owner's disputed/paid-transfer assessment without inventing a payout undo."""
        await LedgerService.owner(db, context)
        if (
            data.owner_approved is not True
            or HawalaService.state(events) not in ("paid", "cancelled")
            or not data.evidence_reference.strip()
        ):
            raise AppValidationError()
        await HawalaService.event(db, context, hawala, "reviewed", data.model_dump())

    @staticmethod
    async def data(db, context, hawala):
        """Return original terms/receipt plus independent payout, settlement and refund history."""
        events = await HawalaService.events(db, context, hawala)
        state = HawalaService.state(events)
        confirmed = next((e for e in events if e.kind == "confirmed"), None)
        cancelled = next((e for e in events if e.kind == "cancelled"), None)
        obligations = [
            await AgentSettlementService.obligation_data(db, context, o)
            for o in await db.scalars(
                context.query(AgentObligation)
                .where(AgentObligation.hawala_id == hawala.id)
                .order_by(AgentObligation.id)
            )
        ]
        active = [o for o in obligations if not o.is_cancelled]
        settlement_status = (
            "none"
            if not active
            else "settled"
            if all(Decimal(o.remaining_amount) == 0 for o in active)
            else "partial"
            if any(Decimal(o.settled_amount) for o in active)
            else "outstanding"
        )
        refunds = list(
            await db.scalars(
                context.query(HawalaRefund).where(HawalaRefund.hawala_id == hawala.id)
            )
        )
        currency = await context.get(db, Currency, hawala.collection_currency_id)
        with localcontext() as ctx:
            ctx.prec = 60
            due = (
                Decimal(cancelled.details.get("refund_due", "0"))
                if cancelled
                else Decimal(0)
            )
            paid = sum((Decimal(row.amount) for row in refunds), Decimal(0))
            remaining = due - paid
        history = []
        for e in events:
            values = e.to_dict()
            if e.created_at.tzinfo is None:
                values["created_at"] = e.created_at.replace(tzinfo=UTC)
            history.append(HawalaEventData.model_validate(values))
        return HawalaData(
            id=hawala.id,
            uuid=hawala.uuid,
            reference=hawala.reference,
            shop_id=hawala.shop_id,
            state=state,
            payout_status="paid"
            if state == "paid"
            else "pending"
            if state == "pending"
            else "not_payable",
            settlement_status=settlement_status,
            draft=HawalaCreateRequest.model_validate(hawala.details),
            receipt=HawalaReceiptData.model_validate(confirmed.details["receipt"])
            if confirmed
            else None,
            refund_due=exact(due, currency.precision),
            refund_paid=exact(paid, currency.precision),
            refund_remaining=exact(remaining, currency.precision),
            refund_currency=currency.code
            if cancelled and hawala.direction == "outgoing"
            else None,
            obligations=obligations,
            history=history,
        )

    @staticmethod
    async def get(db, context, shop_id, hawala_id):
        """Read authorized transfer history, including inactive agent/currency metadata."""
        await TenantOperations.authorize(db, context, "hawala:read")
        return await HawalaService.data(
            db, context, await HawalaService.record(db, context, shop_id, hawala_id)
        )

    @staticmethod
    async def list(
        db,
        context,
        shop_id,
        offset=0,
        limit=50,
        direction=None,
        agent_id=None,
        reference=None,
        date_from=None,
        date_to=None,
    ):
        """Search transfer references, directions, agents and inclusive Kabul creation dates."""
        from app.services.exchange import ExchangeService

        await TenantOperations.authorize(db, context, "hawala:read")
        await context.shop(db, shop_id)
        FinanceService.page(offset, limit)
        query = context.query(Hawala).where(Hawala.shop_id == shop_id)
        if direction is not None:
            if direction not in ("incoming", "outgoing"):
                raise AppValidationError()
            query = query.where(Hawala.direction == direction)
        if agent_id is not None:
            agent = await context.get(db, Contact, agent_id)
            if agent.shop_id != shop_id:
                raise NotFoundError()
            query = query.where(Hawala.agent_id == agent_id)
        if reference:
            query = query.where(Hawala.reference.contains(reference, autoescape=True))
        query = ExchangeService.dates(query, Hawala, date_from, date_to)
        return [
            await HawalaService.data(db, context, row)
            for row in await db.scalars(
                query.order_by(Hawala.id.desc()).offset(offset).limit(limit)
            )
        ]
