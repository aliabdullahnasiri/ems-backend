"""Call services directly so HTTP dependencies cannot hide missing authorization."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.exceptions import AuthorizationError, NotFoundError
from app.models import OrganizationUser, Role
from app.services.agent_settlement import AgentSettlementService
from app.services.contact import ContactService
from app.services.contact_ledger import ContactLedgerService
from app.services.daily import ClosingService, DailyService
from app.services.exchange import ExchangeService
from app.services.finance import FinanceService, LedgerService
from app.services.hawala import HawalaService
from app.services.obligation import ObligationService
from app.services.organization import OrganizationService
from app.services.organization_context import OrganizationContext
from app.services.payment import PaymentService
from app.services.reports import ReportService
from app.services.role import RoleService
from app.services.shop import ShopService
from app.services.tenant_operations import TenantOperations
from tests.services.test_tenant_operations import tenants as tenants  # noqa: PLC0414

ADMIN_ACTIONS = [
    "organization_get",
    "organization_uuid",
    "organization_slug",
    "organization_update",
    "organization_delete",
    "members",
    "add_member",
    "remove_member",
    "update_member",
    "branch_create",
    "branch_get",
    "branch_update",
    "branch_delete",
    "role_list",
    "role_get",
    "role_create",
    "role_update",
    "role_delete",
    "role_permissions",
    "member_roles",
    "set_permissions",
    "set_member_roles",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ADMIN_ACTIONS)
@pytest.mark.parametrize(
    "foreign", [False, True], ids=["missing-grant", "foreign-tenant"]
)
async def test_admin_services_deny_unauthorized_direct_calls(
    db_session, tenants, action, foreign
):
    owner, staff, a, b, ca, cb, sa, sb = tenants
    await OrganizationService.add_member(db_session, a.id, staff.id, actor_id=owner.id)
    org, context, shop = (b, cb, sb) if foreign else (a, ca, sa)
    role = await RoleService.create(db_session, org.id, "Protected", actor_id=owner.id)
    member = await db_session.get(OrganizationUser, context.membership_id)
    actor = {"actor_id": staff.id}
    calls = {
        "organization_get": lambda: OrganizationService.get_by_id(
            db_session, org.id, **actor
        ),
        "organization_uuid": lambda: OrganizationService.get_by_uuid(
            db_session, org.uuid, **actor
        ),
        "organization_slug": lambda: OrganizationService.get_by_slug(
            db_session, org.slug, **actor
        ),
        "organization_update": lambda: OrganizationService.update(
            db_session, org, name="Denied", **actor
        ),
        "organization_delete": lambda: OrganizationService.delete(
            db_session, org, **actor
        ),
        "members": lambda: OrganizationService.members(
            db_session, org.id, 0, 50, **actor
        ),
        "add_member": lambda: OrganizationService.add_member(
            db_session, org.id, staff.id, **actor
        ),
        "remove_member": lambda: OrganizationService.remove_member(
            db_session, org.id, owner.id, **actor
        ),
        "update_member": lambda: OrganizationService.update_member(
            db_session, org, member, False, **actor
        ),
        "branch_create": lambda: OrganizationService.create_branch(
            db_session, org.id, "Denied", **actor
        ),
        "branch_get": lambda: OrganizationService.get_branch_by_id(
            db_session, shop.id, org.id, **actor
        ),
        "branch_update": lambda: OrganizationService.update_branch(
            db_session, shop, org.id, name="Denied", **actor
        ),
        "branch_delete": lambda: OrganizationService.delete_branch(
            db_session, shop, org.id, **actor
        ),
        "role_list": lambda: RoleService.list(db_session, org.id, **actor),
        "role_get": lambda: RoleService.get(db_session, org.id, role.uuid, **actor),
        "role_create": lambda: RoleService.create(
            db_session, org.id, "Denied", **actor
        ),
        "role_update": lambda: RoleService.update(
            db_session, org.id, role.uuid, {"name": "Denied"}, **actor
        ),
        "role_delete": lambda: RoleService.delete(
            db_session, org.id, role.uuid, **actor
        ),
        "role_permissions": lambda: RoleService.permissions(
            db_session, org.id, role.uuid, **actor
        ),
        "member_roles": lambda: RoleService.member_roles(
            db_session, org.id, member.uuid, **actor
        ),
        "set_permissions": lambda: RoleService.set_permissions(
            db_session, org.id, role.uuid, [], staff.id
        ),
        "set_member_roles": lambda: RoleService.set_member_roles(
            db_session, org.id, member.uuid, [], staff.id
        ),
    }
    with pytest.raises(NotFoundError if foreign else AuthorizationError):
        await calls[action]()
    await db_session.refresh(org)
    await db_session.refresh(role)
    await db_session.refresh(member)
    assert org.is_active and org.name != "Denied"
    assert role.name == "Protected"
    assert member.is_active
    assert not await db_session.scalar(select(Role.id).where(Role.name == "Denied"))


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "update", "delete", "grant", "assign"])
async def test_role_action_permission_is_sufficient_without_read_grant(
    db_session, tenants, action
):
    owner, staff, org, _, _, _, _, _ = tenants
    member = await OrganizationService.add_member(
        db_session, org.id, staff.id, actor_id=owner.id
    )
    delegate = await RoleService.create(
        db_session, org.id, "Delegate", actor_id=owner.id
    )
    target = await RoleService.create(db_session, org.id, "Target", actor_id=owner.id)
    permission = {
        "create": "roles:create",
        "update": "roles:update",
        "delete": "roles:delete",
        "grant": "roles:permissions:update",
        "assign": "roles:assign",
    }[action]
    await RoleService.set_permissions(
        db_session, org.id, delegate.uuid, [permission], owner.id
    )
    await RoleService.set_member_roles(
        db_session, org.id, member.uuid, [delegate.uuid], owner.id
    )
    if action == "create":
        result = await RoleService.create(
            db_session, org.id, "Allowed", actor_id=staff.id
        )
        assert result.name == "Allowed"
    elif action == "update":
        result = await RoleService.update(
            db_session, org.id, target.uuid, {"name": "Allowed"}, actor_id=staff.id
        )
        assert result.name == "Allowed"
    elif action == "delete":
        await RoleService.delete(db_session, org.id, target.uuid, actor_id=staff.id)
        assert await db_session.get(Role, target.id) is None
    elif action == "grant":
        assert (
            await RoleService.set_permissions(
                db_session, org.id, target.uuid, [], staff.id
            )
            == []
        )
    else:
        assert (
            await RoleService.set_member_roles(
                db_session, org.id, member.uuid, [], staff.id
            )
            == []
        )


@pytest.mark.asyncio
async def test_foreign_membership_and_owner_transfer_cannot_bypass_service_policy(
    db_session, tenants
):
    owner, staff, a, _, _, cb, _, _ = tenants
    membership = await OrganizationService.add_member(
        db_session, a.id, staff.id, actor_id=owner.id
    )
    foreign = await db_session.get(OrganizationUser, cb.membership_id)
    with pytest.raises(NotFoundError):
        await OrganizationService.update_member(
            db_session, a, foreign, False, actor_id=owner.id
        )
    role = await RoleService.create(db_session, a.id, "Editor", actor_id=owner.id)
    await RoleService.set_permissions(
        db_session, a.id, role.uuid, ["organizations.update"], owner.id
    )
    await RoleService.set_member_roles(
        db_session, a.id, membership.uuid, [role.uuid], owner.id
    )
    with pytest.raises(AuthorizationError):
        await OrganizationService.update(
            db_session, a, owner_id=staff.id, actor_id=staff.id
        )
    assert a.owner_id == owner.id and foreign.is_active


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    ["inactive-user", "inactive-membership", "removed-membership", "inactive-org"],
)
async def test_service_permission_check_revalidates_actor_state(
    db_session, tenants, state
):
    owner, staff, org, _, _, _, _, _ = tenants
    membership = await OrganizationService.add_member(
        db_session, org.id, staff.id, actor_id=owner.id
    )
    role = await RoleService.create(db_session, org.id, "Reader", actor_id=owner.id)
    await RoleService.set_permissions(
        db_session, org.id, role.uuid, ["roles:read"], owner.id
    )
    await RoleService.set_member_roles(
        db_session, org.id, membership.uuid, [role.uuid], owner.id
    )
    assert await RoleService.list(db_session, org.id, actor_id=staff.id)
    if state == "inactive-user":
        staff.is_active = False
    elif state == "inactive-membership":
        membership.is_active = False
    elif state == "removed-membership":
        await OrganizationService.remove_member(
            db_session, org.id, staff.id, actor_id=owner.id
        )
    else:
        org.is_active = False
    await db_session.commit()
    with pytest.raises(NotFoundError if "membership" in state else AuthorizationError):
        await RoleService.list(db_session, org.id, actor_id=staff.id)
    # Even an empty permission list must not allow an inactive/nonmember actor.
    with pytest.raises(AuthorizationError):
        await OrganizationService.require_permissions(db_session, staff.id, org.id, ())


EMS_READS = [
    (ShopService.list, ()),
    (ShopService.export, ()),
    (ShopService.count, ()),
    (FinanceService.settings, (1,)),
    (FinanceService.currencies, ()),
    (FinanceService.cashboxes, (1,)),
    (LedgerService.entries, (1,)),
    (ContactService.get, (uuid4(),)),
    (ContactService.list, (uuid4(),)),
    (ContactLedgerService.statement, (uuid4(),)),
    (ContactLedgerService.balances, (uuid4(),)),
    (ContactLedgerService.export, (uuid4(), None, None)),
    (ExchangeService.rates, (1,)),
    (ExchangeService.get, (1, uuid4())),
    (ExchangeService.list, (1,)),
    (PaymentService.get, (1, uuid4())),
    (PaymentService.list, (1,)),
    (ObligationService.outstanding, (uuid4(),)),
    (HawalaService.get, (1, uuid4())),
    (HawalaService.list, (1,)),
    (AgentSettlementService.get, (1, uuid4())),
    (AgentSettlementService.list, (1,)),
    (AgentSettlementService.outstanding, (uuid4(),)),
    (DailyService.categories, (1,)),
    (ClosingService.current, (1,)),
    (ClosingService.get, (1, uuid4())),
    (ClosingService.list, (1,)),
    (ClosingService.export, (1, uuid4())),
    (ReportService.cash, (1,)),
    (ReportService.report, (1, "audit", {})),
    (TenantOperations.audit_log, ()),
    (TenantOperations.download, (uuid4(),)),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,args", EMS_READS, ids=[m.__qualname__ for m, _ in EMS_READS]
)
async def test_ems_reads_deny_members_without_grants(db_session, tenants, method, args):
    owner, staff, org, _, _, _, _, _ = tenants
    await OrganizationService.add_member(
        db_session, org.id, staff.id, actor_id=owner.id
    )
    context = await OrganizationContext.resolve(db_session, staff.id, org.id)
    with pytest.raises(AuthorizationError):
        await method(db_session, context, *args)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        "exchange",
        "payment",
        "settlement",
        "hawala-confirm",
        "hawala-pay",
        "hawala-cancel",
        "hawala-refund",
        "hawala-review",
        "opening",
        "expense",
        "transfer",
        "day-open",
        "day-close",
        "day-confirm",
        "exchange-reverse",
        "payment-reverse",
        "settlement-reverse",
        "opening-reverse",
        "expense-reverse",
    ],
)
async def test_financial_writes_deny_unprivileged_member_before_posting(
    db_session, tenants, action
):
    from datetime import date

    from app.models import FinancialRetry
    from app.models.finance import JournalEntry
    from app.schemas.daily.request import (
        ClosingRequest,
        ConfirmClosingRequest,
        ExpenseRequest,
        OpenDayRequest,
        TransferRequest,
    )
    from app.schemas.exchange.request import ExchangeCreateRequest
    from app.schemas.finance.request import OpeningBalanceRequest
    from app.schemas.hawala.request import (
        AgentSettlementCreateRequest,
        HawalaCancelRequest,
        HawalaConfirmRequest,
        HawalaPayRequest,
        HawalaRefundRequest,
        HawalaReviewRequest,
    )
    from app.schemas.payment.request import (
        AllocationRequest,
        PaymentCreateRequest,
        PaymentReverseRequest,
    )

    owner, staff, org, _, _, _, shop, _ = tenants
    await OrganizationService.add_member(
        db_session, org.id, staff.id, actor_id=owner.id
    )
    context = await OrganizationContext.resolve(db_session, staff.id, org.id)
    common = (db_session, context, shop.id)
    reversal = PaymentReverseRequest(owner_approved=True, reason="Correction")
    allocation = AllocationRequest(obligation_id=1, amount="10")
    hawala_actions = {
        "confirm": HawalaConfirmRequest(reason="Confirm", instruction_verified=True),
        "pay": HawalaPayRequest(
            reason="Pay",
            mode="partner",
            beneficiary_verified=True,
            verification_reference="verified",
            evidence_reference="receipt",
        ),
        "cancel": HawalaCancelRequest(reason="Cancel", partner_not_paid=True),
        "refund": HawalaRefundRequest(
            reason="Refund", cashbox_id=1, amount="10", evidence_reference="receipt"
        ),
        "review": HawalaReviewRequest(
            owner_approved=True,
            reason="Review",
            decision="investigate",
            evidence_reference="receipt",
        ),
    }
    if action.startswith("hawala-"):
        transition = action.removeprefix("hawala-")
        call = lambda: HawalaService.transition(
            *common, uuid4(), "denied", transition, hawala_actions[transition]
        )
    else:
        calls = {
            "exchange": lambda: ExchangeService.create(
                *common,
                "denied",
                ExchangeCreateRequest(
                    cashbox_id=1,
                    base_currency_id=1,
                    quote_currency_id=2,
                    side="buy",
                    amount="10",
                    rate="70",
                    reason="Trade",
                ),
            ),
            "payment": lambda: PaymentService.create(
                *common,
                "denied",
                PaymentCreateRequest(
                    contact_id=1,
                    cashbox_id=1,
                    currency_id=1,
                    direction="receive",
                    amount="10",
                    allocations=[allocation],
                    reason="Payment",
                ),
            ),
            "settlement": lambda: AgentSettlementService.create(
                *common,
                "denied",
                AgentSettlementCreateRequest(
                    agent_id=1,
                    currency_id=1,
                    direction="receive",
                    method="bank",
                    amount="10",
                    external_reference="bank",
                    evidence_reference="receipt",
                    allocations=[allocation],
                    reason="Settlement",
                ),
            ),
            "opening": lambda: LedgerService.opening(
                *common,
                "denied",
                OpeningBalanceRequest(
                    cashbox_id=1,
                    currency_id=1,
                    amount="10",
                    book_afn="10",
                    owner_approved=True,
                    reason="Opening",
                ),
            ),
            "expense": lambda: DailyService.create(
                *common,
                "denied",
                ExpenseRequest(
                    category_id=1,
                    cashbox_id=1,
                    currency_id=1,
                    amount="10",
                    expense_date=date(2026, 11, 17),
                    payee="Supplier",
                    explanation="Expense",
                ),
            ),
            "transfer": lambda: DailyService.create(
                *common,
                "denied",
                TransferRequest(
                    source_cashbox_id=1,
                    destination_cashbox_id=2,
                    currency_id=1,
                    amount="10",
                    reason="Transfer",
                ),
                transfer=True,
            ),
            "day-open": lambda: ClosingService.open(
                *common, "denied", OpenDayRequest(day=date(2026, 11, 17))
            ),
            "day-close": lambda: ClosingService.create(
                *common, "denied", ClosingRequest(counts=[])
            ),
            "day-confirm": lambda: ClosingService.confirm(
                *common, uuid4(), "denied", ConfirmClosingRequest(owner_approved=True)
            ),
            "exchange-reverse": lambda: ExchangeService.reverse(
                *common, uuid4(), "denied", reversal
            ),
            "payment-reverse": lambda: PaymentService.reverse(
                *common, uuid4(), "denied", reversal
            ),
            "settlement-reverse": lambda: AgentSettlementService.reverse(
                *common, uuid4(), "denied", reversal
            ),
            "opening-reverse": lambda: LedgerService.reverse(
                *common, uuid4(), "denied", "Correction"
            ),
            "expense-reverse": lambda: DailyService.reverse(
                *common, uuid4(), "denied", "Correction"
            ),
        }
        call = calls[action]
    with pytest.raises(AuthorizationError):
        await call()
    assert (await db_session.scalars(select(JournalEntry))).all() == []
    assert (await db_session.scalars(select(FinancialRetry))).all() == []
