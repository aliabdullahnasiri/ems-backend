from collections.abc import Mapping, Sequence
from typing import Any

from app.core.exceptions.base import AppError
from app.core.i18n.types import T


class AppValidationError(AppError):
    """Raised when business-level validation fails."""

    code = 422
    type = "validation_error"
    message = T("validation:failed")

    def __init__(
        self,
        message: str | None = None,
        code: int | None = None,
        type: str | None = None,
        *,
        errors: Sequence[Mapping[str, Any]] | None = None,
    ):
        super().__init__(message=message, code=code, type=type)
        # Keep field locations and error codes, never credentials/input/context.
        self._errors = [
            {
                "type": error.get("type", "validation_error"),
                "loc": tuple(error.get("loc", ())),
                "msg": self.field_message(error.get("type", "validation_error")),
            }
            for error in errors or []
        ]

    def errors(self) -> list[dict[str, Any]]:
        return [dict(error) for error in self._errors]

    @staticmethod
    def field_message(error_type: str) -> str:
        messages = {
            "missing": T("validation:required"),
            "extra_forbidden": T("validation:extra_forbidden"),
            "null_forbidden": T("validation:null_forbidden"),
            "string_too_short": T("validation:string_too_short"),
            "string_too_long": T("validation:string_too_long"),
            "string_pattern_mismatch": T("validation:invalid_format"),
            "uuid_parsing": T("validation:invalid_uuid"),
            "uuid_type": T("validation:invalid_uuid"),
            "int_parsing": T("validation:invalid_integer"),
            "int_type": T("validation:invalid_integer"),
            "bool_parsing": T("validation:invalid_boolean"),
            "bool_type": T("validation:invalid_boolean"),
            "literal_error": T("validation:invalid_choice"),
            "enum": T("validation:invalid_choice"),
        }
        return messages.get(error_type, T("validation:failed"))


class ChallengeTokenRequiredError(AppValidationError):
    """Raised when the 2FA challenge token is missing."""

    type = "challenge_token_required"
    message = T("auth:challenge_token_required")


class AuthTokensRequiredError(AppValidationError):
    """Raised when access and refresh tokens are missing after login."""

    type = "auth_tokens_required"
    message = T("auth:auth_tokens_required")


class ChallengeTokenForbiddenError(AppValidationError):
    """Raised when a challenge token is provided but not expected."""

    type = "challenge_token_forbidden"
    message = T("auth:challenge_token_forbidden")


class InvalidTimestampError(AppValidationError):
    """Raised when a provided timestamp is invalid."""

    type = "invalid_timestamp"
    message = T("value_error:invalid_timestamp")


class EmailContentRequiredError(AppValidationError):
    """Raised when an email is sent without content."""

    type = "email_content_required"
    message = T("errors:email_must_contain_text_or_html_content")


class NullFieldError(AppValidationError):
    type = "null_field"
    message = T("validation:null_forbidden")

    def __init__(self, field: str):
        super().__init__(errors=[{"type": "null_forbidden", "loc": (field,)}])


class InvalidCurrencyPrecisionError(AppValidationError):
    type = "invalid_currency_precision"
    message = T("validation:currency_precision")


class NonFiniteAmountError(AppValidationError):
    type = "non_finite_amount"
    message = T("validation:finite_amount_required")


class AmountPrecisionError(AppValidationError):
    type = "amount_precision_exceeded"
    message = T("validation:amount_precision")


class OrganizationOwnershipError(AppValidationError):
    type = "organization_ownership_override"
    message = T("validation:organization_ownership")


class UnsupportedTwoFactorMethodError(AppValidationError):
    type = "unsupported_two_factor_method"
    message = T("auth:unsupported_two_factor_method")


class InvalidFinancialValueError(AppValidationError):
    type = "invalid_financial_value"
    message = T("validation:decimal_strings_required")


class InvalidRetryKeyError(AppValidationError):
    type = "invalid_retry_key"
    message = T("validation:retry_key_required")


class InvalidFileSizeError(AppValidationError):
    type = "invalid_file_size"
    message = T("validation:file_size")


class InvalidFileTypeError(AppValidationError):
    type = "invalid_file_type"
    message = T("validation:file_type")
