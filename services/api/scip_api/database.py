import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def _database_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "sqlite:///./scip-v2.db",
    )


def make_engine(url: str | None = None):
    connection_url = url or _database_url()
    kwargs = {"check_same_thread": False} if connection_url.startswith("sqlite") else {}
    return create_engine(connection_url, pool_pre_ping=True, connect_args=kwargs)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
