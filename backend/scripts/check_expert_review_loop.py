from __future__ import annotations

import csv
import io
import sys
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.database import get_sessionmaker  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models.admin import AdminUser  # noqa: E402
from app.models.review import ExpertScoreAnnotation  # noqa: E402
from app.models.scoring import ScoreResult, ScoreSnapshot  # noqa: E402
from scripts.check_admin_session_review import (  # noqa: E402
    cleanup_fixture,
    create_fixture,
)


def login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/admin/auth/login",
        json={"username": username, "password": password},
    )
    if response.status_code != 200:
        raise AssertionError(f"Login failed: {response.status_code} {response.text}")
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def score_payload(target: dict, *, score: int) -> dict:
    return {
        "stage_code": target["stage_code"],
        "dimension_key": target["dimension_key"],
        "assessment_status": "scored",
        "score": score,
        "evidence_ids": [],
        "bars_reason": f"专家判断符合 {score} 级行为锚点",
        "next_level_gap": "需要补充更多反例比较",
        "annotator_confidence": "medium",
        "review_flag": False,
        "review_reason": None,
    }


def csv_bytes(rows: list[dict[str, str]]) -> bytes:
    fieldnames = [
        "session_uuid",
        "stage_code",
        "dimension_key",
        "assessment_status",
        "score",
        "evidence_ids",
        "bars_reason",
        "next_level_gap",
        "annotator_confidence",
        "review_flag",
        "review_reason",
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def main() -> int:
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    fixtures: list[dict] = []
    second_admin_id: int | None = None
    try:
        low_fixture = create_fixture(db)
        high_fixture = create_fixture(db)
        fixtures.extend([low_fixture, high_fixture])

        final_snapshot_id = db.execute(
            select(func.max(ScoreSnapshot.id)).where(
                ScoreSnapshot.session_id == low_fixture["session_id"],
                ScoreSnapshot.snapshot_type == "final",
            )
        ).scalar_one()
        low_result = db.execute(
            select(ScoreResult).where(ScoreResult.snapshot_id == final_snapshot_id)
        ).scalars().first()
        if low_result is None:
            raise AssertionError("Low-confidence fixture is missing a score result")
        low_result.confidence = 0.4

        second_username = f"expert_{uuid4().hex[:8]}"
        second_password = "expert-check-password"
        second_admin = AdminUser(
            username=second_username,
            password_hash=hash_password(second_password),
            display_name="第二位测试专家",
            role="REVIEWER",
            status="active",
        )
        db.add(second_admin)
        db.commit()
        db.refresh(second_admin)
        second_admin_id = second_admin.id

        client = TestClient(app)
        settings = get_settings()
        first_headers = login(
            client,
            settings.ADMIN_USERNAME,
            settings.ADMIN_PASSWORD,
        )
        second_headers = login(client, second_username, second_password)

        review_url = (
            f"/api/v1/admin/sessions/{low_fixture['session_uuid']}/review"
        )
        review = client.get(review_url, headers=first_headers)
        if review.status_code != 200:
            raise AssertionError(f"Review read failed: {review.text}")
        targets = review.json()["expert_score_targets"]
        if len(targets) < 3:
            raise AssertionError("At least three expert score targets are required")

        missing_decision = client.put(
            f"/api/v1/admin/sessions/{low_fixture['session_uuid']}/human-review",
            headers=first_headers,
            json={"status": "completed", "decision": None, "notes": "invalid"},
        )
        if missing_decision.status_code != 422:
            raise AssertionError("Completed review without a decision should fail")

        saved_review = client.put(
            f"/api/v1/admin/sessions/{low_fixture['session_uuid']}/human-review",
            headers=first_headers,
            json={
                "status": "completed",
                "decision": "valid",
                "notes": "证据链完整，可进入一致性分析。",
            },
        )
        if saved_review.status_code != 200:
            raise AssertionError(f"Human review save failed: {saved_review.text}")

        first_save = client.put(
            f"/api/v1/admin/sessions/{low_fixture['session_uuid']}/expert-scores",
            headers=first_headers,
            json={"items": [score_payload(targets[0], score=4)]},
        )
        if first_save.status_code != 200 or first_save.json()["saved_count"] != 1:
            raise AssertionError(f"First expert score save failed: {first_save.text}")

        second_save = client.put(
            f"/api/v1/admin/sessions/{low_fixture['session_uuid']}/expert-scores",
            headers=second_headers,
            json={"items": [score_payload(targets[0], score=2)]},
        )
        if second_save.status_code != 200:
            raise AssertionError(f"Second expert score save failed: {second_save.text}")

        reread = client.get(review_url, headers=first_headers)
        scores = [
            item
            for item in reread.json()["expert_scores"]
            if item["stage_code"] == targets[0]["stage_code"]
            and item["dimension_key"] == targets[0]["dimension_key"]
        ]
        if len(scores) != 2 or {item["score"] for item in scores} != {2, 4}:
            raise AssertionError("Independent expert scores did not coexist")

        low_listing = client.get(
            "/api/v1/admin/sessions",
            headers=first_headers,
            params={
                "search": low_fixture["nickname"],
                "low_confidence": "true",
                "confidence_threshold": 0.5,
            },
        )
        if low_listing.status_code != 200 or low_listing.json()["total"] != 1:
            raise AssertionError(f"Low-confidence filter failed: {low_listing.text}")
        high_listing = client.get(
            "/api/v1/admin/sessions",
            headers=first_headers,
            params={
                "search": high_fixture["nickname"],
                "low_confidence": "true",
            },
        )
        if high_listing.status_code != 200 or high_listing.json()["total"] != 0:
            raise AssertionError("High-confidence session passed low-confidence filter")
        completed_listing = client.get(
            "/api/v1/admin/sessions",
            headers=first_headers,
            params={
                "search": low_fixture["nickname"],
                "review_status": "completed",
            },
        )
        item = completed_listing.json()["items"][0]
        if (
            completed_listing.json()["total"] != 1
            or item["review_status"] != "completed"
            or item["min_ai_confidence"] != 0.4
            or item["expert_score_count"] != 1
        ):
            raise AssertionError(f"Review list metadata failed: {completed_listing.text}")

        before_invalid_import = db.execute(
            select(func.count())
            .select_from(ExpertScoreAnnotation)
            .where(
                ExpertScoreAnnotation.session_id == low_fixture["session_id"],
                ExpertScoreAnnotation.annotator_id == second_admin_id,
            )
        ).scalar_one()
        invalid_csv = csv_bytes(
            [
                {
                    "session_uuid": low_fixture["session_uuid"],
                    "stage_code": targets[1]["stage_code"],
                    "dimension_key": targets[1]["dimension_key"],
                    "assessment_status": "scored",
                    "score": "3",
                    "evidence_ids": "",
                    "bars_reason": "有效行也不应部分写入",
                    "next_level_gap": "",
                    "annotator_confidence": "medium",
                    "review_flag": "false",
                    "review_reason": "",
                },
                {
                    "session_uuid": low_fixture["session_uuid"],
                    "stage_code": "unknown_stage",
                    "dimension_key": targets[2]["dimension_key"],
                    "assessment_status": "scored",
                    "score": "3",
                    "evidence_ids": "",
                    "bars_reason": "无效目标",
                    "next_level_gap": "",
                    "annotator_confidence": "medium",
                    "review_flag": "false",
                    "review_reason": "",
                },
            ]
        )
        invalid_import = client.post(
            "/api/v1/admin/expert-scores/import",
            headers={**second_headers, "Content-Type": "text/csv"},
            content=invalid_csv,
        )
        db.expire_all()
        after_invalid_import = db.execute(
            select(func.count())
            .select_from(ExpertScoreAnnotation)
            .where(
                ExpertScoreAnnotation.session_id == low_fixture["session_id"],
                ExpertScoreAnnotation.annotator_id == second_admin_id,
            )
        ).scalar_one()
        if invalid_import.status_code != 422 or before_invalid_import != after_invalid_import:
            raise AssertionError("Invalid CSV import was not atomic")

        valid_csv = csv_bytes(
            [
                {
                    "session_uuid": low_fixture["session_uuid"],
                    "stage_code": targets[1]["stage_code"],
                    "dimension_key": targets[1]["dimension_key"],
                    "assessment_status": "IE",
                    "score": "",
                    "evidence_ids": "",
                    "bars_reason": "当前阶段缺少可独立评分证据",
                    "next_level_gap": "需要补充行为证据",
                    "annotator_confidence": "low",
                    "review_flag": "true",
                    "review_reason": "建议进入裁决",
                }
            ]
        )
        valid_import = client.post(
            "/api/v1/admin/expert-scores/import",
            headers={**second_headers, "Content-Type": "text/csv"},
            content=valid_csv,
        )
        if (
            valid_import.status_code != 200
            or valid_import.json()["imported_count"] != 1
            or not valid_import.json()["import_batch_id"]
        ):
            raise AssertionError(f"Valid CSV import failed: {valid_import.text}")

        export = client.get(
            "/api/v1/admin/sessions/export",
            headers=first_headers,
            params={
                "format": "json",
                "search": low_fixture["nickname"],
                "review_status": "completed",
                "low_confidence": "true",
            },
        )
        export_payload = export.json()
        if (
            export.status_code != 200
            or len(export_payload["human_reviews"]) != 1
            or len(export_payload["expert_scores"]) != 3
            or export_payload["manifest"]["schema_version"] != "research_export_v2"
        ):
            raise AssertionError(f"Review export failed: {export.text}")

        print("Expert review loop check passed:")
        print(f"  session_uuid={low_fixture['session_uuid']}")
        print(f"  expert_scores={len(export_payload['expert_scores'])}")
        print(f"  review_status={item['review_status']}")
        print(f"  min_ai_confidence={item['min_ai_confidence']}")
        print(f"  import_batch_id={valid_import.json()['import_batch_id']}")
        return 0
    finally:
        for fixture in reversed(fixtures):
            cleanup_fixture(db, fixture)
        if second_admin_id is not None:
            db.query(AdminUser).filter(AdminUser.id == second_admin_id).delete()
            db.commit()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
