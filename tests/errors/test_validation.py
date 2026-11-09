import pytest

from app.core.exceptions import (
    AmountPrecisionError,
    AppError,
    AppValidationError,
    InvalidCurrencyPrecisionError,
    NonFiniteAmountError,
    NullFieldError,
    UnsupportedTwoFactorMethodError,
)
from app.core.i18n.types import T
from app.schemas.base.model import Model
from app.schemas.money import Money
from app.schemas.role.request.role import UpdateRoleRequest
from app.services.auth.two_factor.service import TwoFactorService


@pytest.mark.parametrize(
    "validate",
    [
        lambda: Money(amount=0.1, currency="USD"),
        lambda: Money.model_validate({"amount": 0.1, "currency": "USD"}),
        lambda: Money.model_validate_json('{"amount":0.1,"currency":"USD"}'),
        lambda: Money.model_validate_json("{invalid json"),
        lambda: Money.model_validate_strings({"amount": "invalid", "currency": "USD"}),
    ],
)
def test_schema_entry_points_raise_application_errors(validate):
    with pytest.raises(AppValidationError) as caught:
        validate()
    assert isinstance(caught.value, AppError)
    assert caught.value.code == 422
    assert caught.value.message == T("validation:failed")
    assert caught.value.errors()
    assert all(set(e) == {"type", "loc", "msg"} for e in caught.value.errors())
    assert all(e["msg"] != "validation:failed" for e in caught.value.errors())


def test_assignment_errors_use_the_same_application_contract():
    class Example(Model):
        count: int

    value = Example(count=1)
    with pytest.raises(AppValidationError):
        value.count = "invalid"


def test_custom_null_error_retains_field_location():
    with pytest.raises(NullFieldError) as caught:
        UpdateRoleRequest(name=None)
    assert caught.value.errors() == [
        {
            "type": "null_forbidden",
            "loc": ("name",),
            "msg": T("validation:null_forbidden"),
        }
    ]


def test_money_precision_and_non_finite_errors_are_application_errors():
    with pytest.raises(InvalidCurrencyPrecisionError):
        Money(amount="1", currency="USD").decimal(9)
    with pytest.raises(AmountPrecisionError):
        Money(amount="1.001", currency="USD").decimal(2)
    with pytest.raises(NonFiniteAmountError):
        Money.model_construct(amount="NaN", currency="USD").decimal(2)


def test_unsupported_two_factor_method_is_an_application_validation_error():
    with pytest.raises(UnsupportedTwoFactorMethodError) as caught:
        TwoFactorService._get_provider("unsupported")
    assert isinstance(caught.value, AppError)
    assert caught.value.message == T("auth:unsupported_two_factor_method")
