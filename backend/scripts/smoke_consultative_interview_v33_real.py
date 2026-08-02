from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from uuid import uuid4


BASE_URL = "http://127.0.0.1:8030/api/v1"


def request_json(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode() if payload else None
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read())


def request_events(path: str, payload: dict | None = None) -> list[dict]:
    data = json.dumps(payload, ensure_ascii=False).encode() if payload else None
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return [json.loads(line) for line in response if line.strip()]


def completed(events: list[dict]) -> dict:
    return next(item for item in events if item.get("event") == "agent_completed")


def submit_answer(session_uuid: str, content: str) -> dict:
    return completed(
        request_events(
            f"/sessions/{session_uuid}/turns/stream",
            {
                "client_turn_id": str(uuid4()),
                "content": content,
                "content_type": "scenario_answer",
            },
        )
    )


def wait_for_report(session_uuid: str, timeout_seconds: int = 150) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            return request_json("GET", f"/sessions/{session_uuid}/report")["report"]
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
        time.sleep(2)
    raise TimeoutError(f"report was not ready within {timeout_seconds}s")


def main() -> int:
    created = request_json(
        "POST",
        "/sessions",
        {
            "nickname": "v3.3真实冒烟",
            "occupation_category": "学生",
            "occupation": "大学生",
            "consent_accepted": True,
            "consent_version": "critical_thinking_assessment_consent_v1",
        },
    )
    session_uuid = created["session_uuid"]
    if created["flow_version"] != "progressive_v3_3":
        raise AssertionError(created["flow_version"])

    for profile_answer in (
        "我熟悉课程学习和小组作业，通常负责自己分到的部分。",
        "我主要和老师、同学协作，会一起讨论进度和检查结果。",
        "最常见的是在截止时间前完成小组作业并互相检查。",
    ):
        state = request_json("GET", f"/sessions/{session_uuid}")
        if state["phase"] == "opening_pending":
            break
        request_events(
            f"/sessions/{session_uuid}/profile/turns/stream",
            {"content": profile_answer},
        )

    opening = completed(
        request_events(f"/sessions/{session_uuid}/interview/start/stream")
    )
    print(
        json.dumps(
            {
                "session_uuid": session_uuid,
                "kind": "opening",
                "duration_ms": opening.get("duration_ms"),
                "message": opening["ai_turn"]["content"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    context_repair = completed(
        request_events(
            f"/sessions/{session_uuid}/turns/stream",
            {
                "client_turn_id": str(uuid4()),
                "content": "眼下是什么情况",
                "content_type": "scenario_answer",
            },
        )
    )
    if "五天后" not in context_repair["ai_turn"]["content"]:
        raise AssertionError(context_repair)
    print(
        json.dumps(
            {
                "kind": "request_context",
                "message": context_repair["ai_turn"]["content"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    # General dialogue-recovery regression.  These phrases intentionally vary
    # from the original UAT wording so the check exercises intent and state,
    # rather than memorising one screenshot.
    recovery_rows: list[dict] = []
    for content in (
        "我想先核实团队里每个人的任务",
        "全部成员的",
    ):
        recovered = submit_answer(session_uuid, content)
        message = recovered["ai_turn"]["content"]
        if any(
            phrase in message
            for phrase in (
                "具体指哪一部分",
                "全部成员的具体指",
                "换一种说法说明刚才的意思",
            )
        ):
            raise AssertionError(recovered)
        recovery_rows.append({"input": content, "message": message})

    count_before_context = request_json(
        "GET", f"/sessions/{session_uuid}"
    )["interview_progress"]["formal_answer_count"]
    context_turn = submit_answer(session_uuid, "能再梳理一下目前已有的信息吗")
    context_message = context_turn["ai_turn"]["content"]
    if "目前已经知道" not in context_message:
        raise AssertionError(context_turn)
    if "一部分参与者想减少交接" in context_message:
        raise AssertionError("request_context leaked an unreleased future event")
    count_after_context = request_json(
        "GET", f"/sessions/{session_uuid}"
    )["interview_progress"]["formal_answer_count"]
    if count_after_context != count_before_context:
        raise AssertionError("request_context consumed formal answer budget")
    recovery_rows.append(
        {"input": "能再梳理一下目前已有的信息吗", "message": context_message}
    )

    substantive = submit_answer(session_uuid, "我会逐一问清每个人负责的内容")
    recovery_rows.append(
        {
            "input": "我会逐一问清每个人负责的内容",
            "message": substantive["ai_turn"]["content"],
        }
    )
    count_before_clarification = request_json(
        "GET", f"/sessions/{session_uuid}"
    )["interview_progress"]["formal_answer_count"]
    for content in (
        "这句话我没听明白",
        "我问的是你刚才那句话是什么意思",
        "我还是没跟上，你具体在问哪件事",
    ):
        clarified = submit_answer(session_uuid, content)
        message = clarified["ai_turn"]["content"]
        if any(
            phrase in message
            for phrase in (
                "具体指哪一部分",
                "上一问是",
                "我问的是你刚才那句话指的是",
                "四方意见",
            )
        ):
            raise AssertionError(clarified)
        recovery_rows.append({"input": content, "message": message})
    count_after_clarification = request_json(
        "GET", f"/sessions/{session_uuid}"
    )["interview_progress"]["formal_answer_count"]
    if count_after_clarification != count_before_clarification:
        raise AssertionError("clarification consumed formal answer budget")
    print(
        json.dumps(
            {"kind": "dialogue_recovery", "rows": recovery_rows},
            ensure_ascii=False,
        ),
        flush=True,
    )

    answers = [
        "我会先核实当前完成度和质量记录，确认哪些部分还没有完成。",
        "我会看记录由谁填写、覆盖哪些部分，并抽查实际结果是否和记录一致。",
        "进度方想按时交付，质量方担心返工，我会先确认关键部分的质量风险。",
        "我倾向先在非关键部分试用减少交接，关键部分继续逐项检查。",
        "依据是非关键部分出错影响较小；如果返工没有上升，再逐步扩大范围。",
        "如果新信息显示关键部分返工上升，我会停止试用并恢复逐项检查。",
        "我会安排两人分别核对完成度和质量，再汇总未完成项和高风险项。",
        "我会用按时完成率、返工率和抽查错误数判断安排是否可靠。",
        "最终先保留关键部分逐项检查，非关键部分小范围试用，每天复核三项指标。",
        "如果返工率连续两次上升，就停止试用；保持稳定后才扩大范围。",
        "我还会把进度、质量和人员可用时间放在一起，明确负责人和复核时间。",
        "综合这些信息，我会先小范围试用，并保留随时恢复原安排的条件。",
    ]
    latencies: list[int] = []
    for answer in answers:
        state = request_json("GET", f"/sessions/{session_uuid}")
        if state["status"] == "completed":
            break
        started = time.perf_counter()
        turn = submit_answer(session_uuid, answer)
        wall_ms = round((time.perf_counter() - started) * 1000)
        duration_ms = int(turn.get("duration_ms") or 0)
        latencies.append(duration_ms)
        print(
            json.dumps(
                {
                    "formal_answer": len(latencies),
                    "duration_ms": duration_ms,
                    "wall_ms": wall_ms,
                    "next_action": turn.get("next_action"),
                    "message": turn["ai_turn"]["content"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if len(latencies) == 1:
            formal_count_before_repair = request_json(
                "GET", f"/sessions/{session_uuid}"
            )["interview_progress"]["formal_answer_count"]
            repair = submit_answer(session_uuid, "我已经回答过了")
            if "不会再重复" not in repair["ai_turn"]["content"]:
                raise AssertionError(repair)
            repaired_state = request_json("GET", f"/sessions/{session_uuid}")
            if (
                repaired_state["interview_progress"]["formal_answer_count"]
                != formal_count_before_repair
            ):
                raise AssertionError("conversation repair consumed formal budget")
            print(
                json.dumps(
                    {
                        "kind": "conversation_repair",
                        "message": repair["ai_turn"]["content"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    final_state = request_json("GET", f"/sessions/{session_uuid}")
    formal_count = final_state["interview_progress"]["formal_answer_count"]
    if formal_count < 9:
        raise AssertionError(f"formal turns below 9: {formal_count}")
    if max(latencies, default=0) > 32_000:
        raise AssertionError(f"turn latency exceeded fallback boundary: {latencies}")
    result = {
        "session_uuid": session_uuid,
        "formal_answer_count": formal_count,
        "latencies_ms": latencies,
        "status": final_state["status"],
    }
    if final_state["status"] == "completed":
        report = wait_for_report(session_uuid)
        result["measurement_quality"] = report.get("measurement_quality")
        result["dimension_esi"] = {
            item["dimension_key"]: item.get("evidence_sufficiency_index")
            for item in report.get("dimension_reports", [])
        }
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
