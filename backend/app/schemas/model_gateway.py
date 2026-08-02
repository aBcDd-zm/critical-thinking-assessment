from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ModelGatewayStatus(BaseModel):
    provider: str
    mode: str
    model: str
    base_url: str
    api_key_configured: bool
    thinking_enabled: bool
    reasoning_effort: str


class ModelChatRequest(BaseModel):
    messages: list[ChatMessage]
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=1)
    json_mode: bool = False
    thinking_enabled: bool | None = None
    reasoning_effort: str | None = None
    timeout_seconds: float | None = Field(default=None, ge=1, le=60)


class ModelChatResponse(BaseModel):
    provider: str
    model: str
    content: str
    raw_response: dict[str, Any] | None = None
