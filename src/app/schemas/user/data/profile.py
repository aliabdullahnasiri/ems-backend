from datetime import date, datetime
from uuid import UUID

from pydantic import EmailStr

from app.schemas.base import BaseDataModel


class UserData(BaseDataModel):
    """
    Data transfer object for User information.

    Represents the core user attributes used in responses and internal
    data handling.
    """

    uuid: UUID
    first_name: str | None
    middle_name: str | None
    last_name: str | None
    user_name: str
    email: EmailStr
    two_factor_enabled: bool = False
    two_factor_method: str | None = None
    birthday: date | None
    created_at: datetime
    updated_at: datetime
