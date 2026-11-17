from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.exceptions.authentication import (
    EmailVerificationTokenExpiredError,
    PasswordResetTokenExpiredError,
)
from app.models import User
from app.services.datetime import DateTimeService
from app.services.user import UserService


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["email_verification", "password_reset"])
@pytest.mark.parametrize("zone", [None, UTC, timezone(timedelta(hours=4, minutes=30))])
@pytest.mark.parametrize("seconds", [-1, 0, 1])
@pytest.mark.parametrize("aware_clock", [False, True])
async def test_token_expiry_handles_database_timezone_variants(
    db_session, monkeypatch, kind, zone, seconds, aware_clock
):
    now = datetime(2026, 11, 16, 12, tzinfo=UTC)
    monkeypatch.setattr(
        DateTimeService,
        "utc_now",
        staticmethod(lambda: now if aware_clock else now.replace(tzinfo=None)),
    )
    user = User(user_name="expiry", email="expiry@example.com", password_hash="unused")
    setattr(user, f"{kind}_token_hash", "token-hash")
    db_session.add(user)
    await db_session.commit()
    # Keep the assigned value in the identity map to exercise aware PostgreSQL
    # values as well as naive SQLite values (SQLite strips offsets on reload).
    expiry = now + timedelta(seconds=seconds)
    expiry = expiry.astimezone(zone) if zone else expiry.replace(tzinfo=None)
    setattr(user, f"{kind}_expires_at", expiry)
    lookup = (
        UserService.get_by_verification_token
        if kind == "email_verification"
        else UserService.get_by_reset_token
    )
    error = (
        EmailVerificationTokenExpiredError
        if kind == "email_verification"
        else PasswordResetTokenExpiredError
    )
    if seconds <= 0:
        with pytest.raises(error):
            await lookup(db_session, "token-hash")
    else:
        assert await lookup(db_session, "token-hash") is user


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["email_verification", "password_reset"])
async def test_token_without_expiry_is_rejected(db_session, kind):
    user = User(user_name="expiry", email="expiry@example.com", password_hash="unused")
    setattr(user, f"{kind}_token_hash", "token-hash")
    db_session.add(user)
    await db_session.commit()
    if kind == "email_verification":
        with pytest.raises(EmailVerificationTokenExpiredError):
            await UserService.get_by_verification_token(db_session, "token-hash")
    else:
        with pytest.raises(PasswordResetTokenExpiredError):
            await UserService.get_by_reset_token(db_session, "token-hash")
