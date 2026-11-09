"""Database-enforced ownership contracts for EMS records."""

from sqlalchemy import ForeignKey, ForeignKeyConstraint, UniqueConstraint, event
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


class OrganizationOwned:
    """Tenant identity is required and deletion cannot discard business history."""

    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    @declared_attr.directive
    def __table_args__(cls):
        return (
            UniqueConstraint(
                "organization_id", "id", name=f"uq_{cls.__tablename__}_organization_id"
            ),
        )


class ShopOwned(OrganizationOwned):
    shop_id: Mapped[int] = mapped_column(nullable=False, index=True)

    @declared_attr.directive
    def __table_args__(cls):
        return (
            UniqueConstraint(
                "organization_id",
                "shop_id",
                "id",
                name=f"uq_{cls.__tablename__}_shop_id",
            ),
            UniqueConstraint(
                "organization_id", "id", name=f"uq_{cls.__tablename__}_organization_id"
            ),
            ForeignKeyConstraint(
                ["organization_id", "shop_id"],
                ["branches.organization_id", "branches.id"],
                ondelete="RESTRICT",
                name=f"fk_{cls.__tablename__}_shop_organization",
            ),
        )


def owned_reference(name: str, column: str, target: str) -> ForeignKeyConstraint:
    """Include this composite FK on every reference to an organization-owned row."""
    return ForeignKeyConstraint(
        ["organization_id", column],
        [f"{target}.organization_id", f"{target}.id"],
        ondelete="RESTRICT",
        name=name,
    )


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


class ImmutableHistory:
    """Append-only posted tables; database triggers reject updates and deletion."""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "__table__"):
            event.listen(cls.__table__, "after_create", _create_immutable_triggers)


def shop_reference(name: str, column: str, target: str) -> ForeignKeyConstraint:
    """Use for financial links requiring both the same tenant and the same shop."""
    return ForeignKeyConstraint(
        ["organization_id", "shop_id", column],
        [f"{target}.organization_id", f"{target}.shop_id", f"{target}.id"],
        ondelete="RESTRICT",
        name=name,
    )
