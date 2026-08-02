from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parent))

from app.core.config import get_settings  # noqa: E402
from check_agent_fixture_cases import (  # noqa: E402
    build_context_from_dialogue_case,
    load_fixtures,
    load_seed_data,
    validate_dialogue_cases,
    validate_scoring_cases,
    validate_users,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Strict real-model validation for HostAgent and FollowupAgent."
    )
    parser.add_argument(
        "--case-id",
        default="student_weak_s1",
        help="dialogue_cases.json 中的 case_id",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not os.getenv("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY is required for strict real-model validation.")
        return 2
    os.environ["MODEL_GATEWAY_MODE"] = "real"
    get_settings.cache_clear()

    try:
        from app.agents.followup_agent import FollowupAgent
        from app.agents.host_agent import HostAgent
        from app.agents.schemas import FollowupOutput, HostOutput
    except ImportError as exc:
        print("HostAgent / FollowupAgent modules are not available.")
        print(f"reason={exc}")
        return 2

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
    if args.case_id not in dialogue_cases_by_id:
        print(f"Unknown case_id: {args.case_id}")
        return 2

    context = build_context_from_dialogue_case(
        dialogue_cases_by_id[args.case_id],
        users_by_id,
        scenario_seed,
        rubric_seed,
    )
    settings = get_settings()
    print("=" * 72)
    print("Strict real-model dialogue Agent validation")
    print("=" * 72)
    print(f"case_id={args.case_id}")
    print(f"provider={settings.MODEL_PROVIDER}")
    print(f"model={settings.DEEPSEEK_MODEL}")
    print("api_key_configured=true")

    host_output = HostAgent().generate(context)
    HostOutput.model_validate(host_output.model_dump())
    if host_output.fallback_used:
        print("[FAIL] HostAgent used fallback in real mode.")
        print(f"warnings={host_output.warnings}")
        return 1
    if not host_output.message.strip():
        print("[FAIL] HostAgent returned empty message.")
        return 1
    print("[PASS] HostAgent real output")
    print(f"  next_action={host_output.next_action}")
    print(f"  content_type={host_output.content_type}")
    print(f"  message={host_output.message[:160]}")

    followup_output = FollowupAgent().generate(context)
    FollowupOutput.model_validate(followup_output.model_dump())
    if followup_output.fallback_used:
        print("[FAIL] FollowupAgent used fallback in real mode.")
        print(f"warnings={followup_output.warnings}")
        return 1
    if not followup_output.question.strip():
        print("[FAIL] FollowupAgent returned empty question.")
        return 1
    print("[PASS] FollowupAgent real output")
    print(f"  next_action={followup_output.next_action}")
    print(f"  content_type={followup_output.content_type}")
    print(f"  selected_rule_code={followup_output.selected_rule_code}")
    print(f"  selected_dynamic_info_code={followup_output.selected_dynamic_info_code}")
    print(f"  question={followup_output.question[:160]}")
    print("=" * 72)
    print("Strict real-model dialogue Agent validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
