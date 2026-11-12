"""Exact decimals and Kabul business cutoffs, shared by financial workflows."""

import re
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from zoneinfo import ZoneInfo

from app.core.exceptions import AppValidationError

KABUL = ZoneInfo("Asia/Kabul")
TRANSLATION = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫", "01234567890123456789.")


def decimal_text(value: object, precision: int, *, signed=False) -> Decimal:
    """Normalize Persian/Arabic digits; reject floats, grouping and exponents."""
    if (
        not isinstance(value, str)
        or type(precision) is not int
        or not 0 <= precision <= 8
    ):
        raise AppValidationError()
    value = value.translate(TRANSLATION)
    pattern = (
        r"-?(0|[1-9][0-9]{0,17})(\.[0-9]{1,8})?"
        if signed
        else r"(0|[1-9][0-9]{0,17})(\.[0-9]{1,8})?"
    )
    if not re.fullmatch(pattern, value):
        raise AppValidationError()
    amount = Decimal(value)
    exponent = amount.as_tuple().exponent
    if not isinstance(exponent, int) or -exponent > precision:
        raise AppValidationError()
    return amount


def exact(value: Decimal, precision: int) -> str:
    """Round calculated values half-up; input amounts are never silently rounded."""
    with localcontext() as ctx:
        ctx.prec = 60
        return format(
            value.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP), "f"
        )


def business_day(value: datetime) -> date:
    """Require an explicit instant; derive its Kabul calendar day."""
    if value.tzinfo is None:
        raise AppValidationError()
    return value.astimezone(KABUL).date()


def day_cutoffs(value: date) -> tuple[datetime, datetime]:
    """UTC half-open interval [start, next midnight) for a Kabul business day."""
    start = datetime.combine(value, time.min, KABUL)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)
