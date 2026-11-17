import pytest
from httpx import AsyncClient

from app.schemas.auth.request.two_factor import (
    OTPEnableRequest,
    TwoFactorDisableRequest,
)


@pytest.mark.asyncio
async def test_otp_setup(client: AsyncClient, fake_session):
    """Test OTP setup endpoint."""
    res = await client.post("/api/v1/auth/2fa/otp/setup")
    assert res.status_code == 200
    assert "data" in res.json()
    assert "message" in res.json()["data"]


@pytest.mark.asyncio
async def test_otp_resend(client: AsyncClient, fake_session):
    """Test OTP resend endpoint."""
    # First, setup OTP to ensure there is a code to resend
    await client.post("/api/v1/auth/2fa/otp/setup")

    res = await client.post("/api/v1/auth/2fa/otp/resend")
    assert res.status_code == 200
    assert res.json()["success"] is True


@pytest.mark.asyncio
async def test_otp_enable(client: AsyncClient, fake_session):
    """Test OTP enable endpoint (expecting failure due to invalid code)."""
    data = OTPEnableRequest(code="123456")
    res = await client.post("/api/v1/auth/2fa/otp/enable", json=data.model_dump())
    assert res.status_code != 404


@pytest.mark.asyncio
async def test_otp_disable(client: AsyncClient, fake_session):
    """Test OTP disable endpoint."""
    data = TwoFactorDisableRequest(password="password123", code="123456")
    res = await client.post("/api/v1/auth/2fa/otp/disable", json=data.model_dump())
    assert res.status_code != 404


@pytest.mark.asyncio
async def test_email_otp_login_lifecycle(client, fake_session, mock_mail):
    response = await client.post("/api/v1/auth/2fa/otp/setup")
    assert response.status_code == 200, response.text
    code = mock_mail["send_template"].await_args.kwargs["otp_code"]
    response = await client.post("/api/v1/auth/2fa/otp/enable", json={"code": code})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["recovery_codes"]
    client.headers.pop("Authorization", None)
    mock_mail["send_template"].reset_mock()
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "test@example.com", "password": "password123"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["requires_2fa"] is True
    assert data["access_token"] is None
    mock_mail["send_template"].assert_awaited_once()
    code = mock_mail["send_template"].await_args.kwargs["otp_code"]
    challenge_headers = {"Authorization": f"Bearer {data['challenge_token']}"}
    denied = await client.get("/api/v1/auth/me", headers=challenge_headers)
    assert denied.status_code == 401, denied.text
    response = await client.post(
        "/api/v1/auth/2fa/verify", json={"code": code}, headers=challenge_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["access_token"]
    replay = await client.post(
        "/api/v1/auth/2fa/verify", json={"code": code}, headers=challenge_headers
    )
    assert replay.status_code == 401, replay.text
