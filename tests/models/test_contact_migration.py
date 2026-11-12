"""Exercise Phase 7 ownership and evidence constraints with foreign keys enabled."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Base
from tests.models.test_initial_migration import migrated_database as migration_fixture

migrated_database = pytest.fixture(migration_fixture.__wrapped__)


def test_contact_references_are_tenant_shop_scoped_and_retained(migrated_database):
    connection, _ = migrated_database
    tables = Base.metadata.tables
    for ident in (1, 2):
        connection.execute(
            tables["organizations"]
            .insert()
            .values(id=ident, name=f"Org {ident}", slug=f"contact-{ident}")
        )
        connection.execute(
            tables["branches"]
            .insert()
            .values(id=ident, organization_id=ident, name=f"Shop {ident}")
        )
        connection.execute(
            tables["contacts"]
            .insert()
            .values(
                id=ident,
                organization_id=ident,
                shop_id=ident,
                name="احمد",
                search_name="احمد",
            )
        )
        connection.execute(
            tables["private_attachments"]
            .insert()
            .values(
                id=ident,
                organization_id=ident,
                shop_id=ident,
                file_name="id.pdf",
                content_type="application/pdf",
                content=b"%PDF-private",
            )
        )
    links = tables["contact_attachments"]
    for values in (
        {"organization_id": 1, "shop_id": 1, "contact_id": 2, "attachment_id": 1},
        {"organization_id": 1, "shop_id": 1, "contact_id": 1, "attachment_id": 2},
    ):
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(links.insert().values(**values))
    connection.execute(
        links.insert().values(
            id=1, organization_id=1, shop_id=1, contact_id=1, attachment_id=1
        )
    )
    for statement in (
        tables["contacts"].delete().where(tables["contacts"].c.id == 1),
        tables["private_attachments"]
        .delete()
        .where(tables["private_attachments"].c.id == 1),
        tables["private_attachments"].update().values(content=b"%PDF-replaced"),
        links.delete(),
        links.update().values(contact_id=2),
    ):
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(statement)
    assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_mysql_private_storage_supports_full_upload_limit():
    from sqlalchemy.dialects import mysql

    from app.models import PrivateAttachment

    column_type = PrivateAttachment.__table__.c.content.type
    assert column_type.compile(dialect=mysql.dialect()) == "MEDIUMBLOB"
