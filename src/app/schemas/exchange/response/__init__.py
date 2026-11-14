from app.schemas.base.response import BaseResponseModel
from app.schemas.exchange.data import ExchangeData, QuoteData, RateData, ReceiptData


class RateResponse(BaseResponseModel[RateData]):
    pass


class RatesResponse(BaseResponseModel[list[RateData]]):
    pass


class QuoteResponse(BaseResponseModel[QuoteData]):
    pass


class ExchangeResponse(BaseResponseModel[ExchangeData]):
    pass


class ExchangesResponse(BaseResponseModel[list[ExchangeData]]):
    pass


class ReceiptResponse(BaseResponseModel[ReceiptData]):
    pass
