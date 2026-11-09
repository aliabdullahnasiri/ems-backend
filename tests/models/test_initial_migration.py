"""Verify the consolidated migration on an empty database with foreign keys enabled."""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.exc import IntegrityError

from app.models.base import Base
from app.services.datetime import DateTimeService


@pytest.fixture
def migrated_database() -> Iterator[tuple[sa.Connection, ModuleType]]:
    """Apply the initial revision independently of the ORM-created test database."""
    scripts = ScriptDirectory(str(Path(__file__).resolve().parents[2] / "alembic"))
    revision = scripts.get_revision("68ddef4751ec")
    assert revision is not None
    assert revision.down_revision is None
    migration = revision.module
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                for followup in reversed(
                    list(scripts.iterate_revisions("head", "68ddef4751ec"))
                ):
                    followup.module.upgrade()
                yield connection, migration
    finally:
        engine.dispose()


def test_upgrade_matches_models_and_downgrade_removes_schema(migrated_database):
    """The consolidated revision creates the current schema and reverses cleanly."""
    connection, migration = migrated_database
    context = MigrationContext.configure(connection)
    assert compare_metadata(context, Base.metadata) == []

    from alembic.script import ScriptDirectory

    scripts = ScriptDirectory("alembic")
    for followup in scripts.iterate_revisions("head", "68ddef4751ec"):
        followup.module.downgrade()
    migration.downgrade()
    assert sa.inspect(connection).get_table_names() == []

    migration.upgrade()
    for followup in reversed(list(scripts.iterate_revisions("head", "68ddef4751ec"))):
        followup.module.upgrade()
    assert compare_metadata(context, Base.metadata) == []


def test_organization_context_and_session_foreign_key(migrated_database):
    """Persist organization context and clear it when its organization is deleted."""
    connection, _ = migrated_database
    users = Base.metadata.tables["users"]
    organizations = Base.metadata.tables["organizations"]
    sessions = Base.metadata.tables["user_sessions"]
    connection.execute(
        users.insert().values(
            id=1, user_name="owner", email="owner@example.com", password_hash="unused"
        )
    )
    connection.execute(
        organizations.insert().values(
            id=1,
            name="Organization",
            slug="organization",
            owner_id=1,
            settings={"language": "en"},
        )
    )
    connection.execute(
        sessions.insert().values(
            user_id=1,
            organization_id=1,
            expires_at=DateTimeService.utc_now() + timedelta(days=1),
        )
    )
    assert connection.execute(
        sa.select(organizations.c.owner_id, organizations.c.settings)
    ).one() == (1, {"language": "en"})
    assert connection.execute(sa.select(sessions.c.organization_id)).scalar_one() == 1

    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(users.delete().where(users.c.id == 1))

    connection.execute(organizations.delete().where(organizations.c.id == 1))
    assert (
        connection.execute(sa.select(sessions.c.organization_id)).scalar_one() is None
    )
    assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_scoped_roles_and_permission_grants(migrated_database):
    """Enforce tenant-local role names and keep each tenant's grants independent."""
    connection, _ = migrated_database
    organizations = Base.metadata.tables["organizations"]
    roles = Base.metadata.tables["roles"]
    permissions = Base.metadata.tables["permissions"]
    grants = Base.metadata.tables["role_permissions"]
    assert "code" not in {
        column["name"] for column in sa.inspect(connection).get_columns("permissions")
    }
    for organization_id in (1, 2):
        connection.execute(
            organizations.insert().values(
                id=organization_id, name="Organization", slug=f"org-{organization_id}"
            )
        )
        connection.execute(
            roles.insert().values(
                id=organization_id, name="Staff", organization_id=organization_id
            )
        )
    assert connection.execute(
        sa.select(organizations.c.rbac_version).order_by(organizations.c.id)
    ).all() == [(0,), (0,)]

    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(roles.insert().values(name="Staff", organization_id=1))

    connection.execute(permissions.insert().values(id=1, name="read"))
    for role_id in (1, 2):
        connection.execute(grants.insert().values(role_id=role_id, permission_id=1))
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(permissions.insert().values(name="read"))
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(grants.insert().values(role_id=1, permission_id=1))

    connection.execute(organizations.delete().where(organizations.c.id == 1))
    assert connection.execute(sa.select(roles.c.organization_id)).all() == [(2,)]
    assert connection.execute(
        sa.select(grants.c.role_id, grants.c.permission_id)
    ).all() == [(2, 1)]
    assert connection.execute(sa.select(permissions.c.name)).all() == [("read",)]
    assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_business_ownership_constraints_and_history_retention(migrated_database):
    """Composite FKs prevent spoofed tenants/actors even with direct SQL."""
    connection, _ = migrated_database
    t = Base.metadata.tables
    for ident in (1, 2):
        connection.execute(
            t["users"]
            .insert()
            .values(
                id=ident,
                user_name=f"actor-{ident}",
                email=f"actor-{ident}@example.com",
                password_hash="unused",
            )
        )
        connection.execute(
            t["organizations"]
            .insert()
            .values(id=ident, name=f"Org {ident}", slug=f"org-{ident}")
        )
        connection.execute(
            t["organization_users"]
            .insert()
            .values(id=ident, organization_id=ident, user_id=ident)
        )
        connection.execute(
            t["branches"]
            .insert()
            .values(id=ident, organization_id=ident, name=f"Shop {ident}")
        )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            t["private_attachments"]
            .insert()
            .values(
                organization_id=1,
                shop_id=2,
                file_name="a.pdf",
                content_type="application/pdf",
                content=b"%PDF-",
            )
        )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            t["audit_events"]
            .insert()
            .values(
                organization_id=1,
                membership_id=2,
                user_id=2,
                action="test",
                record_type="shop",
                record_id=1,
            )
        )
    connection.execute(
        t["audit_events"]
        .insert()
        .values(
            id=1,
            organization_id=1,
            membership_id=1,
            user_id=1,
            action="test",
            record_type="shop",
            record_id=1,
        )
    )
    for table in ("users", "organizations", "organization_users"):
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(t[table].delete().where(t[table].c.id == 1))
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(t["audit_events"].update().values(reason="rewrite"))
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(t["audit_events"].delete())
    assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
