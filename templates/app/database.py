"""Database engine / session handling (SQLite)."""
import os
import re
from pathlib import Path

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .meta import META
from .models import Base


class Database:
    def __init__(self, url=None):
        if url is None:
            url = os.environ.get("DATABASE_URL")
        if url is None:
            slug = re.sub(r"[^a-z0-9]+", "_", META["domain"].lower()).strip("_")
            url = f"sqlite:///{Path(__file__).resolve().parent.parent / (slug + '.db')}"
        self.url = url
        self.engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(self.engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)

    def dispose(self):
        self.engine.dispose()


def get_session(request: Request):
    """FastAPI dependency: one session per request; uncommitted work is rolled back."""
    session: Session = request.app.state.db.session_factory()
    try:
        yield session
    finally:
        session.close()
