from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from app.agents.report_prompts import build_report_messages
from app.agents.scoring_prompts import build_scoring_messages
from app.agents.schemas import AgentRuntimeContext, ReportOutput, ScoringOutput
from app.core.config import get_settings
from app.schemas.model_gateway import ChatMessage, ModelChatRequest
from app.services.model_gateway_service import ModelGatewayService
from app.services.runtime_reliability_config import scoring_report_timeout_seconds


T = TypeVar("T", bound=BaseModel)


@dataclass
class CLLMResult(Generic[T]):
    success: bool
    output: T | None
    raw_output: str
    error_code: str | None
    error_reason: str | None
    model_name: str | None


class ScoringReportLLMClient:
    def __init__(
        self,
        model_gateway_service: ModelGatewayService | None = None,
        *,
        temperature: float = 0.1,
        max_tokens: int = 2400,
        thinking_enabled: bool = False,
        reasoning_effort: str = "low",
    ) -> None:
        self.model_gateway_service = model_gateway_service or ModelGatewayService(get_settings())
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.thinking_enabled = thinking_enabled
        self.reasoning_effort = reasoning_effort

    def call_scoring(self, context: AgentRuntimeContext) -> CLLMResult[ScoringOutput]:
        return self._call(build_scoring_messages(context), ScoringOutput)

    def call_report(
        self,
        context: AgentRuntimeContext,
        scoring_output: ScoringOutput,
    ) -> CLLMResult[ReportOutput]:
        return self._call(build_report_messages(context, scoring_output), ReportOutput)

    def _call(self, messages: list[dict[str, str]], output_model: type[T]) -> CLLMResult[T]:
        try:
            response = asyncio.run(
                self.model_gateway_service.chat(
                    ModelChatRequest(
                        messages=[ChatMessage(**message) for message in messages],
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                        json_mode=True,
                        thinking_enabled=self.thinking_enabled,
                        reasoning_effort=self.reasoning_effort,
                        timeout_seconds=scoring_report_timeout_seconds(),
                    )
                )
            )
        except Exception as exc:  # noqa: BLE001
            return CLLMResult(False, None, "", "MODEL_GATEWAY_ERROR", str(exc), None)

        raw_output = response.content or ""
        payload = _extract_json_object(raw_output)
        if payload is None:
            return CLLMResult(False, None, raw_output, "INVALID_JSON", "No JSON object found", response.model)
        try:
            output = output_model.model_validate(payload)
        except ValidationError as exc:
            return CLLMResult(False, None, raw_output, "SCHEMA_VALIDATION_ERROR", str(exc), response.model)
        return CLLMResult(True, output, raw_output, None, None, response.model)


def _extract_json_object(text: str) -> dict | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline >= 0:
            stripped = stripped[first_newline + 1 :]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    try:
        loaded = json.loads(stripped)
        return loaded if isinstance(loaded, dict) else None
    except json.JSONDecodeError:
        pass

    start = stripped.find("{")
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(stripped)):
        if stripped[index] == "{":
            depth += 1
        elif stripped[index] == "}":
            depth -= 1
            if depth == 0:
                try:
                    loaded = json.loads(stripped[start : index + 1])
                    return loaded if isinstance(loaded, dict) else None
                except json.JSONDecodeError:
                    return None
    return None
