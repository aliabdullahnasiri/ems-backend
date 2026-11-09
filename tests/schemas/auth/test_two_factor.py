import pytest

from app.core.exceptions import AppValidationError
from app.schemas.auth.internal.two_factor import TwoFactorChallengePayload


def test_two_factor_challenge_payload_valid():
    # Valid data
    data = {
        "sub": "user123",
        "jti": "token456",
        "purpose": "2fa_challenge",
        "exp": 1730000000,
        "iat": 1720000000,
    }
    payload = TwoFactorChallengePayload(**data)
    assert payload.sub == data["sub"]
    assert payload.jti == data["jti"]
    assert payload.purpose == data["purpose"]
    assert payload.exp == data["exp"]
    assert payload.iat == data["iat"]


def test_two_factor_challenge_payload_invalid_purpose():
    # Invalid purpose Literal
    data = {
        "sub": "user123",
        "jti": "token456",
        "purpose": "wrong_purpose",
        "exp": 1730000000,
        "iat": 1720000000,
    }
    with pytest.raises(AppValidationError):
        TwoFactorChallengePayload(**data)


def test_two_factor_challenge_payload_missing_fields():
    # Missing required fields
    with pytest.raises(AppValidationError):
        TwoFactorChallengePayload(sub="user123")


def test_two_factor_challenge_payload_empty_strings():
    # min_length=1 constraint
    with pytest.raises(AppValidationError):
        TwoFactorChallengePayload(
            sub="",
            jti="token456",
            purpose="2fa_challenge",
            exp=1730000000,
            iat=1720000000,
        )


def test_two_factor_challenge_payload_invalid_timestamp():
    # Timestamp overflows are normalized to an application validation error.
    with pytest.raises(AppValidationError):
        TwoFactorChallengePayload(
            sub="user123",
            jti="token456",
            purpose="2fa_challenge",
            exp=float("inf"),
            iat=1720000000,
        )
