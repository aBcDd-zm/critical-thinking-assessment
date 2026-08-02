from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.DATABASE_URL,
        echo=settings.SQL_ECHO,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=get_engine(),
        class_=Session,
    )


def get_db() -> Generator[Session, None, None]:
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()
