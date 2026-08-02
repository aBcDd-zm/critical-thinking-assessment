import sys
from pathlib import Path

from sqlalchemy import inspect, text

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import get_engine

EXPECTED_TABLES = {
    "admin_user",
    "participant",
    "participant_profile",
    "consent_record",
    "scenario",
    "scenario_stage",
    "stage_dynamic_info",
    "stage_intervention_rule",
    "scenario_stage_dimension",
    "stage_dynamic_info_dimension",
    "stage_intervention_rule_dimension",
    "rubric_dimension",
    "rubric_anchor",
    "prompt_template",
    "report_template",
    "scenario_pool",
    "scenario_pool_item",
    "assessment_session",
    "dialogue_turn",
    "agent_trace",
    "score_snapshot",
    "score_result",
    "score_evidence",
    "assessment_report",
    "session_feedback",
}


def main() -> None:
    engine = get_engine()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        table_names = set(inspect(connection).get_table_names())

    missing_tables = sorted(EXPECTED_TABLES - table_names)
    if missing_tables:
        print("Database connected, but some expected tables are missing:")
        for table_name in missing_tables:
            print(f"- {table_name}")
        raise SystemExit(1)

    print("Database check passed.")


if __name__ == "__main__":
    main()
