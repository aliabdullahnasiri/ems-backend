from app.schemas.base.response import BaseResponseModel
from app.schemas.payment.data import (
    ObligationData,
    PaymentData,
    PaymentReceiptData,
    PaymentResultData,
    PaymentReversalData,
)


class PaymentResponse(BaseResponseModel[PaymentData]):
    pass


class PaymentResultResponse(BaseResponseModel[PaymentResultData]):
    pass


class PaymentsResponse(BaseResponseModel[list[PaymentData]]):
    pass


class PaymentReceiptResponse(BaseResponseModel[PaymentReceiptData]):
    pass


class OutstandingResponse(BaseResponseModel[list[ObligationData]]):
    pass


class PaymentReversalResponse(BaseResponseModel[PaymentReversalData]):
    pass
