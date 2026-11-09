"""Tenant-safe audit, retries and private attachment storage."""

from sqlalchemy import JSON, ForeignKeyConstraint, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.ownership import ImmutableHistory, OrganizationOwned, ShopOwned


def actor_constraint(name: str):
    return ForeignKeyConstraint(
        ["organization_id", "membership_id", "user_id"],
        [
            "organization_users.organization_id",
            "organization_users.id",
            "organization_users.user_id",
        ],
        ondelete="RESTRICT",
        name=name,
    )


class AuditEvent(ImmutableHistory, OrganizationOwned, Base):
    __tablename__ = "audit_events"
    membership_id: Mapped[int] = mapped_column(nullable=False)
    user_id: Mapped[int] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    record_type: Mapped[str] = mapped_column(String(100), nullable=False)
    record_id: Mapped[int] = mapped_column(nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    # Deliberately no free-form request payload: credentials and ID documents
    # must never be copied into an audit log.
    __table_args__ = (
        actor_constraint("fk_audit_actor"),
        UniqueConstraint(
            "organization_id", "id", name="uq_audit_events_organization_id"
        ),
    )


class FinancialRetry(ImmutableHistory, OrganizationOwned, Base):
    __tablename__ = "financial_retries"
    membership_id: Mapped[int] = mapped_column(nullable=False)
    user_id: Mapped[int] = mapped_column(nullable=False)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    retry_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__ = (
        actor_constraint("fk_retry_actor"),
        UniqueConstraint(
            "organization_id", "operation", "retry_key", name="uq_financial_retry_scope"
        ),
        UniqueConstraint(
            "organization_id", "id", name="uq_financial_retries_organization_id"
        ),
    )


class PrivateAttachment(ShopOwned, Base):
    __tablename__ = "private_attachments"
    file_name: Mapped[str] = mapped_column(String(200), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # Until contacts/financial records are introduced, attachments belong to a
    # verified shop. Later record links must use composite ownership constraints.
