from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, ValidationError

from app.agents.progressive_schemas import (
    InterviewerOutput,
    InterviewQualityFlags,
    ProgressiveModel,
    ReflectionSourceQuote,
)


INTERVIEWER_OUTPUT_CONTRACT_VERSION = "interviewer_output_contract_v1"


class StrictInterviewerOutput(ProgressiveModel):
    """Complete model-authored envelope required by offline generation."""

    message: str = Field(min_length=1, max_length=500)
    message_type: Literal[
        "opening",
        "followup",
        "event",
        "clarification",
        "integration",
        "closing",
    ]
    question_count: int = Field(ge=0, le=1)
    introduced_fact_codes: list[str]
    reflection_turn_ids: list[int]
    reflection_source_quotes: list[ReflectionSourceQuote]
    quality_flags: InterviewQualityFlags
    fallback_used: bool
    warnings: list[str]

    def to_interviewer_output(self) -> InterviewerOutput:
        return InterviewerOutput.model_validate(self.model_dump(mode="json"))


class OutputContractIssue(ProgressiveModel):
    path: list[str | int]
    code: str = Field(min_length=1, max_length=120)


INTERVIEWER_OUTPUT_REQUIRED_FIELDS = tuple(
    StrictInterviewerOutput.model_fields
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


INTERVIEWER_OUTPUT_CONTRACT_PAYLOAD = {
    "contract_version": INTERVIEWER_OUTPUT_CONTRACT_VERSION,
    "top_level": "one_json_object_without_wrapper",
    "all_declared_fields_required": True,
    "json_schema": StrictInterviewerOutput.model_json_schema(),
}
INTERVIEWER_OUTPUT_CONTRACT_CANONICAL_JSON = _canonical_json(
    INTERVIEWER_OUTPUT_CONTRACT_PAYLOAD
)
INTERVIEWER_OUTPUT_CONTRACT_INSTRUCTION = (
    "\n共享输出结构契约（只规定 JSON 结构，不规定表达风格）："
    "只输出一个符合下列契约的顶层 JSON 对象，不添加包装层，"
    "不省略任何 required 字段，不添加契约外字段。"
    "reflection_source_quotes 的每一项必须是包含 turn_id 和 quote 的对象，"
    "不得用字符串代替对象。"
    f"contract={INTERVIEWER_OUTPUT_CONTRACT_CANONICAL_JSON}"
)
INTERVIEWER_OUTPUT_CONTRACT_SHA256 = hashlib.sha256(
    INTERVIEWER_OUTPUT_CONTRACT_INSTRUCTION.encode("utf-8")
).hexdigest()


def parse_strict_interviewer_output(
    raw: str,
) -> tuple[InterviewerOutput | None, list[OutputContractIssue]]:
    try:
        payload = json.loads(raw.strip())
    except json.JSONDecodeError:
        return None, [OutputContractIssue(path=[], code="json_invalid")]

    try:
        strict = StrictInterviewerOutput.model_validate(payload)
    except ValidationError as exc:
        issues = [
            OutputContractIssue(
                path=[part for part in error["loc"]],
                code=str(error["type"]),
            )
            for error in exc.errors()
        ]
        return None, issues
    return strict.to_interviewer_output(), []


__all__ = [
    "INTERVIEWER_OUTPUT_CONTRACT_CANONICAL_JSON",
    "INTERVIEWER_OUTPUT_CONTRACT_INSTRUCTION",
    "INTERVIEWER_OUTPUT_CONTRACT_PAYLOAD",
    "INTERVIEWER_OUTPUT_REQUIRED_FIELDS",
    "INTERVIEWER_OUTPUT_CONTRACT_SHA256",
    "INTERVIEWER_OUTPUT_CONTRACT_VERSION",
    "OutputContractIssue",
    "StrictInterviewerOutput",
    "parse_strict_interviewer_output",
]
