# Backend

FastAPI backend for the critical thinking dynamic assessment baseline.

## Quick Start

From the repository root, start MySQL:

```bash
docker compose up -d mysql
```

Then start the backend:

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
alembic upgrade head
python scripts/seed_db.py
python scripts/check_db.py
uvicorn app.main:app --reload
```

Health endpoints:

```text
GET /api/v1/health
GET /api/v1/health/db
GET /api/v1/scenarios/default
```

Expert review endpoints:

```text
GET  /api/v1/admin/sessions
GET  /api/v1/admin/sessions/{session_uuid}/review
PUT  /api/v1/admin/sessions/{session_uuid}/human-review
PUT  /api/v1/admin/sessions/{session_uuid}/expert-scores
POST /api/v1/admin/expert-scores/import
GET  /api/v1/admin/sessions/export
```

Assessment report and feedback endpoints:

```text
GET  /api/v1/sessions/{session_uuid}/report
GET  /api/v1/sessions/{session_uuid}/report.pdf
GET  /api/v1/sessions/{session_uuid}/feedback
POST /api/v1/sessions/{session_uuid}/feedback
```

`GET feedback` returns a state wrapper. A session without submitted feedback is a
normal `200` response with `submitted: false` and `feedback: null`; only a missing
session returns `404`. The PDF endpoint builds a Chinese report from the saved
report JSON on demand. It does not call the model or persist a duplicate file.

The PDF generator uses the bundled Noto Sans SC font at
`app/assets/fonts/NotoSansSC-Variable.ttf`. Its Open Font License is distributed
beside it as `app/assets/fonts/OFL.txt`.

Run the review-loop regression checks after applying migrations and seed data:

```bash
python scripts/check_admin_session_review.py
python scripts/check_expert_review_loop.py
python scripts/check_feedback_state.py
python scripts/check_report_pdf_export.py
```

## Main Layers

| Directory | Responsibility |
| --- | --- |
| `app/api/` | HTTP routes |
| `app/core/` | Configuration and database session |
| `app/models/` | SQLAlchemy ORM models |
| `app/schemas/` | Pydantic schemas |
| `app/repositories/` | Database query wrappers |
| `app/services/` | Business orchestration |
| `migrations/` | Alembic migrations |
| `seeds/` | Versioned assessment configuration |
| `scripts/` | Local maintenance scripts |
