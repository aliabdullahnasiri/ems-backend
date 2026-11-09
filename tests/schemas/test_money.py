from decimal import Decimal

import pytest

from app.core.exceptions import AmountPrecisionError, AppValidationError
from app.schemas.money import Money


def test_money_preserves_exact_values_and_precision():
    value = Money(amount="999999999999999999.01", currency="AFN")
    assert value.decimal(2) == Decimal("999999999999999999.01")
    assert value.model_dump() == {"amount": "999999999999999999.01", "currency": "AFN"}
    with pytest.raises(AmountPrecisionError):
        Money(amount="1.001", currency="AFN").decimal(2)


@pytest.mark.parametrize(
    "amount", [0.1, 1, "NaN", "Infinity", "1e3", "-1", "01", "1.123456789"]
)
def test_money_rejects_inexact_or_invalid_input(amount):
    with pytest.raises(AppValidationError):
        Money(amount=amount, currency="USD")
