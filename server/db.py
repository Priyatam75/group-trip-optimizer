"""Database setup. MySQL in production (DATABASE_URL), SQLite fallback for local dev."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def database_url() -> str:
    url = os.environ.get("DATABASE_URL") or os.environ.get("MYSQL_URL") or "sqlite:///./gto_dev.db"
    # Railway and most hosts hand out mysql://...; SQLAlchemy needs a driver name.
    if url.startswith("mysql://"):
        url = "mysql+pymysql://" + url.removeprefix("mysql://")
    return url


URL = database_url()
engine = create_engine(
    URL,
    pool_pre_ping=True,
    pool_recycle=280,
    connect_args={"check_same_thread": False} if URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class TripRow(Base):
    __tablename__ = "trips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[str] = mapped_column(String(100), default="")
    input_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    runs: Mapped[list["RunRow"]] = relationship(
        back_populates="trip", cascade="all, delete-orphan", order_by="RunRow.id.desc()"
    )


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    fare_source: Mapped[str] = mapped_column(String(100))
    savings_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    result_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    trip: Mapped[TripRow] = relationship(back_populates="runs")


def init_db() -> None:
    Base.metadata.create_all(engine)
