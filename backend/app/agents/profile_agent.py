from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.schemas.model_gateway import ChatMessage, ModelChatRequest
from app.services.model_gateway_service import ModelGatewayService

PROFILE_PROMPT_VERSION = "occupation_profile_v2"
MAX_PROFILE_QUESTIONS = 3

OPENING_PROFILE_QUESTION = (
    "在正式开始前，我想先了解一点你熟悉的日常。你平时最常处理哪一类任务？" "只说任务类型即可，不需要提供单位、地点或真实人物信息。"
)
HUMANISTIC_V11_PROFILE_OPENING = (
    "正式开始前，我们先聊两句你熟悉的日常。你平时最常做哪类任务？" "说任务类型就可以，不用提真实单位、地点或人名。"
)


class ProfileSummary(BaseModel):
    common_tasks: list[str] = Field(default_factory=list, max_length=5)
    collaborators: list[str] = Field(default_factory=list, max_length=5)
    familiar_decision_context: str = Field(default="", max_length=240)
    summary: str = Field(default="", max_length=300)


class ProfileOutput(BaseModel):
    next_action: Literal["ask", "complete"]
    message: str = Field(min_length=2, max_length=300)
    profile: ProfileSummary


@dataclass
class ProfileAgentResult:
    success: bool
    output: ProfileOutput
    raw_output: str
    model_name: str | None
    error_code: str | None = None
    error_reason: str | None = None


class ProfileAgent:
    def __init__(self, gateway: ModelGatewayService | None = None) -> None:
        self.settings = get_settings()
        self.gateway = gateway or ModelGatewayService(self.settings)

    def respond(
        self,
        *,
        occupation_category: str,
        occupation: str,
        answers: list[str],
        question_count: int,
        template_content: str | None = None,
        style_version: str | None = None,
    ) -> ProfileAgentResult:
        if self.settings.MODEL_GATEWAY_MODE.lower() == "mock":
            output = _mock_profile_output(answers, question_count)
            return ProfileAgentResult(
                success=True,
                output=output,
                raw_output=output.model_dump_json(),
                model_name="mock",
            )

        prompt = _profile_prompt(
            occupation_category=occupation_category,
            occupation=occupation,
            answers=answers,
            question_count=question_count,
            template_content=template_content,
        )
        request = ModelChatRequest(
            messages=[
                ChatMessage(
                    role="system",
                    content=(
                        "你是测评前的背景访谈助手，语气温和自然。只采集常见任务、"
                        "协作对象和熟悉的判断场面，不询问任何可识别个人的信息。只输出JSON。"
                    ),
                ),
                ChatMessage(role="user", content=prompt),
            ],
            temperature=0.25,
            max_tokens=700,
            json_mode=True,
            thinking_enabled=False,
            reasoning_effort="low",
        )
        try:
            response = asyncio.run(self.gateway.chat(request))
            payload = _normalize_profile_payload(_extract_json(response.content))
            output = ProfileOutput.model_validate(payload)
            if question_count >= MAX_PROFILE_QUESTIONS:
                output.next_action = "complete"
            if style_version == "humanistic_v1_1":
                output.message = _humanistic_v11_profile_message(
                    answers,
                    next_action=output.next_action,
                )
            return ProfileAgentResult(
                success=True,
                output=output,
                raw_output=response.content,
                model_name=response.model,
            )
        except Exception as exc:  # noqa: BLE001
            output = _fallback_profile_output(answers, question_count)
            return ProfileAgentResult(
                success=False,
                output=output,
                raw_output="",
                model_name=None,
                error_code="PROFILE_AGENT_ERROR",
                error_reason=str(exc),
            )


def _mock_profile_output(answers: list[str], question_count: int) -> ProfileOutput:
    profile = _profile_from_answers(answers)
    if question_count < 2:
        return ProfileOutput(
            next_action="ask",
            message="谢谢。完成这类任务时，你通常会和哪些角色协作，又最常需要判断什么？",
            profile=profile,
        )
    return ProfileOutput(
        next_action="complete",
        message="明白了。我会用你熟悉的任务和协作方式组织接下来的情景。",
        profile=profile,
    )


