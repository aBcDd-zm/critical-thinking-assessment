from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.schemas.model_gateway import (
    ModelChatRequest,
    ModelChatResponse,
    ModelGatewayStatus,
)
from app.services.model_gateway_service import ModelGatewayService

router = APIRouter(prefix="/model-gateway", tags=["model-gateway"])


@router.get("/status", response_model=ModelGatewayStatus)
def get_model_gateway_status(
    settings: Settings = Depends(get_settings),
) -> ModelGatewayStatus:
    return ModelGatewayService(settings).get_status()


@router.post("/chat", response_model=ModelChatResponse)
async def chat(
    payload: ModelChatRequest,
    settings: Settings = Depends(get_settings),
) -> ModelChatResponse:
    return await ModelGatewayService(settings).chat(payload)
