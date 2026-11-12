from app.schemas.base.response import BaseResponseModel
from app.schemas.finance.data import CashboxData, CurrencyData, EntryData, SettingsData


class SettingsResponse(BaseResponseModel[SettingsData]):
    pass


class CurrencyResponse(BaseResponseModel[CurrencyData]):
    pass


class CurrenciesResponse(BaseResponseModel[list[CurrencyData]]):
    pass


class CashboxResponse(BaseResponseModel[CashboxData]):
    pass


class CashboxesResponse(BaseResponseModel[list[CashboxData]]):
    pass


class EntryResponse(BaseResponseModel[EntryData]):
    pass


class EntriesResponse(BaseResponseModel[list[EntryData]]):
    pass
