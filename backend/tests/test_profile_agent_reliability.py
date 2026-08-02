from __future__ import annotations

import unittest

from app.agents.profile_agent import (
    _humanistic_v11_profile_message,
    _normalize_profile_payload,
)


class ProfileAgentReliabilityTests(unittest.TestCase):
    def test_list_decision_context_is_joined_and_bounded(self) -> None:
        payload = {
            "next_action": "complete",
            "message": "信息足够。",
            "profile": {
                "common_tasks": ["课程项目"],
                "collaborators": ["同学"],
                "familiar_decision_context": ["确认分工", "核实进度"],
                "summary": "课程项目协作",
            },
        }
        normalized = _normalize_profile_payload(payload)
        self.assertEqual(
            normalized["profile"]["familiar_decision_context"],
            "确认分工；核实进度",
        )
        self.assertIsNot(normalized, payload)
        self.assertIsNot(normalized["profile"], payload["profile"])

    def test_unexpected_non_string_list_item_remains_a_failure(self) -> None:
        payload = {
            "profile": {
                "familiar_decision_context": ["确认分工", {"unsafe": True}],
            }
        }
        with self.assertRaisesRegex(ValueError, "only strings"):
            _normalize_profile_payload(payload)

    def test_non_list_shape_is_not_silently_coerced(self) -> None:
        payload = {"profile": {"familiar_decision_context": 42}}
        self.assertIs(_normalize_profile_payload(payload), payload)

    def test_v11_profile_messages_are_conversational_not_customer_service(self) -> None:
        asking = _humanistic_v11_profile_message(
            ["课程项目"],
            next_action="ask",
        )
        complete = _humanistic_v11_profile_message(
            ["课程项目", "同学"],
            next_action="complete",
        )

        self.assertEqual(asking, "好，课程项目是你熟悉的任务。这类任务通常和谁一起完成？")
        self.assertEqual(
            complete,
            "好，平时做课程项目，主要和同学一起，我记住了。我们接着进入正式情境。",
        )
        self.assertNotIn("感谢你的分享", complete)
        self.assertNotIn("已经了解", complete)


if __name__ == "__main__":
    unittest.main()