def _fallback_profile_output(answers: list[str], question_count: int) -> ProfileOutput:
    profile = _profile_from_answers(answers)
    if question_count < 2:
        return ProfileOutput(
            next_action="ask",
            message="你在这类任务中通常会与哪些人协作？只需要说角色类型。",
            profile=profile,
        )
    return ProfileOutput(
        next_action="complete",
        message="谢谢，这些信息已经足够用于准备情景。",
        profile=profile,
    )


def _profile_from_answers(answers: list[str]) -> ProfileSummary:
    clean = [answer.strip()[:160] for answer in answers if answer.strip()]
    return ProfileSummary(
        common_tasks=clean[:1],
        collaborators=clean[1:2],
        familiar_decision_context=clean[-1] if clean else "",
        summary="；".join(clean)[:300],
    )


def _humanistic_v11_profile_message(
    answers: list[str],
    *,
    next_action: str,
) -> str:
    clean = [answer.strip(" ，,；;。！？!?")[:40] for answer in answers if answer.strip()]
    task = clean[0] if clean else "这类任务"
    if next_action == "complete":
        collaborator = clean[1] if len(clean) > 1 else "熟悉的伙伴"
        return f"好，平时做{task}，主要和{collaborator}一起，我记住了。我们接着进入正式情境。"
    if len(clean) <= 1:
        return f"好，{task}是你熟悉的任务。这类任务通常和谁一起完成？"
    return "在这类任务里，通常哪种情况最需要你作判断？"


def _profile_prompt(
    *,
    occupation_category: str,
    occupation: str,
    answers: list[str],
    question_count: int,
    template_content: str | None,
) -> str:
    return f"""
已启用的版本化模板：
{template_content or '使用内置 occupation_profile_v2 模板。'}

职业大类：{occupation_category}
具体职业/身份：{occupation}
已提问次数：{question_count}，最多{MAX_PROFILE_QUESTIONS}次。
用户回答：{json.dumps(answers, ensure_ascii=False)}

如果已经能概括用户熟悉的任务、协作对象和判断场面，next_action=complete；否则只追问一个缺失点。
不得询问单位、地点、姓名、联系方式、真实敏感事件、患者/客户/学生身份信息。
输出字段：next_action、message、profile。profile包含common_tasks、collaborators、familiar_decision_context、summary。
""".strip()


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        stripped = stripped[first_newline + 1 :] if first_newline >= 0 else stripped
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    payload = json.loads(stripped.strip())
    if not isinstance(payload, dict):
        raise ValueError("profile output must be a JSON object")
    return payload


def _normalize_profile_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize one observed provider shape without weakening the schema.

    DeepSeek occasionally returns ``familiar_decision_context`` as a list of
    short strings even though the versioned contract defines one string.  The
    information is still usable, so join only a bounded list of strings.  All
    other unexpected shapes remain strict validation failures and continue to
    use the existing deterministic fallback.
    """
    profile = payload.get("profile")
    if not isinstance(profile, dict):
        return payload
    value = profile.get("familiar_decision_context")
    if not isinstance(value, list):
        return payload
    if any(not isinstance(item, str) for item in value):
        raise ValueError(
            "profile.familiar_decision_context list must contain only strings"
        )
    normalized_profile = {
        **profile,
        "familiar_decision_context": "；".join(
            item.strip() for item in value if item.strip()
        )[:240],
    }
    return {**payload, "profile": normalized_profile}


__all__ = [
    "MAX_PROFILE_QUESTIONS",
    "OPENING_PROFILE_QUESTION",
    "HUMANISTIC_V11_PROFILE_OPENING",
    "PROFILE_PROMPT_VERSION",
    "ProfileAgent",
    "ProfileOutput",
    "ProfileSummary",
]
