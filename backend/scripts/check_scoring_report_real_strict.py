from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parent))

from app.agents import ReportOutput, ScoringOutput  # noqa: E402
from app.agents.report_agent import ReportAgent  # noqa: E402
from app.agents.scoring_agent import ScoringAgent  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from check_scoring_report_agent import (  # noqa: E402
    build_scoring_context,
    load_fixtures,
    load_seed_data,
    validate_dialogue_cases,
    validate_scoring_cases,
    validate_users,
)


def main() -> int:
    settings = get_settings()
    if settings.MODEL_GATEWAY_MODE.lower() != "real":
        raise AssertionError("MODEL_GATEWAY_MODE must be real for strict real check.")
    if not settings.DEEPSEEK_API_KEY:
        raise AssertionError("DEEPSEEK_API_KEY is required for strict real check.")

    users, dialogue_cases, scoring_cases = load_fixtures()
    scenario_seed, rubric_seed = load_seed_data()
    users_by_id = validate_users(users)
    dialogue_cases_by_id = validate_dialogue_cases(
        dialogue_cases,
        users_by_id,
        scenario_seed,
        rubric_seed,
    )
    validate_scoring_cases(scoring_cases, users_by_id, dialogue_cases_by_id, rubric_seed)

    scoring_case = next(
        case for case in scoring_cases if case["case_id"] == "workplace_strong_scoring"
    )
    context = build_scoring_context(
        scoring_case,
        users_by_id,
        dialogue_cases_by_id,
        scenario_seed,
        rubric_seed,
    )

    scoring_output = ScoringOutput.model_validate(
        ScoringAgent().generate(context, snapshot_type="final").model_dump()
    )
    if scoring_output.fallback_used:
        raise AssertionError(f"Real scoring fell back: {scoring_output.warnings}")

    report_output = ReportOutput.model_validate(
        ReportAgent().generate(context, scoring_output).model_dump()
    )
    if report_output.fallback_used:
        raise AssertionError(f"Real report fell back: {report_output.warnings}")

    print("Strict real scoring/report check passed.")
    print(f"score_count={len(scoring_output.scores)}")
    print(f"dimension_reports={len(report_output.dimension_reports)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
