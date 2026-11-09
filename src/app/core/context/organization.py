from app.core.i18n.types import T

"""Defense in depth for ORM work inside verified request/job/posting contexts."""

from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session, with_loader_criteria

from app.core.exceptions import AuthorizationError
from app.models.branch import Branch
from app.models.ownership import OrganizationOwned

_current_organization: ContextVar[int | None] = ContextVar(
    "ems_organization", default=None
)


@contextmanager
def organization_scope(context):
    token = _current_organization.set(context.organization_id)
    try:
        yield
    finally:
        _current_organization.reset(token)


@event.listens_for(Session, "do_orm_execute")
def _scope_queries(state):
    organization_id = _current_organization.get()
    if organization_id is None:
        return
    # Textual/Core SQL and bulk INSERT cannot carry ORM ownership predicates.
    # EMS code must use ORM reads/writes or a separately reviewed migration.
    if not state.is_orm_statement or state.is_insert:
        raise AuthorizationError(message=T("validation:scoped_orm_required"))
    if state.is_update:
        values = getattr(state.statement, "_values", {}) or {}
        if any(
            getattr(column, "key", column)
            in {"organization_id", "shop_id", "membership_id", "user_id"}
            for column in values
        ):
            raise AuthorizationError(message=T("validation:immutable_ownership"))
        if state.is_executemany:
            raise AuthorizationError(
                message=T("validation:scoped_bulk_update_required")
            )
    if state.is_select or state.is_update or state.is_delete:
        state.statement = state.statement.options(
            with_loader_criteria(
                OrganizationOwned,
                lambda model: model.organization_id == organization_id,
                include_aliases=True,
            ),
            with_loader_criteria(
                Branch,
                lambda model: model.organization_id == organization_id,
                include_aliases=True,
            ),
        )


@event.listens_for(Session, "before_flush")
def _scope_writes(session, flush_context, instances):
    organization_id = _current_organization.get()
    if organization_id is None:
        return
    for record in session.new | session.dirty | session.deleted:
        if not isinstance(record, (OrganizationOwned, Branch)):
            continue
        if record in session.new and record.organization_id is None:
            record.organization_id = organization_id
        if record.organization_id != organization_id:
            raise AuthorizationError()
        if (
            inspect(record).persistent
            and inspect(record).attrs.organization_id.history.has_changes()
        ):
            raise AuthorizationError(message=T("validation:immutable_ownership"))


_posting_boundaries: ContextVar[dict | None] = ContextVar(
    "ems_posting_boundaries", default=None
)


@contextmanager
def posting_callback(db):
    """A callback may use savepoints but cannot commit the posting transaction."""
    session = db.sync_session
    token = _posting_boundaries.set(
        {
            **(_posting_boundaries.get() or {}),
            id(session): session.get_nested_transaction(),
        }
    )
    try:
        yield
    finally:
        _posting_boundaries.reset(token)


@event.listens_for(Session, "before_commit")
def _protect_posting_commit(session):
    boundaries = _posting_boundaries.get() or {}
    if (
        id(session) in boundaries
        and session.get_nested_transaction() is boundaries[id(session)]
    ):
        from app.core.exceptions import ConflictError

        raise ConflictError(message=T("validation:posting_commit_forbidden"))
