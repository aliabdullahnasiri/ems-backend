from app.core.exceptions import (
    AmountPrecisionError,
    InvalidCurrencyPrecisionError,
    NonFiniteAmountError,
)

"""Exact, explicit money transport shared by future EMS financial endpoints."""

from decimal import Decimal

from pydantic import ConfigDict, Field

from app.schemas.base.model import Model


class Money(Model):
    model_config = ConfigDict(extra="forbid")
    # JSON numbers and scientific notation are intentionally not money input.
    amount: str = Field(strict=True, pattern=r"^(0|[1-9][0-9]{0,17})(\.[0-9]{1,8})?$")
    currency: str = Field(strict=True, pattern=r"^[A-Z]{3}$")

    def decimal(self, precision: int) -> Decimal:
        """Validate the configured currency precision without implicit rounding."""
        if not 0 <= precision <= 8:
            raise InvalidCurrencyPrecisionError()
        amount = Decimal(self.amount)
        exponent = amount.as_tuple().exponent
        if not isinstance(exponent, int):
            raise NonFiniteAmountError()
        if -exponent > precision:
            raise AmountPrecisionError()
        return amount
