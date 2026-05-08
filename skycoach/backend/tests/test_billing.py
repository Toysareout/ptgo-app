"""Free-tier quota enforcement tests."""

from __future__ import annotations

import os
from datetime import datetime, timezone

os.environ.setdefault("SKYCOACH_DB_URL", "sqlite:///:memory:")
os.environ.setdefault("SKYCOACH_FREE_MONTHLY_ANALYSES", "3")

import pytest
from fastapi import HTTPException

from skycoach import billing
from skycoach.db import Flight, SessionLocal, User, init_db


@pytest.fixture
def db_session():
    init_db()
    session = SessionLocal()
    yield session
    # Clean up everything between tests
    session.query(Flight).delete()
    session.query(User).delete()
    session.commit()
    session.close()


def _user(plan: str = "free") -> User:
    u = User(email=f"u{datetime.now().timestamp()}@x", password_hash="x", plan=plan)
    return u


def test_free_user_under_quota_passes(db_session) -> None:
    u = _user()
    db_session.add(u)
    db_session.commit()
    billing.enforce_quota(u, db_session)  # no exception


def test_free_user_at_quota_blocks(db_session) -> None:
    u = _user()
    db_session.add(u)
    db_session.commit()
    for _ in range(billing.FREE_MONTHLY_ANALYSES):
        db_session.add(
            Flight(
                user_id=u.id,
                filename="f.igc",
                flight_date="2026-05-01",
                analysis_json="{}",
                created_at=datetime.now(tz=timezone.utc).replace(tzinfo=None),
            )
        )
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        billing.enforce_quota(u, db_session)
    assert exc.value.status_code == 402


def test_pro_user_never_blocked(db_session) -> None:
    u = _user(plan="pro")
    db_session.add(u)
    db_session.commit()
    for _ in range(20):
        db_session.add(
            Flight(
                user_id=u.id,
                filename="f.igc",
                flight_date="2026-05-01",
                analysis_json="{}",
                created_at=datetime.now(tz=timezone.utc).replace(tzinfo=None),
            )
        )
    db_session.commit()
    billing.enforce_quota(u, db_session)  # no exception


def test_old_flights_dont_count_toward_quota(db_session) -> None:
    u = _user()
    db_session.add(u)
    db_session.commit()
    last_month = datetime(2026, 4, 15)
    for _ in range(billing.FREE_MONTHLY_ANALYSES + 5):
        db_session.add(
            Flight(
                user_id=u.id,
                filename="f.igc",
                flight_date="2026-04-15",
                analysis_json="{}",
                created_at=last_month,
            )
        )
    db_session.commit()
    # Current month is empty → quota intact
    billing.enforce_quota(u, db_session)
