from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def get_db_dsn() -> str:
    dsn = os.getenv("AGENT_DB_DSN")
    if not dsn:
        raise RuntimeError("AGENT_DB_DSN is required")
    return dsn


def make_engine(dsn: str | None = None) -> Engine:
    return create_engine(dsn or get_db_dsn(), pool_pre_ping=True, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )


@contextlib.contextmanager
def db_session(engine: Engine) -> Iterator[Session]:
    SessionLocal = make_session_factory(engine)
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
