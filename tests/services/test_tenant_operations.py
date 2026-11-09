from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    AppValidationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
)
from app.models import AuditEvent, Branch, FinancialRetry, OrganizationUser, User
from app.services.organization import OrganizationService
from app.services.organization_context import OrganizationContext
from app.services.shop import ShopService
from app.services.tenant_operations import TenantOperations
from app.services.user import UserService


@pytest_asyncio.fixture
async def tenants(db_session):
    owner = User(
        user_name="boundary-owner", email="boundary@example.com", password_hash="unused"
    )
    staff = User(
        user_name="boundary-staff",
        email="staff-boundary@example.com",
        password_hash="unused",
    )
    db_session.add_all([owner, staff])
    await db_session.flush()
    a = await OrganizationService.create(db_session, "A", "boundary-a", owner)
    b = await OrganizationService.create(db_session, "B", "boundary-b", owner)
    ca = await OrganizationContext.resolve(db_session, owner.id, a.id)
    cb = await OrganizationContext.resolve(db_session, owner.id, b.id)
    sa = await ShopService.create(db_session, ca, "دکان الف")
    sb = await ShopService.create(db_session, cb, "Shop B")
    await db_session.commit()
    return owner, staff, a, b, ca, cb, sa, sb


@pytest.mark.asyncio
async def test_retry_is_scoped_and_payload_bound(db_session, tenants):
    _, _, _, _, ca, cb, sa, sb = tenants
    post = AsyncMock(return_value={"amount": "100.00", "currency": "USD"})

    async def run(ctx, payload, **kwargs):
        return await TenantOperations.execute_once(
            db_session,
            ctx,
            "exchange:create",
            "retry-1",
            payload,
            "exchange:create",
            post,
            **kwargs,
        )

    payload = {"amount": "100.00", "currency": "USD"}
    assert await run(ca, payload) == await run(ca, payload)
    assert post.await_count == 1
    with pytest.raises(ConflictError):
        await run(ca, {"amount": "101.00", "currency": "USD"})
    await run(cb, payload)
    assert post.await_count == 2
    assert len(list(await db_session.scalars(select(FinancialRetry)))) == 2
    with pytest.raises(AppValidationError):
        await run(ca, {"amount": 0.1})
    with pytest.raises(NotFoundError):
        await TenantOperations.execute_once(
            db_session,
            ca,
            "payment:create",
            "foreign-reference",
            {},
            "payment:create",
            post,
            references=((Branch, sb.id, sb.id),),
        )
    assert post.await_count == 2
    # Shop IDs cannot replace tenant validation.
    with pytest.raises(NotFoundError):
        await TenantOperations.execute_once(
            db_session,
            ca,
            "payment:create",
            "wrong-shop",
            {},
            "payment:create",
            post,
            references=((Branch, sa.id, sb.id),),
        )


@pytest.mark.asyncio
async def test_failed_posting_rolls_back_partial_writes(db_session, tenants):
    _, _, _, _, ca, _, _, _ = tenants

    async def broken_post():
        db_session.add(ca.new(Branch, name="Partial"))
        await db_session.flush()
        raise AppValidationError()

    with pytest.raises(AppValidationError):
        await TenantOperations.execute_once(
            db_session,
            ca,
            "exchange:create",
            "failed",
            {},
            "exchange:create",
            broken_post,
        )
    assert (
        await db_session.scalar(select(Branch.id).where(Branch.name == "Partial"))
        is None
    )
    assert await db_session.scalar(select(FinancialRetry.id)) is None


