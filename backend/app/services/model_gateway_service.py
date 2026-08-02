import json
from collections.abc import Iterator
from typing import Any

import httpx
from fastapi import HTTPException, status

from app.core.config import Settings
from app.schemas.model_gateway import (
    ChatMessage,
    ModelChatRequest,
    ModelChatResponse,
    ModelGatewayStatus,
)


class ModelIdentityResponseError(HTTPException):
    """The provider response omitted a usable raw model identity."""

    def __init__(self, *, raw_output: str) -> None:
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="DeepSeek API response does not contain a valid model identity.",
        )
        self.raw_output = raw_output


class ModelGatewayService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def get_status(self) -> ModelGatewayStatus:
        return ModelGatewayStatus(
            provider=self.settings.MODEL_PROVIDER,
            mode=self.settings.MODEL_GATEWAY_MODE,
            model=self.settings.DEEPSEEK_MODEL,
            base_url=self.settings.DEEPSEEK_BASE_URL,
            api_key_configured=bool(self.settings.DEEPSEEK_API_KEY),
            thinking_enabled=self.settings.DEEPSEEK_ENABLE_THINKING,
            reasoning_effort=self.settings.DEEPSEEK_REASONING_EFFORT,
        )

    async def chat(self, payload: ModelChatRequest) -> ModelChatResponse:
        mode = self.settings.MODEL_GATEWAY_MODE.lower()
        provider = self.settings.MODEL_PROVIDER.lower()

        if mode == "mock":
            return self._mock_chat(payload)

        if provider != "deepseek":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported model provider: {self.settings.MODEL_PROVIDER}",
            )

        return await self._deepseek_chat(payload)

    def _mock_chat(self, payload: ModelChatRequest) -> ModelChatResponse:
        last_user_message = self._last_user_message(payload.messages)
        if payload.json_mode:
            content = json.dumps(
                {
                    "mode": "mock",
                    "message": "\u8fd9\u662f\u7edf\u4e00\u6a21\u578b\u7f51\u5173\u7684 mock JSON \u8f93\u51fa\u3002",
                    "echo": last_user_message,
                    "next_action": "replace_with_agent_logic",
                },
                ensure_ascii=False,
            )
        else:
            content = (
                "\u8fd9\u662f\u7edf\u4e00\u6a21\u578b\u7f51\u5173\u7684 mock \u56de\u590d\u3002"
                "\u771f\u5b9e DeepSeek \u63a5\u5165\u4f4d\u7f6e\u5df2\u9884\u7559\uff0c"
                "\u5f53\u524d\u7528\u4e8e\u4fdd\u8bc1\u57fa\u7ebf\u7248\u53ef\u5728\u65e0 API Key "
                f"\u73af\u5883\u4e0b\u8fd0\u884c\u3002\u4f60\u7684\u8f93\u5165\uff1a{last_user_message}"
            )

        return ModelChatResponse(
            provider=self.settings.MODEL_PROVIDER,
            model=self.settings.DEEPSEEK_MODEL,
            content=content,
            raw_response={
                "mode": "mock",
                "request_message_count": len(payload.messages),
            },
        )

    async def _deepseek_chat(self, payload: ModelChatRequest) -> ModelChatResponse:
        if not self.settings.DEEPSEEK_API_KEY:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="DEEPSEEK_API_KEY is required when MODEL_GATEWAY_MODE=real.",
            )

        request_body = self._build_deepseek_request_body(payload, stream=False)
        endpoint = f"{self.settings.DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"
        headers = self._deepseek_headers()

        try:
            async with httpx.AsyncClient(
                timeout=payload.timeout_seconds or self.settings.DEEPSEEK_TIMEOUT_SECONDS
            ) as client:
                response = await client.post(endpoint, headers=headers, json=request_body)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "message": "DeepSeek API returned an error.",
                    "status_code": exc.response.status_code,
                    "response": exc.response.text,
                },
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"DeepSeek API request failed ({type(exc).__name__}): {exc}",
            ) from exc

        raw_response = response.json()
        content = self._extract_content(raw_response)
        response_model = self._extract_model(raw_response, raw_output=content)
        return ModelChatResponse(
            provider=self.settings.MODEL_PROVIDER,
            model=response_model,
            content=content,
            raw_response=raw_response,
        )

    def stream_chat_text(self, payload: ModelChatRequest) -> Iterator[str]:
        mode = self.settings.MODEL_GATEWAY_MODE.lower()
        provider = self.settings.MODEL_PROVIDER.lower()

        if mode == "mock":
            yield from self._mock_chat_stream(payload)
            return

        if provider != "deepseek":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported model provider: {self.settings.MODEL_PROVIDER}",
            )

        yield from self._deepseek_chat_stream(payload)

    def _deepseek_chat_stream(self, payload: ModelChatRequest) -> Iterator[str]:
        if not self.settings.DEEPSEEK_API_KEY:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="DEEPSEEK_API_KEY is required when MODEL_GATEWAY_MODE=real.",
            )

        request_body = self._build_deepseek_request_body(payload, stream=True)
        endpoint = f"{self.settings.DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"

        try:
            with httpx.Client(
                timeout=payload.timeout_seconds or self.settings.DEEPSEEK_TIMEOUT_SECONDS
            ) as client:
                with client.stream(
                    "POST",
                    endpoint,
                    headers=self._deepseek_headers(),
                    json=request_body,
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        delta = self._extract_sse_delta(line)
                        if delta:
                            yield delta
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "message": "DeepSeek API returned an error.",
                    "status_code": exc.response.status_code,
                    "response": exc.response.text,
                },
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"DeepSeek API streaming request failed: {exc}",
            ) from exc

    def _mock_chat_stream(self, payload: ModelChatRequest) -> Iterator[str]:
        content = self._mock_chat(payload).content
        chunk_size = 6
        for index in range(0, len(content), chunk_size):
            yield content[index : index + chunk_size]

    def _build_deepseek_request_body(
        self,
        payload: ModelChatRequest,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        thinking_enabled = (
            payload.thinking_enabled
            if payload.thinking_enabled is not None
            else self.settings.DEEPSEEK_ENABLE_THINKING
        )
        reasoning_effort = payload.reasoning_effort or self.settings.DEEPSEEK_REASONING_EFFORT

        request_body: dict[str, Any] = {
            "model": self.settings.DEEPSEEK_MODEL,
            "messages": [message.model_dump() for message in payload.messages],
            "temperature": payload.temperature,
            "max_tokens": payload.max_tokens,
            "stream": stream,
            "reasoning_effort": reasoning_effort,
            "thinking": {
                "type": "enabled" if thinking_enabled else "disabled"
            },
        }
        if payload.json_mode:
            request_body["response_format"] = {"type": "json_object"}
        return request_body

    def _deepseek_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _last_user_message(messages: list[ChatMessage]) -> str:
        for message in reversed(messages):
            if message.role == "user":
                return message.content
        return ""

    @staticmethod
    def _extract_content(raw_response: dict[str, Any]) -> str:
        try:
            return str(raw_response["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="DeepSeek API response does not contain choices[0].message.content.",
            )

    @staticmethod
    def _extract_model(
        raw_response: dict[str, Any],
        *,
        raw_output: str,
    ) -> str:
        model = raw_response.get("model")
        if not isinstance(model, str) or not model.strip():
            raise ModelIdentityResponseError(raw_output=raw_output)
        return model.strip()

    @staticmethod
    def _extract_sse_delta(line: str) -> str | None:
        stripped = line.strip()
        if not stripped or not stripped.startswith("data:"):
            return None

        data = stripped.removeprefix("data:").strip()
        if data == "[DONE]":
            return None

        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return None

        try:
            return payload["choices"][0]["delta"].get("content") or None
        except (KeyError, IndexError, TypeError, AttributeError):
            return None
