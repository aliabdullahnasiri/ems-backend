from app.schemas.base.response import BaseResponseModel
from app.schemas.contact.data import (
    ContactBalanceData,
    ContactData,
    ContactStatementData,
)


class ContactResponse(BaseResponseModel[ContactData]):
    pass


class ContactListResponse(BaseResponseModel[list[ContactData]]):
    pass


class ContactBalanceResponse(BaseResponseModel[list[ContactBalanceData]]):
    pass


class ContactStatementResponse(BaseResponseModel[ContactStatementData]):
    pass


class ContactOpeningResponse(BaseResponseModel[dict]):
    pass
