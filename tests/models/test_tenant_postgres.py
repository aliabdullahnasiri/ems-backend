"""Opt-in PostgreSQL verification in a transaction-local, disposable schema.

Run EMS_TEST_POSTGRES=1 pytest tests/models/test_tenant_postgres.py. No application
schema is changed: all DDL/data are rolled back when the connection closes.
"""

import os
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.models import AuditEvent, User
from app.services.organization import OrganizationService
from app.services.organization_context import OrganizationContext
from app.services.shop import ShopService


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("EMS_TEST_POSTGRES") != "1",
    reason="Opt-in disposable PostgreSQL schema test",
)
async def test_postgres_migrations_and_immutable_history():
    engine = create_async_engine(settings.DATABASE_URL, hide_parameters=True)
    schema = "ems_phase5_" + uuid4().hex
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                scripts = ScriptDirectory("alembic")

                def migrate(sync_connection):
                    with Operations.context(
                        MigrationContext.configure(sync_connection)
                    ):
                        for revision in reversed(list(scripts.walk_revisions())):
                            revision.module.upgrade()

                await connection.run_sync(migrate)
                async with AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as db:
                    user = User(
                        user_name="postgres-owner",
                        email="postgres-owner@example.com",
                        password_hash="unused",
                    )
                    db.add(user)
                    await db.flush()
                    org = await OrganizationService.create(
                        db, "Postgres", "postgres-test", user
                    )
                    ctx = await OrganizationContext.resolve(db, user.id, org.id)
                    await ShopService.create(db, ctx, "Immutable audit")
                    await db.commit()
                    with pytest.raises(DBAPIError):
                        async with db.begin_nested():
                            await db.execute(
                                update(AuditEvent).values(reason="must fail")
                            )
                    await db.rollback()

                def downgrade(sync_connection):
                    with Operations.context(
                        MigrationContext.configure(sync_connection)
                    ):
                        for revision in scripts.walk_revisions():
                            revision.module.downgrade()

                await connection.run_sync(downgrade)
                assert not (
                    await connection.execute(
                        text(
                            "SELECT tablename FROM pg_tables WHERE schemaname = :schema"
                        ),
                        {"schema": schema},
                    )
                ).all()
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
