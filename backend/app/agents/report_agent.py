from __future__ import annotations

from app.agents.mock_scoring_report import build_mock_report_output
from app.agents.schemas import AgentRuntimeContext, ReportOutput, ScoringOutput
from app.agents.scoring_report_llm_client import ScoringReportLLMClient
from app.agents.scoring_report_validators import validate_report_output
from app.core.config import get_settings


class ReportAgent:
    def __init__(self, llm_client: ScoringReportLLMClient | None = None) -> None:
        self.llm_client = llm_client or ScoringReportLLMClient()

    def generate(
        self,
        context: AgentRuntimeContext,
        scoring_output: ScoringOutput,
    ) -> ReportOutput:
        settings = get_settings()
        if settings.MODEL_GATEWAY_MODE.lower() == "mock":
            output = build_mock_report_output(context, scoring_output)
            return validate_report_output(context, scoring_output, output)

        validation_errors: list[str] = []

        for attempt in range(2):
            result = self.llm_client.call_report(
                context,
                scoring_output,
            )

            if not result.success or result.output is None:
                error_message = (
                    f"attempt={attempt + 1}: "
                    f"{result.error_code or 'MODEL_ERROR'}: "
                    f"{result.error_reason or 'unknown error'}"
                )
                validation_errors.append(error_message)
                if attempt == 0 and result.error_code == "MODEL_GATEWAY_ERROR":
                    continue
                return _fallback(
                    context,
                    scoring_output,
                    " | ".join(validation_errors),
                )

            try:
                return validate_report_output(
                    context,
                    scoring_output,
                    result.output,
                )
            except Exception as exc:  # noqa: BLE001
                validation_errors.append(
                    f"attempt={attempt + 1}: {exc}"
                )
                if attempt == 0:
                    continue

        return _fallback(
            context,
            scoring_output,
            "REPORT_ERROR_AFTER_RETRY: "
            + " | ".join(validation_errors),
        )

def _fallback(
    context: AgentRuntimeContext,
    scoring_output: ScoringOutput,
    warning: str,
) -> ReportOutput:
    output = build_mock_report_output(context, scoring_output)
    output = output.model_copy(
        update={
            "fallback_used": True,
            "warnings": output.warnings + [warning],
        }
    )
    return validate_report_output(context, scoring_output, output)


__all__ = ["ReportAgent"]