@pytest.mark.asyncio
async def test_jobs_cache_download_and_reports_revalidate(
    db_session, tenants, mock_redis
):
    _owner, staff, a, _b, ca, cb, sa, _sb = tenants
    await TenantOperations.cache_set(
        db_session, mock_redis, ca, "report:read", "report", "same", {"tenant": "a"}
    )
    assert (
        await TenantOperations.cache_get(
            db_session, mock_redis, cb, "report:read", "report", "same"
        )
        is None
    )
    assert await TenantOperations.cache_get(
        db_session, mock_redis, ca, "report:read", "report", "same"
    ) == {"tenant": "a"}
    attachment = await TenantOperations.upload(
        db_session, ca, sa.id, "evidence.pdf", "application/pdf", b"%PDF-test"
    )
    with pytest.raises(NotFoundError):
        await TenantOperations.download(db_session, cb, attachment.id)
    assert "دکان الف" in await ShopService.export(db_session, ca)
    assert "Shop B" not in await ShopService.export(db_session, ca)
    assert await ShopService.count(db_session, ca) == 1
    assert (
        await TenantOperations.run_job(
            db_session, ca.job_payload(), ("report:read",), AsyncMock(return_value="ok")
        )
        == "ok"
    )
    member = await OrganizationService.add_member(db_session, a.id, staff.id)
    cs = await OrganizationContext.resolve(db_session, staff.id, a.id)
    with pytest.raises(AuthorizationError):
        await TenantOperations.run_job(
            db_session, cs.job_payload(), ("report:read",), AsyncMock()
        )
    member.is_active = False
    await db_session.commit()
    with pytest.raises(NotFoundError):
        await OrganizationContext.from_job(db_session, cs.job_payload())
    # A forged membership belonging to a different actor is also denied.
    with pytest.raises(AuthorizationError):
        await OrganizationContext.from_job(
            db_session, {**ca.job_payload(), "membership_id": member.id}
        )
    a.is_active = False
    await db_session.commit()
    with pytest.raises(AuthorizationError):
        await TenantOperations.cache_get(
            db_session, mock_redis, ca, "report:read", "report", "same"
        )
    with pytest.raises(AuthorizationError):
        await TenantOperations.download(db_session, ca, attachment.id)


@pytest.mark.asyncio
async def test_audit_and_history_survive_deactivation(db_session, tenants):
    _owner, staff, a, _, _ca, _, sa, _ = tenants
    member = await OrganizationService.add_member(db_session, a.id, staff.id)
    cs = await OrganizationContext.resolve(db_session, staff.id, a.id)
    event = await TenantOperations.audit(
        db_session, cs, "exchange:create", "shop", sa.id
    )
    await db_session.commit()
    event_id = event.id
    await OrganizationService.remove_member(db_session, a.id, staff.id)
    retained = await db_session.get(OrganizationUser, member.id)
    assert retained is not None and not retained.is_active
    await UserService.delete(db_session, staff)
    assert (await db_session.get(User, staff.id)).is_active is False
    assert (await db_session.get(AuditEvent, event_id)).membership_id == member.id
    await OrganizationService.delete(db_session, a)
    assert not a.is_active
    assert await db_session.get(AuditEvent, event_id) is not None


@pytest.mark.asyncio
async def test_audit_and_retry_cannot_be_rewritten_by_bulk_sql(db_session, tenants):
    _, _, _, _, ca, _, sa, _ = tenants
    event = await TenantOperations.audit(db_session, ca, "shop:update", "shop", sa.id)
    await db_session.commit()
    for statement in (
        update(AuditEvent).where(AuditEvent.id == event.id).values(reason="changed"),
        delete(AuditEvent).where(AuditEvent.id == event.id),
    ):
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                await db_session.execute(statement)
    await TenantOperations.execute_once(
        db_session,
        ca,
        "exchange:create",
        "immutable",
        {},
        "exchange:create",
        AsyncMock(return_value={"ok": True}),
    )
    await db_session.commit()
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                update(FinancialRetry).values(result={"ok": False})
            )


