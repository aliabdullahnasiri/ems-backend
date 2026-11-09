"""Add an explicit active-user access policy.

Revision ID: 91e5a0b23c10
Revises: 68ddef4751ec
"""

from alembic import op
import sqlalchemy as sa

revision = "91e5a0b23c10"
down_revision = "68ddef4751ec"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade():
    op.drop_column("users", "is_active")
