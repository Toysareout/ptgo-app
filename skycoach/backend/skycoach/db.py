"""Database setup and ORM models for SkyCoach AI MVP."""

from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker


DB_URL = os.environ.get("SKYCOACH_DB_URL", "sqlite:///./skycoach.db")

connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False, default="")
    password_hash = Column(String(255), nullable=False)
    pilot_level = Column(String(64), default="beginner")     # beginner|advanced|xc|instructor
    license_type = Column(String(64), default="")
    wing_class = Column(String(32), default="")              # EN-A|EN-B|EN-C|EN-D|CCC
    flight_hours = Column(Integer, default=0)
    region = Column(String(128), default="")

    # Subscription state
    plan = Column(String(32), default="free")                # free|pro|flight_school
    stripe_customer_id = Column(String(64), default="")
    stripe_subscription_id = Column(String(64), default="")
    plan_renews_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    flights = relationship("Flight", back_populates="user", cascade="all, delete-orphan")


class Flight(Base):
    __tablename__ = "flights"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    flight_date = Column(String(32), nullable=False)
    pilot = Column(String(255), default="")
    glider = Column(String(255), default="")
    duration_s = Column(Integer, default=0)
    track_distance_km = Column(Float, default=0.0)
    straight_distance_km = Column(Float, default=0.0)
    max_alt_m = Column(Integer, default=0)
    max_climb_ms = Column(Float, default=0.0)
    max_sink_ms = Column(Float, default=0.0)
    risk_score = Column(Integer, default=0)
    risk_level = Column(String(16), default="low")
    weather_json = Column(Text, default="")                  # cached Open-Meteo snapshot
    analysis_json = Column(Text, nullable=False)             # full analysis payload
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="flights")


def init_db() -> None:
    """Create tables if they don't exist."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency: yields a db session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
