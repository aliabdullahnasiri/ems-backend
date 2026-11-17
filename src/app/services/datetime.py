from datetime import UTC, datetime


class DateTimeService:
    @staticmethod
    def is_expired(expires_at: datetime | None) -> bool:
        """Check expiry inclusively, interpreting naive database timestamps as UTC."""
        if expires_at is None:
            return True
        now = DateTimeService.utc_now()
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        return expires_at <= now

    @staticmethod
    def utc_now() -> datetime:
        """Return the current UTC time as a naive datetime."""
        return datetime.now(UTC).replace(tzinfo=None)
