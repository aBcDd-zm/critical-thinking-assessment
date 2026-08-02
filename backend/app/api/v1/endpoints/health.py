from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db

router = APIRouter()


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/db")
def database_health_check(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1")).scalar_one()
    return {"status": "ok", "database": "connected"}

