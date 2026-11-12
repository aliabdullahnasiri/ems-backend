"""Shop settings and exact double-entry financial foundation.

Revision ID: f6a001c9d210
Revises: c25e510a47d1
"""

import sqlalchemy as sa

from alembic import op

revision = "f6a001c9d210"
down_revision = "c25e510a47d1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "currencies",
        sa.Column("code", sa.String(length=3), nullable=False),
        sa.Column("precision", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("rial_per_unit", sa.Integer(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(code = 'IRR' AND rial_per_unit IS NOT NULL AND rial_per_unit = 1) OR (code = 'IRT' AND rial_per_unit IS NOT NULL AND rial_per_unit = 10) OR (code NOT IN ('IRR', 'IRT') AND rial_per_unit IS NULL)",
            name="ck_currency_iran_unit",
        ),
        sa.CheckConstraint(
            "precision >= 0 AND precision <= 8", name="ck_currency_precision"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "code", name="uq_currency_code"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_currencies_organization_id"
        ),
        sa.UniqueConstraint("uuid"),
    )
    with op.batch_alter_table("currencies", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_currencies_organization_id"),
            ["organization_id"],
            unique=False,
        )

    op.create_table(
        "cashboxes",
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["branches.organization_id", "branches.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_cashboxes_organization_id"
        ),
        sa.UniqueConstraint(
            "organization_id", "shop_id", "id", name="uq_cashboxes_shop_id"
        ),
        sa.UniqueConstraint(
            "organization_id", "shop_id", "name", name="uq_cashbox_name"
        ),
        sa.UniqueConstraint("uuid"),
    )
    with op.batch_alter_table("cashboxes", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_cashboxes_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_cashboxes_shop_id"), ["shop_id"], unique=False
        )

    op.create_table(
        "journal_entries",
        sa.Column("membership_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("reversal_of_id", sa.Integer(), nullable=True),
        sa.Column("opening_key", sa.String(length=100), nullable=True),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "membership_id", "user_id"],
            [
                "organization_users.organization_id",
                "organization_users.id",
                "organization_users.user_id",
            ],
            name="fk_journal_actor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "reversal_of_id"],
            [
                "journal_entries.organization_id",
                "journal_entries.shop_id",
                "journal_entries.id",
            ],
            name="fk_journal_reversal",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["branches.organization_id", "branches.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_journal_entries_organization_id"
        ),
        sa.UniqueConstraint("organization_id", "opening_key", name="uq_opening_once"),
        sa.UniqueConstraint(
            "organization_id", "reversal_of_id", name="uq_journal_reversal"
        ),
        sa.UniqueConstraint(
            "organization_id", "shop_id", "id", name="uq_journal_entries_shop_id"
        ),
        sa.UniqueConstraint("uuid"),
    )
    with op.batch_alter_table("journal_entries", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_journal_entries_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_journal_entries_shop_id"), ["shop_id"], unique=False
        )

    op.create_table(
        "shop_settings",
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("logo_reference", sa.String(length=500), nullable=True),
        sa.Column("preferred_language", sa.String(length=10), nullable=False),
        sa.Column("receipt_footer", sa.String(length=1000), nullable=True),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["branches.organization_id", "branches.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_shop_settings_organization_id"
        ),
        sa.UniqueConstraint(
            "organization_id", "shop_id", "id", name="uq_shop_settings_shop_id"
        ),
        sa.UniqueConstraint("organization_id", "shop_id", name="uq_shop_settings_shop"),
        sa.UniqueConstraint("uuid"),
    )
    with op.batch_alter_table("shop_settings", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_shop_settings_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_shop_settings_shop_id"), ["shop_id"], unique=False
        )

    op.create_table(
        "journal_postings",
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "entry_id"],
            [
                "journal_entries.organization_id",
                "journal_entries.shop_id",
                "journal_entries.id",
            ],
            name="fk_posting_entry",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["branches.organization_id", "branches.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_id", name="uq_posting_entry"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_journal_postings_organization_id"
        ),
        sa.UniqueConstraint(
            "organization_id", "shop_id", "id", name="uq_journal_postings_shop_id"
        ),
        sa.UniqueConstraint("uuid"),
    )
    with op.batch_alter_table("journal_postings", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_journal_postings_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_journal_postings_shop_id"), ["shop_id"], unique=False
        )

    op.create_table(
        "ledger_accounts",
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("currency_id", sa.Integer(), nullable=False),
        sa.Column("cashbox_id", sa.Integer(), nullable=True),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(kind = 'cash' AND cashbox_id IS NOT NULL) OR (kind <> 'cash' AND cashbox_id IS NULL)",
            name="ck_account_cashbox",
        ),
        sa.CheckConstraint(
            "kind IN ('cash', 'equity', 'receivable', 'payable', 'income', 'expense', 'clearing')",
            name="ck_account_kind",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "currency_id"],
            ["currencies.organization_id", "currencies.id"],
            name="fk_account_currency",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "cashbox_id"],
            ["cashboxes.organization_id", "cashboxes.shop_id", "cashboxes.id"],
            name="fk_account_cashbox",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["branches.organization_id", "branches.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_ledger_accounts_organization_id"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "shop_id",
            "code",
            "currency_id",
            name="uq_ledger_account",
        ),
        sa.UniqueConstraint(
            "organization_id", "shop_id", "id", name="uq_ledger_accounts_shop_id"
        ),
        sa.UniqueConstraint("uuid"),
    )
    with op.batch_alter_table("ledger_accounts", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ledger_accounts_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_ledger_accounts_shop_id"), ["shop_id"], unique=False
        )

    op.create_table(
        "journal_lines",
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.String(length=40), nullable=False),
        sa.Column("book_afn", sa.String(length=40), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "account_id"],
            [
                "ledger_accounts.organization_id",
                "ledger_accounts.shop_id",
                "ledger_accounts.id",
            ],
            name="fk_line_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "entry_id"],
            [
                "journal_entries.organization_id",
                "journal_entries.shop_id",
                "journal_entries.id",
            ],
            name="fk_line_entry",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["branches.organization_id", "branches.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_journal_lines_organization_id"
        ),
        sa.UniqueConstraint(
            "organization_id", "shop_id", "id", name="uq_journal_lines_shop_id"
        ),
        sa.UniqueConstraint("uuid"),
    )
    with op.batch_alter_table("journal_lines", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_journal_lines_account_id"), ["account_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_journal_lines_entry_id"), ["entry_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_journal_lines_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_journal_lines_shop_id"), ["shop_id"], unique=False
        )

    for name in (
        "ledger_accounts",
        "journal_entries",
        "journal_lines",
        "journal_postings",
    ):
        _create_immutable_triggers(sa.table(name), op.get_bind())
    protect_sealed_lines(None, op.get_bind())