@pytest.mark.asyncio
async def test_concurrent_retries_share_one_committed_result(tmp_path):
    """Independent connections cannot both execute the same financial callback."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'concurrent.sqlite'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as db:
            user = User(
                user_name="concurrent",
                email="concurrent@example.com",
                password_hash="unused",
            )
            db.add(user)
            await db.flush()
            org = await OrganizationService.create(db, "Concurrent", "concurrent", user)
            user_id, org_id = user.id, org.id
        second_started = asyncio.Event()
        calls = 0

        async def run(second=False):
            async with sessions() as db:
                context = await OrganizationContext.resolve(db, user_id, org_id)
                if second:
                    second_started.set()

                async def post():
                    nonlocal calls
                    calls += 1
                    if not second:
                        await second_started.wait()
                    shop = context.new(Branch, name="Posted exactly once")
                    db.add(shop)
                    await db.flush()
                    return {"record_id": shop.id, "amount": "100.00", "currency": "USD"}

                result = await TenantOperations.execute_once(
                    db,
                    context,
                    "exchange:create",
                    "concurrent-key",
                    {"amount": "100.00", "currency": "USD"},
                    "exchange:create",
                    post,
                )
                await db.commit()
                return result

        first, second = await asyncio.wait_for(
            asyncio.gather(run(), run(True)), timeout=15
        )
        assert first == second and calls == 1
        async with sessions() as db:
            assert len(list(await db.scalars(select(FinancialRetry)))) == 1
            assert len(list(await db.scalars(select(Branch)))) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_old_job_cannot_reuse_recreated_membership(db_session, tenants):
    _, staff, a, _, _, _, _, _ = tenants
    await OrganizationService.add_member(db_session, a.id, staff.id)
    old = await OrganizationContext.resolve(db_session, staff.id, a.id)
    payload = old.job_payload()
    await OrganizationService.remove_member(db_session, a.id, staff.id)
    await OrganizationService.add_member(db_session, a.id, staff.id)
    with pytest.raises(AuthorizationError):
        await OrganizationContext.from_job(db_session, payload)


@pytest.mark.asyncio
async def test_scoped_orm_filters_joins_bulk_changes_and_new_rows(db_session, tenants):
    """Unfiltered ORM operations remain tenant-scoped inside EMS work."""
    from sqlalchemy import func, text

    from app.core.context.organization import organization_scope
    from app.models import PrivateAttachment

    _, _, _, _, ca, cb, sa, sb = tenants
    await TenantOperations.upload(
        db_session, ca, sa.id, "a.pdf", "application/pdf", b"%PDF-a"
    )
    await TenantOperations.upload(
        db_session, cb, sb.id, "b.pdf", "application/pdf", b"%PDF-b"
    )
    with organization_scope(ca):
        rows = list(await db_session.scalars(select(Branch)))
        assert [row.id for row in rows] == [sa.id]
        assert await db_session.scalar(select(func.count(Branch.id))) == 1
        joined = (
            await db_session.execute(
                select(Branch.id, PrivateAttachment.id).join(
                    PrivateAttachment, PrivateAttachment.shop_id == Branch.id
                )
            )
        ).all()
        assert len(joined) == 1 and joined[0][0] == sa.id
        await db_session.execute(update(Branch).values(address="Only A"))
        shop = Branch(name="Assigned by context")
        db_session.add(shop)
        await db_session.flush()
        assert shop.organization_id == ca.organization_id
        with pytest.raises(AuthorizationError):
            await db_session.execute(text("SELECT * FROM branches"))
        with pytest.raises(AuthorizationError):
            async with db_session.begin_nested():
                db_session.add(Branch(name="Spoof", organization_id=cb.organization_id))
                await db_session.flush()
    await db_session.refresh(sb)
    assert sb.address is None


@pytest.mark.asyncio
async def test_posting_callback_cannot_commit_partial_state(db_session, tenants):
    _, _, _, _, ca, _, _, _ = tenants

    async def accidental_commit():
        db_session.add(ca.new(Branch, name="Must roll back"))
        await db_session.commit()
        return {"ok": True}

    with pytest.raises(ConflictError):
        await TenantOperations.execute_once(
            db_session,
            ca,
            "exchange:create",
            "illegal-commit",
            {},
            "exchange:create",
            accidental_commit,
        )
    assert (
        await db_session.scalar(
            select(Branch.id).where(Branch.name == "Must roll back")
        )
        is None
    )
    assert await db_session.scalar(select(FinancialRetry.id)) is None
