from __future__ import annotations

import json
from uuid import uuid4

from smoke_consultative_interview_v33_real import (
    completed,
    request_events,
    request_json,
)


def main() -> int:
    created = request_json(
        "POST",
        "/sessions",
        {
            "nickname": "运行可靠性冒烟",
            "occupation_category": "学生",
            "occupation": "大学生",
            "consent_accepted": True,
            "consent_version": "critical_thinking_assessment_consent_v1",
        },
    )
    session_uuid = created["session_uuid"]
    if created.get("interviewer_style_version") != "humanistic_v1":
        raise AssertionError(created.get("interviewer_style_version"))
    for answer in (
        "我熟悉课程项目，通常负责自己分到的部分。",
        "我主要和同学协作，会确认分工和检查进度。",
    ):
        state = request_json("GET", f"/sessions/{session_uuid}")
        if state["phase"] == "opening_pending":
            break
        request_events(
            f"/sessions/{session_uuid}/profile/turns/stream",
            {"content": answer},
        )
    opening = completed(
        request_events(f"/sessions/{session_uuid}/interview/start/stream")
    )
    turn = completed(
        request_events(
            f"/sessions/{session_uuid}/turns/stream",
            {
                "client_turn_id": str(uuid4()),
                "content": "我会先核实当前完成度和质量记录，再决定怎么分工。",
                "content_type": "scenario_answer",
            },
        )
    )
    message = turn["ai_turn"]["content"]
    if len(message) > 90:
        raise AssertionError(f"visible message too long: {len(message)}")
    if message.count("？") + message.count("?") != 1:
        raise AssertionError("visible message must contain exactly one question")
    print(
        json.dumps(
            {
                "session_uuid": session_uuid,
                "style": created["interviewer_style_version"],
                "opening_length": len(opening["ai_turn"]["content"]),
                "turn_duration_ms": turn.get("duration_ms"),
                "next_action": turn.get("next_action"),
                "visible_length": len(message),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
