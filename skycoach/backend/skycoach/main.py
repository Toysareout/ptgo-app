"""SkyCoach AI — FastAPI entrypoint.

Routes:
    GET  /health                    - liveness check
    POST /api/auth/register         - create account
    POST /api/auth/login            - issue bearer token
    GET  /api/me                    - current user profile
    PATCH /api/me                   - update pilot profile

    POST /api/analyze               - parse + analyse an IGC file (no save)
    POST /api/flights               - parse, analyse, AND persist
    GET  /api/flights               - flight log for current user
    GET  /api/flights/{id}          - full analysis for one flight
    DELETE /api/flights/{id}        - remove a flight from the log

LEGAL NOTE: SkyCoach AI is a training- and analysis tool. It is NOT a certified
flight instrument and does not prevent accidents. See README.md.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from .analyzer import analysis_to_dict, analyze_flight
from .auth import get_current_user, hash_password, issue_token, verify_password
from .db import Flight, User, get_db, init_db
from .igc_parser import parse_igc

log = logging.getLogger("skycoach")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

app = FastAPI(title="SkyCoach AI", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    log.info("SkyCoach AI started — DB initialised")


# ----- schemas ------------------------------------------------------------


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str = ""


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ProfileOut(BaseModel):
    id: int
    email: str
    name: str
    pilot_level: str
    license_type: str
    wing_class: str
    flight_hours: int
    region: str


class ProfilePatch(BaseModel):
    name: str | None = None
    pilot_level: str | None = None
    license_type: str | None = None
    wing_class: str | None = None
    flight_hours: int | None = None
    region: str | None = None


class FlightSummary(BaseModel):
    id: int
    filename: str
    flight_date: str
    pilot: str
    glider: str
    duration_s: int
    track_distance_km: float
    max_alt_m: int
    max_climb_ms: float
    max_sink_ms: float
    risk_score: int
    risk_level: str
    created_at: str


# ----- helpers ------------------------------------------------------------


def _profile(u: User) -> ProfileOut:
    return ProfileOut(
        id=u.id,
        email=u.email,
        name=u.name or "",
        pilot_level=u.pilot_level or "beginner",
        license_type=u.license_type or "",
        wing_class=u.wing_class or "",
        flight_hours=u.flight_hours or 0,
        region=u.region or "",
    )


async def _read_igc(file: UploadFile) -> str:
    if not file.filename or not file.filename.lower().endswith(".igc"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Datei muss .igc sein")
    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Datei zu groß (max 10 MB)")
    try:
        return raw.decode("utf-8", errors="ignore")
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Datei konnte nicht gelesen werden")


def _analyze_text(text: str) -> dict[str, Any]:
    try:
        flight = parse_igc(text)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Ungültige IGC-Datei: {e}")
    analysis = analyze_flight(flight)
    return analysis_to_dict(analysis)


# ----- routes -------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "skycoach-ai", "version": "0.1.0"}


@app.post("/api/auth/register", response_model=TokenOut)
def register(body: RegisterIn, db: Session = Depends(get_db)) -> TokenOut:
    existing = db.query(User).filter(User.email == body.email.lower()).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "E-Mail bereits registriert")
    user = User(
        email=body.email.lower(),
        name=body.name,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenOut(access_token=issue_token(user.id))


@app.post("/api/auth/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)) -> TokenOut:
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "E-Mail oder Passwort falsch")
    return TokenOut(access_token=issue_token(user.id))


@app.get("/api/me", response_model=ProfileOut)
def me(user: User = Depends(get_current_user)) -> ProfileOut:
    return _profile(user)


@app.patch("/api/me", response_model=ProfileOut)
def update_me(
    body: ProfilePatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileOut:
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(user, k, v)
    db.commit()
    db.refresh(user)
    return _profile(user)


@app.post("/api/analyze")
async def analyze_only(file: UploadFile = File(...)) -> dict[str, Any]:
    """Analyse an IGC without persisting. Useful for the public demo."""
    text = await _read_igc(file)
    return _analyze_text(text)


@app.post("/api/flights")
async def upload_flight(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    text = await _read_igc(file)
    analysis = _analyze_text(text)
    metrics = analysis["metrics"]

    flight = Flight(
        user_id=user.id,
        filename=file.filename or "flight.igc",
        flight_date=analysis["flight_date"],
        pilot=analysis.get("pilot", "") or "",
        glider=analysis.get("glider", "") or "",
        duration_s=metrics["duration_s"],
        track_distance_km=metrics["track_distance_km"],
        straight_distance_km=metrics["straight_distance_km"],
        max_alt_m=metrics["max_alt_m"],
        max_climb_ms=metrics["max_climb_ms"],
        max_sink_ms=metrics["max_sink_ms"],
        risk_score=analysis["risk_score"],
        risk_level=analysis["risk_level"],
        analysis_json=json.dumps(analysis),
    )
    db.add(flight)
    db.commit()
    db.refresh(flight)

    return {"id": flight.id, **analysis}


@app.get("/api/flights", response_model=list[FlightSummary])
def list_flights(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[FlightSummary]:
    rows = (
        db.query(Flight)
        .filter(Flight.user_id == user.id)
        .order_by(Flight.created_at.desc())
        .all()
    )
    return [
        FlightSummary(
            id=r.id,
            filename=r.filename,
            flight_date=r.flight_date,
            pilot=r.pilot or "",
            glider=r.glider or "",
            duration_s=r.duration_s,
            track_distance_km=r.track_distance_km,
            max_alt_m=r.max_alt_m,
            max_climb_ms=r.max_climb_ms,
            max_sink_ms=r.max_sink_ms,
            risk_score=r.risk_score,
            risk_level=r.risk_level,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


@app.get("/api/flights/{flight_id}")
def get_flight(
    flight_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    flight = db.query(Flight).filter(Flight.id == flight_id, Flight.user_id == user.id).first()
    if not flight:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flug nicht gefunden")
    return {"id": flight.id, **json.loads(flight.analysis_json)}


@app.delete("/api/flights/{flight_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_flight(
    flight_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    flight = db.query(Flight).filter(Flight.id == flight_id, Flight.user_id == user.id).first()
    if not flight:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flug nicht gefunden")
    db.delete(flight)
    db.commit()
