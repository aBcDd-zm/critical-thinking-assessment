from __future__ import annotations

import unittest

from app.agents.scenario_design_agent import build_mock_occupation_scenario


class ScenarioDesignFallbackCopyTests(unittest.TestCase):
    def test_fallback_background_is_complete_and_plain(self) -> None:
        scenario = build_mock_occupation_scenario("学生", "大学生")

        self.assertIn("无需运用专业规则", scenario.background)
        self.assertIn("只需根据随后给出的事实", scenario.background)
        self.assertNotIn("运用专业规则，而是", scenario.background)


if __name__ == "__main__":
    unittest.main()