def downgrade():
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS journal_lines_sealed() CASCADE")
        for name in (
            "ledger_accounts",
            "journal_entries",
            "journal_lines",
            "journal_postings",
        ):
            op.execute(f"DROP FUNCTION IF EXISTS {name}_immutable() CASCADE")
    elif dialect in ("sqlite", "mysql"):
        op.execute("DROP TRIGGER IF EXISTS journal_lines_sealed")
    with op.batch_alter_table("journal_lines", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_journal_lines_shop_id"))
        batch_op.drop_index(batch_op.f("ix_journal_lines_organization_id"))
        batch_op.drop_index(batch_op.f("ix_journal_lines_entry_id"))
        batch_op.drop_index(batch_op.f("ix_journal_lines_account_id"))

    op.drop_table("journal_lines")
    with op.batch_alter_table("ledger_accounts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ledger_accounts_shop_id"))
        batch_op.drop_index(batch_op.f("ix_ledger_accounts_organization_id"))

    op.drop_table("ledger_accounts")
    with op.batch_alter_table("journal_postings", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_journal_postings_shop_id"))
        batch_op.drop_index(batch_op.f("ix_journal_postings_organization_id"))

    op.drop_table("journal_postings")
    with op.batch_alter_table("shop_settings", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_shop_settings_shop_id"))
        batch_op.drop_index(batch_op.f("ix_shop_settings_organization_id"))

    op.drop_table("shop_settings")
    with op.batch_alter_table("journal_entries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_journal_entries_shop_id"))
        batch_op.drop_index(batch_op.f("ix_journal_entries_organization_id"))

    op.drop_table("journal_entries")
    with op.batch_alter_table("cashboxes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_cashboxes_shop_id"))
        batch_op.drop_index(batch_op.f("ix_cashboxes_organization_id"))

    op.drop_table("cashboxes")
    with op.batch_alter_table("currencies", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_currencies_organization_id"))

    op.drop_table("currencies")


def _create_immutable_triggers(target, connection, **kwargs):
    """Keep audit and retry history immutable even through bulk SQL."""
    name = target.name
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for action in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"CREATE TRIGGER {name}_no_{action.lower()} BEFORE {action} ON {name} "
                "BEGIN SELECT RAISE(ABORT, 'Retained history is immutable'); END"
            )
    elif dialect == "postgresql":
        connection.exec_driver_sql(
            f"CREATE OR REPLACE FUNCTION {name}_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'Retained history is immutable'; END; $$"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER {name}_immutable BEFORE UPDATE OR DELETE ON {name} "
            f"FOR EACH ROW EXECUTE FUNCTION {name}_immutable()"
        )
    elif dialect == "mysql":
        for action in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"CREATE TRIGGER {name}_no_{action.lower()} BEFORE {action} ON {name} "
                "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Retained history is immutable'"
            )


def protect_sealed_lines(target, connection, **kwargs):
    """No additional lines may be appended after posting, even via raw SQL."""
    dialect = connection.dialect.name
    condition = "EXISTS (SELECT 1 FROM journal_postings WHERE entry_id = NEW.entry_id)"
    if dialect == "sqlite":
        connection.exec_driver_sql(
            f"CREATE TRIGGER journal_lines_sealed BEFORE INSERT ON journal_lines WHEN {condition} BEGIN SELECT RAISE(ABORT, 'Journal is posted'); END"
        )
    elif dialect == "postgresql":
        connection.exec_driver_sql(
            f"CREATE OR REPLACE FUNCTION journal_lines_sealed() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF {condition} THEN RAISE EXCEPTION 'Journal is posted'; END IF; RETURN NEW; END; $$"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER journal_lines_sealed BEFORE INSERT ON journal_lines FOR EACH ROW EXECUTE FUNCTION journal_lines_sealed()"
        )
    elif dialect == "mysql":
        connection.exec_driver_sql(
            f"CREATE TRIGGER journal_lines_sealed BEFORE INSERT ON journal_lines FOR EACH ROW BEGIN IF {condition} THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Journal is posted'; END IF; END"
        )
