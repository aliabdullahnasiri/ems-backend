from app.schemas.base.response import BaseResponseModel
from app.schemas.finance.data import EntryData
from app.schemas.hawala.data import (
    AgentObligationData,
    AgentSettlementData,
    HawalaData,
    HawalaReceiptData,
)


class HawalaResponse(BaseResponseModel[HawalaData]):
    pass


class HawalasResponse(BaseResponseModel[list[HawalaData]]):
    pass


class HawalaReceiptResponse(BaseResponseModel[HawalaReceiptData]):
    pass


class AgentObligationsResponse(BaseResponseModel[list[AgentObligationData]]):
    pass


class AgentSettlementResponse(BaseResponseModel[AgentSettlementData]):
    pass


class AgentSettlementsResponse(BaseResponseModel[list[AgentSettlementData]]):
    pass


class AgentReversalResponse(BaseResponseModel[EntryData]):
    pass
