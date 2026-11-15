from app.schemas.base.response import BaseResponseModel
from app.schemas.daily.data import CategoryData, ClosingData, DayData, OperationData


class CategoryResponse(BaseResponseModel[CategoryData]):
    pass


class CategoriesResponse(BaseResponseModel[list[CategoryData]]):
    pass


class OperationResponse(BaseResponseModel[OperationData]):
    pass


class OperationsResponse(BaseResponseModel[list[OperationData]]):
    pass


class DayResponse(BaseResponseModel[DayData | None]):
    pass


class ClosingResponse(BaseResponseModel[ClosingData]):
    pass


class ClosingsResponse(BaseResponseModel[list[ClosingData]]):
    pass
