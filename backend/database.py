"""
database.py — SQLAlchemy engine and session factory.

SQLite is used with synchronous SQLAlchemy.  For Phase 1 (1-3 concurrent
users) this is simpler and more than sufficient; async SQLAlchemy can be
added in Phase 2 if concurrent load increases.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = "sqlite:///./chat_data.db"

# check_same_thread=False is required because FastAPI may dispatch requests
# across different threads while sharing the same SQLite connection.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


def get_db():
    """
    FastAPI dependency that yields a DB session and ensures it is closed
    after the request completes (even on exceptions).

    Note: do NOT use this dependency inside a StreamingResponse generator —
    the generator outlives the request scope.  Open a fresh SessionLocal()
    directly inside the generator for any post-stream writes.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
