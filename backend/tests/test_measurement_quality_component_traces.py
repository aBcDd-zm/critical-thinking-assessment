from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.models.agent import AgentTrace
from app.services.evidence_sufficiency_service import EvidenceSufficiencyService


def _trace(
    trace_id: int,
    *,
    status: str,
    config: dict,
    error_code: str | None = None,
) -> AgentTrace:
    return AgentTrace(
        id=trace_id,
        session_id=1,
        agent_name="consultative_turn",
        input_json={},
        status=status,
        error_code=error_code,
        config_snapshot_json=config,
    )


class MeasurementQualityComponentTraceTests(unittest.TestCase):
    def _quality(self, traces: list[AgentTrace]):
        db = MagicMock()
        db.execute.return_value.scalars.return_value = traces
        session = SimpleNamespace(
            id=1,
            interview_state_json={
                "released_event_codes": [
                    "opening_context",
                    "evidence_uncertainty",
                    "stakeholder_conflict",
                    "decision_pressure",
                    "counter_evidence",
                    "integration",
                ],
                "dimension_slots": {},
                "technical_fallback_count": 0,
            },
        )
        service = EvidenceSufficiencyService(db)
        with patch.object(
            service,
            "_scoring_contamination_turn_ids",
            return_value=[],
        ):
            return service.measurement_quality(session, scores=[])

    def test_renderer_fallback_does_not_pollute_measurement_core_rates(self) -> None:
        traces = [
            _trace(
                1,
                status="success",
                config={
                    "measurement_scope": "opening",
                    "measurement_core_status": "success",
                },
            ),
            _trace(
                2,
                status="success",
                config={
                    "measurement_scope": "formal_answer",
                    "measurement_core_status": "success",
                },
            ),
        ]

        quality = self._quality(traces)

        self.assertEqual(quality.status, "valid")
        self.assertEqual(quality.technical_failure_rate, 0)
        self.assertEqual(quality.total_fallback_rate, 0)

    def test_opening_and_repair_do_not_dilute_formal_core_failure_rate(self) -> None:
        traces = [
            _trace(
                1,
                status="success",
                config={
                    "measurement_scope": "opening",
                    "measurement_core_status": "success",
                },
            ),
            _trace(
                2,
                status="success",
                config={
                    "measurement_scope": "non_measurement",
                    "measurement_core_status": "success",
                },
            ),
            _trace(
                3,
                status="fallback",
                config={
                    "measurement_scope": "formal_answer",
                    "measurement_core_status": "failed",
                },
            ),
        ]

        quality = self._quality(traces)

        self.assertEqual(quality.technical_failure_rate, 1)
        self.assertEqual(quality.total_fallback_rate, 1)
        self.assertEqual(quality.status, "invalid")

    def test_legacy_trace_sessions_keep_original_rate_calculation(self) -> None:
        traces = [
            _trace(
                1,
                status="success",
                config={"model_call_status": "success"},
            ),
            _trace(
                2,
                status="fallback",
                config={"model_call_status": "failed"},
                error_code="CONSULTATIVE_TURN_FALLBACK",
            ),
        ]

        quality = self._quality(traces)

        self.assertEqual(quality.technical_failure_rate, 0.5)
        self.assertEqual(quality.total_fallback_rate, 0.5)
        self.assertEqual(quality.status, "invalid")


if __name__ == "__main__":
    unittest.main()
