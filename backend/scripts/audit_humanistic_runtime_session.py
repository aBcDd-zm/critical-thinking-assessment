from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.database import get_sessionmaker
from app.models.agent import AgentTrace
from app.models.assessment import AssessmentSession
from app.models.report import AssessmentReport


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("session_uuid")
    args = parser.parse_args()

    with get_sessionmaker()() as db:
        session = db.execute(
            select(AssessmentSession).where(
                AssessmentSession.session_uuid == args.session_uuid
            )
        ).scalar_one()
        traces = list(
            db.execute(
                select(AgentTrace)
                .where(AgentTrace.session_id == session.id)
                .order_by(AgentTrace.id)
            ).scalars()
        )
        by_agent: dict[str, list[AgentTrace]] = defaultdict(list)
        for trace in traces:
            by_agent[trace.agent_name].append(trace)
        summary = {}
        for name, items in by_agent.items():
            summary[name] = {
                "total": len(items),
                "status": dict(Counter(item.status for item in items)),
                "errors": dict(
                    Counter(item.error_code for item in items if item.error_code)
                ),
                "models": sorted(
                    {item.model_name for item in items if item.model_name}
                ),
                "durations_ms": [item.duration_ms for item in items],
                "fallback_details": [
                    {
                        "trace_id": item.id,
                        "error_code": item.error_code,
                        "validation_codes": (
                            item.config_snapshot_json or {}
                        ).get("validation_codes")
                        or (item.config_snapshot_json or {}).get(
                            "validation_errors"
                        ),
                        "action": (item.config_snapshot_json or {}).get("action"),
                        "model_call_status": (
                            item.config_snapshot_json or {}
                        ).get("model_call_status"),
                        "timeout_ms": (
                            item.config_snapshot_json or {}
                        ).get("timeout_ms"),
                    }
                    for item in items
                    if item.status in {"fallback", "failed"}
                ],
            }
        report = db.execute(
            select(AssessmentReport).where(
                AssessmentReport.session_id == session.id
            )
        ).scalar_one_or_none()
        report_json = report.report_json if report is not None else {}
        print(
            json.dumps(
                {
                    "session_uuid": session.session_uuid,
                    "status": session.status,
                    "flow_version": session.flow_version,
                    "style": session.interviewer_style_version,
                    "trace_summary": summary,
                    "report_status": report.status if report is not None else None,
                    "measurement_quality": report_json.get("measurement_quality"),
                    "report_fallback_used": report_json.get("fallback_used"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
