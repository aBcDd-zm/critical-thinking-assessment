from __future__ import annotations

from typing import Literal

from app.agents.mock_scoring_report import build_mock_scoring_output
from app.agents.schemas import AgentRuntimeContext, ScoringOutput
from app.agents.scoring_report_llm_client import CLLMResult, ScoringReportLLMClient
from app.agents.scoring_report_validators import validate_scoring_output
from app.agents.semantic_scoring import apply_semantic_evidence_guardrails
from app.core.config import get_settings


class ScoringAgent:
    def __init__(self, llm_client: ScoringReportLLMClient | None = None) -> None:
        self.llm_client = llm_client or ScoringReportLLMClient()

    def generate(
        self,
        context: AgentRuntimeContext,
        snapshot_type: Literal["turn", "stage", "final"] = "final",
    ) -> ScoringOutput:
        settings = get_settings()
        if settings.MODEL_GATEWAY_MODE.lower() == "mock":
            return apply_semantic_evidence_guardrails(
                context,
                build_mock_scoring_output(context, snapshot_type=snapshot_type),
            )

        errors: list[str] = []
        for attempt in range(2):
            result = self.llm_client.call_scoring(context)
            if result.success and result.output is not None:
                try:
                    output = result.output.model_copy(
                        update={"snapshot_type": snapshot_type}
                    )
                    return apply_semantic_evidence_guardrails(
                        context,
                        validate_scoring_output(context, output),
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"attempt={attempt + 1}: VALIDATION_ERROR: {exc}")
                    break

            errors.append(
                f"attempt={attempt + 1}: "
                f"{result.error_code or 'MODEL_ERROR'}: "
                f"{result.error_reason or 'unknown error'}"
            )
            if attempt == 0 and result.error_code == "MODEL_GATEWAY_ERROR":
                continue
            break

        return _fallback(context, snapshot_type, " | ".join(errors))


def _fallback(
    context: AgentRuntimeContext,
    snapshot_type: Literal["turn", "stage", "final"],
    warning: str,
) -> ScoringOutput:
    output = build_mock_scoring_output(context, snapshot_type=snapshot_type)
    fallback = output.model_copy(
        update={
            "fallback_used": True,
            "warnings": output.warnings + [warning],
        }
    )
    return apply_semantic_evidence_guardrails(context, fallback)


__all__ = ["ScoringAgent"]
