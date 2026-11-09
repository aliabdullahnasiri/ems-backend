from contextvars import ContextVar, Token

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions.request import DatabaseContextUnavailableError

_db_context: ContextVar[AsyncSession | None] = ContextVar(
    "db_context",
    default=None,
)


def set_db(db: AsyncSession) -> Token[AsyncSession | None]:
    """
    Set the current database session.
    """
    return _db_context.set(db)


def get_current_db() -> AsyncSession:
    """
    Return the database session associated with the current request.

    Raises:
        DatabaseContextUnavailableError: If no database session is available.
    """
    db = _db_context.get()

    if db is None:
        raise DatabaseContextUnavailableError()

    return db


def reset_db(token: Token[AsyncSession | None]) -> None:
    """
    Reset the current database session context.
    """
    _db_context.reset(token)
