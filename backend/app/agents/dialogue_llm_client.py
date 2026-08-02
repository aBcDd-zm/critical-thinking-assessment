from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from app.agents.base import AgentOutputParseError, parse_agent_output
from app.agents.dialogue_prompts import build_followup_messages, build_host_messages
from app.agents.schemas import AgentRuntimeContext, FollowupOutput, HostOutput
from app.core.config import get_settings
from app.schemas.model_gateway import ChatMessage, ModelChatRequest, ModelChatResponse
from app.services.model_gateway_service import ModelGatewayService

logger = logging.getLogger(__name__)


@dataclass
class DialogueLLMResult:
    success: bool
    output: HostOutput | FollowupOutput | None
    raw_output: str
    error_code: str | None
    error_reason: str | None
    model_name: str | None


class DialogueLLMClient:
    def __init__(
        self,
        model_gateway_service: ModelGatewayService | None = None,
        *,
        temperature: float = 0.2,
        max_tokens: int = 1200,
        thinking_enabled: bool = False,
        reasoning_effort: str = "low",
    ) -> None:
        self.model_gateway_service = model_gateway_service or ModelGatewayService(
            get_settings()
        )
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.thinking_enabled = thinking_enabled
        self.reasoning_effort = reasoning_effort

    def call_host(self, context: AgentRuntimeContext) -> DialogueLLMResult:
        return self._call_model(build_host_messages(context), HostOutput, "host")

    def call_followup(self, context: AgentRuntimeContext) -> DialogueLLMResult:
        return self._call_model(
            build_followup_messages(context),
            FollowupOutput,
            "followup",
        )

    def _call_model(
        self,
        messages: list[dict[str, str]],
        output_model: type[HostOutput | FollowupOutput],
        agent_name: str,
    ) -> DialogueLLMResult:
        try:
            request = ModelChatRequest(
                messages=[ChatMessage(**message) for message in messages],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                json_mode=True,
                thinking_enabled=self.thinking_enabled,
                reasoning_effort=self.reasoning_effort,
            )
            response = self._invoke_chat(request)
        except Exception as exc:  # noqa: BLE001
            logger.warning("dialogue model call failed for %s: %s", agent_name, exc)
            return DialogueLLMResult(
                success=False,
                output=None,
                raw_output="",
                error_code="MODEL_GATEWAY_ERROR",
                error_reason=str(exc),
                model_name=None,
            )

        raw_output = response.content or ""
        parsed = self._parse_output(output_model, raw_output)
        if parsed is None:
            return DialogueLLMResult(
                success=False,
                output=None,
                raw_output=raw_output,
                error_code="INVALID_OUTPUT",
                error_reason="model output does not match dialogue schema",
                model_name=response.model,
            )

        return DialogueLLMResult(
            success=True,
            output=parsed,
            raw_output=raw_output,
            error_code=None,
            error_reason=None,
            model_name=response.model,
        )

    def _invoke_chat(self, request: ModelChatRequest) -> ModelChatResponse:
        settings = get_settings()
        if settings.MODEL_GATEWAY_MODE.lower() == "mock":
            return self.model_gateway_service._mock_chat(request)
        return asyncio.run(self.model_gateway_service.chat(request))

    @staticmethod
    def _parse_output(
        output_model: type[HostOutput | FollowupOutput],
        raw_output: str,
    ) -> HostOutput | FollowupOutput | None:
        text = _strip_markdown_json(raw_output)
        try:
            return parse_agent_output(output_model, text)
        except AgentOutputParseError:
            pass

        payload = _extract_json_object(text)
        if payload is None:
            return None
        payload = _normalize_dialogue_payload(output_model, payload)
        try:
            return output_model.model_validate(payload)
        except Exception:  # noqa: BLE001
            return None


def _strip_markdown_json(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    first_newline = stripped.find("\n")
    if first_newline >= 0:
        stripped = stripped[first_newline + 1 :]
    if stripped.endswith("```"):
        stripped = stripped[:-3]
    return stripped.strip()


def _extract_json_object(text: str) -> dict[str, Any] | None:
    try:
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else None
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                try:
                    loaded = json.loads(text[start : index + 1])
                    return loaded if isinstance(loaded, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def _normalize_dialogue_payload(
    output_model: type[HostOutput | FollowupOutput],
    payload: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(payload)
    if output_model is HostOutput:
        normalized["content_type"] = _map_value(
            normalized.get("content_type"),
            {
                "text": "stage_question",
                "question": "stage_question",
                "stage": "stage_question",
                "opening": "stage_question",
                "开场问题": "stage_question",
            },
            default="stage_question",
        )
        normalized["next_action"] = _map_value(
            normalized.get("next_action"),
            {
                "等待用户回答": "wait_user_answer",
                "wait_for_user_response": "wait_user_answer",
                "wait_for_user_answer": "wait_user_answer",
                "ask_user": "wait_user_answer",
                "next_stage": "advance_stage",
                "进入下一阶段": "advance_stage",
            },
            default="wait_user_answer",
        )
        return normalized

    normalized["content_type"] = _map_value(
        normalized.get("content_type"),
        {
            "text": "followup_question",
            "question": "followup_question",
            "follow_up": "followup_question",
            "followup": "followup_question",
            "追问": "followup_question",
            "dynamic_update": "dynamic_info_question",
            "dynamic_info": "dynamic_info_question",
            "动态信息": "dynamic_info_question",
            "补充问题": "supplement_question",
            "继续补充": "supplement_question",
            "需要选择": "stage_incomplete_prompt",
            "advance": "advance_prompt",
            "阶段推进": "advance_prompt",
        },
        default="followup_question",
    )
    normalized["question_type"] = _map_value(
        normalized.get("question_type"),
        {
            "follow_up": "open_followup",
            "followup": "open_followup",
            "follow_up_question": "open_followup",
            "open": "open_followup",
            "probe": "clarify",
            "clarification": "clarify",
            "动态更新": "dynamic_update",
            "动态信息": "dynamic_update",
            "阶段推进": "advance",
        },
        default="clarify",
    )
    normalized["next_action"] = _map_value(
        normalized.get("next_action"),
        {
            "等待用户回答": "ask_followup",
            "wait_for_user_response": "ask_followup",
            "wait_for_user_answer": "ask_followup",
            "wait_user_answer": "ask_followup",
            "follow_up": "ask_followup",
            "continue": "ask_followup",
            "继续追问": "ask_followup",
            "进入下一阶段": "advance_stage",
            "next_stage": "advance_stage",
            "完成测评": "finish_ready",
            "准备报告": "finish_ready",
        },
        default="ask_followup",
    )
    resolved_evidence = normalized.get("resolved_evidence")
    if isinstance(resolved_evidence, list):
        normalized["resolved_evidence"] = [
            _normalize_resolved_evidence_item(item)
            for item in resolved_evidence
            if isinstance(item, dict)
        ]
    return normalized


def _normalize_resolved_evidence_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        key: value
        for key, value in item.items()
        if key != "supporting_turn_ids"
    }
    normalized["coverage"] = _map_value(
        item.get("coverage"),
        {
            "complete": "covered",
            "completed": "covered",
            "充分": "covered",
            "部分": "partial",
            "incomplete": "partial",
            "not_covered": "missing",
            "缺失": "missing",
        },
        default="missing",
    )
    normalized["supporting_turn_indexes"] = item.get(
        "supporting_turn_indexes",
        item.get("supporting_turn_ids", []),
    )
    return normalized


def _map_value(value: Any, aliases: dict[str, str], *, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return aliases.get(text, text)


__all__ = ["DialogueLLMClient", "DialogueLLMResult"]
