# app.py — PTGO v2 — FastAPI
# Modules: 1 (Conversational), 2 (Signal Extraction), 4 (Login Tracking),
#          5 (Pattern Engine), 6 (Action Library), 7 (Action Engine),
#          8 (Result Screen), 9 (Outcome Feedback),
#          16 (Identity Layer), 17 (Recovery Score Engine), 18 (Pattern Timeline)
#
# DEPLOY:
# - Uvicorn behind Nginx (HTTPS)
# - systemd service
# - BASE_URL=https://app.ptgo.de

import os
import json
import time
import random
import secrets
import hashlib
import threading
import smtplib
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

import requests
from email.message import EmailMessage

from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from pydantic import EmailStr
from starlette.middleware.sessions import SessionMiddleware

from zoneinfo import ZoneInfo

from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Boolean, Text,
    ForeignKey, Float, Index
)
from sqlalchemy.orm import sessionmaker, declarative_base, relationship


# =========================================================
# CONFIG
# =========================================================

APP_SECRET = os.getenv("APP_SECRET", "dev-secret-change-me")
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
APP_TZ = os.getenv("APP_TZ", "Europe/Berlin")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "").strip()
THERAPIST_WHATSAPP_TO = os.getenv("THERAPIST_WHATSAPP_TO", "").strip()

SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASS = os.getenv("SMTP_PASS", "").strip()
SMTP_FROM = os.getenv("SMTP_FROM", "").strip()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "").strip()
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "").strip()  # created below if empty
SUBSCRIPTION_PRICE_EUR = 499  # cents = 4.99€

REMINDER_LOOP_SECONDS = int(os.getenv("REMINDER_LOOP_SECONDS", "30"))
THERAPIST_TOKEN_SECRET = os.getenv("THERAPIST_TOKEN_SECRET", APP_SECRET + "-therapist")


# =========================================================
# DB
# =========================================================

DB_URL = os.getenv("DB_URL", "sqlite:////opt/ptgo/ptgo.db")

engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Therapist(Base):
    __tablename__ = "therapists"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    phone = Column(String(64), nullable=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    patients = relationship("Patient", back_populates="therapist")


class Patient(Base):
    __tablename__ = "patients"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    phone = Column(String(64), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    email_verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    verify_code_hash = Column(String(128), nullable=True)
    verify_code_expires_at = Column(DateTime, nullable=True)
    magic_token_hash = Column(String(128), nullable=True)
    magic_token_expires_at = Column(DateTime, nullable=True)
    subscription_active = Column(Boolean, default=False)
    subscription_stripe_session = Column(String(255), nullable=True)
    reminder_enabled = Column(Boolean, default=True)
    reminder_time_local = Column(String(5), default="08:00")
    last_reminder_sent_on = Column(String(10), nullable=True)
    last_evening_sent_on = Column(String(10), nullable=True)
    therapist_id = Column(Integer, ForeignKey("therapists.id"), nullable=True)
    therapist = relationship("Therapist", back_populates="patients")
    checkins = relationship("CheckIn", back_populates="patient")
    login_events = relationship("LoginEvent", back_populates="patient")


class CheckIn(Base):
    __tablename__ = "checkins"
    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    local_day = Column(String(10), index=True)

    # Modul 1 – Conversational inputs
    daily_state = Column(Integer, nullable=True)        # 0-10
    overall_text = Column(Text, nullable=True)
    stress = Column(Integer, nullable=True)             # 0-10
    sleep = Column(Integer, nullable=True)              # 0-10
    context_text = Column(Text, nullable=True)
    body = Column(Integer, nullable=True)               # 0-10
    body_text = Column(Text, nullable=True)
    pain_map_json = Column(Text, nullable=True)
    pain_region = Column(String(64), nullable=True)
    pain_type = Column(String(64), nullable=True)
    craving = Column(Integer, nullable=True)            # 0-10
    avoidance = Column(Integer, nullable=True)          # 0-10
    mental_text = Column(Text, nullable=True)
    goal_text = Column(Text, nullable=True)

    # Modul 2 – Signal Extraction
    signals_json = Column(Text, nullable=True)

    # Modul 5 – Pattern Engine
    pattern_code = Column(String(64), nullable=True)
    pattern_label = Column(String(128), nullable=True)

    # Modul 7 – Action Engine
    action_code = Column(String(64), nullable=True)
    action_label = Column(String(128), nullable=True)
    action_text = Column(Text, nullable=True)

    # Score
    score = Column(Integer, nullable=False, default=0)
    risk_level = Column(String(16), nullable=False, default="low")

    # Legacy
    answers_json = Column(Text, nullable=True)
    ptgo_score = Column(Integer, nullable=False, default=0)
    one_action = Column(Text, nullable=True)

    patient = relationship("Patient", back_populates="checkins")
    outcomes = relationship("Outcome", back_populates="checkin")


class Outcome(Base):
    __tablename__ = "outcomes"
    id = Column(Integer, primary_key=True, index=True)
    checkin_id = Column(Integer, ForeignKey("checkins.id"), index=True, nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), index=True, nullable=False)
    rating = Column(String(16), nullable=False)   # better | same | worse
    outcome_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    checkin = relationship("CheckIn", back_populates="outcomes")


class LoginEvent(Base):
    __tablename__ = "login_events"
    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True)
    role = Column(String(32), nullable=False, default="patient")
    event_type = Column(String(32), nullable=False)   # login | logout | magic
    created_at = Column(DateTime, default=datetime.utcnow)
    ip_address = Column(String(64), nullable=True)
    user_agent = Column(String(255), nullable=True)
    patient = relationship("Patient", back_populates="login_events")


class TokenUsage(Base):
    __tablename__ = "token_usage"
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    feature = Column(String(64), nullable=False, index=True)   # e.g. signal_extraction, coaching, trend_insights, value_extraction, evening_message, mastery_today, music_analysis
    model = Column(String(64), nullable=False, default="claude-haiku-4-5")
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    cost_usd = Column(Float, nullable=False, default=0.0)
    success = Column(Boolean, nullable=False, default=True)
    error_message = Column(Text, nullable=True)
    patient_id = Column(Integer, nullable=True)


class ProductSale(Base):
    __tablename__ = "product_sales"
    id = Column(Integer, primary_key=True, index=True)
    product_name = Column(String(255), nullable=False, index=True)
    quantity = Column(Integer, nullable=False, default=1)
    price_cents = Column(Integer, nullable=False, default=0)  # Preis in Cent
    sold_at = Column(DateTime, default=datetime.utcnow, index=True)
    local_day = Column(String(10), index=True)  # YYYY-MM-DD
    therapist_id = Column(Integer, ForeignKey("therapists.id"), nullable=True)


Index("ix_checkins_patient_day", CheckIn.patient_id, CheckIn.local_day)
Index("ix_product_sales_day", ProductSale.local_day)
Index("ix_product_sales_product_day", ProductSale.product_name, ProductSale.local_day)


# =========================================================
# SELF VS SELF — DB MODELS
# =========================================================

class AvatarProfile(Base):
    """Visuelles Profil + aktuelle Stats eines Nutzers."""
    __tablename__ = "svs_avatar_profiles"
    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    nickname = Column(String(128), nullable=True)

    # Biometrie
    height_cm = Column(Integer, nullable=True)
    weight_kg = Column(Float, nullable=True)
    age = Column(Integer, nullable=True)

    # 6 Hauptstats (0–100)
    strength = Column(Integer, nullable=False, default=30)
    stamina = Column(Integer, nullable=False, default=30)
    recovery = Column(Integer, nullable=False, default=30)
    focus = Column(Integer, nullable=False, default=30)
    composure = Column(Integer, nullable=False, default=30)
    charisma = Column(Integer, nullable=False, default=30)

    # Sekundärstats (0–100)
    punch_power = Column(Integer, nullable=False, default=20)
    explosiveness = Column(Integer, nullable=False, default=20)
    balance = Column(Integer, nullable=False, default=20)
    confidence = Column(Integer, nullable=False, default=20)
    rhythm = Column(Integer, nullable=False, default=20)
    courage = Column(Integer, nullable=False, default=20)

    # Abgeleitete Kampfwerte
    footwork = Column(Integer, nullable=False, default=20)
    timing = Column(Integer, nullable=False, default=20)
    dominance = Column(Integer, nullable=False, default=20)

    # Evolution
    evolution_tag = Column(String(64), nullable=False, default="v1.0")
    total_events = Column(Integer, nullable=False, default=0)
    total_battles = Column(Integer, nullable=False, default=0)
    wins = Column(Integer, nullable=False, default=0)
    losses = Column(Integer, nullable=False, default=0)

    patient = relationship("Patient", backref="avatar_profile")
    versions = relationship("AvatarVersion", back_populates="profile", order_by="AvatarVersion.created_at")


class AvatarVersion(Base):
    """Eingefrorener Snapshot für Kämpfe."""
    __tablename__ = "svs_avatar_versions"
    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(Integer, ForeignKey("svs_avatar_profiles.id"), index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    version_tag = Column(String(64), nullable=False)
    is_opponent = Column(Boolean, default=False)

    # Snapshot aller Stats
    strength = Column(Integer, nullable=False, default=30)
    stamina = Column(Integer, nullable=False, default=30)
    recovery = Column(Integer, nullable=False, default=30)
    focus = Column(Integer, nullable=False, default=30)
    composure = Column(Integer, nullable=False, default=30)
    charisma = Column(Integer, nullable=False, default=30)
    punch_power = Column(Integer, nullable=False, default=20)
    explosiveness = Column(Integer, nullable=False, default=20)
    balance = Column(Integer, nullable=False, default=20)
    confidence = Column(Integer, nullable=False, default=20)
    rhythm = Column(Integer, nullable=False, default=20)
    courage = Column(Integer, nullable=False, default=20)
    footwork = Column(Integer, nullable=False, default=20)
    timing = Column(Integer, nullable=False, default=20)
    dominance = Column(Integer, nullable=False, default=20)

    profile = relationship("AvatarProfile", back_populates="versions")


class RealLifeEvent(Base):
    """Reale Handlung die Stats verändert."""
    __tablename__ = "svs_events"
    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(Integer, ForeignKey("svs_avatar_profiles.id"), index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    local_day = Column(String(10), index=True)

    # Art des Events
    event_type = Column(String(64), nullable=False)  # workout_chest, workout_legs, cardio, sleep, supplement, meditation, music, communication, nutrition
    event_detail = Column(Text, nullable=True)        # Freitext
    duration_min = Column(Integer, nullable=True)

    # Direkte Stat-Änderungen (berechnet vom Skill-System)
    primary_stat = Column(String(32), nullable=True)
    primary_delta = Column(Integer, nullable=False, default=0)
    secondary_stat = Column(String(32), nullable=True)
    secondary_delta = Column(Integer, nullable=False, default=0)
    buff_name = Column(String(64), nullable=True)
    buff_value = Column(Integer, nullable=False, default=0)
    buff_expires_at = Column(DateTime, nullable=True)

    profile = relationship("AvatarProfile", backref="events")


class PsychResponse(Base):
    """Psychologische Frage + Antwort + Tiefenbewertung."""
    __tablename__ = "svs_psych_responses"
    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(Integer, ForeignKey("svs_avatar_profiles.id"), index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    question_type = Column(String(32), nullable=False)  # reflection, action, confrontation, transfer
    question_text = Column(Text, nullable=False)
    answer_text = Column(Text, nullable=True)
    depth_score = Column(Integer, nullable=False, default=0)   # 0–10
    honesty_score = Column(Integer, nullable=False, default=0) # 0–10
    stat_affected = Column(String(32), nullable=True)
    stat_delta = Column(Integer, nullable=False, default=0)
    transfer_task = Column(Text, nullable=True)
    transfer_done = Column(Boolean, default=False)

    profile = relationship("AvatarProfile", backref="psych_responses")


class BattleSimulation(Base):
    """Kampf zwischen alter und neuer Avatar-Version."""
    __tablename__ = "svs_battles"
    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(Integer, ForeignKey("svs_avatar_profiles.id"), index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    old_version_id = Column(Integer, ForeignKey("svs_avatar_versions.id"), nullable=False)
    new_version_id = Column(Integer, ForeignKey("svs_avatar_versions.id"), nullable=False)

    # Ergebnis
    winner = Column(String(16), nullable=False, default="new")  # "old" | "new" | "draw"
    result_type = Column(String(32), nullable=False, default="decision")  # knockout, decision, close_decision
    rounds_total = Column(Integer, nullable=False, default=3)
    replay_json = Column(Text, nullable=True)  # JSON mit Runden-Details
    commentary_json = Column(Text, nullable=True)  # JSON mit Kampf-Kommentaren

    # Vergleich
    stat_diffs_json = Column(Text, nullable=True)  # JSON: welche Stats gestiegen/gefallen
    biggest_lever = Column(Text, nullable=True)     # Empfohlene nächste Handlung

    profile = relationship("AvatarProfile", backref="battles")
    old_version = relationship("AvatarVersion", foreign_keys=[old_version_id])
    new_version = relationship("AvatarVersion", foreign_keys=[new_version_id])


Index("ix_svs_events_profile_day", RealLifeEvent.profile_id, RealLifeEvent.local_day)

Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =========================================================
# TOKEN TRACKING
# =========================================================

# Pricing per 1M tokens (USD) — Claude Haiku 4.5
_AI_PRICING = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}

def _track_ai_usage(feature: str, resp_json: dict, model: str = "claude-haiku-4-5",
                     success: bool = True, error_message: str = None, patient_id: int = None):
    """Record token usage from a Claude API response."""
    try:
        usage = resp_json.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        total = input_tokens + output_tokens
        pricing = _AI_PRICING.get(model, {"input": 1.0, "output": 5.0})
        cost = (input_tokens / 1_000_000) * pricing["input"] + (output_tokens / 1_000_000) * pricing["output"]

        db = SessionLocal()
        try:
            db.add(TokenUsage(
                feature=feature, model=model,
                input_tokens=input_tokens, output_tokens=output_tokens,
                total_tokens=total, cost_usd=cost,
                success=success, error_message=error_message,
                patient_id=patient_id,
            ))
            db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[WARN] Token tracking failed: {e}")


def _track_ai_error(feature: str, error: str, patient_id: int = None):
    """Record a failed AI call."""
    try:
        db = SessionLocal()
        try:
            db.add(TokenUsage(
                feature=feature, model="claude-haiku-4-5",
                input_tokens=0, output_tokens=0, total_tokens=0, cost_usd=0.0,
                success=False, error_message=str(error)[:500],
                patient_id=patient_id,
            ))
            db.commit()
        finally:
            db.close()
    except Exception:
        pass


# =========================================================
# APP
# =========================================================

app = FastAPI(title="PTGO Daily Loop v2")
app.add_middleware(SessionMiddleware, secret_key=APP_SECRET)


# =========================================================
# THETOYSAREOUT — served from local file
# =========================================================

_TTAO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "thetoysareout.html")
_LIVE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live.html")
_DASHBOARD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mein-dashboard.html")
_COACHING_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coaching.html")

@app.get("/thetoysareout", response_class=HTMLResponse)
async def thetoysareout():
    if os.path.exists(_TTAO_PATH):
        return FileResponse(_TTAO_PATH, media_type="text/html")
    raise HTTPException(status_code=404, detail="Not found")

@app.get("/live", response_class=HTMLResponse)
async def live_page():
    if os.path.exists(_LIVE_PATH):
        return FileResponse(_LIVE_PATH, media_type="text/html")
    raise HTTPException(status_code=404, detail="Not found")

@app.get("/mein-dashboard", response_class=HTMLResponse)
async def mein_dashboard():
    if os.path.exists(_DASHBOARD_PATH):
        return FileResponse(_DASHBOARD_PATH, media_type="text/html")
    raise HTTPException(status_code=404, detail="Not found")

@app.get("/coaching", response_class=HTMLResponse)
async def coaching_page():
    if os.path.exists(_COACHING_PATH):
        return FileResponse(_COACHING_PATH, media_type="text/html")
    raise HTTPException(status_code=404, detail="Not found")


# =========================================================
# UTILS
# =========================================================

def _now_utc() -> datetime:
    return datetime.utcnow()

def _now_local() -> datetime:
    return datetime.now(ZoneInfo(APP_TZ))

def _clamp_int(v, lo: int, hi: int) -> int:
    return max(lo, min(int(v), hi))

def _hash_code(code: str) -> str:
    return hashlib.sha256((code + APP_SECRET).encode("utf-8")).hexdigest()

def _hash_magic(token: str) -> str:
    return hashlib.sha256((token + APP_SECRET + "MAGIC").encode("utf-8")).hexdigest()

def _hash_password(pw: str) -> str:
    return hashlib.sha256((pw + APP_SECRET + "PW").encode("utf-8")).hexdigest()

def require_patient_login(request: Request, db) -> Patient:
    pid = request.session.get("patient_id")
    if not pid:
        raise HTTPException(status_code=401, detail="Not logged in")
    p = db.query(Patient).filter(Patient.id == pid).first()
    if not p:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Not logged in")
    return p

def require_therapist_login(request: Request, db) -> Therapist:
    tid = request.session.get("therapist_id")
    if not tid:
        raise HTTPException(status_code=401, detail="Therapist not logged in")
    t = db.query(Therapist).filter(Therapist.id == tid).first()
    if not t:
        request.session.pop("therapist_id", None)
        raise HTTPException(status_code=401, detail="Therapist not logged in")
    return t

def issue_magic_link(db, patient: Patient, ttl_minutes: int = 60 * 24) -> str:
    token = secrets.token_urlsafe(32)
    patient.magic_token_hash = _hash_magic(token)
    patient.magic_token_expires_at = _now_utc() + timedelta(minutes=ttl_minutes)
    db.commit()
    return f"{BASE_URL}/magic/{token}"

def log_login_event(db, request: Request, patient_id: Optional[int], role: str, event_type: str):
    try:
        ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "")
        ua = request.headers.get("user-agent", "")[:255]
        ev = LoginEvent(patient_id=patient_id, role=role, event_type=event_type, ip_address=ip, user_agent=ua)
        db.add(ev)
        db.commit()
    except Exception as e:
        print("[WARN] login event log failed:", e)


# =========================================================
# WHATSAPP
# =========================================================

def _twilio_enabled() -> bool:
    return bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM)

def _send_whatsapp(to_e164_phone: str, message: str) -> None:
    if not _twilio_enabled():
        print("[DEV] WhatsApp ->", to_e164_phone)
        print(message)
        return
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    data = {"From": TWILIO_WHATSAPP_FROM, "To": f"whatsapp:{to_e164_phone}", "Body": message.strip()}
    r = requests.post(url, data=data, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=20)
    r.raise_for_status()

def send_whatsapp_to_patient(patient: Patient, message: str) -> None:
    _send_whatsapp(patient.phone, message)

def send_whatsapp_to_therapist(patient: Patient, therapist: Optional[Therapist], message: str) -> None:
    target = None
    if therapist and therapist.phone:
        target = therapist.phone
    elif THERAPIST_WHATSAPP_TO:
        target = THERAPIST_WHATSAPP_TO.replace("whatsapp:", "").strip()
    if not target:
        print("[DEV] No therapist WhatsApp target configured.")
        return
    _send_whatsapp(target, f"[PTGO] {patient.name}: " + message)


# =========================================================
# VOICE HELPERS
# =========================================================

def _extract_pain_region(text: str) -> str:
    text = text.lower()
    if any(w in text for w in ["nacken", "hals", "nackenschmerz"]): return "neck"
    if any(w in text for w in ["schulter", "schultern"]): return "shoulder"
    if any(w in text for w in ["rücken", "oberer rücken", "oberer"]): return "upper_back"
    if any(w in text for w in ["unterer rücken", "lendenwirbel", "kreuz"]): return "lower_back"
    if any(w in text for w in ["kopf", "kopfschmerz", "migräne"]): return "head"
    if any(w in text for w in ["brust", "herz", "brustkorb"]): return "chest"
    if any(w in text for w in ["bauch", "magen", "magensch"]): return "stomach"
    if any(w in text for w in ["bein", "knie", "beine"]): return "legs"
    return ""

def _ai_extract_values(data: Dict[str, Any]) -> Dict[str, Any]:
    """Use Claude to extract numeric values (0-10) from voice text."""
    if not ANTHROPIC_API_KEY:
        return data

    combined = (
        f"Stimmung: {data.get('overall_text','')}\n"
        f"Herausforderung: {data.get('context_text','')}\n"
        f"Körper: {data.get('body_text','')}\n"
        f"Gedanken: {data.get('mental_text','')}\n"
        f"Ziel: {data.get('goal_text','')}"
    )

    prompt = (
        f"Analysiere diese Spracheingaben eines Patienten und extrahiere numerische Werte (0-10).\n\n"
        f"{combined}\n\n"
        f"Antworte NUR mit validem JSON (keine Erklärung):\n"
        f'{{"daily_state":5,"stress":5,"sleep":5,"body":5,"craving":0,"avoidance":0}}\n'
        f"Skala: 0=sehr schlecht/niedrig, 10=sehr gut/hoch. Stress/Craving/Avoidance: 0=keins, 10=extrem."
    )

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5", "max_tokens": 150, "messages": [{"role": "user", "content": prompt}]},
            timeout=15,
        )
        resp.raise_for_status()
        resp_data = resp.json()
        _track_ai_usage("value_extraction", resp_data)
        text = resp_data["content"][0]["text"].strip().replace("```json","").replace("```","").strip()
        vals = json.loads(text)
        for k in ["daily_state","stress","sleep","body","craving","avoidance"]:
            if k in vals:
                data[k] = _clamp_int(vals[k], 0, 10)
    except Exception as e:
        _track_ai_error("value_extraction", str(e))
        print("[WARN] AI value extraction failed:", e)

    return data


# =========================================================
# MODUL 2 – SIGNAL EXTRACTION (via Claude AI)
# =========================================================

def extract_signals(data: Dict[str, Any]) -> Dict[str, Any]:
    """Use Claude to extract structured signals from conversational inputs."""
    if not ANTHROPIC_API_KEY:
        return {}

    prompt = (
        f"Du analysierst die Eingaben eines Patienten aus einem therapeutischen Check-in.\n\n"
        f"Eingaben:\n"
        f"- Tagesstimmung (0-10): {data.get('daily_state')}\n"
        f"- Freitext Stimmung: {data.get('overall_text', '')}\n"
        f"- Stress (0-10): {data.get('stress')}\n"
        f"- Schlaf (0-10): {data.get('sleep')}\n"
        f"- Kontext: {data.get('context_text', '')}\n"
        f"- Körper (0-10): {data.get('body')}\n"
        f"- Körper Text: {data.get('body_text', '')}\n"
        f"- Craving (0-10): {data.get('craving')}\n"
        f"- Vermeidung (0-10): {data.get('avoidance')}\n"
        f"- Mental Text: {data.get('mental_text', '')}\n"
        f"- Tagesziel: {data.get('goal_text', '')}\n\n"
        f"Extrahiere die wichtigsten Signale als JSON. Antworte NUR mit validem JSON:\n"
        f'{{"stress_level":"low|medium|high","sleep_quality":"poor|fair|good","body_tension":"low|medium|high","craving_risk":"low|medium|high","avoidance_risk":"low|medium|high","key_theme":"ein Satz was den Tag prägt"}}'
    )

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5", "max_tokens": 300, "messages": [{"role": "user", "content": prompt}]},
            timeout=15,
        )
        resp.raise_for_status()
        resp_data = resp.json()
        _track_ai_usage("signal_extraction", resp_data)
        text = resp_data["content"][0]["text"].strip()
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        _track_ai_error("signal_extraction", str(e))
        print("[WARN] Signal extraction failed:", e)
        return {}


# =========================================================
# MODUL 5 – PATTERN ENGINE
# =========================================================

PATTERNS = {
    "stress_overload":     "Stress Overload",
    "recovery_deficit":    "Recovery Deficit",
    "upper_body_tension":  "Upper Body Tension",
    "neck_guarding":       "Neck Guarding",
    "impulse_pattern":     "Impulse Pattern",
    "avoidance_pattern":   "Avoidance Pattern",
    "low_mood":            "Low Mood",
    "balanced":            "Balanced",
}

def detect_pattern(data: Dict[str, Any]) -> tuple:
    stress   = _clamp_int(data.get("stress", 5), 0, 10)
    sleep    = _clamp_int(data.get("sleep", 5), 0, 10)
    body     = _clamp_int(data.get("body", 5), 0, 10)
    craving  = _clamp_int(data.get("craving", 0), 0, 10)
    avoidance= _clamp_int(data.get("avoidance", 0), 0, 10)
    mood     = _clamp_int(data.get("daily_state", 5), 0, 10)
    pain_region = data.get("pain_region", "")

    if stress > 7 and sleep < 5:
        return "stress_overload", PATTERNS["stress_overload"]
    if sleep < 4:
        return "recovery_deficit", PATTERNS["recovery_deficit"]
    if pain_region in ("shoulder", "upper_back"):
        return "upper_body_tension", PATTERNS["upper_body_tension"]
    if pain_region == "neck":
        return "neck_guarding", PATTERNS["neck_guarding"]
    if craving > 6:
        return "impulse_pattern", PATTERNS["impulse_pattern"]
    if avoidance > 6:
        return "avoidance_pattern", PATTERNS["avoidance_pattern"]
    if mood < 4:
        return "low_mood", PATTERNS["low_mood"]
    return "balanced", PATTERNS["balanced"]


# =========================================================
# MODUL 6 – ACTION LIBRARY
# =========================================================

ACTION_LIBRARY = {
    "physiological_sigh": {
        "label": "Physiological Sigh",
        "why": "Zwei kurze Einatemzüge gefolgt von einem langen Ausatmen aktivieren den Parasympathikus und senken Stress sofort.",
        "instructions": "Atme zweimal kurz durch die Nase ein (doppelter Einatemzug), dann langsam und vollständig durch den Mund aus. Wiederhole 3-5x.",
        "duration": "2 Minuten",
        "voice_script": "Einatmen... nochmal kurz einatmen... und langsam ausatmen.",
    },
    "extended_exhale": {
        "label": "Extended Exhale",
        "why": "Verlängertes Ausatmen aktiviert den Vagusnerv und beruhigt das Nervensystem.",
        "instructions": "4 Sekunden einatmen, 6-8 Sekunden ausatmen. 5 Wiederholungen.",
        "duration": "3 Minuten",
        "voice_script": "Einatmen... 2... 3... 4... Ausatmen... 2... 3... 4... 5... 6.",
    },
    "shoulder_release": {
        "label": "Shoulder Release",
        "why": "Schultern sind das erste Spannungsspeicher bei Stress. Bewusste Entlastung löst den Kreislauf.",
        "instructions": "Schultern hochziehen, 5 Sekunden halten, dann fallen lassen. 5x wiederholen. Danach Schulterkreisen.",
        "duration": "2 Minuten",
        "voice_script": "Schultern hoch... halten... und fallen lassen.",
    },
    "neck_reset": {
        "label": "Neck Reset",
        "why": "Der Nacken trägt emotionale Spannung. Sanfte Mobilisation löst Schutzspannung.",
        "instructions": "Kopf langsam zur rechten Schulter, 10 Sekunden halten, dann links. Danach langsames Nicken 5x.",
        "duration": "3 Minuten",
        "voice_script": "Kopf langsam zur Seite... atme in die Dehnung... und zurück.",
    },
    "walk_reset": {
        "label": "Walk Reset",
        "why": "10 Minuten Gehen reguliert Cortisol und verbessert Stimmung messbar.",
        "instructions": "10 Minuten draußen gehen, kein Handy, bewusst atmen. Tempo: angenehm, nicht sportlich.",
        "duration": "10 Minuten",
        "voice_script": "Geh raus. 10 Minuten. Kein Handy. Einfach gehen.",
    },
    "five_minute_start": {
        "label": "5-Minute Start",
        "why": "Vermeidung löst sich durch minimale Exposition. 5 Minuten starten reicht um den Kreislauf zu durchbrechen.",
        "instructions": "Stelle einen Timer auf 5 Minuten. Fang mit der vermiedenen Aufgabe an. Nach 5 Minuten darfst du aufhören.",
        "duration": "5 Minuten",
        "voice_script": "Timer auf 5 Minuten. Anfangen. Nur 5 Minuten.",
    },
    "urge_interrupt": {
        "label": "Urge Interrupt",
        "why": "Craving dauert im Schnitt 3-7 Minuten. Überbrücken mit körperlicher Aktivität unterbricht den Impuls.",
        "instructions": "Sofort: 15 Kniebeugen oder 20 Liegestütze. Dann ein großes Glas Wasser. Warte 10 Minuten.",
        "duration": "3 Minuten",
        "voice_script": "Kniebeugen jetzt. Los. Zähle laut mit.",
    },
    "sleep_downshift": {
        "label": "Sleep Downshift",
        "why": "Der Übergang Wach→Schlaf braucht einen bewussten Shutdown.",
        "instructions": "60 Minuten vor Schlaf: Handy weg, Licht dimmen, Temperatur senken. 10 Minuten lesen oder Körperscan.",
        "duration": "60 Minuten vor Schlaf",
        "voice_script": "Handy weg. Licht aus. Augen zu.",
    },
    "write_down_reset": {
        "label": "Write Down Reset",
        "why": "Gedanken aufschreiben leert den mentalen Arbeitsspeicher und reduziert Grübeln.",
        "instructions": "3 Minuten alles aufschreiben was im Kopf ist. Kein Filter, kein Ziel. Danach Zettel weglegen.",
        "duration": "3 Minuten",
        "voice_script": "Stift. Papier. Alles raus was im Kopf ist.",
    },
}


# =========================================================
# MODUL 7 – ACTION ENGINE
# =========================================================

PATTERN_TO_ACTION = {
    "stress_overload":    "physiological_sigh",
    "recovery_deficit":   "sleep_downshift",
    "upper_body_tension": "shoulder_release",
    "neck_guarding":      "neck_reset",
    "impulse_pattern":    "urge_interrupt",
    "avoidance_pattern":  "five_minute_start",
    "low_mood":           "walk_reset",
    "balanced":           "write_down_reset",
}

def get_action(pattern_code: str) -> tuple:
    action_code = PATTERN_TO_ACTION.get(pattern_code, "walk_reset")
    action = ACTION_LIBRARY.get(action_code, ACTION_LIBRARY["walk_reset"])
    return action_code, action


# =========================================================
# SCORE CALCULATION
# =========================================================

def compute_score(data: Dict[str, Any]) -> tuple:
    mood     = _clamp_int(data.get("daily_state", 5), 0, 10)
    sleep    = _clamp_int(data.get("sleep", 5), 0, 10)
    body     = _clamp_int(data.get("body", 5), 0, 10)
    stress   = _clamp_int(data.get("stress", 5), 0, 10)
    craving  = _clamp_int(data.get("craving", 0), 0, 10)
    avoidance= _clamp_int(data.get("avoidance", 0), 0, 10)

    raw = (
        0.28 * mood + 0.22 * sleep + 0.18 * body +
        0.12 * (10 - stress) + 0.10 * (10 - craving) + 0.10 * (10 - avoidance)
    )
    score = _clamp_int(int(round((raw / 10.0) * 100)), 0, 100)

    risk_points = 0
    if stress >= 8:    risk_points += 2
    if craving >= 7:   risk_points += 2
    if avoidance >= 7: risk_points += 2
    if mood <= 3:      risk_points += 2
    if sleep <= 3:     risk_points += 1

    risk = "high" if risk_points >= 6 else ("medium" if risk_points >= 3 else "low")
    return score, risk


# =========================================================
# PSYCHOLOGY HELPERS — real data only, no fake numbers
# =========================================================

def _get_platform_stats(db) -> Dict[str, Any]:
    """Return real platform stats for social proof. All numbers from DB."""
    total_checkins = db.query(CheckIn).count()
    total_patients = db.query(Patient).count()
    total_outcomes = db.query(Outcome).filter(Outcome.rating == "better").count()
    return {
        "total_checkins": total_checkins,
        "total_patients": total_patients,
        "positive_outcomes": total_outcomes,
    }

def _get_patient_streak(db, patient_id: int) -> Dict[str, Any]:
    """Calculate consecutive-day check-in streak for a patient."""
    rows = (
        db.query(CheckIn.local_day)
        .filter(CheckIn.patient_id == patient_id)
        .distinct()
        .order_by(CheckIn.local_day.desc())
        .limit(365)
        .all()
    )
    if not rows:
        return {"current_streak": 0, "total_checkins": 0, "longest_streak": 0}

    days = sorted(set(r[0] for r in rows if r[0]), reverse=True)
    total = len(days)

    # Current streak: count consecutive days from today backwards
    today_str = _now_local().strftime("%Y-%m-%d")
    streak = 0
    check_date = _now_local().date()
    for d in days:
        if d == check_date.strftime("%Y-%m-%d"):
            streak += 1
            check_date -= timedelta(days=1)
        elif d == (check_date).strftime("%Y-%m-%d"):
            # allow gap of today if not yet checked in
            pass
        else:
            break

    # If streak is 0 but yesterday was checked in, count from yesterday
    if streak == 0 and days:
        yesterday = (_now_local().date() - timedelta(days=1)).strftime("%Y-%m-%d")
        if days[0] == yesterday:
            check_date = _now_local().date() - timedelta(days=1)
            for d in days:
                if d == check_date.strftime("%Y-%m-%d"):
                    streak += 1
                    check_date -= timedelta(days=1)
                else:
                    break

    return {"current_streak": streak, "total_checkins": total}


# =========================================================
# UI HELPERS
# =========================================================

def _page(title: str, body_html: str, request: Optional[Request] = None, step: int = 0, total: int = 5) -> HTMLResponse:
    progress_bar = ""
    if step > 0:
        pct = int((step / total) * 100)
        progress_bar = f"""
        <div style="height:4px;background:#1f2937;border-radius:999px;margin-bottom:20px;">
          <div style="height:4px;background:#f59e0b;border-radius:999px;width:{pct}%;transition:width .3s"></div>
        </div>
        <p class="small" style="margin-bottom:16px;color:#6b7280">Schritt {step} von {total}</p>
        """

    css = """
    <style>
      :root { --bg:#0b0f1a; --card:#0f172a; --muted:#94a3b8; --text:#e5e7eb; --accent:#f59e0b; --line:#1f2937; }
      html,body{height:100%;}
      body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Inter,Arial,sans-serif;background:radial-gradient(1000px 600px at 50% -100px,#1f2a52,transparent),var(--bg);color:var(--text);}
      a{color:var(--accent);text-decoration:none}
      .wrap{max-width:720px;margin:0 auto;padding:26px 16px 60px;}
      .top{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;}
      .brand{font-weight:700;letter-spacing:.2px}
      .pill{font-size:12px;color:var(--muted);border:1px solid var(--line);padding:6px 10px;border-radius:999px;}
      .card{background:linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,.02));border:1px solid var(--line);border-radius:18px;padding:24px 20px 20px;box-shadow:0 20px 60px rgba(0,0,0,.35);}
      h1{font-size:32px;line-height:1.1;margin:8px 0 12px;}
      h2{font-size:18px;margin:18px 0 10px;color:#f3f4f6}
      p{color:var(--muted);line-height:1.6}
      .hr{height:1px;background:var(--line);margin:18px 0;}
      label{display:block;color:#cbd5e1;font-size:13px;margin:14px 0 6px}
      input,select,textarea{width:100%;box-sizing:border-box;background:#0b1223;border:1px solid #263246;color:#e5e7eb;border-radius:12px;padding:12px;font-size:16px;outline:none}
      input:focus,textarea:focus{border-color:#f59e0b}
      .row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
      button,.btn{display:inline-block;background:linear-gradient(180deg,#fbbf24,#f59e0b);color:#111827;border:none;border-radius:14px;padding:14px 20px;font-weight:700;font-size:16px;cursor:pointer;text-align:center;width:100%;margin-top:8px;}
      .btn-outline{background:transparent;border:1px solid var(--line);color:var(--muted);width:auto;padding:10px 16px;font-size:14px;}
      .small{font-size:12px;color:var(--muted)}
      .code{font-family:monospace;background:#0b1223;border:1px solid #263246;border-radius:12px;padding:10px;color:#e5e7eb;word-break:break-all}
      .grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
      .kpi{border:1px solid var(--line);border-radius:14px;padding:14px;background:rgba(255,255,255,.02)}
      .kpi b{display:block;font-size:22px;margin-top:4px}
      .tag{display:inline-block;font-size:12px;border:1px solid #374151;padding:4px 8px;border-radius:999px;color:#cbd5e1;margin-right:6px}
      .warn{color:#fecaca} .ok{color:#bbf7d0}
      .action-box{background:rgba(245,158,11,.07);border:1px solid rgba(245,158,11,.3);border-radius:16px;padding:18px;margin:16px 0}
      .pattern-tag{display:inline-block;background:rgba(99,102,241,.15);border:1px solid rgba(99,102,241,.4);color:#a5b4fc;border-radius:999px;padding:4px 12px;font-size:13px;margin-bottom:12px;}
      .outcome-btn{display:inline-block;background:rgba(255,255,255,.04);border:1px solid var(--line);border-radius:12px;padding:12px 20px;font-size:15px;cursor:pointer;text-align:center;margin:4px;width:calc(33% - 10px);font-weight:600;}
      .outcome-btn:hover{border-color:#f59e0b;color:#f59e0b}
      .slider-wrap{margin:8px 0}
      input[type=range]{padding:0;height:6px;accent-color:#f59e0b;}
      .slider-val{font-size:24px;font-weight:700;color:#f59e0b;display:inline-block;min-width:30px;}
    </style>
    """

    top = """
      <div class="top">
        <div class="brand">PTGO <span style="opacity:.4">•</span> Daily</div>
        <div class="pill">v2</div>
      </div>
    """

    html = f"""
    <html><head>
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>{title}</title>
      {css}
    </head>
    <body>
      <div class="wrap">
        {top}
        <div class="card">
          {progress_bar}
          {body_html}
        </div>
      </div>
    </body></html>
    """
    return HTMLResponse(html)


# =========================================================
# REMINDERS
# =========================================================

def _should_send_reminder_now(p: Patient, now_local: datetime) -> bool:
    if not p.reminder_enabled:
        return False
    today = now_local.date().isoformat()
    if p.last_reminder_sent_on == today:
        return False
    try:
        hh, mm = (p.reminder_time_local or "08:00").split(":")
        target = now_local.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    except Exception:
        target = now_local.replace(hour=8, minute=0, second=0, microsecond=0)
    delta = (now_local - target).total_seconds()
    return 0 <= delta <= 60 * 6

def _patient_checked_in_today(db, p: Patient) -> bool:
    today = _now_local().date().isoformat()
    return bool(db.query(CheckIn).filter(CheckIn.patient_id == p.id, CheckIn.local_day == today).first())

def reminder_loop():
    while True:
        try:
            now_local = _now_local()
            db = SessionLocal()
            patients = db.query(Patient).filter(Patient.reminder_enabled == True).all()
            for p in patients:
                # Morning reminder
                if _should_send_reminder_now(p, now_local):
                    if _patient_checked_in_today(db, p):
                        p.last_reminder_sent_on = now_local.date().isoformat()
                        db.commit()
                        continue
                    magic = issue_magic_link(db, p, ttl_minutes=60 * 24)
                    msg = (
                        f"Guten Morgen {p.name} ☀️\n\n"
                        f"Dein Daily Check wartet.\n"
                        f"30 Sekunden → Pattern → 1 Action.\n\n"
                        f"➡️ {magic}"
                    )
                    try:
                        send_whatsapp_to_patient(p, msg)
                        p.last_reminder_sent_on = now_local.date().isoformat()
                        db.commit()
                    except Exception as e:
                        print("[WARN] Reminder send failed:", e)

                # Evening reflection (2nd daily message)
                if _should_send_evening_message(p, now_local):
                    today = now_local.date().isoformat()
                    if getattr(p, 'last_evening_sent_on', None) != today:
                        try:
                            evening_msg = _generate_evening_message(db, p)
                            send_whatsapp_to_patient(p, evening_msg)
                            p.last_evening_sent_on = today
                            db.commit()
                        except Exception as e:
                            print("[WARN] Evening message failed:", e)

            db.close()
        except Exception as e:
            print("[WARN] Reminder loop error:", e)
        time.sleep(REMINDER_LOOP_SECONDS)

@app.on_event("startup")
def startup():
    th = threading.Thread(target=reminder_loop, daemon=True)
    th.start()


# =========================================================
# HEALTH
# =========================================================

@app.get("/health")
def health():
    return {"ok": True, "time_utc": int(time.time()), "tz": APP_TZ, "base_url": BASE_URL, "twilio": _twilio_enabled(), "ai": bool(ANTHROPIC_API_KEY)}


# =========================================================
# AUTH – Magic Link
# =========================================================

@app.get("/", response_class=HTMLResponse)
def index(request: Request, db=Depends(get_db)):
    if request.session.get("patient_id"):
        return RedirectResponse("/checkin/1", status_code=303)

    # Social Proof — real numbers from DB
    stats = _get_platform_stats(db)
    social_proof = ""
    if stats["total_checkins"] >= 10:
        social_proof = f"""
        <div style="display:flex;gap:12px;margin:16px 0;flex-wrap:wrap">
          <div style="flex:1;min-width:100px;background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.2);border-radius:12px;padding:12px;text-align:center">
            <div style="font-size:22px;font-weight:700;color:#f59e0b">{stats['total_checkins']}</div>
            <div style="font-size:11px;color:#6b7280">Check-ins durchgeführt</div>
          </div>
          <div style="flex:1;min-width:100px;background:rgba(34,197,94,.06);border:1px solid rgba(34,197,94,.2);border-radius:12px;padding:12px;text-align:center">
            <div style="font-size:22px;font-weight:700;color:#22c55e">{stats['total_patients']}</div>
            <div style="font-size:11px;color:#6b7280">Nutzer vertrauen PTGO</div>
          </div>
        </div>
        """

    body = f"""
      <h1>Daily State Check</h1>
      <p>30 Sekunden. Ehrlich. Dann bekommst du dein Pattern + 1 klare Action.</p>

      {social_proof}

      <div style="display:flex;gap:8px;flex-wrap:wrap;margin:12px 0">
        <span style="font-size:11px;padding:4px 10px;border-radius:999px;border:1px solid rgba(99,102,241,.3);color:#a5b4fc">Evidenzbasierte Methoden</span>
        <span style="font-size:11px;padding:4px 10px;border-radius:999px;border:1px solid rgba(34,197,94,.3);color:#86efac">Therapeuten-geprüft</span>
        <span style="font-size:11px;padding:4px 10px;border-radius:999px;border:1px solid rgba(245,158,11,.3);color:#fcd34d">KI-gestützte Analyse</span>
      </div>

      <div class="hr"></div>
      <h2>Start per WhatsApp Link</h2>
      <form method="post" action="/auth/start">
        <div class="row">
          <div>
            <label>Name</label>
            <input name="name" required placeholder="Vor- und Nachname">
          </div>
          <div>
            <label>Handynummer (E.164)</label>
            <input name="phone" required placeholder="+49...">
          </div>
        </div>
        <label>E-Mail</label>
        <input name="email" required placeholder="name@email.de" type="email">
        <div style="height:12px"></div>
        <button type="submit">Link per WhatsApp senden</button>
        <p class="small" style="margin-top:10px">Kein Passwort. Kein Code. 1-Tap Magic Link.</p>
      </form>
      <div class="hr"></div>
      <p class="small">Therapeut? <a href="/therapist/login">Login</a></p>
    """
    return _page("PTGO • Start", body, request=request)


@app.post("/auth/start", response_class=HTMLResponse)
def auth_start(request: Request, name: str = Form(...), phone: str = Form(...), email: EmailStr = Form(...), db=Depends(get_db)):
    name = name.strip()
    phone = phone.strip()
    email = str(email).strip().lower()

    patient = db.query(Patient).filter((Patient.phone == phone) | (Patient.email == email)).first()
    if not patient:
        patient = Patient(name=name, phone=phone, email=email, email_verified=True)
        db.add(patient)
        db.commit()
        db.refresh(patient)
    else:
        patient.name = name
        patient.phone = phone
        patient.email = email
        db.commit()

    request.session["patient_id"] = patient.id
    log_login_event(db, request, patient.id, "patient", "login")

    magic = issue_magic_link(db, patient, ttl_minutes=60 * 24)
    try:
        msg = f"Hallo {patient.name} 👋\n\nDein PTGO Daily Check:\n→ Pattern erkennen\n→ 1 Action\n\n1 Tap: {magic}"
        send_whatsapp_to_patient(patient, msg)
    except Exception as e:
        print("[WARN] WhatsApp send failed:", e)

    body = f"""
      <h1>Link gesendet ✅</h1>
      <p>Wir haben dir einen 1-Tap Link per WhatsApp geschickt.</p>
      <div style="background:rgba(37,211,102,.08);border:1px solid rgba(37,211,102,.3);border-radius:14px;padding:14px 16px;margin:14px 0">
        <p style="color:#4ade80;font-size:13px;font-weight:600;margin:0 0 6px">⚠️ Ersten Schritt nicht vergessen!</p>
        <p style="font-size:13px;margin:0 0 8px;color:#d1fae5">Damit WhatsApp funktioniert, schick <strong>einmalig</strong> diese Nachricht an unsere Nummer:</p>
        <div style="background:#0b1223;border:1px solid #1f2937;border-radius:10px;padding:10px 14px;margin:8px 0;font-family:monospace;font-size:15px;color:#f59e0b;text-align:center">
          join least-fight
        </div>
        <p style="font-size:13px;margin:6px 0 0;color:#d1fae5">An diese WhatsApp Nummer senden:</p>
        <div style="background:#0b1223;border:1px solid #1f2937;border-radius:10px;padding:10px 14px;margin:6px 0;font-family:monospace;font-size:15px;color:#f59e0b;text-align:center">
          +1 415 523 8886
        </div>
        <p class="small" style="margin:8px 0 0;color:#6b7280">Nur einmal nötig · danach funktioniert alles automatisch</p>
      </div>
      <div style="height:10px"></div>
      <a class="btn" href="/checkin/1">Jetzt starten</a>
      <div style="height:12px"></div>
      <p class="small">Oder kopiere den Link:</p>
      <div class="code">{magic}</div>
    """
    return _page("PTGO • Link", body, request=request)


@app.get("/magic/{token}")
def magic_login(token: str, request: Request, db=Depends(get_db)):
    token_hash = _hash_magic(token.strip())
    patient = db.query(Patient).filter(Patient.magic_token_hash == token_hash).first()
    if not patient:
        raise HTTPException(status_code=401, detail="Invalid link")
    if not patient.magic_token_expires_at or _now_utc() > patient.magic_token_expires_at:
        raise HTTPException(status_code=401, detail="Link expired")
    request.session["patient_id"] = patient.id
    log_login_event(db, request, patient.id, "patient", "magic")
    return RedirectResponse("/checkin/1", status_code=303)


@app.get("/logout")
def logout(request: Request, db=Depends(get_db)):
    pid = request.session.get("patient_id")
    if pid:
        log_login_event(db, request, pid, "patient", "logout")
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# =========================================================
# MODUL 1 – CONVERSATIONAL CHECK-IN (5 Screens)
# =========================================================

@app.get("/checkin/1", response_class=HTMLResponse)
def checkin_1(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)

    voice_js = """
    <script>
    // ── PTGO Voice Check-in Engine v3 ─────────────────────
    // Deep questions from the wisest healers, body map, avatar, correction

    const QUESTIONS = [
      {
        key: "overall_text",
        prompt: "Schließ kurz die Augen. Atme einmal tief ein. Wie fühlt sich dein innerer Zustand gerade wirklich an – nicht wie du sein solltest, sondern wie du bist?",
        avatar: "🧘",
        bodyZone: null,
      },
      {
        key: "context_text",
        prompt: "Was trägt dein Nervensystem heute? Gibt es etwas das dich seit dem Aufwachen begleitet – eine Anspannung, ein Gedanke, eine Situation?",
        avatar: "🧠",
        bodyZone: null,
      },
      {
        key: "body_text",
        prompt: "Scanne deinen Körper von oben nach unten. Wo spürst du Widerstand, Schwere, Enge oder Schmerz? Beschreibe was du wahrnimmst – auch wenn es klein ist.",
        avatar: "🫀",
        bodyZone: "show",
      },
      {
        key: "sleep_text",
        prompt: "Wie war deine letzte Nacht wirklich? War dein Schlaf erholsam – hast du tief geschlafen, oder war da Unruhe, Aufwachen, schwere Träume?",
        avatar: "🌙",
        bodyZone: null,
      },
      {
        key: "mental_text",
        prompt: "Gibt es etwas das du gerade vor dir herschiebst oder vermeidest? Einen Gedanken, eine Aufgabe, ein Gespräch – etwas dem du ausweichst?",
        avatar: "🪞",
        bodyZone: null,
      },
      {
        key: "goal_text",
        prompt: "Was braucht dein System heute wirklich? Nicht was du leisten sollst – sondern was dir heute gut täte. In einem Satz.",
        avatar: "🌱",
        bodyZone: null,
      },
      {
        key: "confirm",
        prompt: "Ich habe alles aufgenommen. Soll ich diese Einschätzung jetzt an deinen Therapeuten senden? Sag Ja oder Nein.",
        avatar: "✅",
        bodyZone: null,
      },
    ];

    let currentQ = 0;
    let answers = {};
    let recognition = null;
    let synth = window.speechSynthesis;
    let isListening = false;
    let bodyMapData = [];
    let drawMode = "point"; // point | line | area
    let isDrawing = false;
    let drawStart = null;

    // ── Avatar ─────────────────────────────────────────────
    function updateAvatar(emoji, pulse) {
      const av = document.getElementById("avatar");
      if (!av) return;
      av.textContent = emoji;
      av.style.animation = pulse ? "avatarPulse 1.5s infinite" : "none";
    }

    // ── Speech ─────────────────────────────────────────────
    function speak(text, callback) {
      synth.cancel();
      const utter = new SpeechSynthesisUtterance(text);
      utter.lang = "de-DE";
      utter.rate = 0.9;
      utter.pitch = 1.0;
      utter.onend = () => { if (callback) callback(); };
      synth.speak(utter);
    }

    function updateUI(state, text) {
      document.getElementById("status").textContent = text;
      const btn = document.getElementById("mic-btn");
      btn.className = state === "listening" ? "mic-btn listening" : "mic-btn";
      document.getElementById("mic-icon").textContent = state === "listening" ? "🔴" : "🎙️";
      updateAvatar(QUESTIONS[currentQ]?.avatar || "🧘", state === "listening");
    }

    function showTranscript(text) {
      document.getElementById("transcript").textContent = text;
    }

    // ── Body Map ───────────────────────────────────────────
    function initBodyMap() {
      const canvas = document.getElementById("body-canvas");
      if (!canvas) return;
      const ctx = canvas.getContext("2d");

      // Draw body silhouette (front + back)
      drawBodySilhouette(ctx);

      // Draw mode buttons
      document.querySelectorAll(".draw-mode-btn").forEach(btn => {
        btn.onclick = () => {
          drawMode = btn.dataset.mode;
          document.querySelectorAll(".draw-mode-btn").forEach(b => b.style.borderColor = "#374151");
          btn.style.borderColor = "#f59e0b";
        };
      });

      // Touch/mouse events
      canvas.addEventListener("mousedown", startDraw);
      canvas.addEventListener("mousemove", continueDraw);
      canvas.addEventListener("mouseup", endDraw);
      canvas.addEventListener("touchstart", e => { e.preventDefault(); startDraw(e.touches[0]); }, {passive:false});
      canvas.addEventListener("touchmove", e => { e.preventDefault(); continueDraw(e.touches[0]); }, {passive:false});
      canvas.addEventListener("touchend", e => { e.preventDefault(); endDraw(e.changedTouches[0]); }, {passive:false});
    }

    function getCanvasPos(e) {
      const canvas = document.getElementById("body-canvas");
      const rect = canvas.getBoundingClientRect();
      return {
        x: (e.clientX - rect.left) * (canvas.width / rect.width),
        y: (e.clientY - rect.top) * (canvas.height / rect.height),
      };
    }

    function startDraw(e) {
      isDrawing = true;
      drawStart = getCanvasPos(e);
      if (drawMode === "point") {
        const pos = drawStart;
        bodyMapData.push({type:"point", x:pos.x, y:pos.y});
        redrawBodyMap();
        isDrawing = false;
      }
    }

    function continueDraw(e) {
      if (!isDrawing || drawMode === "point") return;
      // Preview
    }

    function endDraw(e) {
      if (!isDrawing) return;
      isDrawing = false;
      const pos = getCanvasPos(e);
      if (drawMode === "line") {
        bodyMapData.push({type:"line", x1:drawStart.x, y1:drawStart.y, x2:pos.x, y2:pos.y});
      } else if (drawMode === "area") {
        bodyMapData.push({type:"area", x:drawStart.x, y:drawStart.y, w:pos.x-drawStart.x, h:pos.y-drawStart.y});
      }
      redrawBodyMap();
    }

    function redrawBodyMap() {
      const canvas = document.getElementById("body-canvas");
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      drawBodySilhouette(ctx);

      bodyMapData.forEach(item => {
        ctx.strokeStyle = "#ef4444";
        ctx.fillStyle = "rgba(239,68,68,0.4)";
        ctx.lineWidth = 3;
        if (item.type === "point") {
          ctx.beginPath();
          ctx.arc(item.x, item.y, 8, 0, Math.PI*2);
          ctx.fill();
        } else if (item.type === "line") {
          ctx.beginPath();
          ctx.moveTo(item.x1, item.y1);
          ctx.lineTo(item.x2, item.y2);
          ctx.stroke();
        } else if (item.type === "area") {
          ctx.beginPath();
          ctx.rect(item.x, item.y, item.w, item.h);
          ctx.fill();
        }
      });
    }

    function drawBodySilhouette(ctx) {
      ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
      ctx.strokeStyle = "#4b5563";
      ctx.lineWidth = 2;
      ctx.fillStyle = "rgba(30,41,59,0.8)";

      // ── Front body (left half) ──
      const fx = 70, fy = 20;
      // Head
      ctx.beginPath(); ctx.arc(fx, fy+20, 18, 0, Math.PI*2); ctx.fill(); ctx.stroke();
      // Neck
      ctx.beginPath(); ctx.moveTo(fx-6,fy+37); ctx.lineTo(fx-6,fy+50); ctx.lineTo(fx+6,fy+50); ctx.lineTo(fx+6,fy+37); ctx.stroke();
      // Torso
      ctx.beginPath(); ctx.moveTo(fx-22,fy+50); ctx.lineTo(fx-25,fy+110); ctx.lineTo(fx+25,fy+110); ctx.lineTo(fx+22,fy+50); ctx.closePath(); ctx.fill(); ctx.stroke();
      // Arms
      ctx.beginPath(); ctx.moveTo(fx-22,fy+55); ctx.lineTo(fx-38,fy+100); ctx.lineTo(fx-32,fy+100); ctx.lineTo(fx-16,fy+56); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(fx+22,fy+55); ctx.lineTo(fx+38,fy+100); ctx.lineTo(fx+32,fy+100); ctx.lineTo(fx+16,fy+56); ctx.fill(); ctx.stroke();
      // Legs
      ctx.beginPath(); ctx.moveTo(fx-20,fy+110); ctx.lineTo(fx-22,fy+180); ctx.lineTo(fx-8,fy+180); ctx.lineTo(fx-4,fy+110); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(fx+20,fy+110); ctx.lineTo(fx+22,fy+180); ctx.lineTo(fx+8,fy+180); ctx.lineTo(fx+4,fy+110); ctx.fill(); ctx.stroke();
      // Label
      ctx.fillStyle = "#6b7280"; ctx.font = "10px sans-serif"; ctx.fillText("Vorne", fx-14, fy+200);
      ctx.fillStyle = "rgba(30,41,59,0.8)";

      // ── Back body (right half) ──
      const bx = 210, by = 20;
      ctx.strokeStyle = "#4b5563";
      // Head
      ctx.beginPath(); ctx.arc(bx, by+20, 18, 0, Math.PI*2); ctx.fill(); ctx.stroke();
      // Neck
      ctx.beginPath(); ctx.moveTo(bx-6,by+37); ctx.lineTo(bx-6,by+50); ctx.lineTo(bx+6,by+50); ctx.lineTo(bx+6,by+37); ctx.stroke();
      // Torso
      ctx.beginPath(); ctx.moveTo(bx-22,by+50); ctx.lineTo(bx-25,by+110); ctx.lineTo(bx+25,by+110); ctx.lineTo(bx+22,by+50); ctx.closePath(); ctx.fill(); ctx.stroke();
      // Arms
      ctx.beginPath(); ctx.moveTo(bx-22,by+55); ctx.lineTo(bx-38,by+100); ctx.lineTo(bx-32,by+100); ctx.lineTo(bx-16,by+56); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(bx+22,by+55); ctx.lineTo(bx+38,by+100); ctx.lineTo(bx+32,by+100); ctx.lineTo(bx+16,by+56); ctx.fill(); ctx.stroke();
      // Legs
      ctx.beginPath(); ctx.moveTo(bx-20,by+110); ctx.lineTo(bx-22,by+180); ctx.lineTo(bx-8,by+180); ctx.lineTo(bx-4,by+110); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(bx+20,by+110); ctx.lineTo(bx+22,by+180); ctx.lineTo(bx+8,by+180); ctx.lineTo(bx+4,by+110); ctx.fill(); ctx.stroke();
      // Label
      ctx.fillStyle = "#6b7280"; ctx.font = "10px sans-serif"; ctx.fillText("Hinten", bx-14, by+200);
      ctx.fillStyle = "rgba(30,41,59,0.8)";

      // Divider
      ctx.strokeStyle = "#1f2937"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(140, 0); ctx.lineTo(140, 220); ctx.stroke();
    }

    function clearBodyMap() {
      bodyMapData = [];
      const canvas = document.getElementById("body-canvas");
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      drawBodySilhouette(ctx);
    }

    function showBodyMap(show) {
      const bm = document.getElementById("body-map-section");
      if (bm) bm.style.display = show ? "block" : "none";
      if (show) setTimeout(initBodyMap, 100);
    }

    // ── Correction ─────────────────────────────────────────
    function correctAnswer(key) {
      // Find which question this was
      const idx = QUESTIONS.findIndex(q => q.key === key);
      if (idx < 0) return;

      // Remove the answer card
      const cards = document.querySelectorAll(".answer-card");
      cards.forEach(c => { if (c.dataset.key === key) c.remove(); });

      delete answers[key];
      currentQ = idx;
      updateProgress();
      showBodyMap(QUESTIONS[currentQ].bodyZone === "show");

      speak("Okay, ich frage nochmal. " + QUESTIONS[currentQ].prompt, () => {
        setTimeout(startListening, 500);
      });
    }

    // ── Listening ──────────────────────────────────────────
    function startListening() {
      if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {
        alert("Bitte Chrome nutzen für Spracherkennung.");
        return;
      }
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      recognition = new SR();
      recognition.lang = "de-DE";
      recognition.continuous = true;
      recognition.interimResults = true;

      recognition.onstart = () => {
        isListening = true;
        updateUI("listening", "Ich höre zu...");
      };

      recognition.onresult = (e) => {
        let interim = "", final = "";
        for (let i = e.resultIndex; i < e.results.length; i++) {
          if (e.results[i].isFinal) final += e.results[i][0].transcript;
          else interim += e.results[i][0].transcript;
        }
        showTranscript(final || interim);
        if (interim || final) document.getElementById("confirm-btn").style.display = "inline-block";
        if (final) {
          clearTimeout(window._answerTimer);
          window._pendingAnswer = ((window._pendingAnswer || "") + " " + final.trim()).trim();
          showTranscript(window._pendingAnswer);
          window._answerTimer = setTimeout(() => {
            recognition && recognition.stop();
            handleAnswer(window._pendingAnswer);
            window._pendingAnswer = "";
            document.getElementById("confirm-btn").style.display = "none";
          }, 2500);
        }
      };

      recognition.onerror = () => { updateUI("idle", "Fehler – Mikrofon-Button tippen."); isListening = false; };
      recognition.onend  = () => { isListening = false; };
      recognition.start();
    }

    function handleAnswer(text) {
      const q = QUESTIONS[currentQ];
      updateUI("idle", "✓ Verstanden");

      if (q.key === "confirm") {
        const lower = text.toLowerCase();
        if (lower.includes("ja") || lower.includes("yes") || lower.includes("senden") || lower.includes("okay") || lower.includes("ok")) {
          speak("Super. Ich sende es jetzt.", () => submitCheckin());
        } else {
          speak("Okay, ich sende nichts. Du kannst das Fenster schließen.");
          updateUI("idle", "Abgebrochen.");
        }
        return;
      }

      // Save body map if this was body question
      if (q.key === "body_text") {
        document.getElementById("f_pain_map").value = JSON.stringify(bodyMapData);
        showBodyMap(false);
      }

      answers[q.key] = text;
      showAnswerCard(q.key, text);
      currentQ++;

      if (currentQ < QUESTIONS.length) {
        updateProgress();
        showBodyMap(QUESTIONS[currentQ].bodyZone === "show");
        setTimeout(() => {
          speak(QUESTIONS[currentQ].prompt, () => setTimeout(startListening, 500));
        }, 600);
      }
    }

    function showAnswerCard(key, text) {
      const labels = {
        overall_text: "Innerer Zustand",
        context_text: "Nervensystem",
        body_text: "Körper",
        sleep_text: "Schlaf",
        mental_text: "Vermeidung",
        goal_text: "Bedürfnis",
      };
      const list = document.getElementById("answers-list");
      const card = document.createElement("div");
      card.className = "answer-card";
      card.dataset.key = key;
      card.style = "background:rgba(255,255,255,.03);border:1px solid #1f2937;border-radius:10px;padding:10px 14px;margin:6px 0;font-size:14px;display:flex;justify-content:space-between;align-items:flex-start;gap:8px";
      card.innerHTML = `
        <div>
          <span style="color:#6b7280;font-size:11px">${labels[key] || key}</span><br>
          <span>${text}</span>
        </div>
        <button onclick="correctAnswer('${key}')" style="background:transparent;border:1px solid #374151;color:#6b7280;border-radius:8px;padding:4px 8px;font-size:11px;cursor:pointer;white-space:nowrap;flex-shrink:0">
          ✏️ Korrigieren
        </button>
      `;
      list.appendChild(card);
    }

    function updateProgress() {
      const pct = Math.round((currentQ / QUESTIONS.length) * 100);
      document.getElementById("progress-bar").style.width = pct + "%";
      document.getElementById("progress-text").textContent = `Frage ${Math.min(currentQ+1, QUESTIONS.length)} von ${QUESTIONS.length}`;
      if (QUESTIONS[currentQ]) {
        document.getElementById("question-text").textContent = QUESTIONS[currentQ].prompt;
        updateAvatar(QUESTIONS[currentQ].avatar || "🧘", false);
      }
    }

    function submitCheckin() {
      updateUI("idle", "Wird gesendet...");
      document.getElementById("mic-btn").disabled = true;
      document.getElementById("f_overall_text").value = answers.overall_text || "";
      document.getElementById("f_context_text").value = answers.context_text || "";
      document.getElementById("f_body_text").value = answers.body_text || "";
      document.getElementById("f_sleep_text").value = answers.sleep_text || "";
      document.getElementById("f_mental_text").value = answers.mental_text || "";
      document.getElementById("f_goal_text").value = answers.goal_text || "";
      document.getElementById("checkin-form").submit();
    }

    function confirmAnswer() {
      if (window._pendingAnswer) {
        clearTimeout(window._answerTimer);
        recognition && recognition.stop();
        handleAnswer(window._pendingAnswer);
        window._pendingAnswer = "";
        document.getElementById("confirm-btn").style.display = "none";
      }
    }

    function startVoiceCheck() {
      document.getElementById("start-screen").style.display = "none";
      document.getElementById("voice-screen").style.display = "block";
      updateProgress();
      speak(QUESTIONS[0].prompt, () => setTimeout(startListening, 500));
    }

    document.addEventListener("DOMContentLoaded", () => {
      document.getElementById("mic-btn").onclick = () => {
        if (isListening) { recognition && recognition.stop(); }
        else { speak(QUESTIONS[currentQ].prompt, () => setTimeout(startListening, 300)); }
      };
    });
    </script>
    """

    body = f"""
      {voice_js}

      <!-- START SCREEN -->
      <div id="start-screen">
        <!-- Avatar -->
        <div style="text-align:center;margin:8px 0 16px">
          <div id="avatar-start" style="font-size:60px;line-height:1">🧘</div>
          <div style="font-size:11px;color:#6b7280;margin-top:6px;letter-spacing:1px">PTGO DAILY CHECK</div>
        </div>
        <h1 style="text-align:center">Wie geht es dir heute?</h1>
        <p style="text-align:center">6 tiefe Fragen. Dein Körper. Dein System. Deine Wahrheit.</p>
        <div class="hr"></div>
        <p class="small" style="text-align:center">🎙️ Sprachgesteuert · läuft im Browser · kein Download</p>
        <div style="height:16px"></div>
        <button onclick="startVoiceCheck()" style="font-size:18px;padding:18px;">
          🎙️ Check starten
        </button>
        <div class="hr"></div>
        <p class="small" style="text-align:center"><a href="/mastery">⚡ Mastery</a> · <a href="/subscribe">⭐ Premium</a> · <a href="/profile">Body Profile</a> · <a href="/logout">Logout</a></p>
      </div>

      <!-- VOICE SCREEN -->
      <div id="voice-screen" style="display:none">

        <!-- Avatar -->
        <div style="text-align:center;margin:4px 0 12px">
          <div id="avatar" style="font-size:48px;line-height:1;transition:all .3s">🧘</div>
        </div>

        <!-- Progress -->
        <div style="height:4px;background:#1f2937;border-radius:999px;margin-bottom:6px">
          <div id="progress-bar" style="height:4px;background:#f59e0b;border-radius:999px;width:0%;transition:width .4s"></div>
        </div>
        <p class="small" id="progress-text" style="text-align:center">Frage 1 von 6</p>

        <!-- Question -->
        <div style="background:rgba(245,158,11,.07);border:1px solid rgba(245,158,11,.25);border-radius:14px;padding:14px 16px;margin:10px 0">
          <p style="color:#fbbf24;font-size:15px;margin:0;line-height:1.5" id="question-text">...</p>
        </div>

        <!-- Body Map (shown only for body question) -->
        <div id="body-map-section" style="display:none;margin:10px 0">
          <p class="small" style="margin-bottom:6px;color:#a5b4fc">PTGO Body System – Zeichne wo du etwas spürst:</p>
          <div style="display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap">
            <button class="draw-mode-btn" data-mode="point" style="background:rgba(239,68,68,.15);border:1px solid #ef4444;color:#fca5a5;border-radius:8px;padding:6px 10px;font-size:12px;cursor:pointer">● Punkt</button>
            <button class="draw-mode-btn" data-mode="line" style="background:rgba(245,158,11,.15);border:1px solid #374151;color:#fcd34d;border-radius:8px;padding:6px 10px;font-size:12px;cursor:pointer">— Linie</button>
            <button class="draw-mode-btn" data-mode="area" style="background:rgba(99,102,241,.15);border:1px solid #374151;color:#a5b4fc;border-radius:8px;padding:6px 10px;font-size:12px;cursor:pointer">▭ Fläche</button>
            <button onclick="clearBodyMap()" style="background:transparent;border:1px solid #374151;color:#6b7280;border-radius:8px;padding:6px 10px;font-size:12px;cursor:pointer">✕ Löschen</button>
          </div>
          <canvas id="body-canvas" width="280" height="220" style="width:100%;max-width:320px;border:1px solid #1f2937;border-radius:12px;display:block;margin:0 auto;touch-action:none;background:#0b1223"></canvas>
          <p class="small" style="text-align:center;margin-top:6px">Tippe oder ziehe auf dem Körper</p>
        </div>

        <!-- Transcript -->
        <div style="min-height:44px;background:rgba(255,255,255,.02);border:1px solid #1f2937;border-radius:12px;padding:12px;margin:8px 0;font-size:14px;color:#e5e7eb" id="transcript">
          Deine Antwort erscheint hier...
        </div>

        <!-- Mic -->
        <div style="text-align:center;margin:12px 0">
          <button id="mic-btn" class="mic-btn" style="background:rgba(245,158,11,.15);border:2px solid #f59e0b;border-radius:50%;width:72px;height:72px;font-size:28px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;transition:all .2s">
            <span id="mic-icon">🎙️</span>
          </button>
          <p class="small" id="status" style="margin-top:6px">Tippe um zu sprechen</p>
          <button id="confirm-btn" onclick="confirmAnswer()" style="display:none;margin-top:8px;background:rgba(34,197,94,.15);border:1px solid #22c55e;color:#22c55e;border-radius:10px;padding:8px 18px;font-size:13px;cursor:pointer;">
            ✓ Antwort bestätigen
          </button>
        </div>

        <!-- Answers -->
        <div id="answers-list"></div>
      </div>

      <!-- Hidden form -->
      <form id="checkin-form" method="post" action="/checkin/voice" style="display:none">
        <input type="hidden" id="f_overall_text" name="overall_text">
        <input type="hidden" id="f_context_text" name="context_text">
        <input type="hidden" id="f_body_text" name="body_text">
        <input type="hidden" id="f_sleep_text" name="sleep_text">
        <input type="hidden" id="f_mental_text" name="mental_text">
        <input type="hidden" id="f_goal_text" name="goal_text">
        <input type="hidden" id="f_pain_map" name="pain_map_json">
      </form>

      <style>
        .mic-btn.listening {{
          background: rgba(239,68,68,.2) !important;
          border-color: #ef4444 !important;
          box-shadow: 0 0 20px rgba(239,68,68,.4);
          animation: pulse 1s infinite;
        }}
        @keyframes pulse {{
          0%, 100% {{ transform: scale(1); }}
          50% {{ transform: scale(1.08); }}
        }}
        @keyframes avatarPulse {{
          0%, 100% {{ transform: scale(1); opacity:1; }}
          50% {{ transform: scale(1.15); opacity:0.8; }}
        }}
      </style>
    """
    return _page("PTGO • Voice Check", body, request=request)


# Keep old routes as redirects for backwards compatibility
@app.post("/checkin/1", response_class=HTMLResponse)
def checkin_1_post(request: Request, db=Depends(get_db)):
    return RedirectResponse("/checkin/1", status_code=303)

@app.get("/checkin/2", response_class=HTMLResponse)
def checkin_2(request: Request, db=Depends(get_db)):
    return RedirectResponse("/checkin/1", status_code=303)

@app.post("/checkin/2", response_class=HTMLResponse)
def checkin_2_post(request: Request, db=Depends(get_db)):
    return RedirectResponse("/checkin/1", status_code=303)

@app.get("/checkin/3", response_class=HTMLResponse)
def checkin_3(request: Request, db=Depends(get_db)):
    return RedirectResponse("/checkin/1", status_code=303)

@app.post("/checkin/3", response_class=HTMLResponse)
def checkin_3_post(request: Request, db=Depends(get_db)):
    return RedirectResponse("/checkin/1", status_code=303)

@app.get("/checkin/4", response_class=HTMLResponse)
def checkin_4(request: Request, db=Depends(get_db)):
    return RedirectResponse("/checkin/1", status_code=303)

@app.post("/checkin/4", response_class=HTMLResponse)
def checkin_4_post(request: Request, db=Depends(get_db)):
    return RedirectResponse("/checkin/1", status_code=303)

@app.get("/checkin/5", response_class=HTMLResponse)
def checkin_5(request: Request, db=Depends(get_db)):
    return RedirectResponse("/checkin/1", status_code=303)

@app.post("/checkin/voice", response_class=HTMLResponse)
def checkin_voice_submit(
    request: Request,
    overall_text: str = Form(""),
    context_text: str = Form(""),
    body_text: str = Form(""),
    sleep_text: str = Form(""),
    mental_text: str = Form(""),
    goal_text: str = Form(""),
    pain_map_json: str = Form(""),
    db=Depends(get_db),
):
    p = require_patient_login(request, db)

    full_context = context_text.strip()
    if sleep_text.strip():
        full_context = full_context + (" | Schlaf: " + sleep_text.strip() if full_context else "Schlaf: " + sleep_text.strip())

    data = {
        "daily_state": 5,
        "overall_text": overall_text.strip(),
        "stress": 5,
        "sleep": 5,
        "context_text": full_context,
        "body": 5,
        "body_text": body_text.strip(),
        "pain_region": _extract_pain_region(body_text),
        "craving": 0,
        "avoidance": 0,
        "mental_text": mental_text.strip(),
        "goal_text": goal_text.strip(),
    }

    data = _ai_extract_values(data)

    # Modul 2 – Signal Extraction
    signals = extract_signals(data)

    # Modul 5 – Pattern Engine
    pattern_code, pattern_label = detect_pattern(data)

    # Modul 7 – Action Engine
    action_code, action = get_action(pattern_code)

    # Modul 17 – Recovery Score
    score = compute_recovery_score(data)
    risk_data = {**data}
    _, risk = compute_score(risk_data)  # still use old for risk level

    local_day = _now_local().date().isoformat()
    c = CheckIn(
        patient_id=p.id,
        local_day=local_day,
        daily_state=data["daily_state"],
        overall_text=data["overall_text"],
        stress=data["stress"],
        sleep=data["sleep"],
        context_text=data["context_text"],
        body=data["body"],
        body_text=data["body_text"],
        pain_region=data["pain_region"],
        pain_map_json=pain_map_json or None,
        craving=data["craving"],
        avoidance=data["avoidance"],
        mental_text=data["mental_text"],
        goal_text=data["goal_text"],
        signals_json=json.dumps(signals, ensure_ascii=False),
        pattern_code=pattern_code,
        pattern_label=pattern_label,
        action_code=action_code,
        action_label=action["label"],
        action_text=action["instructions"],
        score=score,
        risk_level=risk,
        answers_json=json.dumps(data, ensure_ascii=False),
        ptgo_score=score,
        one_action=action["instructions"],
    )
    db.add(c)
    db.commit()
    db.refresh(c)

    # WhatsApp result
    try:
        msg = (
            f"PTGO Result ✅\n\n"
            f"Score: {score}/100\n"
            f"Pattern: {pattern_label}\n\n"
            f"Deine Action:\n{action['label']}: {action['instructions']}\n\n"
            f"Details: {BASE_URL}/result/{c.id}"
        )
        send_whatsapp_to_patient(p, msg)
    except Exception as e:
        print("[WARN] WhatsApp result failed:", e)

    # Therapist alert – always send full summary
    try:
        tmsg = (
            f"{'⚠️ HIGH RISK' if risk == 'high' else '📋 Daily Check'}\n"
            f"Patient: {p.name}\n"
            f"Score {score}/100 • Pattern: {pattern_label}\n\n"
            f"Stimmung: {data.get('overall_text','–')}\n"
            f"Herausforderung: {data.get('context_text','–')}\n"
            f"Körper: {data.get('body_text','–')}\n"
            f"Gedanken: {data.get('mental_text','–')}\n"
            f"Tagesziel: {data.get('goal_text','–')}\n\n"
            f"Action: {action['label']}\n"
            f"Details: {BASE_URL}/therapist/checkin/{c.id}"
        )
        send_whatsapp_to_therapist(p, p.therapist, tmsg)
    except Exception as e:
        print("[WARN] Therapist WhatsApp failed:", e)

    # Emergency escalation for critical values
    try:
        _check_emergency_escalation(db, p, c)
    except Exception as e:
        print("[WARN] Emergency check failed:", e)

    return RedirectResponse(f"/result/{c.id}", status_code=303)


# =========================================================
# MODUL 8 – RESULT SCREEN
# =========================================================

@app.get("/result/{checkin_id}", response_class=HTMLResponse)
def result_page(checkin_id: int, request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)
    c = db.query(CheckIn).filter(CheckIn.id == checkin_id, CheckIn.patient_id == p.id).first()
    if not c:
        raise HTTPException(status_code=404)

    action = ACTION_LIBRARY.get(c.action_code or "", None)
    risk_color = "ok" if c.risk_level == "low" else ("warn" if c.risk_level == "high" else "")

    # Check if outcome already submitted
    existing_outcome = db.query(Outcome).filter(Outcome.checkin_id == c.id).first()
    outcome_section = ""
    if not existing_outcome:
        outcome_section = f"""
        <div class="hr"></div>
        <h2>Wie war gestern?</h2>
        <p class="small">Hat dir die letzte Aktion geholfen?</p>
        <form method="post" action="/outcome/{c.id}">
          <div style="display:flex;gap:8px;margin:12px 0">
            <button type="submit" name="rating" value="better" class="outcome-btn">😌 Besser</button>
            <button type="submit" name="rating" value="same" class="outcome-btn">😐 Gleich</button>
            <button type="submit" name="rating" value="worse" class="outcome-btn">😔 Schlechter</button>
          </div>
        </form>
        """

    # Streak for commitment/consistency
    streak = _get_patient_streak(db, p.id)
    streak_html = ""
    if streak["current_streak"] >= 2:
        streak_html = f"""
        <div style="margin:14px 0;padding:12px;background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.2);border-radius:12px;text-align:center">
          <span style="font-size:24px">🔥</span>
          <span style="font-size:18px;font-weight:700;color:#f59e0b">{streak['current_streak']} Tage Streak</span>
          <div style="font-size:11px;color:#6b7280;margin-top:2px">Bleib dran – Kontinuität ist der Schlüssel zur Veränderung</div>
        </div>
        """
    elif streak["total_checkins"] > 1:
        streak_html = f"""
        <div style="margin:14px 0;padding:10px;border:1px solid #1f2937;border-radius:12px;text-align:center">
          <span style="font-size:12px;color:#6b7280">Insgesamt <b style="color:#f59e0b">{streak['total_checkins']}</b> Check-ins – morgen wieder? Streaks starten mit Tag 2.</span>
        </div>
        """

    # Premium teaser if not subscribed
    premium_teaser = ""
    if not p.subscription_active:
        premium_teaser = f"""
        <div class="hr"></div>
        <div style="padding:14px;background:rgba(99,102,241,.05);border:1px solid rgba(99,102,241,.2);border-radius:14px">
          <p style="font-size:13px;color:#a5b4fc;margin:0 0 6px;font-weight:600">Mehr aus deinen Daten herausholen?</p>
          <p style="font-size:12px;color:#6b7280;margin:0 0 8px">Premium erkennt Muster über Wochen und gibt dir personalisierte Empfehlungen.</p>
          <a href="/upgrade" style="font-size:13px;color:#f59e0b;font-weight:600">Premium entdecken →</a>
        </div>
        """

    body = f"""
      <h1>Dein Ergebnis</h1>

      {streak_html}

      <div class="grid3">
        <div class="kpi"><span class="small">Recovery Score</span><b>{c.score}</b></div>
        <div class="kpi"><span class="small">Risk</span><b class="{risk_color}">{c.risk_level}</b></div>
        <div class="kpi"><span class="small">Tag</span><b>{c.local_day}</b></div>
      </div>

      <div class="hr"></div>
      <h2>Detected Pattern</h2>
      <div class="pattern-tag">{c.pattern_label or "–"}</div>

      <div class="hr"></div>
      <h2>Today's Action</h2>
      <div class="action-box">
        <b style="color:#f59e0b;font-size:18px">{c.action_label or "–"}</b>
        <p style="margin:10px 0 6px">{action["why"] if action else ""}</p>
        <div class="hr"></div>
        <p><b>So geht's:</b><br>{action["instructions"] if action else c.action_text or ""}</p>
        <p class="small">⏱ {action["duration"] if action else ""}</p>
      </div>

      {outcome_section}

      <div class="hr"></div>
      <div style="background:rgba(99,102,241,.06);border:1px solid rgba(99,102,241,.25);border-radius:16px;padding:18px;margin:12px 0">
        <h2 style="margin:0 0 8px;color:#a5b4fc;font-size:16px">AI Coaching</h2>
        <p style="font-size:13px;color:#94a3b8;margin:0 0 10px">Dein personalisierter KI-Coach analysiert dein Pattern und gibt dir einen Impuls.</p>
        <a href="/coaching/{c.id}" class="btn" style="background:linear-gradient(180deg,#818cf8,#6366f1);font-size:14px;padding:12px 16px">Coaching-Impuls erhalten</a>
      </div>

      {premium_teaser}

      <div class="hr"></div>
      <p class="small">
        <a href="/checkin/1">Neuer Check</a> •
        <a href="/pain-assistant">Schmerz-Analyse</a> •
        <a href="/progress">Progress</a> •
        <a href="/profile">Body Profile</a> •
        <a href="/timeline">Timeline</a> •
        <a href="/upgrade">{'⭐ Premium' if not p.subscription_active else '✅ Premium'}</a> •
        <a href="/logout">Logout</a>
      </p>
    """
    return _page("PTGO • Ergebnis", body, request=request)


# =========================================================
# MODUL 9 – OUTCOME FEEDBACK
# =========================================================

@app.post("/outcome/{checkin_id}", response_class=HTMLResponse)
def outcome_post(checkin_id: int, request: Request, rating: str = Form(...), outcome_note: str = Form(""), db=Depends(get_db)):
    p = require_patient_login(request, db)
    c = db.query(CheckIn).filter(CheckIn.id == checkin_id, CheckIn.patient_id == p.id).first()
    if not c:
        raise HTTPException(status_code=404)

    o = Outcome(checkin_id=c.id, patient_id=p.id, rating=rating, outcome_note=outcome_note.strip())
    db.add(o)
    db.commit()

    emoji = {"better": "😌", "same": "😐", "worse": "😔"}.get(rating, "")
    body = f"""
      <h1>Danke {emoji}</h1>
      <p>Dein Feedback hilft dabei, die nächste Aktion noch besser für dich anzupassen.</p>
      <div class="hr"></div>
      <a class="btn" href="/checkin/1">Neuer Check</a>
    """
    return _page("PTGO • Feedback", body, request=request)


# =========================================================
# PROGRESS
# =========================================================

@app.get("/progress", response_class=HTMLResponse)
def progress_page(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)
    rows = db.query(CheckIn).filter(CheckIn.patient_id == p.id).order_by(CheckIn.created_at.desc()).limit(30).all()

    items = ""
    for c in rows:
        outcome = db.query(Outcome).filter(Outcome.checkin_id == c.id).first()
        outcome_tag = ""
        if outcome:
            emoji = {"better": "😌", "same": "😐", "worse": "😔"}.get(outcome.rating, "")
            outcome_tag = f"<span class='tag'>{emoji} {outcome.rating}</span>"
        items += f"""
          <div class="kpi" style="margin-bottom:10px">
            <div class="small">{c.local_day}</div>
            <b>{c.score}</b>
            <div class="small">{c.pattern_label or c.risk_level}</div>
            <div style="height:6px"></div>
            {outcome_tag}
            <a class="small" href="/result/{c.id}">open</a>
          </div>
        """

    # Streak + milestones for commitment
    streak = _get_patient_streak(db, p.id)
    streak_html = ""
    if streak["current_streak"] >= 2:
        # Milestone messages
        msg = "Weiter so!"
        if streak["current_streak"] >= 30:
            msg = "30 Tage – ein ganzer Monat Selbstreflexion. Beeindruckend."
        elif streak["current_streak"] >= 14:
            msg = "2 Wochen am Stück. Echte Veränderung entsteht genau so."
        elif streak["current_streak"] >= 7:
            msg = "Eine ganze Woche! Dein Gehirn baut neue Gewohnheiten auf."
        elif streak["current_streak"] >= 3:
            msg = "3+ Tage. Die Forschung zeigt: hier beginnt die Gewohnheitsbildung."

        streak_html = f"""
        <div style="margin:0 0 16px;padding:14px;background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.2);border-radius:14px;text-align:center">
          <div><span style="font-size:28px">🔥</span> <span style="font-size:22px;font-weight:700;color:#f59e0b">{streak['current_streak']} Tage Streak</span></div>
          <div style="font-size:12px;color:#6b7280;margin-top:4px">{msg}</div>
        </div>
        """
    elif streak["total_checkins"] > 0 and streak["current_streak"] == 0:
        streak_html = f"""
        <div style="margin:0 0 16px;padding:12px;border:1px solid #1f2937;border-radius:12px;text-align:center">
          <span style="font-size:13px;color:#6b7280">Deine Streak ist pausiert. <a href="/checkin/1" style="color:#f59e0b">Jetzt Check-in machen</a> um sie neu zu starten.</span>
        </div>
        """

    total_html = ""
    if streak["total_checkins"] > 0:
        total_html = f"""
        <div style="margin:0 0 16px;display:flex;gap:10px">
          <div style="flex:1;padding:10px;border:1px solid #1f2937;border-radius:10px;text-align:center">
            <div style="font-size:11px;color:#6b7280">Gesamt</div>
            <div style="font-size:20px;font-weight:700;color:#e5e7eb">{streak['total_checkins']}</div>
          </div>
          <div style="flex:1;padding:10px;border:1px solid #1f2937;border-radius:10px;text-align:center">
            <div style="font-size:11px;color:#6b7280">Streak</div>
            <div style="font-size:20px;font-weight:700;color:#f59e0b">{streak['current_streak']}</div>
          </div>
        </div>
        """

    body = f"""
      <h1>Progress</h1>
      <p class="small">Letzte 30 Check-ins</p>

      {streak_html}
      {total_html}

      <div class="hr"></div>
      {items if items else "<p class='small'>Noch keine Daten.</p>"}
      <div class="hr"></div>
      <p class="small">
        <a href="/checkin/1">Neuer Check</a> •
        <a href="/pain-assistant">Schmerz-Analyse</a> •
        <a href="/insights">AI Trends</a> •
        <a href="/timeline">Timeline</a>
      </p>
    """
    return _page("PTGO • Progress", body, request=request)


# =========================================================
# SETTINGS
# =========================================================

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)
    body = f"""
      <h1>Settings</h1>
      <p class="small">{p.name} • {p.phone}</p>
      <div class="hr"></div>
      <form method="post" action="/settings">
        <label>Daily Reminder</label>
        <select name="enabled">
          <option value="1" {"selected" if p.reminder_enabled else ""}>An</option>
          <option value="0" {"selected" if not p.reminder_enabled else ""}>Aus</option>
        </select>
        <label>Uhrzeit (HH:MM)</label>
        <input name="time_str" value="{p.reminder_time_local or '08:00'}" placeholder="08:00">
        <div style="height:12px"></div>
        <button type="submit">Speichern</button>
      </form>
      <div class="hr"></div>
      <p><a href="/checkin/1">Back</a> • <a href="/logout">Logout</a></p>
    """
    return _page("PTGO • Settings", body, request=request)

@app.post("/settings", response_class=HTMLResponse)
def settings_save(request: Request, enabled: str = Form("1"), time_str: str = Form("08:00"), db=Depends(get_db)):
    p = require_patient_login(request, db)
    p.reminder_enabled = (enabled == "1")
    p.reminder_time_local = (time_str or "08:00")[:5]
    db.commit()
    return RedirectResponse("/settings", status_code=303)


# =========================================================
# MODUL 17 – RECOVERY SCORE ENGINE
# =========================================================

def compute_recovery_score(data: Dict[str, Any]) -> int:
    """
    Recovery Score = 100 minus penalties.
    Higher = better recovery state.
    """
    sleep    = _clamp_int(data.get("sleep", 5), 0, 10)
    stress   = _clamp_int(data.get("stress", 5), 0, 10)
    body     = _clamp_int(data.get("body", 5), 0, 10)
    avoidance= _clamp_int(data.get("avoidance", 0), 0, 10)
    craving  = _clamp_int(data.get("craving", 0), 0, 10)
    mood     = _clamp_int(data.get("daily_state", 5), 0, 10)

    sleep_penalty     = max(0, (5 - sleep)) * 6      # poor sleep hurts most
    stress_weight     = stress * 4                    # high stress reduces score
    pain_weight       = max(0, (5 - body)) * 3        # body tension penalty
    avoidance_penalty = avoidance * 2
    craving_penalty   = craving * 2
    mood_bonus        = mood * 1                      # good mood adds back

    score = 100 - sleep_penalty - stress_weight - pain_weight - avoidance_penalty - craving_penalty + mood_bonus
    return _clamp_int(score, 0, 100)


# =========================================================
# MODUL 16 – IDENTITY LAYER
# =========================================================

def build_body_profile(checkins: list) -> Dict[str, Any]:
    """Analyze last 30 checkins to build a Body Profile."""
    if not checkins:
        return {}

    pattern_counts: Dict[str, int] = {}
    recovery_scores = []
    stress_vals = []
    sleep_vals = []

    for c in checkins:
        if c.pattern_code:
            pattern_counts[c.pattern_code] = pattern_counts.get(c.pattern_code, 0) + 1
        if c.score:
            recovery_scores.append(c.score)
        if c.stress is not None:
            stress_vals.append(c.stress)
        if c.sleep is not None:
            sleep_vals.append(c.sleep)

    sorted_patterns = sorted(pattern_counts.items(), key=lambda x: x[1], reverse=True)
    primary = sorted_patterns[0] if len(sorted_patterns) > 0 else ("balanced", 1)
    secondary = sorted_patterns[1] if len(sorted_patterns) > 1 else None

    avg_score = int(sum(recovery_scores) / len(recovery_scores)) if recovery_scores else 0
    avg_stress = sum(stress_vals) / len(stress_vals) if stress_vals else 5
    avg_sleep = sum(sleep_vals) / len(sleep_vals) if sleep_vals else 5

    # Recovery Sensitivity
    if avg_score < 50:
        sensitivity = "High"
        sensitivity_desc = "Dein System reagiert stark auf Stress und Schlafmangel."
    elif avg_score < 70:
        sensitivity = "Medium"
        sensitivity_desc = "Du erholst dich gut, wenn du auf die Basics achtest."
    else:
        sensitivity = "Low"
        sensitivity_desc = "Du bist resilient – dein System erholt sich schnell."

    return {
        "primary_pattern": PATTERNS.get(primary[0], primary[0]),
        "primary_count": primary[1],
        "secondary_pattern": PATTERNS.get(secondary[0], secondary[0]) if secondary else None,
        "secondary_count": secondary[1] if secondary else 0,
        "avg_recovery_score": avg_score,
        "avg_stress": round(avg_stress, 1),
        "avg_sleep": round(avg_sleep, 1),
        "recovery_sensitivity": sensitivity,
        "sensitivity_desc": sensitivity_desc,
        "total_checkins": len(checkins),
    }


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)
    checkins = db.query(CheckIn).filter(CheckIn.patient_id == p.id).order_by(CheckIn.created_at.desc()).limit(30).all()
    profile = build_body_profile(checkins)

    if not profile:
        body = """
          <h1>Your Body Profile</h1>
          <p>Noch keine Daten. Mach mindestens 3 Check-ins um dein Profil zu sehen.</p>
          <div class="hr"></div>
          <a class="btn" href="/checkin/1">Ersten Check starten</a>
        """
        return _page("PTGO • Profil", body, request=request)

    secondary_html = ""
    if profile.get("secondary_pattern"):
        secondary_html = f"""
        <div class="kpi">
          <span class="small">Secondary Pattern</span>
          <b style="font-size:16px">{profile['secondary_pattern']}</b>
          <div class="small">{profile['secondary_count']}x erkannt</div>
        </div>
        """

    body = f"""
      <h1>Your Body Profile</h1>
      <p class="small">Basierend auf deinen letzten {profile['total_checkins']} Check-ins</p>

      <div class="hr"></div>

      <div class="action-box" style="margin-bottom:16px">
        <div class="small" style="color:#a5b4fc;margin-bottom:6px">PRIMARY PATTERN</div>
        <b style="font-size:22px;color:#f59e0b">{profile['primary_pattern']}</b>
        <div class="small" style="margin-top:4px">{profile['primary_count']}x in letzten Checks erkannt</div>
      </div>

      <div class="grid3" style="margin-bottom:16px">
        {secondary_html}
        <div class="kpi">
          <span class="small">Avg Recovery Score</span>
          <b>{profile['avg_recovery_score']}</b>
        </div>
        <div class="kpi">
          <span class="small">Recovery Sensitivity</span>
          <b style="font-size:16px">{profile['recovery_sensitivity']}</b>
        </div>
      </div>

      <div class="kpi" style="margin-bottom:16px">
        <span class="small">Was das bedeutet</span>
        <p style="margin:8px 0 0;font-size:14px">{profile['sensitivity_desc']}</p>
      </div>

      <div class="grid3">
        <div class="kpi">
          <span class="small">Ø Stress</span>
          <b>{profile['avg_stress']}/10</b>
        </div>
        <div class="kpi">
          <span class="small">Ø Schlaf</span>
          <b>{profile['avg_sleep']}/10</b>
        </div>
        <div class="kpi">
          <span class="small">Check-ins</span>
          <b>{profile['total_checkins']}</b>
        </div>
      </div>

      <div class="hr"></div>
      <p class="small">
        <a href="/timeline">Pattern Timeline</a> •
        <a href="/progress">Progress</a> •
        <a href="/checkin/1">Neuer Check</a>
      </p>
    """
    return _page("PTGO • Body Profile", body, request=request)


# =========================================================
# MODUL 18 – PATTERN TIMELINE
# =========================================================

@app.get("/timeline", response_class=HTMLResponse)
def timeline_page(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)
    checkins = (
        db.query(CheckIn)
        .filter(CheckIn.patient_id == p.id)
        .order_by(CheckIn.created_at.desc())
        .limit(14)
        .all()
    )
    checkins = list(reversed(checkins))  # oldest first

    PATTERN_COLORS = {
        "stress_overload":    "#ef4444",
        "recovery_deficit":   "#f97316",
        "upper_body_tension": "#eab308",
        "neck_guarding":      "#84cc16",
        "impulse_pattern":    "#a855f7",
        "avoidance_pattern":  "#ec4899",
        "low_mood":           "#6b7280",
        "balanced":           "#22c55e",
    }

    rows = ""
    for i, c in enumerate(checkins):
        color = PATTERN_COLORS.get(c.pattern_code or "balanced", "#6b7280")
        outcome = db.query(Outcome).filter(Outcome.checkin_id == c.id).first()
        outcome_emoji = ""
        if outcome:
            outcome_emoji = {"better": "😌", "same": "😐", "worse": "😔"}.get(outcome.rating, "")

        rows += f"""
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">
          <div style="min-width:80px;font-size:12px;color:#6b7280">{c.local_day}</div>
          <div style="flex:1;background:rgba(255,255,255,.03);border:1px solid #1f2937;border-radius:10px;padding:10px 14px;display:flex;justify-content:space-between;align-items:center">
            <div>
              <div style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{color};margin-right:8px"></div>
              <span style="font-size:14px;font-weight:600">{c.pattern_label or "–"}</span>
            </div>
            <div style="display:flex;align-items:center;gap:10px">
              <span style="font-size:13px;color:#f59e0b">{c.score}</span>
              <span>{outcome_emoji}</span>
              <a href="/result/{c.id}" style="font-size:11px;color:#6b7280">→</a>
            </div>
          </div>
        </div>
        """

    empty = "<p class='small'>Noch keine Daten. Mach mindestens 1 Check-in.</p>" if not rows else ""

    body = f"""
      <h1>Pattern Timeline</h1>
      <p class="small">Letzte 14 Tage – Selbsterkenntnis durch Muster</p>
      <div class="hr"></div>

      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px">
        <span style="font-size:11px;color:#6b7280">Legende:</span>
        {"".join(f'<span style="font-size:11px;padding:2px 8px;border-radius:999px;background:{c};color:#fff">{PATTERNS[k]}</span>' for k, c in PATTERN_COLORS.items())}
      </div>

      {rows or empty}

      <div class="hr"></div>
      <p class="small">
        <a href="/profile">Body Profile</a> •
        <a href="/progress">Progress</a> •
        <a href="/checkin/1">Neuer Check</a>
      </p>
    """
    return _page("PTGO • Timeline", body, request=request)


# =========================================================
# STRIPE PAYMENTS
# =========================================================

def _stripe_headers():
    return {
        "Authorization": f"Bearer {STRIPE_SECRET_KEY}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

def _get_or_create_price() -> str:
    """Get or create a recurring 4.99€/month price in Stripe."""
    if STRIPE_PRICE_ID:
        return STRIPE_PRICE_ID
    # Create product + price on the fly
    try:
        r = requests.post("https://api.stripe.com/v1/products",
            headers=_stripe_headers(),
            data={"name": "PTGO Premium", "description": "Unlimitierte Check-ins + KI Empfehlungen"},
            timeout=10)
        product_id = r.json()["id"]
        r2 = requests.post("https://api.stripe.com/v1/prices",
            headers=_stripe_headers(),
            data={
                "product": product_id,
                "unit_amount": str(SUBSCRIPTION_PRICE_EUR),
                "currency": "eur",
                "recurring[interval]": "month",
            },
            timeout=10)
        return r2.json()["id"]
    except Exception as e:
        print("[WARN] Stripe price creation failed:", e)
        return ""

@app.get("/upgrade", response_class=HTMLResponse)
def upgrade_page(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)

    if p.subscription_active:
        streak = _get_patient_streak(db, p.id)
        body = f"""
          <h1>Du bist Premium ✅</h1>
          <p>Dein Account hat vollen Zugriff auf alle Features.</p>
          {"<div style='margin:12px 0;padding:12px;background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.2);border-radius:12px;text-align:center'><span style='font-size:11px;color:#6b7280'>Deine Streak</span><div style='font-size:28px;font-weight:700;color:#f59e0b'>" + str(streak['current_streak']) + " Tage</div></div>" if streak['current_streak'] > 0 else ""}
          <div class="hr"></div>
          <p class="small">
            <a href="/checkin/1">Check starten</a> •
            <a href="/subscription/cancel">Abo kündigen</a>
          </p>
        """
        return _page("PTGO • Premium", body, request=request)

    # Endowment: show what they've already built
    streak = _get_patient_streak(db, p.id)
    stats = _get_platform_stats(db)
    positive_rate = ""
    if stats["total_checkins"] > 20 and stats["positive_outcomes"] > 0:
        rate = int(stats["positive_outcomes"] / max(stats["total_checkins"], 1) * 100)
        if rate > 0:
            positive_rate = f"""
            <div style="margin:12px 0;padding:12px;background:rgba(34,197,94,.06);border:1px solid rgba(34,197,94,.2);border-radius:12px;text-align:center">
              <span style="font-size:11px;color:#6b7280">Nutzer berichten Verbesserung</span>
              <div style="font-size:28px;font-weight:700;color:#22c55e">{rate}%</div>
            </div>
            """

    endowment = ""
    if streak["total_checkins"] > 0:
        endowment = f"""
        <div style="margin:12px 0;padding:14px;background:rgba(99,102,241,.06);border:1px solid rgba(99,102,241,.2);border-radius:12px">
          <p style="font-size:13px;color:#a5b4fc;margin:0 0 4px">Du hast bereits <b>{streak['total_checkins']} Check-in{'s' if streak['total_checkins'] != 1 else ''}</b> gemacht.</p>
          <p style="font-size:12px;color:#6b7280;margin:0">Mit Premium wird dein Fortschritt noch wertvoller – KI-Analyse erkennt deine Muster über Zeit.</p>
        </div>
        """

    body = f"""
      <h1>PTGO Premium</h1>
      <p>Schalte alle Features frei.</p>

      {endowment}
      {positive_rate}

      <div class="hr"></div>

      <div style="margin:12px 0;padding:12px;border:1px solid #374151;border-radius:12px;text-align:center">
        <div style="font-size:13px;color:#6b7280;text-decoration:line-through">Eine Therapiestunde: 80–150€</div>
        <div style="font-size:28px;font-weight:700;color:#f59e0b;margin:4px 0">4,99€<span style="font-size:14px;color:#6b7280"> / Monat</span></div>
        <div style="font-size:12px;color:#6b7280">= 0,17€ pro Tag für deine Gesundheit</div>
      </div>

      <div class="action-box">
        <p>✅ Unlimitierte Voice Check-ins</p>
        <p>✅ KI Pattern Analyse</p>
        <p>✅ Body Profile & Timeline</p>
        <p>✅ WhatsApp Ergebnisse</p>
        <p>✅ Therapeuten Dashboard</p>
      </div>

      <div class="hr"></div>
      <form method="post" action="/subscription/create">
        <button type="submit" style="font-size:18px;padding:16px">
          Jetzt für 4,99€/Monat starten
        </button>
      </form>
      <p class="small" style="margin-top:12px">Sicher über Stripe • Jederzeit kündbar • Keine versteckten Kosten</p>
      <div class="hr"></div>
      <p class="small"><a href="/checkin/1">Zurück</a></p>
    """
    return _page("PTGO • Upgrade", body, request=request)


@app.post("/subscription/create", response_class=HTMLResponse)
def subscription_create(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)

    if not STRIPE_SECRET_KEY:
        return _page("Fehler", "<h1>Stripe nicht konfiguriert</h1>", request=request)

    price_id = _get_or_create_price()
    if not price_id:
        return _page("Fehler", "<h1>Stripe Fehler – bitte später nochmal</h1>", request=request)

    try:
        r = requests.post(
            "https://api.stripe.com/v1/checkout/sessions",
            headers=_stripe_headers(),
            data={
                "mode": "subscription",
                "line_items[0][price]": price_id,
                "line_items[0][quantity]": "1",
                "customer_email": p.email,
                "success_url": f"{BASE_URL}/subscription/success?session_id={{CHECKOUT_SESSION_ID}}",
                "cancel_url": f"{BASE_URL}/upgrade",
                "metadata[patient_id]": str(p.id),
            },
            timeout=15,
        )
        session = r.json()
        checkout_url = session.get("url")
        if not checkout_url:
            raise Exception(f"No URL in response: {session}")
        return RedirectResponse(checkout_url, status_code=303)
    except Exception as e:
        print("[WARN] Stripe checkout failed:", e)
        return _page("Fehler", f"<h1>Stripe Fehler</h1><p>{e}</p><p><a href='/upgrade'>Zurück</a></p>", request=request)


@app.get("/subscription/success", response_class=HTMLResponse)
def subscription_success(request: Request, session_id: str = "", db=Depends(get_db)):
    p = require_patient_login(request, db)

    # Verify with Stripe
    try:
        r = requests.get(
            f"https://api.stripe.com/v1/checkout/sessions/{session_id}",
            headers=_stripe_headers(),
            timeout=10,
        )
        session = r.json()
        if session.get("payment_status") == "paid":
            p.subscription_active = True
            db.commit()
    except Exception as e:
        print("[WARN] Stripe verify failed:", e)

    body = f"""
      <h1>Willkommen bei Premium! 🎉</h1>
      <p>Dein Account ist jetzt freigeschaltet.</p>
      <div class="hr"></div>
      <a class="btn" href="/checkin/1">🎙️ Ersten Check starten</a>
    """
    return _page("PTGO • Premium aktiv", body, request=request)


@app.get("/subscription/cancel", response_class=HTMLResponse)
def subscription_cancel_page(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)

    # Loss Aversion: show what they'll lose
    streak = _get_patient_streak(db, p.id)
    loss_section = ""
    losses = []
    if streak["current_streak"] > 0:
        losses.append(f"🔥 Deine aktuelle Streak von <b>{streak['current_streak']} Tagen</b>")
    if streak["total_checkins"] > 0:
        losses.append(f"📊 KI-Analyse über <b>{streak['total_checkins']} Check-ins</b>")
    losses.append("🎙️ Unlimitierte Voice Check-ins")
    losses.append("📱 WhatsApp Reminder")
    losses.append("👨‍⚕️ Therapeuten-Berichte")

    loss_section = f"""
    <div style="margin:14px 0;padding:14px;background:rgba(239,68,68,.05);border:1px solid rgba(239,68,68,.2);border-radius:14px">
      <p style="font-size:13px;color:#fca5a5;margin:0 0 10px;font-weight:600">Das verlierst du mit der Kündigung:</p>
      {"".join(f'<p style="font-size:13px;color:#94a3b8;margin:4px 0">{l}</p>' for l in losses)}
    </div>
    """

    body = f"""
      <h1>Abo kündigen</h1>
      <p>Möchtest du dein Premium Abo wirklich kündigen?</p>

      {loss_section}

      <div class="hr"></div>
      <a class="btn" href="/upgrade">Premium behalten</a>
      <div style="height:10px"></div>
      <form method="post" action="/subscription/cancel">
        <button type="submit" style="background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.3);color:#fca5a5;border-radius:14px;padding:12px 20px;font-size:14px;cursor:pointer;width:100%">
          Trotzdem kündigen
        </button>
      </form>
    """
    return _page("PTGO • Kündigen", body, request=request)

@app.post("/subscription/cancel", response_class=HTMLResponse)
def subscription_cancel(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)
    p.subscription_active = False
    db.commit()
    body = """
      <h1>Abo gekündigt</h1>
      <p>Dein Premium Abo wurde beendet. Du kannst es jederzeit wieder aktivieren.</p>
      <div class="hr"></div>
      <a class="btn" href="/upgrade">Wieder upgraden</a>
    """
    return _page("PTGO • Gekündigt", body, request=request)


# =========================================================
# STRIPE – SUBSCRIPTION
# =========================================================

def _stripe_enabled() -> bool:
    return bool(STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY)

def _stripe_headers() -> dict:
    return {
        "Authorization": f"Bearer {STRIPE_SECRET_KEY}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

def _get_or_create_price() -> Optional[str]:
    """Get existing price or create one for 4.99€/month."""
    if STRIPE_PRICE_ID:
        return STRIPE_PRICE_ID
    try:
        # Create product
        r = requests.post("https://api.stripe.com/v1/products",
            headers=_stripe_headers(),
            data={"name": "PTGO Premium", "description": "Unlimited Check-ins + AI + WhatsApp"},
            timeout=10)
        product_id = r.json()["id"]

        # Create price
        r = requests.post("https://api.stripe.com/v1/prices",
            headers=_stripe_headers(),
            data={
                "product": product_id,
                "unit_amount": SUBSCRIPTION_PRICE_EUR,
                "currency": "eur",
                "recurring[interval]": "month",
            },
            timeout=10)
        return r.json()["id"]
    except Exception as e:
        print("[WARN] Stripe price creation failed:", e)
        return None


@app.get("/subscribe", response_class=HTMLResponse)
def subscribe_page(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)

    if p.subscription_active:
        body = f"""
          <h1>Du bist bereits Premium ✅</h1>
          <p>Dein Abo ist aktiv. Du hast Zugang zu allen Features.</p>
          <div class="hr"></div>
          <form method="post" action="/subscribe/cancel">
            <button type="submit" style="background:transparent;border:1px solid #374151;color:#6b7280;border-radius:10px;padding:10px 16px;font-size:13px;cursor:pointer;">
              Abo kündigen
            </button>
          </form>
          <div class="hr"></div>
          <a class="btn" href="/checkin/1">Zurück zum Check-in</a>
        """
        return _page("PTGO • Premium", body, request=request)

    if not _stripe_enabled():
        body = """
          <h1>Premium</h1>
          <p>Zahlung noch nicht konfiguriert. Bitte kontaktiere deinen Therapeuten.</p>
        """
        return _page("PTGO • Premium", body, request=request)

    # Endowment + Anchoring
    streak = _get_patient_streak(db, p.id)
    endowment = ""
    if streak["total_checkins"] > 0:
        endowment = f"""
        <div style="margin:0 0 14px;padding:12px;background:rgba(99,102,241,.06);border:1px solid rgba(99,102,241,.2);border-radius:12px">
          <p style="font-size:13px;color:#a5b4fc;margin:0">Du hast bereits <b>{streak['total_checkins']} Check-in{'s' if streak['total_checkins'] != 1 else ''}</b> gemacht. Mach mehr daraus.</p>
        </div>
        """

    body = f"""
      <h1>PTGO Premium</h1>
      <p>Unbegrenzte Check-ins, KI-Analyse, WhatsApp Reminder.</p>

      {endowment}

      <div style="margin:12px 0;padding:12px;border:1px solid #374151;border-radius:12px;text-align:center">
        <div style="font-size:13px;color:#6b7280;text-decoration:line-through">Eine Therapiestunde: 80–150€</div>
        <div style="font-size:32px;font-weight:700;color:#f59e0b;margin:4px 0">4,99€<span style="font-size:14px;color:#6b7280">/Monat</span></div>
        <div style="font-size:12px;color:#6b7280">= 0,17€ pro Tag für deine Gesundheit</div>
      </div>

      <div class="action-box" style="margin:16px 0">
        <p style="margin:6px 0">✅ Unbegrenzte Voice Check-ins</p>
        <p style="margin:6px 0">✅ KI Pattern-Analyse</p>
        <p style="margin:6px 0">✅ WhatsApp Daily Reminder</p>
        <p style="margin:6px 0">✅ Body Profile + Timeline</p>
        <p style="margin:6px 0">✅ Therapeuten-Berichte</p>
      </div>

      <div id="payment-form">
        <div id="card-element" style="background:#0b1223;border:1px solid #263246;border-radius:12px;padding:14px;margin:12px 0"></div>
        <div id="card-errors" style="color:#fecaca;font-size:13px;margin:6px 0"></div>
        <button id="pay-btn" onclick="startPayment()" style="font-size:18px;padding:16px;">
          💳 Jetzt abonnieren
        </button>
      </div>

      <p class="small" style="margin-top:12px">Sicher über Stripe • Jederzeit kündbar • Keine versteckten Kosten</p>

      <script src="https://js.stripe.com/v3/"></script>
      <script>
        const stripe = Stripe('{STRIPE_PUBLISHABLE_KEY}');
        const elements = stripe.elements();
        const card = elements.create('card', {{
          style: {{
            base: {{
              color: '#e5e7eb',
              fontFamily: '-apple-system, sans-serif',
              fontSize: '16px',
              '::placeholder': {{ color: '#6b7280' }}
            }}
          }}
        }});
        card.mount('#card-element');

        card.on('change', (e) => {{
          document.getElementById('card-errors').textContent = e.error ? e.error.message : '';
        }});

        async function startPayment() {{
          const btn = document.getElementById('pay-btn');
          btn.disabled = true;
          btn.textContent = 'Wird verarbeitet...';

          const r = await fetch('/subscribe/create-session', {{method: 'POST'}});
          const data = await r.json();

          if (data.error) {{
            document.getElementById('card-errors').textContent = data.error;
            btn.disabled = false;
            btn.textContent = '💳 Jetzt abonnieren';
            return;
          }}

          const result = await stripe.redirectToCheckout({{ sessionId: data.session_id }});
          if (result.error) {{
            document.getElementById('card-errors').textContent = result.error.message;
            btn.disabled = false;
            btn.textContent = '💳 Jetzt abonnieren';
          }}
        }}
      </script>
    """
    return _page("PTGO • Premium", body, request=request)


@app.post("/subscribe/create-session")
async def create_checkout_session(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)

    if not _stripe_enabled():
        return {"error": "Stripe nicht konfiguriert"}

    price_id = _get_or_create_price()
    if not price_id:
        return {"error": "Preis konnte nicht erstellt werden"}

    try:
        r = requests.post(
            "https://api.stripe.com/v1/checkout/sessions",
            headers=_stripe_headers(),
            data={
                "payment_method_types[]": "card",
                "mode": "subscription",
                "line_items[0][price]": price_id,
                "line_items[0][quantity]": "1",
                "success_url": f"{BASE_URL}/subscribe/success?session_id={{CHECKOUT_SESSION_ID}}",
                "cancel_url": f"{BASE_URL}/subscribe",
                "customer_email": p.email,
                "metadata[patient_id]": str(p.id),
            },
            timeout=15,
        )
        data = r.json()
        if "error" in data:
            return {"error": data["error"]["message"]}
        return {"session_id": data["id"]}
    except Exception as e:
        print("[WARN] Stripe session failed:", e)
        return {"error": "Zahlung fehlgeschlagen"}


@app.get("/subscribe/success", response_class=HTMLResponse)
def subscribe_success(request: Request, session_id: str = "", db=Depends(get_db)):
    p = require_patient_login(request, db)

    # Verify with Stripe
    if session_id and STRIPE_SECRET_KEY:
        try:
            r = requests.get(
                f"https://api.stripe.com/v1/checkout/sessions/{session_id}",
                headers=_stripe_headers(),
                timeout=10,
            )
            data = r.json()
            if data.get("payment_status") == "paid":
                p.subscription_active = True
                p.subscription_stripe_session = session_id
                db.commit()
        except Exception as e:
            print("[WARN] Stripe verify failed:", e)

    body = f"""
      <h1>Willkommen bei Premium! 🎉</h1>
      <p>Dein Abo ist aktiv. Du hast jetzt Zugang zu allen Features.</p>
      <div class="hr"></div>
      <a class="btn" href="/checkin/1">Check-in starten</a>
    """
    return _page("PTGO • Premium aktiv", body, request=request)


@app.post("/subscribe/cancel", response_class=HTMLResponse)
def subscribe_cancel(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)
    p.subscription_active = False
    db.commit()
    body = """
      <h1>Abo gekündigt</h1>
      <p>Dein Abo wurde gekündigt. Du kannst es jederzeit wieder aktivieren.</p>
      <div style="margin:14px 0;padding:12px;background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.2);border-radius:12px;text-align:center">
        <p style="font-size:13px;color:#fcd34d;margin:0">Deine bisherigen Daten bleiben gespeichert. Komm jederzeit zurück.</p>
      </div>
      <div class="hr"></div>
      <a class="btn" href="/subscribe">Wieder abonnieren</a>
    """
    return _page("PTGO • Gekündigt", body, request=request)




@app.get("/therapist/login", response_class=HTMLResponse)
def therapist_login_page(request: Request):
    body = """
      <h1>Therapist Login</h1>
      <form method="post" action="/therapist/login">
        <label>E-Mail</label>
        <input name="email" type="email" required>
        <label>Passwort</label>
        <input name="password" type="password" required>
        <button type="submit">Login</button>
      </form>
      <div class="hr"></div>
      <h2>Registrieren</h2>
      <form method="post" action="/therapist/register">
        <label>Name</label>
        <input name="name" required>
        <label>E-Mail</label>
        <input name="email" type="email" required>
        <label>Phone (E.164)</label>
        <input name="phone" placeholder="+49...">
        <label>Passwort</label>
        <input name="password" type="password" required>
        <button type="submit">Account erstellen</button>
      </form>
      <div class="hr"></div>
      <p><a href="/">Back</a></p>
    """
    return _page("Therapist Login", body, request=request)

@app.post("/therapist/register", response_class=HTMLResponse)
def therapist_register(request: Request, name: str = Form(...), email: EmailStr = Form(...), phone: str = Form(""), password: str = Form(...), db=Depends(get_db)):
    email = str(email).strip().lower()
    if db.query(Therapist).filter(Therapist.email == email).first():
        return _page("Fehler", "<h1>E-Mail existiert bereits</h1><p><a href='/therapist/login'>Zurück</a></p>", request=request)
    t = Therapist(name=name.strip(), email=email, phone=phone.strip() or None, password_hash=_hash_password(password))
    db.add(t)
    db.commit()
    request.session["therapist_id"] = t.id
    return RedirectResponse("/therapist", status_code=303)

@app.post("/therapist/login", response_class=HTMLResponse)
def therapist_login(request: Request, email: EmailStr = Form(...), password: str = Form(...), db=Depends(get_db)):
    email = str(email).strip().lower()
    t = db.query(Therapist).filter(Therapist.email == email).first()
    if not t or t.password_hash != _hash_password(password):
        return _page("Fehler", "<h1>Login fehlgeschlagen</h1><p><a href='/therapist/login'>Zurück</a></p>", request=request)
    request.session["therapist_id"] = t.id
    return RedirectResponse("/therapist", status_code=303)

@app.get("/therapist/logout")
def therapist_logout(request: Request):
    request.session.pop("therapist_id", None)
    return RedirectResponse("/therapist/login", status_code=303)

@app.get("/therapist", response_class=HTMLResponse)
def therapist_dashboard(request: Request, db=Depends(get_db)):
    t = require_therapist_login(request, db)
    patients = db.query(Patient).filter(Patient.therapist_id == t.id).all()

    rows = ""
    for p in patients:
        last = db.query(CheckIn).filter(CheckIn.patient_id == p.id).order_by(CheckIn.created_at.desc()).first()
        if last:
            tag = f"<span class='tag'>Score {last.score}</span><span class='tag'>{last.pattern_label or last.risk_level}</span>"
            link = f"<a href='/therapist/checkin/{last.id}'>open</a>"
            when = last.local_day
        else:
            tag = "<span class='tag'>no data</span>"
            link = ""
            when = "-"
        rows += f"""
        <div class="kpi" style="margin-bottom:10px">
          <div><b>{p.name}</b></div>
          <div class="small">{p.phone} • {p.email}</div>
          <div style="height:6px"></div>
          {tag} <span class="small">({when})</span> {link}
        </div>
        """

    body = f"""
      <h1>Therapist</h1>
      <p class="small">Eingeloggt als <b>{t.name}</b> &bull; <a href="/product">Verkaufs-Tracker</a> &bull; <a href="/therapist/logout">logout</a></p>
      <div class="hr"></div>
      <h2>Patient zuweisen</h2>
      <form method="post" action="/therapist/assign">
        <label>Patient Phone (E.164)</label>
        <input name="phone" placeholder="+49..." required>
        <button type="submit">Zuweisen</button>
      </form>
      <div class="hr"></div>
      <h2>Patienten</h2>
      {rows if rows else "<p class='small'>Noch keine Patienten.</p>"}
    """
    return _page("Therapist Dashboard", body, request=request)

@app.post("/therapist/assign", response_class=HTMLResponse)
def therapist_assign(request: Request, phone: str = Form(...), db=Depends(get_db)):
    t = require_therapist_login(request, db)
    p = db.query(Patient).filter(Patient.phone == phone.strip()).first()
    if not p:
        return _page("Fehler", "<h1>Patient nicht gefunden</h1><p><a href='/therapist'>Back</a></p>", request=request)
    p.therapist_id = t.id
    db.commit()
    return RedirectResponse("/therapist", status_code=303)

@app.get("/therapist/checkin/{checkin_id}", response_class=HTMLResponse)
def therapist_view_checkin(checkin_id: int, request: Request, db=Depends(get_db)):
    t = require_therapist_login(request, db)
    c = db.query(CheckIn).filter(CheckIn.id == checkin_id).first()
    if not c:
        raise HTTPException(status_code=404)
    p = db.query(Patient).filter(Patient.id == c.patient_id).first()
    if not p or p.therapist_id != t.id:
        raise HTTPException(status_code=403)

    signals = {}
    try:
        signals = json.loads(c.signals_json or "{}")
    except Exception:
        pass

    outcome = db.query(Outcome).filter(Outcome.checkin_id == c.id).first()
    outcome_html = ""
    if outcome:
        emoji = {"better": "😌", "same": "😐", "worse": "😔"}.get(outcome.rating, "")
        outcome_html = f"<p><b>Outcome:</b> {emoji} {outcome.rating}</p>"

    body = f"""
      <h1>{p.name}</h1>
      <p class="small">{p.phone} • {p.email}</p>
      <div class="hr"></div>
      <div class="grid3">
        <div class="kpi"><span class="small">Score</span><b>{c.score}</b></div>
        <div class="kpi"><span class="small">Risk</span><b>{c.risk_level}</b></div>
        <div class="kpi"><span class="small">Tag</span><b>{c.local_day}</b></div>
      </div>
      <div class="hr"></div>
      <h2>Pattern</h2>
      <div class="pattern-tag">{c.pattern_label or "–"}</div>
      <h2>Action</h2>
      <p>{c.action_label}: {c.action_text}</p>
      {outcome_html}
      <div class="hr"></div>
      <h2>Signals</h2>
      <div class="code">{json.dumps(signals, ensure_ascii=False, indent=2)}</div>
      <div class="hr"></div>
      <h2>Rohdaten</h2>
      <div class="code">Stress: {c.stress} | Schlaf: {c.sleep} | Körper: {c.body} | Craving: {c.craving} | Vermeidung: {c.avoidance}</div>
      <div style="height:16px"></div>
      <p><a href="/therapist">← Back</a></p>
    """
    return _page("Therapist • Checkin", body, request=request)


# =========================================================
# AI MICRO-COACHING
# =========================================================

def _generate_coaching_impulse(checkin: CheckIn, patient: Patient, recent_checkins: list) -> str:
    """Generate a personalized AI coaching impulse based on check-in data and history."""
    if not ANTHROPIC_API_KEY:
        return ""

    history = ""
    if recent_checkins:
        for rc in recent_checkins[:5]:
            history += f"- {rc.local_day}: Score {rc.score}, Pattern: {rc.pattern_label}, Stress: {rc.stress}, Schlaf: {rc.sleep}\n"

    prompt = (
        f"Du bist ein empathischer, erfahrener Therapeut und Coach. "
        f"Der Patient '{patient.name}' hat gerade einen Check-in gemacht.\n\n"
        f"HEUTIGES ERGEBNIS:\n"
        f"- Recovery Score: {checkin.score}/100\n"
        f"- Risk Level: {checkin.risk_level}\n"
        f"- Pattern: {checkin.pattern_label}\n"
        f"- Stress: {checkin.stress}/10, Schlaf: {checkin.sleep}/10, Körper: {checkin.body}/10\n"
        f"- Craving: {checkin.craving}/10, Vermeidung: {checkin.avoidance}/10\n"
        f"- Stimmung: {checkin.overall_text or '–'}\n"
        f"- Herausforderung: {checkin.context_text or '–'}\n"
        f"- Gedanken: {checkin.mental_text or '–'}\n"
        f"- Tagesziel: {checkin.goal_text or '–'}\n\n"
        f"LETZTE TAGE:\n{history or 'Keine Historie.'}\n\n"
        f"Schreibe einen kurzen, persönlichen Coaching-Impuls (3-5 Sätze) auf Deutsch. "
        f"Sei warm aber direkt. Erkenne Muster. Gib EINEN konkreten Micro-Tipp für die nächsten 2 Stunden. "
        f"Kein Gelaber, kein Therapeuten-Deutsch. Sprich wie ein weiser Freund."
    )

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5", "max_tokens": 400, "messages": [{"role": "user", "content": prompt}]},
            timeout=20,
        )
        resp.raise_for_status()
        resp_data = resp.json()
        _track_ai_usage("coaching_impulse", resp_data)
        return resp_data["content"][0]["text"].strip()
    except Exception as e:
        _track_ai_error("coaching_impulse", str(e))
        print("[WARN] Coaching impulse failed:", e)
        return ""


@app.get("/coaching/{checkin_id}", response_class=HTMLResponse)
def coaching_page(checkin_id: int, request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)
    c = db.query(CheckIn).filter(CheckIn.id == checkin_id, CheckIn.patient_id == p.id).first()
    if not c:
        raise HTTPException(status_code=404)

    recent = db.query(CheckIn).filter(
        CheckIn.patient_id == p.id, CheckIn.id != c.id
    ).order_by(CheckIn.created_at.desc()).limit(5).all()

    impulse = _generate_coaching_impulse(c, p, recent)
    if not impulse:
        impulse = "KI-Coaching ist gerade nicht verfügbar. Deine heutige Action ist dein bester nächster Schritt."

    action = ACTION_LIBRARY.get(c.action_code or "", None)

    body = f"""
      <div style="text-align:center;margin:8px 0 16px">
        <div style="font-size:48px;line-height:1">🧠</div>
        <div style="font-size:11px;color:#6b7280;margin-top:6px;letter-spacing:1px">AI COACHING</div>
      </div>
      <h1 style="text-align:center;font-size:22px">Dein Coaching-Impuls</h1>

      <div style="background:rgba(99,102,241,.06);border:1px solid rgba(99,102,241,.25);border-radius:16px;padding:20px;margin:16px 0">
        <p style="font-size:15px;line-height:1.7;color:#e5e7eb;margin:0">{impulse}</p>
      </div>

      <div class="grid3" style="margin:16px 0">
        <div class="kpi"><span class="small">Score</span><b>{c.score}</b></div>
        <div class="kpi"><span class="small">Pattern</span><b style="font-size:14px">{c.pattern_label}</b></div>
        <div class="kpi"><span class="small">Risk</span><b>{c.risk_level}</b></div>
      </div>

      <div class="action-box">
        <p class="small" style="color:#f59e0b;margin:0 0 6px">DEINE ACTION</p>
        <b style="font-size:16px">{c.action_label or '–'}</b>
        <p style="font-size:13px;margin:8px 0 0">{action['instructions'] if action else c.action_text or ''}</p>
      </div>

      <div class="hr"></div>
      <p class="small" style="text-align:center">
        <a href="/result/{c.id}">← Ergebnis</a> •
        <a href="/checkin/1">Neuer Check</a> •
        <a href="/insights">Trends</a>
      </p>
    """
    return _page("PTGO • AI Coaching", body, request=request)


# =========================================================
# SMART TREND INSIGHTS (AI-powered)
# =========================================================

def _generate_trend_insights(checkins: list, patient_name: str) -> str:
    """Generate AI-powered trend analysis from recent check-ins."""
    if not ANTHROPIC_API_KEY or len(checkins) < 3:
        return ""

    data_rows = []
    for c in checkins:
        data_rows.append(
            f"{c.local_day}: Score={c.score}, Stress={c.stress}, Schlaf={c.sleep}, "
            f"Körper={c.body}, Craving={c.craving}, Vermeidung={c.avoidance}, "
            f"Pattern={c.pattern_label}, Risk={c.risk_level}"
        )

    prompt = (
        f"Du analysierst die Check-in-Daten von {patient_name}.\n\n"
        f"DATEN (neueste zuerst):\n" + "\n".join(data_rows) + "\n\n"
        f"Erstelle eine kurze Trend-Analyse auf Deutsch (3-4 Sätze):\n"
        f"1. Welcher Trend ist erkennbar? (besser/schlechter/stabil)\n"
        f"2. Was ist der größte Risikofaktor?\n"
        f"3. Was läuft gut?\n"
        f"4. Ein konkreter Tipp für die nächste Woche.\n"
        f"Sei direkt und konkret. Keine Floskeln."
    )

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5", "max_tokens": 300, "messages": [{"role": "user", "content": prompt}]},
            timeout=20,
        )
        resp.raise_for_status()
        resp_data = resp.json()
        _track_ai_usage("trend_insights", resp_data)
        return resp_data["content"][0]["text"].strip()
    except Exception as e:
        _track_ai_error("trend_insights", str(e))
        print("[WARN] Trend insights failed:", e)
        return ""


@app.get("/insights", response_class=HTMLResponse)
def insights_page(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)
    checkins = db.query(CheckIn).filter(
        CheckIn.patient_id == p.id
    ).order_by(CheckIn.created_at.desc()).limit(14).all()

    if len(checkins) < 3:
        body = """
          <h1>Trend Insights</h1>
          <p>Du brauchst mindestens 3 Check-ins für eine Trend-Analyse.</p>
          <a class="btn" href="/checkin/1">Check-in starten</a>
        """
        return _page("PTGO • Insights", body, request=request)

    insight = _generate_trend_insights(checkins, p.name)

    scores = [c.score for c in reversed(checkins)]
    max_s = max(scores) if scores else 100
    min_s = min(scores) if scores else 0
    range_s = max(max_s - min_s, 1)
    bar_w = max(int(100 / len(scores)), 4)
    sparkline = ""
    for s in scores:
        h = max(int((s - min_s) / range_s * 60), 4)
        color = "#22c55e" if s >= 70 else ("#f59e0b" if s >= 40 else "#ef4444")
        sparkline += f'<div style="width:{bar_w}%;height:{h}px;background:{color};border-radius:3px;flex-shrink:0" title="Score {s}"></div>'

    avg_stress = sum(c.stress or 5 for c in checkins) / len(checkins)
    avg_sleep = sum(c.sleep or 5 for c in checkins) / len(checkins)
    avg_score = sum(c.score or 0 for c in checkins) / len(checkins)

    body = f"""
      <div style="text-align:center;margin:8px 0 16px">
        <div style="font-size:48px;line-height:1">📊</div>
        <div style="font-size:11px;color:#6b7280;margin-top:6px;letter-spacing:1px">AI TREND INSIGHTS</div>
      </div>
      <h1 style="text-align:center;font-size:22px">Deine Trends</h1>
      <p class="small" style="text-align:center">Basierend auf {len(checkins)} Check-ins</p>

      <div style="margin:16px 0;padding:14px;background:rgba(99,102,241,.06);border:1px solid rgba(99,102,241,.25);border-radius:16px">
        <p style="font-size:14px;line-height:1.7;color:#e5e7eb;margin:0">{insight or 'KI-Analyse nicht verfügbar.'}</p>
      </div>

      <div style="margin:16px 0">
        <p class="small" style="margin-bottom:6px">Recovery Score Verlauf</p>
        <div style="display:flex;align-items:end;gap:2px;height:64px;padding:4px;background:rgba(255,255,255,.02);border:1px solid #1f2937;border-radius:12px">
          {sparkline}
        </div>
      </div>

      <div class="grid3">
        <div class="kpi">
          <span class="small">Ø Score</span>
          <b style="color:{'#22c55e' if avg_score >= 70 else '#f59e0b' if avg_score >= 40 else '#ef4444'}">{int(avg_score)}</b>
        </div>
        <div class="kpi">
          <span class="small">Ø Stress</span>
          <b style="color:{'#ef4444' if avg_stress > 7 else '#f59e0b' if avg_stress > 5 else '#22c55e'}">{avg_stress:.1f}</b>
        </div>
        <div class="kpi">
          <span class="small">Ø Schlaf</span>
          <b style="color:{'#ef4444' if avg_sleep < 4 else '#f59e0b' if avg_sleep < 6 else '#22c55e'}">{avg_sleep:.1f}</b>
        </div>
      </div>

      <div class="hr"></div>
      <p class="small" style="text-align:center">
        <a href="/progress">Progress</a> •
        <a href="/timeline">Timeline</a> •
        <a href="/profile">Body Profile</a> •
        <a href="/checkin/1">Neuer Check</a>
      </p>
    """
    return _page("PTGO • Trend Insights", body, request=request)


# =========================================================
# EMERGENCY ESCALATION
# =========================================================

def _check_emergency_escalation(db, patient: Patient, checkin: CheckIn):
    """Check for critical patterns and escalate to therapist immediately."""
    risk_triggers = []

    if checkin.risk_level == "high":
        risk_triggers.append("HIGH RISK Check-in")
    if (checkin.craving or 0) >= 9:
        risk_triggers.append(f"Extremes Craving: {checkin.craving}/10")
    if (checkin.stress or 0) >= 9 and (checkin.sleep or 10) <= 2:
        risk_triggers.append(f"Kritisch: Stress {checkin.stress} + Schlaf {checkin.sleep}")
    if (checkin.daily_state or 10) <= 1:
        risk_triggers.append(f"Minimale Stimmung: {checkin.daily_state}/10")

    # Check for rapid decline
    prev = db.query(CheckIn).filter(
        CheckIn.patient_id == patient.id, CheckIn.id != checkin.id
    ).order_by(CheckIn.created_at.desc()).first()
    if prev and prev.score and checkin.score and (prev.score - checkin.score) >= 30:
        risk_triggers.append(f"Rapider Abfall: {prev.score} → {checkin.score} (-{prev.score - checkin.score})")

    if not risk_triggers:
        return

    alert_msg = (
        f"🚨 EMERGENCY ALERT 🚨\n"
        f"Patient: {patient.name}\n"
        f"Score: {checkin.score}/100\n\n"
        f"Auslöser:\n" + "\n".join(f"• {t}" for t in risk_triggers) + "\n\n"
        f"Sofort prüfen: {BASE_URL}/therapist/checkin/{checkin.id}"
    )

    try:
        therapist = db.query(Therapist).filter(Therapist.id == patient.therapist_id).first() if patient.therapist_id else None
        send_whatsapp_to_therapist(patient, therapist, alert_msg)
    except Exception as e:
        print("[WARN] Emergency escalation failed:", e)

    if SMTP_HOST and patient.therapist_id:
        try:
            therapist = db.query(Therapist).filter(Therapist.id == patient.therapist_id).first()
            if therapist and therapist.email:
                msg = EmailMessage()
                msg["Subject"] = f"🚨 PTGO Emergency: {patient.name}"
                msg["From"] = SMTP_FROM
                msg["To"] = therapist.email
                msg.set_content(alert_msg)
                with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                    server.starttls()
                    server.login(SMTP_USER, SMTP_PASS)
                    server.send_message(msg)
        except Exception as e:
            print("[WARN] Emergency email failed:", e)


# =========================================================
# EVENING REFLECTION — 2x daily automated coaching
# =========================================================

EVENING_COACHING_PROMPTS = [
    "Was war heute dein kleiner Sieg – auch wenn er winzig war?",
    "Wofür bist du heute dankbar, auch wenn der Tag schwer war?",
    "Was hast du heute über dich gelernt?",
    "Wenn du deinem Körper eine Nachricht schicken könntest – was würdest du sagen?",
    "Was brauchst du jetzt gerade in diesem Moment?",
    "Welche Entscheidung hat dir heute am meisten Energie gegeben?",
    "Was kannst du morgen anders machen als heute?",
]

def _generate_evening_message(db, patient: Patient) -> str:
    """Generate personalized evening reflection message."""
    today = _now_local().date().isoformat()
    todays_checkin = db.query(CheckIn).filter(
        CheckIn.patient_id == patient.id, CheckIn.local_day == today
    ).order_by(CheckIn.created_at.desc()).first()

    prompt_of_day = random.choice(EVENING_COACHING_PROMPTS)

    if todays_checkin and ANTHROPIC_API_KEY:
        try:
            ai_prompt = (
                f"Der Patient {patient.name} hatte heute folgendes Ergebnis:\n"
                f"Score: {todays_checkin.score}/100, Pattern: {todays_checkin.pattern_label}\n"
                f"Action: {todays_checkin.action_label}\n\n"
                f"Schreibe eine kurze Abend-Reflexion (2 Sätze) auf Deutsch. "
                f"Beziehe dich auf den heutigen Tag. Sei warm und ermutigend. "
                f"Ende mit einer Reflexionsfrage."
            )
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-haiku-4-5", "max_tokens": 200, "messages": [{"role": "user", "content": ai_prompt}]},
                timeout=15,
            )
            resp.raise_for_status()
            resp_data = resp.json()
            _track_ai_usage("evening_message", resp_data, patient_id=patient.id)
            return resp_data["content"][0]["text"].strip()
        except Exception as e:
            _track_ai_error("evening_message", str(e), patient_id=patient.id)
            print("[WARN] Evening AI message failed:", e)

    if todays_checkin:
        return (
            f"Guten Abend {patient.name} 🌙\n\n"
            f"Dein Score heute: {todays_checkin.score}/100\n"
            f"Pattern: {todays_checkin.pattern_label}\n\n"
            f"Abend-Reflexion:\n{prompt_of_day}\n\n"
            f"Morgen geht's weiter. Schlaf gut."
        )

    return (
        f"Guten Abend {patient.name} 🌙\n\n"
        f"Du hast heute keinen Check-in gemacht. Kein Stress – morgen ist ein neuer Tag.\n\n"
        f"Abend-Impuls:\n{prompt_of_day}\n\n"
        f"Schlaf gut."
    )


def _should_send_evening_message(p: Patient, now_local: datetime) -> bool:
    """Check if evening message should be sent (around 20:00)."""
    if not p.reminder_enabled:
        return False
    today = now_local.date().isoformat()
    # Use a simple marker: last_reminder_sent_on tracks morning, we track evening differently
    # Evening window: 19:45 - 20:15
    hour = now_local.hour
    minute = now_local.minute
    if hour == 20 and minute <= 15:
        return True
    if hour == 19 and minute >= 45:
        return True
    return False


# =========================================================
# MODUL 20 – ROLLO TOMASSI FRAMEWORK
# =========================================================

ROLLO_TOMASSI = {
    "core_principles": {
        "iron_rules": [
            {
                "rule": "Iron Rule #1: Frame ist alles",
                "description": "Wer den Frame kontrolliert, kontrolliert die Dynamik. In jeder Interaktion gibt es einen Frame – deinen oder den des anderen.",
                "daily_practice": "Beobachte heute jede Interaktion: Wer setzt den Frame? Übernimm bewusst die Führung in mindestens 3 Gesprächen.",
                "metric": "frame_control",
            },
            {
                "rule": "Iron Rule #2: Zeige niemals mehr Interesse als sie",
                "description": "Investition muss immer proportional sein. Wer mehr investiert, hat weniger Macht.",
                "daily_practice": "Spiegel heute das Engagement deines Gegenübers. Antworte nicht sofort. Setze deine Zeit bewusst ein.",
                "metric": "investment_balance",
            },
            {
                "rule": "Iron Rule #3: Jede Frau ist eine Option, keine Garantie",
                "description": "Abundance Mentality. Nie alles auf eine Karte setzen. Du bist die Wahl, nicht der Wartende.",
                "daily_practice": "Erweitere heute dein soziales Netzwerk. Sprich mit 3 neuen Menschen. Baue Optionen auf.",
                "metric": "abundance_mindset",
            },
            {
                "rule": "Iron Rule #4: Verdecke deine Absichten nie mit Freundschaft",
                "description": "Sei direkt. Friendzone entsteht durch fehlende Polarisierung. Mache deine Intention klar.",
                "daily_practice": "Kommuniziere heute eine unbequeme Wahrheit direkt. Keine Umwege. Keine Entschuldigung.",
                "metric": "directness",
            },
            {
                "rule": "Iron Rule #5: Dein Wert steigt, wenn du dich selbst priorisierst",
                "description": "Sexual Market Value (SMV) ist real. Dein Wert wird durch dein Verhalten definiert, nicht durch deine Worte.",
                "daily_practice": "Priorisiere heute DEIN Training, DEINE Karriere, DEINE Ziele. Sage mindestens einmal Nein.",
                "metric": "self_priority",
            },
            {
                "rule": "Iron Rule #6: Frauen sind keine Männer mit anderen Körpern",
                "description": "Verstehe die fundamentalen Unterschiede in Kommunikation, Attraktion und Bindung.",
                "daily_practice": "Beobachte heute Kommunikationsmuster. Höre auf das WAS nicht gesagt wird.",
                "metric": "awareness",
            },
            {
                "rule": "Iron Rule #7: Hypergamie ist Natur, nicht Moral",
                "description": "Frauen optimieren instinktiv nach oben. Das ist keine Kritik, sondern Biologie. Deine Aufgabe: Werde die beste Version.",
                "daily_practice": "Arbeite heute an einem Skill der deinen Marktwert steigert. Körper, Geld, oder Status.",
                "metric": "smv_improvement",
            },
            {
                "rule": "Iron Rule #8: Sei der Preis",
                "description": "Wenn du dich als Preis verhältst, wirst du als Preis behandelt. Internalisiere deinen Wert.",
                "daily_practice": "Handle heute so, als wärst du die wichtigste Person im Raum. Nicht arrogant – aber sicher.",
                "metric": "prize_mentality",
            },
            {
                "rule": "Iron Rule #9: Vertraue dem Verhalten, nicht den Worten",
                "description": "Was Menschen TUN ist die Wahrheit. Was sie SAGEN ist oft Rationalisierung.",
                "daily_practice": "Ignoriere heute was dir gesagt wird. Beobachte was getan wird. Entscheide basierend auf Handlungen.",
                "metric": "behavioral_reading",
            },
        ],
    },
    "books": {
        "rational_male_1": {
            "title": "The Rational Male – Buch 1",
            "key_concepts": [
                "Hypergamie verstehen und akzeptieren",
                "SMV (Sexual Market Value) Kurven – Männer peaken später",
                "Blue Pill vs Red Pill Bewusstsein",
                "Oneitis als größte Gefahr",
                "Plate Theory – Optionen aufbauen",
                "Frame Control als Lebensphilosophie",
            ],
        },
        "rational_male_2": {
            "title": "The Rational Male – Preventive Medicine",
            "key_concepts": [
                "Die 5 Phasen der Red Pill Entwicklung",
                "Phase 1: Denial – Ablehnung der Realität",
                "Phase 2: Anger – Wut über die Täuschung",
                "Phase 3: Bargaining – Verhandlung mit dem alten Ich",
                "Phase 4: Depression – Trauer über die Illusion",
                "Phase 5: Acceptance – Integration und Neuaufbau",
                "Feminine Imperative erkennen",
                "Social Conventions die Männer klein halten",
            ],
        },
        "rational_male_3": {
            "title": "The Rational Male – Positive Masculinity",
            "key_concepts": [
                "Maskulinität ist kein Fehler",
                "Konventionelle Attraktivität aufbauen",
                "Mission vor Beziehung",
                "Red Pill Parenting",
                "Komplementäre Geschlechterrollen",
                "Authentic vs Performance Masculinity",
            ],
        },
        "rational_male_4": {
            "title": "The Rational Male – Religion",
            "key_concepts": [
                "Spiritualität und Red Pill vereinen",
                "Traditionelle Werte im modernen Kontext",
                "Purpose-driven Leadership",
                "Moralische Integrität ohne Blue Pill Conditioning",
            ],
        },
    },
    "smv_pillars": {
        "physique": {
            "label": "Körper / Physique",
            "weight": 0.25,
            "actions": [
                "5x/Woche Krafttraining (Push/Pull/Legs)",
                "Körperfett unter 15% halten",
                "Kleidung die deinen Körperbau betont",
                "Körpersprache: offen, breit, ruhig",
            ],
        },
        "status": {
            "label": "Status / Einfluss",
            "weight": 0.30,
            "actions": [
                "Karriere als Mission behandeln",
                "Social Proof aufbauen (Events, Netzwerk)",
                "Führungsrollen übernehmen",
                "Expertise in deinem Feld demonstrieren",
            ],
        },
        "game": {
            "label": "Game / Soziale Kompetenz",
            "weight": 0.25,
            "actions": [
                "Täglich mit Fremden sprechen",
                "Push/Pull Dynamik meistern",
                "Kino Escalation verstehen",
                "Storytelling und Humor entwickeln",
            ],
        },
        "resources": {
            "label": "Ressourcen / Vermögen",
            "weight": 0.20,
            "actions": [
                "Einkommensströme diversifizieren",
                "Investieren lernen und umsetzen",
                "Lifestyle Design: wenig Kosten, hoher Impact",
                "Finanzielle Unabhängigkeit als Ziel #1",
            ],
        },
    },
}


# =========================================================
# MODUL 21 – MILLIARDÄRS-TAGESPLAN
# =========================================================

BILLIONAIRE_DAILY_PLAN = {
    "meta": {
        "based_on": [
            "Elon Musk (Tesla, SpaceX, X) – Time Blocking in 5-Minuten-Einheiten",
            "Jeff Bezos (Amazon) – Regret Minimization Framework",
            "Ray Dalio (Bridgewater) – Principles-based Decision Making",
            "Naval Ravikant – Specific Knowledge + Leverage",
            "Andrew Huberman – Neuroscience-optimierte Routinen",
            "Alex Hormozi – $100M Offers Methodik",
            "Sam Altman – Compound Growth Thinking",
        ],
        "core_philosophy": "Milliardäre optimieren nicht ihre Zeit – sie optimieren ihren IMPACT pro Stunde. "
                          "Jede Stunde muss entweder Lernen, Bauen, oder Skalieren sein.",
    },
    "schedule": [
        {"time": "05:00", "block": "WAKE PROTOCOL", "duration": "15min",
         "action": "Kein Handy. Wasser (500ml). 2min Sonnenlicht oder helles Licht. Kalt duschen (30sec).",
         "why": "Huberman: Cortisol-Peak durch Licht. Dopamin-Reset durch Kälte. Musk: 'I wake up and think about problems.'",
         "category": "health"},
        {"time": "05:15", "block": "DEEP WORK I – BUILD", "duration": "120min",
         "action": "Die EINE Sache die am meisten Impact hat. Kein E-Mail, kein Social Media. Phone off. Timer auf 25min Pomodoro.",
         "why": "Bezos: Die wichtigsten Entscheidungen morgens. Dein präfrontaler Cortex ist jetzt am schärfsten.",
         "category": "build"},
        {"time": "07:15", "block": "TRAINING", "duration": "45min",
         "action": "Krafttraining (Push/Pull/Legs Rotation). Keine Ausdauer am Morgen. Heavy Compound Lifts.",
         "why": "Musk trainiert 2-3x/Woche. Bezos macht es täglich. Testosteron + Disziplin + Körper = höherer SMV.",
         "category": "health"},
        {"time": "08:00", "block": "FUEL", "duration": "20min",
         "action": "High-Protein Frühstück (40g+). Schwarzer Kaffee. Keine Kohlenhydrate vor 12:00.",
         "why": "Hormozi: 'Your body is your first business.' Insulin-Kontrolle = Energie-Kontrolle.",
         "category": "health"},
        {"time": "08:20", "block": "REVIEW & PLAN", "duration": "10min",
         "action": "PTGO Check-in machen. 3 MIT (Most Important Tasks) festlegen. Kalender checken.",
         "why": "Dalio: 'Without data, you're just guessing.' Dein Check-in IST dein Daten-Dashboard.",
         "category": "review"},
        {"time": "08:30", "block": "DEEP WORK II – REVENUE", "duration": "150min",
         "action": "Direkt umsatzgenerierende Arbeit. Verkaufen, Pitchen, Content erstellen, Produkte bauen.",
         "why": "Hormozi: Die ersten 4 Stunden deines Tages gehören der Umsatzgenerierung. Alles andere ist Ablenkung.",
         "category": "revenue"},
        {"time": "11:00", "block": "COMMUNICATION BLOCK", "duration": "60min",
         "action": "Alle E-Mails, Calls, Messages gebündelt. Batch Processing. Entscheidungen treffen, nicht aufschieben.",
         "why": "Musk: Time-Boxing. Bezos: 'I do my email at 10am.' Nie reactive arbeiten.",
         "category": "communication"},
        {"time": "12:00", "block": "LUNCH + LEARNING", "duration": "45min",
         "action": "Essen + Podcast/Audiobook. Themen: Business, Psychology, Finance. 30min Input pro Tag minimum.",
         "why": "Naval: 'Read what you love until you love to read.' Compound Knowledge = Compound Wealth.",
         "category": "learning"},
        {"time": "12:45", "block": "DEEP WORK III – SCALE", "duration": "120min",
         "action": "Systeme bauen. Automatisierung. Delegation. SOPs schreiben. Das Geschäft ohne dich möglich machen.",
         "why": "Bezos: 'Your margin is my opportunity.' Skalierung = Marge = Freiheit. Arbeite AM Business, nicht IM Business.",
         "category": "scale"},
        {"time": "14:45", "block": "NETWORKING / SOCIAL", "duration": "60min",
         "action": "2-3 strategische Gespräche. LinkedIn Outreach. Mastermind. Kontakte die 10x deinem Level sind.",
         "why": "Naval: 'Your network is your net worth.' Du bist der Durchschnitt der 5 Menschen um dich.",
         "category": "network"},
        {"time": "15:45", "block": "CONTENT & BRAND", "duration": "75min",
         "action": "1 Content Piece pro Tag. Video, Thread, oder Artikel. Dokumentiere was du lernst/baust.",
         "why": "Hormozi: 'Content is the new cold call.' Musk kommuniziert direkt. Deine Brand IST dein Hebel.",
         "category": "brand"},
        {"time": "17:00", "block": "REVIEW & ITERATE", "duration": "30min",
         "action": "KPIs checken. Was hat heute Impact gehabt? Was war Zeitverschwendung? Morgen anpassen.",
         "why": "Dalio: 'Pain + Reflection = Progress.' Tägliche Iteration schlägt jährliche Planung.",
         "category": "review"},
        {"time": "17:30", "block": "RELATIONSHIPS & LIFE", "duration": "150min",
         "action": "Frame-bewusste Quality Time. Rollo-Prinzipien leben. Sei der Preis. Führe Interaktionen.",
         "why": "Tomassi: 'Your mission comes first.' Aber Beziehungen sind Teil der Mission. Balance durch Frame.",
         "category": "relationships"},
        {"time": "20:00", "block": "EVENING PROTOCOL", "duration": "60min",
         "action": "PTGO Abend-Reflexion. Journaling. Lesen (30min). Screen-Time reduzieren. Schlaf vorbereiten.",
         "why": "Huberman: Licht dimmen 2h vor Schlaf. Melatonin-Produktion. Schlaf = Recovery = Performance.",
         "category": "wind_down"},
        {"time": "21:00", "block": "SLEEP", "duration": "8h",
         "action": "Zimmer kalt (18°C), dunkel, kein Handy. 7-8h durchschlafen. Nicht verhandelbar.",
         "why": "Jeder Top-Performer sagt: Schlaf ist nicht optional. Walker: 'Sleep is the greatest legal performance enhancer.'",
         "category": "sleep"},
    ],
}


# =========================================================
# MODUL 22 – HIGH-INCOME STRATEGIE & FAHRPLAN
# =========================================================

INCOME_STRATEGY = {
    "reality_check": {
        "title": "Reality Check – Die Wahrheit über 5000€/Tag",
        "facts": [
            "5.000€/Tag = 150.000€/Monat = 1.800.000€/Jahr",
            "Das ist Top 0.1% in Deutschland",
            "Es ist MÖGLICH – aber nicht in einer Woche, nicht passiv, nicht ohne extremen Einsatz",
            "Elon Musk hat 12 Jahre gebraucht um seinen ersten großen Exit zu machen",
            "Alex Hormozi hat 3 Jahre gebraucht für $100M Revenue",
            "Naval Ravikant: 'You won't get rich renting out your time. You must own equity.'",
        ],
    },
    "elon_prediction": {
        "title": "Was Elon meint mit 'viele neue Milliardäre'",
        "analysis": [
            "AI-native Businesses: Wer KI als Hebel nutzt, kann mit 1-3 Leuten Firmen bauen die früher 100 brauchten",
            "Robotik + Physical AI: Tesla Optimus, humanoide Roboter – neue Industrien entstehen",
            "xAI + Grok: AI-Tools werden Produktivität 10-100x steigern",
            "Musk's These: Arbeit wird durch AI so produktiv, dass Wertschöpfung pro Person explodiert",
            "ABER: Du musst der BUILDER sein, nicht der Konsument. Builder profitieren, Konsumenten werden ersetzt.",
        ],
    },
    "phases": [
        {
            "phase": "Phase 1: Foundation (Monat 1-3)",
            "target": "500-2.000€/Tag",
            "focus": "High-Value Skill + erstes Angebot",
            "actions": [
                "SKILL: Lerne AI-Tools (Claude, GPT, Midjourney) bis du schneller bist als 99% der Leute",
                "ANGEBOT: Biete AI-Transformation für KMUs an (5.000-15.000€ pro Projekt)",
                "KUNDEN: 100 kalte Nachrichten pro Tag auf LinkedIn. Ohne Ausnahme.",
                "PREIS: Starte bei 3.000€/Projekt. Steigere auf 10.000€ nach 3 Kunden.",
                "CONTENT: 1 LinkedIn Post pro Tag über deine AI-Ergebnisse",
                "MINDSET: Du verkaufst nicht 'AI' – du verkaufst ERGEBNISSE. Zeitersparnis. Kostensenkung. Umsatzsteigerung.",
            ],
            "weekly_kpi": "Mindestens 3 Erstgespräche pro Woche, 1 Abschluss pro Woche",
        },
        {
            "phase": "Phase 2: Scale (Monat 4-12)",
            "target": "2.000-5.000€/Tag",
            "focus": "Systeme + Team + Recurring Revenue",
            "actions": [
                "PRODUCTIZE: Mach dein Angebot wiederholbar. SOPs für alles.",
                "TEAM: Stelle 2-3 Freelancer ein die die Delivery machen",
                "RETAINER: Verkaufe monatliche AI-Betreuung (2.000-5.000€/Monat pro Kunde)",
                "CONTENT: Skaliere auf YouTube + Newsletter. Zeige Case Studies.",
                "LEVERAGE: Nutze AI um 10x so viel zu liefern bei gleicher Zeit",
                "UPSELL: Biete Premium-Pakete an (25.000€+ für große Transformationen)",
            ],
            "weekly_kpi": "10+ aktive Kunden, 50.000€+ Monatsrevenue, 60%+ Marge",
        },
        {
            "phase": "Phase 3: Compound (Jahr 2-3)",
            "target": "5.000-50.000€/Tag",
            "focus": "Equity + Software + Multiple Income Streams",
            "actions": [
                "SAAS: Baue ein AI-Tool das deine beste Dienstleistung automatisiert",
                "EQUITY: Nimm Beteiligungen an statt Cash bei ausgewählten Kunden",
                "FUND: Starte einen kleinen AI-Fonds oder Angel-Investing",
                "BRAND: Du bist jetzt bekannt. Monetisiere die Audience (Kurse, Events, Consulting)",
                "TEAM: 10-20 Leute. Du bist CEO, nicht Operator.",
                "EXIT: Baue verkaufbare Assets (SaaS mit ARR, Content-Imperium, Kundenportfolio)",
            ],
            "weekly_kpi": "7-stelliger Monatsumsatz, multiple Revenue Streams, <20h/Woche operativ",
        },
    ],
    "immediate_actions": {
        "title": "Was du HEUTE tun kannst",
        "today": [
            "Erstelle ein LinkedIn Profil das dich als AI-Experte positioniert",
            "Schreibe 20 personalisierte Nachrichten an Geschäftsführer von KMUs",
            "Lerne ein AI-Tool bis du es besser kannst als 95% der Leute",
            "Erstelle ein einfaches Angebot: 'Ich spare Ihrem Team 10h/Woche mit AI – oder Sie zahlen nichts'",
            "Setze einen Preis fest (mind. 3.000€) und stehe dazu",
        ],
        "this_week": [
            "30 Outreach-Nachrichten pro Tag (Mo-Fr = 150 Kontakte)",
            "3 Discovery Calls buchen",
            "1 Case Study erstellen (auch wenn es ein Eigenprojekt ist)",
            "PTGO Daily Check-in JEDEN Tag – dein State bestimmt dein Income",
            "Abends: 1h Skill-Development (AI, Sales, Marketing)",
        ],
    },
    "leverage_types": {
        "title": "Die 4 Hebel zum Reichtum (nach Naval Ravikant)",
        "levers": [
            {
                "name": "Code (Software)",
                "description": "Software arbeitet 24/7, skaliert unendlich, kostet fast nichts zu duplizieren.",
                "action": "Baue ein SaaS-Tool, eine App, oder automatisiere Prozesse.",
            },
            {
                "name": "Media (Content)",
                "description": "Ein Video kann 10 Millionen Menschen erreichen. Ein Buch kann 50 Jahre verkaufen.",
                "action": "Erstelle täglich Content. Baue eine Audience. Audience = Attention = Money.",
            },
            {
                "name": "Capital (Geld)",
                "description": "Geld arbeiten lassen. Investieren. Compound Interest. Aber du brauchst erst Capital.",
                "action": "Investiere 20% von allem was reinkommt. Ab Tag 1. Auch wenn es 50€ sind.",
            },
            {
                "name": "People (Team)",
                "description": "Ein Team multipliziert deine Kapazität. Du kannst nur 16h/Tag arbeiten. 10 Leute = 160h.",
                "action": "Stelle so früh wie möglich ein. Billige Freelancer zuerst, dann A-Player.",
            },
        ],
    },
}


# =========================================================
# DEV LOGIN (only works when APP_SECRET is default)
# =========================================================

@app.get("/dev-login", response_class=HTMLResponse)
def dev_login(request: Request, db=Depends(get_db)):
    if APP_SECRET != "dev-secret-change-me":
        raise HTTPException(status_code=404)
    patient = db.query(Patient).first()
    if not patient:
        raise HTTPException(status_code=404, detail="No patients in DB")
    request.session["patient_id"] = patient.id
    return RedirectResponse("/mastery", status_code=302)


# =========================================================
# ROLLO TOMASSI + INCOME + TAGESPLAN – ROUTES
# =========================================================

@app.get("/mastery", response_class=HTMLResponse)
def mastery_hub(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)

    # Get today's Rollo rule (rotate daily)
    day_of_year = _now_local().timetuple().tm_yday
    rules = ROLLO_TOMASSI["core_principles"]["iron_rules"]
    todays_rule = rules[day_of_year % len(rules)]

    # Get current schedule block
    now = _now_local()
    current_block = None
    for block in BILLIONAIRE_DAILY_PLAN["schedule"]:
        bh, bm = block["time"].split(":")
        block_time = now.replace(hour=int(bh), minute=int(bm), second=0)
        if now >= block_time:
            current_block = block

    streak = _get_patient_streak(db, p.id)

    body = f"""
      <div style="text-align:center;margin:8px 0 16px">
        <div style="font-size:48px;line-height:1">⚡</div>
        <div style="font-size:11px;color:#6b7280;margin-top:6px;letter-spacing:2px">MASTERY HUB</div>
      </div>
      <h1 style="text-align:center;font-size:24px">Dein Weg zur Meisterschaft</h1>

      <!-- Current Time Block -->
      <div style="background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.3);border-radius:16px;padding:18px;margin:16px 0">
        <div class="small" style="color:#f59e0b;margin-bottom:4px">JETZT – {current_block['time'] if current_block else '--:--'}</div>
        <b style="font-size:18px;color:#fbbf24">{current_block['block'] if current_block else 'Schlaf-Modus'}</b>
        <p style="font-size:13px;margin:8px 0 0">{current_block['action'] if current_block else 'Erhole dich. Morgen wird gebaut.'}</p>
      </div>

      <!-- Today's Iron Rule -->
      <div style="background:rgba(239,68,68,.06);border:1px solid rgba(239,68,68,.25);border-radius:16px;padding:18px;margin:16px 0">
        <div class="small" style="color:#f87171;margin-bottom:4px">IRON RULE DES TAGES</div>
        <b style="font-size:16px;color:#fca5a5">{todays_rule['rule']}</b>
        <p style="font-size:13px;margin:8px 0 4px">{todays_rule['description']}</p>
        <p style="font-size:12px;color:#f59e0b;margin:4px 0 0">→ {todays_rule['daily_practice']}</p>
      </div>

      <!-- Streak -->
      {"<div style='margin:12px 0;padding:12px;background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.2);border-radius:12px;text-align:center'><span style='font-size:24px'>🔥</span> <span style='font-size:18px;font-weight:700;color:#f59e0b'>" + str(streak['current_streak']) + " Tage Streak</span></div>" if streak['current_streak'] >= 2 else ""}

      <!-- Navigation -->
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:16px 0">
        <a href="/mastery/rollo" style="display:block;padding:16px;background:rgba(239,68,68,.06);border:1px solid rgba(239,68,68,.2);border-radius:14px;text-align:center;text-decoration:none">
          <div style="font-size:28px">🔴</div>
          <div style="font-size:14px;font-weight:700;color:#fca5a5;margin-top:6px">Rollo Tomassi</div>
          <div style="font-size:11px;color:#6b7280">Iron Rules & SMV</div>
        </a>
        <a href="/mastery/tagesplan" style="display:block;padding:16px;background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.2);border-radius:14px;text-align:center;text-decoration:none">
          <div style="font-size:28px">📋</div>
          <div style="font-size:14px;font-weight:700;color:#fcd34d;margin-top:6px">Tagesplan</div>
          <div style="font-size:11px;color:#6b7280">Milliardärs-Routine</div>
        </a>
        <a href="/mastery/income" style="display:block;padding:16px;background:rgba(34,197,94,.06);border:1px solid rgba(34,197,94,.2);border-radius:14px;text-align:center;text-decoration:none">
          <div style="font-size:28px">💰</div>
          <div style="font-size:14px;font-weight:700;color:#86efac;margin-top:6px">Income Engine</div>
          <div style="font-size:11px;color:#6b7280">5.000€/Tag Fahrplan</div>
        </a>
        <a href="/mastery/today" style="display:block;padding:16px;background:rgba(99,102,241,.06);border:1px solid rgba(99,102,241,.2);border-radius:14px;text-align:center;text-decoration:none">
          <div style="font-size:28px">🎯</div>
          <div style="font-size:14px;font-weight:700;color:#a5b4fc;margin-top:6px">Heute umsetzen</div>
          <div style="font-size:11px;color:#6b7280">Sofort-Actions</div>
        </a>
      </div>

      <div class="hr"></div>
      <p class="small" style="text-align:center">
        <a href="/checkin/1">Check-in</a> •
        <a href="/insights">Trends</a> •
        <a href="/progress">Progress</a>
      </p>
    """
    return _page("PTGO • Mastery Hub", body, request=request)


@app.get("/mastery/rollo", response_class=HTMLResponse)
def mastery_rollo(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)

    # Iron Rules
    rules_html = ""
    for i, rule in enumerate(ROLLO_TOMASSI["core_principles"]["iron_rules"]):
        rules_html += f"""
        <div style="border:1px solid rgba(239,68,68,.2);border-radius:14px;padding:16px;margin-bottom:12px;background:rgba(239,68,68,.03)">
          <b style="color:#fca5a5;font-size:15px">{rule['rule']}</b>
          <p style="font-size:13px;margin:8px 0 6px">{rule['description']}</p>
          <div style="background:rgba(245,158,11,.08);border-radius:10px;padding:10px;margin-top:8px">
            <span style="font-size:11px;color:#f59e0b;font-weight:600">DAILY PRACTICE:</span>
            <p style="font-size:12px;margin:4px 0 0;color:#fcd34d">{rule['daily_practice']}</p>
          </div>
        </div>
        """

    # Books
    books_html = ""
    for key, book in ROLLO_TOMASSI["books"].items():
        concepts = "".join(f"<li style='font-size:12px;margin:3px 0;color:#94a3b8'>{c}</li>" for c in book["key_concepts"])
        books_html += f"""
        <div style="border:1px solid #1f2937;border-radius:14px;padding:16px;margin-bottom:12px;background:rgba(255,255,255,.02)">
          <b style="color:#e5e7eb;font-size:14px">{book['title']}</b>
          <ul style="margin:8px 0 0;padding-left:18px">{concepts}</ul>
        </div>
        """

    # SMV Pillars
    smv_html = ""
    for key, pillar in ROLLO_TOMASSI["smv_pillars"].items():
        actions = "".join(f"<li style='font-size:12px;margin:3px 0;color:#94a3b8'>{a}</li>" for a in pillar["actions"])
        pct = int(pillar["weight"] * 100)
        smv_html += f"""
        <div style="border:1px solid rgba(99,102,241,.2);border-radius:14px;padding:14px;margin-bottom:10px;background:rgba(99,102,241,.03)">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <b style="color:#a5b4fc;font-size:14px">{pillar['label']}</b>
            <span style="font-size:12px;color:#f59e0b;font-weight:700">{pct}%</span>
          </div>
          <div style="height:4px;background:#1f2937;border-radius:999px;margin:8px 0">
            <div style="height:4px;background:#6366f1;border-radius:999px;width:{pct}%"></div>
          </div>
          <ul style="margin:6px 0 0;padding-left:18px">{actions}</ul>
        </div>
        """

    body = f"""
      <div style="text-align:center;margin:8px 0 16px">
        <div style="font-size:48px;line-height:1">🔴</div>
        <div style="font-size:11px;color:#6b7280;margin-top:6px;letter-spacing:2px">THE RATIONAL MALE</div>
      </div>
      <h1 style="text-align:center;font-size:22px">Rollo Tomassi Framework</h1>
      <p class="small" style="text-align:center">Alle Iron Rules, alle Bücher, das komplette System</p>

      <div class="hr"></div>
      <h2 style="color:#fca5a5">Iron Rules of Tomassi</h2>
      {rules_html}

      <div class="hr"></div>
      <h2 style="color:#a5b4fc">SMV Pillars – Dein Marktwert</h2>
      <p class="small">Sexual Market Value = Summe aus 4 Bereichen. Optimiere alle parallel.</p>
      {smv_html}

      <div class="hr"></div>
      <h2>Bücher & Key Concepts</h2>
      {books_html}

      <div class="hr"></div>
      <p class="small" style="text-align:center">
        <a href="/mastery">← Hub</a> •
        <a href="/mastery/tagesplan">Tagesplan</a> •
        <a href="/mastery/income">Income</a>
      </p>
    """
    return _page("PTGO • Rollo Tomassi", body, request=request)


@app.get("/mastery/tagesplan", response_class=HTMLResponse)
def mastery_tagesplan(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)

    now = _now_local()
    schedule_html = ""
    for block in BILLIONAIRE_DAILY_PLAN["schedule"]:
        bh, bm = block["time"].split(":")
        block_time = now.replace(hour=int(bh), minute=int(bm), second=0)

        # Determine if this block is current, past, or future
        is_current = False
        idx = BILLIONAIRE_DAILY_PLAN["schedule"].index(block)
        next_block = BILLIONAIRE_DAILY_PLAN["schedule"][idx + 1] if idx + 1 < len(BILLIONAIRE_DAILY_PLAN["schedule"]) else None
        if next_block:
            nh, nm = next_block["time"].split(":")
            next_time = now.replace(hour=int(nh), minute=int(nm), second=0)
            is_current = block_time <= now < next_time
        else:
            is_current = now >= block_time

        cat_colors = {
            "health": "#22c55e", "build": "#f59e0b", "revenue": "#ef4444",
            "review": "#6366f1", "communication": "#94a3b8", "learning": "#a855f7",
            "scale": "#ec4899", "network": "#06b6d4", "brand": "#f97316",
            "relationships": "#e879f9", "wind_down": "#6b7280", "sleep": "#334155",
        }
        color = cat_colors.get(block["category"], "#6b7280")
        border = f"2px solid {color}" if is_current else "1px solid #1f2937"
        bg = f"rgba({int(color[1:3],16)},{int(color[3:5],16)},{int(color[5:7],16)},.08)" if is_current else "rgba(255,255,255,.02)"

        schedule_html += f"""
        <div style="border:{border};border-radius:14px;padding:14px;margin-bottom:10px;background:{bg}">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div>
              <span style="font-size:13px;color:{color};font-weight:700">{block['time']}</span>
              <b style="margin-left:10px;font-size:15px">{block['block']}</b>
            </div>
            <span style="font-size:11px;color:#6b7280">{block['duration']}</span>
          </div>
          <p style="font-size:13px;margin:8px 0 4px">{block['action']}</p>
          <p style="font-size:11px;color:#6b7280;margin:0;font-style:italic">{block['why']}</p>
          {'<div style="margin-top:6px;font-size:11px;color:' + color + ';font-weight:700">← DU BIST HIER</div>' if is_current else ''}
        </div>
        """

    sources = "".join(f"<li style='font-size:11px;color:#6b7280;margin:2px 0'>{s}</li>" for s in BILLIONAIRE_DAILY_PLAN["meta"]["based_on"])

    body = f"""
      <div style="text-align:center;margin:8px 0 16px">
        <div style="font-size:48px;line-height:1">📋</div>
        <div style="font-size:11px;color:#6b7280;margin-top:6px;letter-spacing:2px">MILLIARDÄRS-TAGESPLAN</div>
      </div>
      <h1 style="text-align:center;font-size:22px">Der optimale Tag</h1>

      <div style="background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.2);border-radius:14px;padding:14px;margin:12px 0">
        <p style="font-size:13px;color:#fcd34d;margin:0">{BILLIONAIRE_DAILY_PLAN['meta']['core_philosophy']}</p>
      </div>

      <div class="hr"></div>
      {schedule_html}

      <div class="hr"></div>
      <h2>Quellen</h2>
      <ul style="padding-left:18px">{sources}</ul>

      <div class="hr"></div>
      <p class="small" style="text-align:center">
        <a href="/mastery">← Hub</a> •
        <a href="/mastery/rollo">Rollo</a> •
        <a href="/mastery/income">Income</a>
      </p>
    """
    return _page("PTGO • Tagesplan", body, request=request)


@app.get("/mastery/income", response_class=HTMLResponse)
def mastery_income(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)

    # Reality Check
    facts = "".join(f"<li style='font-size:13px;margin:4px 0;color:#94a3b8'>{f}</li>" for f in INCOME_STRATEGY["reality_check"]["facts"])

    # Elon Analysis
    elon = "".join(f"<li style='font-size:13px;margin:4px 0;color:#86efac'>{a}</li>" for a in INCOME_STRATEGY["elon_prediction"]["analysis"])

    # Phases
    phases_html = ""
    phase_colors = ["#f59e0b", "#22c55e", "#6366f1"]
    for i, phase in enumerate(INCOME_STRATEGY["phases"]):
        color = phase_colors[i]
        actions = "".join(f"<li style='font-size:12px;margin:4px 0;color:#94a3b8'>{a}</li>" for a in phase["actions"])
        phases_html += f"""
        <div style="border:1px solid {color}40;border-radius:16px;padding:18px;margin-bottom:14px;background:{color}08">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <b style="color:{color};font-size:16px">{phase['phase']}</b>
            <span style="font-size:14px;font-weight:700;color:{color}">{phase['target']}</span>
          </div>
          <p style="font-size:13px;color:#e5e7eb;margin:8px 0 4px;font-weight:600">{phase['focus']}</p>
          <ul style="padding-left:18px;margin:8px 0">{actions}</ul>
          <div style="background:rgba(255,255,255,.03);border-radius:10px;padding:10px;margin-top:8px">
            <span style="font-size:11px;color:{color};font-weight:600">WEEKLY KPI:</span>
            <span style="font-size:12px;color:#94a3b8"> {phase['weekly_kpi']}</span>
          </div>
        </div>
        """

    # Leverage Types
    levers_html = ""
    lever_icons = ["💻", "📱", "💰", "👥"]
    for i, lever in enumerate(INCOME_STRATEGY["leverage_types"]["levers"]):
        levers_html += f"""
        <div style="border:1px solid #1f2937;border-radius:14px;padding:14px;margin-bottom:10px;background:rgba(255,255,255,.02)">
          <div style="font-size:20px;display:inline">{lever_icons[i]}</div>
          <b style="margin-left:8px;color:#e5e7eb">{lever['name']}</b>
          <p style="font-size:12px;margin:6px 0 4px">{lever['description']}</p>
          <p style="font-size:12px;color:#f59e0b;margin:0">→ {lever['action']}</p>
        </div>
        """

    body = f"""
      <div style="text-align:center;margin:8px 0 16px">
        <div style="font-size:48px;line-height:1">💰</div>
        <div style="font-size:11px;color:#6b7280;margin-top:6px;letter-spacing:2px">INCOME ENGINE</div>
      </div>
      <h1 style="text-align:center;font-size:22px">5.000€/Tag Fahrplan</h1>

      <!-- Reality Check -->
      <div style="border:1px solid rgba(239,68,68,.3);border-radius:16px;padding:18px;margin:16px 0;background:rgba(239,68,68,.05)">
        <h2 style="color:#fca5a5;margin:0 0 8px;font-size:16px">⚠️ {INCOME_STRATEGY['reality_check']['title']}</h2>
        <ul style="padding-left:18px;margin:0">{facts}</ul>
      </div>

      <!-- Elon Prediction -->
      <div style="border:1px solid rgba(34,197,94,.3);border-radius:16px;padding:18px;margin:16px 0;background:rgba(34,197,94,.05)">
        <h2 style="color:#86efac;margin:0 0 8px;font-size:16px">🚀 {INCOME_STRATEGY['elon_prediction']['title']}</h2>
        <ul style="padding-left:18px;margin:0">{elon}</ul>
      </div>

      <div class="hr"></div>
      <h2>Die 4 Hebel zum Reichtum</h2>
      <p class="small">Nach Naval Ravikant – nutze mindestens 2 gleichzeitig</p>
      {levers_html}

      <div class="hr"></div>
      <h2>Der 3-Phasen Fahrplan</h2>
      {phases_html}

      <div class="hr"></div>
      <p class="small" style="text-align:center">
        <a href="/mastery">← Hub</a> •
        <a href="/mastery/today">Sofort-Actions</a> •
        <a href="/mastery/rollo">Rollo</a>
      </p>
    """
    return _page("PTGO • Income Engine", body, request=request)


@app.get("/mastery/today", response_class=HTMLResponse)
def mastery_today(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)

    # Today's actions
    today_actions = "".join(
        f"""<div style="display:flex;align-items:flex-start;gap:10px;padding:12px;border:1px solid #1f2937;border-radius:12px;margin-bottom:8px;background:rgba(255,255,255,.02)">
          <div style="font-size:18px;margin-top:2px">⬜</div>
          <div style="font-size:13px;color:#e5e7eb">{a}</div>
        </div>"""
        for a in INCOME_STRATEGY["immediate_actions"]["today"]
    )

    week_actions = "".join(
        f"""<div style="display:flex;align-items:flex-start;gap:10px;padding:12px;border:1px solid rgba(245,158,11,.2);border-radius:12px;margin-bottom:8px;background:rgba(245,158,11,.03)">
          <div style="font-size:18px;margin-top:2px">📌</div>
          <div style="font-size:13px;color:#e5e7eb">{a}</div>
        </div>"""
        for a in INCOME_STRATEGY["immediate_actions"]["this_week"]
    )

    # Today's Rollo rule
    day_of_year = _now_local().timetuple().tm_yday
    rules = ROLLO_TOMASSI["core_principles"]["iron_rules"]
    todays_rule = rules[day_of_year % len(rules)]

    # AI Coaching for today
    coaching_prompt = ""
    if ANTHROPIC_API_KEY:
        last_checkin = db.query(CheckIn).filter(
            CheckIn.patient_id == p.id
        ).order_by(CheckIn.created_at.desc()).first()

        if last_checkin:
            try:
                prompt = (
                    f"Du bist ein Coach der Rollo Tomassi's Prinzipien kennt UND ein Business-Mentor ist.\n"
                    f"Der Nutzer hat folgende Daten:\n"
                    f"- Recovery Score: {last_checkin.score}/100\n"
                    f"- Stress: {last_checkin.stress}/10\n"
                    f"- Schlaf: {last_checkin.sleep}/10\n"
                    f"- Pattern: {last_checkin.pattern_label}\n\n"
                    f"Heute gilt Iron Rule: {todays_rule['rule']}\n\n"
                    f"Gib ihm einen persönlichen Tages-Impuls (3 Sätze) der:\n"
                    f"1. Seinen aktuellen State berücksichtigt\n"
                    f"2. Die heutige Iron Rule integriert\n"
                    f"3. Einen konkreten Business/Income Tipp gibt\n"
                    f"Deutsch. Direkt. Kein Gelaber."
                )
                resp = requests.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": "claude-haiku-4-5", "max_tokens": 250, "messages": [{"role": "user", "content": prompt}]},
                    timeout=15,
                )
                resp.raise_for_status()
                resp_data = resp.json()
                _track_ai_usage("mastery_today", resp_data, patient_id=p.id)
                coaching_prompt = resp_data["content"][0]["text"].strip()
            except Exception as e:
                _track_ai_error("mastery_today", str(e), patient_id=p.id)
                print("[WARN] Today coaching failed:", e)

    coaching_html = ""
    if coaching_prompt:
        coaching_html = f"""
        <div style="background:rgba(99,102,241,.06);border:1px solid rgba(99,102,241,.25);border-radius:16px;padding:18px;margin:16px 0">
          <div class="small" style="color:#a5b4fc;margin-bottom:4px">AI COACHING – DEIN TAGES-IMPULS</div>
          <p style="font-size:14px;line-height:1.7;color:#e5e7eb;margin:0">{coaching_prompt}</p>
        </div>
        """

    body = f"""
      <div style="text-align:center;margin:8px 0 16px">
        <div style="font-size:48px;line-height:1">🎯</div>
        <div style="font-size:11px;color:#6b7280;margin-top:6px;letter-spacing:2px">HEUTE UMSETZEN</div>
      </div>
      <h1 style="text-align:center;font-size:22px">Dein Action Plan für heute</h1>

      {coaching_html}

      <!-- Today's Iron Rule -->
      <div style="background:rgba(239,68,68,.06);border:1px solid rgba(239,68,68,.2);border-radius:14px;padding:14px;margin:12px 0">
        <div class="small" style="color:#f87171;margin-bottom:4px">IRON RULE</div>
        <b style="font-size:14px;color:#fca5a5">{todays_rule['rule']}</b>
        <p style="font-size:12px;margin:4px 0 0;color:#f59e0b">→ {todays_rule['daily_practice']}</p>
      </div>

      <div class="hr"></div>
      <h2>Sofort-Actions für heute</h2>
      {today_actions}

      <div class="hr"></div>
      <h2>Diese Woche</h2>
      {week_actions}

      <div class="hr"></div>
      <div style="background:rgba(34,197,94,.06);border:1px solid rgba(34,197,94,.2);border-radius:14px;padding:14px;text-align:center">
        <p style="font-size:14px;color:#86efac;margin:0;font-weight:600">
          "Specific knowledge + leverage + judgment = wealth"
        </p>
        <p class="small" style="margin:6px 0 0">— Naval Ravikant</p>
      </div>

      <div class="hr"></div>
      <p class="small" style="text-align:center">
        <a href="/mastery">← Hub</a> •
        <a href="/mastery/income">Income</a> •
        <a href="/checkin/1">Check-in</a>
      </p>
    """
    return _page("PTGO • Heute", body, request=request)


# =========================================================
# MUSIC ANALYZER — 80er Rock Style Fusion Engine
# =========================================================

EIGHTIES_BANDS = {
    "guns_n_roses": {
        "name": "Guns N' Roses",
        "era": "1985–1993",
        "style": "Hard Rock / Blues Rock / Punk-infused Rock",
        "traits": [
            "Aggressive Riffs mit Blues-Bending (Slash)",
            "Raw, emotionale Vocals mit großem Stimmumfang (Axl Rose)",
            "Epische Song-Strukturen (November Rain, Estranged)",
            "Punk-Energie trifft klassischen Rock",
            "Storytelling-Texte über Straßenleben und Liebe",
            "Doppel-Gitarren-Harmonien",
            "Dynamik von leise/akustisch zu laut/verzerrrt",
        ],
        "key_songs": ["Welcome to the Jungle", "Sweet Child O' Mine", "Paradise City", "November Rain", "Patience"],
    },
    "def_leppard": {
        "name": "Def Leppard",
        "era": "1980–1992",
        "style": "Arena Rock / Pop Metal / Glam Rock",
        "traits": [
            "Multi-layered Vocal-Harmonien",
            "Hochglanz-Produktion (Mutt Lange)",
            "Eingängige Hooks und Refrains",
            "Gitarren-Riffs mit Pop-Sensibilität",
            "Backing-Vocal-Walls",
        ],
        "key_songs": ["Pour Some Sugar on Me", "Hysteria", "Photograph", "Love Bites", "Rock of Ages"],
    },
    "bon_jovi": {
        "name": "Bon Jovi",
        "era": "1984–1992",
        "style": "Arena Rock / Pop Rock / Heartland Rock",
        "traits": [
            "Anthemische Refrains zum Mitsingen",
            "Motivierende, positive Texte",
            "Akustik-Elemente in Hard Rock",
            "Talk-Box-Gitarre (Richie Sambora)",
            "Brücke zwischen Pop und Rock",
        ],
        "key_songs": ["Livin' on a Prayer", "You Give Love a Bad Name", "Wanted Dead or Alive", "Bad Medicine"],
    },
    "motley_crue": {
        "name": "Mötley Crüe",
        "era": "1981–1989",
        "style": "Glam Metal / Heavy Metal / Sleaze Rock",
        "traits": [
            "Party-Rock-Attitude",
            "Schwere, down-tuned Riffs",
            "Provokante Texte und Image",
            "Groovige Drum-Patterns (Tommy Lee)",
            "Mischung aus Punk-Schnelligkeit und Metal-Schwere",
        ],
        "key_songs": ["Dr. Feelgood", "Girls, Girls, Girls", "Kickstart My Heart", "Home Sweet Home"],
    },
    "van_halen": {
        "name": "Van Halen",
        "era": "1978–1988",
        "style": "Hard Rock / Heavy Metal / Pop Rock",
        "traits": [
            "Revolutionäres Tapping-Gitarrenspiel (Eddie Van Halen)",
            "Virtuose Instrumentalpassagen",
            "Fun-Rock mit Party-Vibes",
            "Keyboard-Integration ab 1984",
            "Energetische Live-Performance",
        ],
        "key_songs": ["Jump", "Panama", "Hot for Teacher", "Eruption", "Ain't Talkin' 'Bout Love"],
    },
    "ac_dc": {
        "name": "AC/DC",
        "era": "1973–heute",
        "style": "Hard Rock / Blues Rock",
        "traits": [
            "Kompromisslos einfache, kraftvolle Riffs",
            "Roh und direkt — keine Überproduktion",
            "Boogie-Blues-Grundlage",
            "Ikonische Gitarren-Sounds (Angus Young)",
            "Call-and-Response Strukturen",
        ],
        "key_songs": ["Back in Black", "Highway to Hell", "Thunderstruck", "T.N.T.", "You Shook Me All Night Long"],
    },
    "metallica": {
        "name": "Metallica",
        "era": "1983–heute",
        "style": "Thrash Metal / Heavy Metal",
        "traits": [
            "Komplexe Song-Strukturen",
            "Down-Picking Technik (James Hetfield)",
            "Progressive Arrangements",
            "Dynamik von clean zu brutal heavy",
            "Sozialkritische und introspektive Texte",
        ],
        "key_songs": ["Master of Puppets", "One", "Enter Sandman", "Fade to Black", "Nothing Else Matters"],
    },
    "iron_maiden": {
        "name": "Iron Maiden",
        "era": "1980–heute",
        "style": "Heavy Metal / New Wave of British Heavy Metal",
        "traits": [
            "Galoppierende Bass-Lines (Steve Harris)",
            "Doppel-Gitarren-Harmonien",
            "Epische, literarische Texte",
            "Operatische Vocals (Bruce Dickinson)",
            "Progessive Song-Längen",
        ],
        "key_songs": ["The Trooper", "Run to the Hills", "Hallowed Be Thy Name", "Fear of the Dark", "Aces High"],
    },
}


def _analyze_music_with_ai(song_links: List[str], selected_bands: List[str]) -> Dict[str, Any]:
    """Use Claude AI to analyze songs and create a fusion with 80s rock styles."""
    if not ANTHROPIC_API_KEY:
        return {
            "error": "Kein ANTHROPIC_API_KEY konfiguriert. AI-Analyse nicht verfügbar.",
            "fusion_name": "",
            "fusion_description": "",
            "style_analysis": "",
            "songwriting_tips": [],
            "influences": [],
        }

    band_descriptions = []
    for band_key in selected_bands:
        band = EIGHTIES_BANDS.get(band_key)
        if band:
            band_descriptions.append(
                f"**{band['name']}** ({band['era']}) — {band['style']}\n"
                f"Traits: {', '.join(band['traits'])}\n"
                f"Key Songs: {', '.join(band['key_songs'])}"
            )

    bands_text = "\n\n".join(band_descriptions) if band_descriptions else "Alle 80er Bands als Referenz."

    links_text = "\n".join(f"- {link}" for link in song_links)

    prompt = f"""Du bist ein Musikproduzent und Songwriter-Coach, spezialisiert auf Rock-Musik der 80er Jahre.

Der Nutzer hat folgende Song-Links geteilt:
{links_text}

Er möchte seinen Sound mit folgenden 80er-Bands fusionieren:
{bands_text}

Bitte analysiere die Songs anhand der Links (Titel, Künstler, vermuteter Stil basierend auf den URLs/Titeln) und erstelle:

1. **STYLE-ANALYSE**: Analysiere den vermuteten Stil der geteilten Songs (basierend auf Songtitel, Künstlername aus den URLs). Beschreibe Tempo, Energie, mögliche Instrumente, Stimmung.

2. **FUSION-NAME**: Erfinde einen einzigartigen Genre-Namen für die Fusion des Nutzer-Stils mit den gewählten 80er-Bands (z.B. "Neon Thunder Rock", "Sunset Rebellion Metal").

3. **FUSION-BESCHREIBUNG**: Beschreibe in 3-4 Sätzen wie dieser neue Fusion-Sound klingen würde. Sei kreativ und konkret.

4. **SONGWRITING-TIPPS**: Gib 5 konkrete, umsetzbare Songwriting-Tipps, wie der Nutzer seinen Sound in Richtung dieser Fusion weiterentwickeln kann. Jeder Tipp soll eine konkrete Technik oder Übung enthalten.

5. **EINFLUSS-MAP**: Liste die Top-3 spezifischen Elemente auf, die der Nutzer von jeder gewählten Band übernehmen sollte.

Antworte NUR mit validem JSON (keine Erklärung davor/danach):
{{
  "style_analysis": "...",
  "fusion_name": "...",
  "fusion_description": "...",
  "songwriting_tips": ["Tipp 1", "Tipp 2", "Tipp 3", "Tipp 4", "Tipp 5"],
  "influences": [
    {{"band": "Bandname", "elements": ["Element 1", "Element 2", "Element 3"]}}
  ]
}}
"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5",
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        resp_data = resp.json()
        _track_ai_usage("music_analysis", resp_data)
        text = resp_data["content"][0]["text"].strip()
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        _track_ai_error("music_analysis", str(e))
        print(f"[WARN] Music AI analysis failed: {e}")
        return {
            "error": f"AI-Analyse fehlgeschlagen: {e}",
            "fusion_name": "",
            "fusion_description": "",
            "style_analysis": "",
            "songwriting_tips": [],
            "influences": [],
        }


@app.get("/music", response_class=HTMLResponse)
async def music_analyzer_page(request: Request):
    """Music Analyzer — Hauptseite mit Link-Eingabe und Band-Auswahl."""
    band_cards = ""
    for key, band in EIGHTIES_BANDS.items():
        songs = ", ".join(band["key_songs"][:3])
        band_cards += f"""
        <label style="display:flex;align-items:flex-start;gap:10px;padding:12px;border:1px solid var(--line);border-radius:14px;margin-bottom:8px;cursor:pointer;background:rgba(255,255,255,.02);">
          <input type="checkbox" name="bands" value="{key}" style="width:auto;margin-top:4px;"
                 {"checked" if key == "guns_n_roses" else ""}>
          <div>
            <b style="color:#f3f4f6">{band['name']}</b>
            <span class="small"> — {band['style']}</span><br>
            <span class="small">{songs}</span>
          </div>
        </label>
        """

    body = f"""
      <h1>🎸 Music Analyzer</h1>
      <p>Lade deine Songs (Spotify/YouTube Links) und lass deinen Sound mit den größten 80er-Bands fusionieren.</p>
      <div class="hr"></div>

      <form method="post" action="/music/analyze">
        <h2>Deine Songs</h2>
        <p class="small">Füge Spotify- oder YouTube-Links ein (ein Link pro Zeile)</p>
        <textarea name="song_links" rows="6" placeholder="https://open.spotify.com/track/...&#10;https://www.youtube.com/watch?v=...&#10;https://open.spotify.com/track/..." style="font-size:14px;"></textarea>

        <div class="hr"></div>

        <h2>80er Bands für Fusion</h2>
        <p class="small">Wähle die Bands aus, mit denen dein Sound fusioniert werden soll</p>
        {band_cards}

        <div style="height:12px"></div>
        <button type="submit">🔥 Sound analysieren & fusionieren</button>
      </form>

      <div style="height:20px"></div>
      <p class="small" style="text-align:center">Powered by PTGO • AI Music Engine</p>
    """
    return _page("Music Analyzer — 80er Rock Fusion", body, request=request)


@app.post("/music/analyze", response_class=HTMLResponse)
async def music_analyze(request: Request, song_links: str = Form(""), bands: List[str] = Form([])):
    """Analyze submitted songs and generate fusion results."""
    links = [l.strip() for l in song_links.strip().split("\n") if l.strip()]

    if not links:
        body = """
          <h1>⚠️ Keine Songs</h1>
          <p>Bitte füge mindestens einen Spotify- oder YouTube-Link ein.</p>
          <a href="/music" class="btn" style="display:inline-block;margin-top:16px;">← Zurück</a>
        """
        return _page("Music Analyzer", body, request=request)

    if not bands:
        bands = list(EIGHTIES_BANDS.keys())

    result = _analyze_music_with_ai(links, bands)

    if result.get("error"):
        error_body = f"""
          <h1>⚠️ Analyse-Fehler</h1>
          <p>{result['error']}</p>
          <a href="/music" class="btn" style="display:inline-block;margin-top:16px;">← Zurück</a>
        """
        return _page("Music Analyzer", error_body, request=request)

    # Build influence map HTML
    influences_html = ""
    for inf in result.get("influences", []):
        elements = "".join(f"<li>{e}</li>" for e in inf.get("elements", []))
        influences_html += f"""
        <div style="border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:10px;background:rgba(255,255,255,.02);">
          <b style="color:#f59e0b">{inf.get('band', '')}</b>
          <ul style="margin:8px 0 0;padding-left:20px;color:var(--muted)">{elements}</ul>
        </div>
        """

    # Build tips HTML
    tips_html = ""
    for i, tip in enumerate(result.get("songwriting_tips", []), 1):
        tips_html += f"""
        <div style="border:1px solid rgba(245,158,11,.3);border-radius:14px;padding:14px;margin-bottom:10px;background:rgba(245,158,11,.05);">
          <b style="color:#f59e0b">Tipp {i}</b>
          <p style="margin:6px 0 0">{tip}</p>
        </div>
        """

    # Build links display
    links_display = "".join(
        f'<div class="tag" style="margin-bottom:4px;word-break:break-all;">{l[:60]}{"..." if len(l) > 60 else ""}</div><br>'
        for l in links
    )

    # Selected bands display
    selected_bands_display = " ".join(
        f'<span class="pattern-tag">{EIGHTIES_BANDS[b]["name"]}</span>'
        for b in bands if b in EIGHTIES_BANDS
    )

    body = f"""
      <h1>🎸 {result.get('fusion_name', 'Dein neuer Sound')}</h1>
      <p style="color:#f59e0b;font-size:18px;font-weight:600">{result.get('fusion_description', '')}</p>

      <div class="hr"></div>

      <div style="margin-bottom:12px">
        <span class="small">Analysierte Songs:</span><br>
        {links_display}
      </div>
      <div style="margin-bottom:12px">
        <span class="small">Fusioniert mit:</span><br>
        {selected_bands_display}
      </div>

      <div class="hr"></div>

      <h2>🎵 Style-Analyse</h2>
      <div class="card" style="margin-bottom:16px;">
        <p>{result.get('style_analysis', 'Keine Analyse verfügbar.')}</p>
      </div>

      <h2>🔥 Songwriting-Tipps</h2>
      {tips_html}

      <div class="hr"></div>

      <h2>🎯 Einfluss-Map</h2>
      <p class="small">Übernimm diese Elemente von jeder Band:</p>
      {influences_html}

      <div class="hr"></div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <a href="/music" class="btn btn-outline" style="text-align:center;">← Neue Analyse</a>
        <a href="/" class="btn btn-outline" style="text-align:center;">Home</a>
      </div>

      <div style="height:20px"></div>
      <p class="small" style="text-align:center">Powered by PTGO • AI Music Engine</p>
    """
    return _page(f"Music Fusion — {result.get('fusion_name', 'Ergebnis')}", body, request=request)


# =========================================================
# ZEIS PROTOCOL — 18 Schmerzverzerrungen & Self-Treatment
# =========================================================

ZEIS_TYPES = {
    "katastrophisieren": {
        "nr": 1,
        "name": "Katastrophisieren",
        "short": "Schmerz wird zur Katastrophe aufgeblasen",
        "desc": "Du erwartest immer das Schlimmste. Ein leichtes Ziehen wird in deinem Kopf zum Bandscheibenvorfall. Dein Nervensystem lernt: Gefahr ist überall.",
        "body_zones": ["kopf", "nacken", "unterer_ruecken"],
        "signal": "Gedanken wie: 'Das wird nie aufhören', 'Da ist bestimmt was Schlimmes'",
        "reframe": "Schmerz ist ein Signal, keine Diagnose. Die meisten Schmerzen sind vorübergehend und ungefährlich.",
        "protocol": [
            {"min": 0, "text": "Setz dich hin. Atme 3× tief. Benenne den Schmerz auf einer Skala 1–10."},
            {"min": 2, "text": "Frage dich: Was ist die wahrscheinlichste Erklärung? Nicht die schlimmste."},
            {"min": 4, "text": "Erinnere dich: Wie oft hattest du diesen Schmerz schon — und er ging weg?"},
            {"min": 6, "text": "Lege eine Hand auf die schmerzende Stelle. Sage: 'Ich bin sicher. Das geht vorbei.'"},
            {"min": 8, "text": "Bewege dich sanft. Steh auf, gehe 10 Schritte. Dein Körper ist belastbar."},
        ],
    },
    "hypervigilanz": {
        "nr": 2,
        "name": "Hypervigilanz",
        "short": "Ständige Körperbeobachtung verstärkt Schmerz",
        "desc": "Du scannst deinen Körper permanent nach Signalen. Jedes Zwicken wird registriert, analysiert, bewertet. Das Nervensystem bleibt im Alarmmodus.",
        "body_zones": ["kopf", "brust", "bauch"],
        "signal": "Ständiges 'Reinspüren', Angst vor neuen Symptomen, Gesundheits-Googlen",
        "reframe": "Aufmerksamkeit verstärkt Empfindung. Weniger Beobachten = weniger Schmerz.",
        "protocol": [
            {"min": 0, "text": "Erkenne den Scan-Modus: 'Ich beobachte gerade meinen Körper zu stark.'"},
            {"min": 2, "text": "Lenke die Aufmerksamkeit nach außen: Benenne 5 Dinge die du siehst."},
            {"min": 4, "text": "4 Dinge die du hörst. 3 die du fühlst (Oberfläche, nicht innen)."},
            {"min": 6, "text": "Gib dir eine Aufgabe: Räume etwas auf, schreibe eine Nachricht, koche."},
            {"min": 8, "text": "Vereinbare mit dir: Nächster Body-Check erst in 2 Stunden. Timer stellen."},
        ],
    },
    "bewegungsangst": {
        "nr": 3,
        "name": "Bewegungsangst (Kinesiophobie)",
        "short": "Angst vor Bewegung führt zu mehr Schmerz",
        "desc": "Du vermeidest Bewegung aus Angst, dir zu schaden. Aber Inaktivität schwächt Muskeln, versteift Gelenke und macht dich empfindlicher.",
        "body_zones": ["unterer_ruecken", "knie", "schulter"],
        "signal": "Vermeidung von Sport, Treppen, Heben. Gedanken: 'Das macht es schlimmer'",
        "reframe": "Bewegung ist Medizin. Dein Körper ist für Bewegung gebaut, nicht für Stillstand.",
        "protocol": [
            {"min": 0, "text": "Steh auf. Jetzt. Keine Diskussion mit dem Kopf."},
            {"min": 1, "text": "Hebe die Arme über den Kopf. Halte 10 Sekunden. Spüre die Kraft."},
            {"min": 3, "text": "Mache 5 sanfte Kniebeugen. Langsam. Dein Körper hält das aus."},
            {"min": 5, "text": "Gehe 2 Minuten durch den Raum. Spüre den Boden unter den Füßen."},
            {"min": 8, "text": "Notiere: Was war so schlimm? Meistens: nichts. Bewegung = Sicherheit."},
        ],
    },
    "schmerz_identitaet": {
        "nr": 4,
        "name": "Schmerzidentität",
        "short": "Schmerz wird Teil deiner Identität",
        "desc": "Du definierst dich über deinen Schmerz: 'Ich bin Schmerzpatient.' Das zementiert neuronale Muster und macht Heilung schwerer.",
        "body_zones": ["kopf", "unterer_ruecken", "ganzer_koerper"],
        "signal": "'Ich habe schon immer...', 'Bei mir ist das chronisch', Diagnosen als Identität",
        "reframe": "Du HAST Schmerz, du BIST nicht dein Schmerz. Dein Gehirn kann umlernen.",
        "protocol": [
            {"min": 0, "text": "Schreibe auf: Wie beschreibst du dich selbst? Welche Rolle spielt Schmerz darin?"},
            {"min": 3, "text": "Streiche Sätze mit 'immer', 'nie', 'chronisch'. Ersetze durch 'gerade', 'aktuell'."},
            {"min": 5, "text": "Schreibe 3 Sätze über dich OHNE Schmerz. Wer bist du sonst noch?"},
            {"min": 7, "text": "Neuer Satz: 'Ich bin jemand, der lernt, anders mit Empfindungen umzugehen.'"},
            {"min": 9, "text": "Lies dir alle 3 Sätze laut vor. Das ist der neue Grundton."},
        ],
    },
    "emotionale_unterdrückung": {
        "nr": 5,
        "name": "Emotionale Unterdrückung",
        "short": "Unterdrückte Emotionen werden zu Körperschmerz",
        "desc": "Was du nicht fühlen willst, fühlt dein Körper. Wut, Trauer, Scham — sie suchen sich einen Ausweg. Oft als Rücken-, Nacken- oder Bauchschmerz.",
        "body_zones": ["nacken", "unterer_ruecken", "bauch", "schulter"],
        "signal": "Schmerz ohne klare Ursache, verstärkt bei Stress, nach Konflikten",
        "reframe": "Dein Körper speichert, was du nicht aussprichst. Fühlen ist der Weg zur Auflösung.",
        "protocol": [
            {"min": 0, "text": "Setz dich still hin. Schließe die Augen. Frage: Was fühle ich WIRKLICH gerade?"},
            {"min": 2, "text": "Benenne die Emotion. Nicht den Schmerz — die Emotion dahinter. Wut? Trauer? Angst?"},
            {"min": 4, "text": "Wo sitzt diese Emotion im Körper? Lege die Hand dorthin."},
            {"min": 6, "text": "Sage laut: 'Ich erlaube mir, das zu fühlen. Es ist sicher.'"},
            {"min": 8, "text": "Atme 5× tief in diese Stelle. Lass zu, was kommt. Auch Tränen."},
        ],
    },
    "schwarz_weiss": {
        "nr": 6,
        "name": "Schwarz-Weiß-Denken",
        "short": "Entweder schmerzfrei oder kaputt — kein Dazwischen",
        "desc": "Du kennst nur 0 oder 100. Entweder geht es dir gut oder du bist 'am Ende'. Nuancen gehen verloren — und damit die Fähigkeit, Fortschritt zu sehen.",
        "body_zones": ["kopf", "ganzer_koerper"],
        "signal": "'Es bringt nichts', 'Entweder ganz oder gar nicht', Aufgeben bei Rückschlägen",
        "reframe": "Heilung ist ein Spektrum. 10% besser ist besser. Jeder kleine Schritt zählt.",
        "protocol": [
            {"min": 0, "text": "Wie geht es dir gerade? Nicht 'gut' oder 'schlecht'. Gib eine Zahl: 1–10."},
            {"min": 2, "text": "Vergleiche mit letzter Woche. Gibt es einen Unterschied — egal wie klein?"},
            {"min": 4, "text": "Schreibe 3 Dinge auf, die heute besser sind als vor einem Monat."},
            {"min": 6, "text": "Erkenne: Perfekt gibt es nicht. 'Gut genug' ist das Ziel."},
            {"min": 8, "text": "Neuer Satz: 'Ich bin auf dem Weg. Nicht am Ziel — und das ist okay.'"},
        ],
    },
    "nocebo": {
        "nr": 7,
        "name": "Nocebo-Effekt",
        "short": "Negative Erwartung erzeugt echten Schmerz",
        "desc": "Wenn du erwartest, dass etwas wehtut, tut es weh. Dein Gehirn produziert Schmerz auf Basis von Erwartung, nicht von Gewebeschaden.",
        "body_zones": ["kopf", "unterer_ruecken", "nacken"],
        "signal": "Schmerz bei bestimmten Bewegungen die 'angeblich' gefährlich sind, Angst vor dem MRT-Befund",
        "reframe": "Erwartung formt Erfahrung. Ändere die Erwartung — ändere den Schmerz.",
        "protocol": [
            {"min": 0, "text": "Welche Bewegung oder Situation erwartest du als schmerzhaft? Benenne sie."},
            {"min": 2, "text": "Frage: Woher kommt diese Erwartung? Arzt? Google? Eigene Erfahrung?"},
            {"min": 4, "text": "Neues Experiment: Führe die Bewegung langsam aus. Beobachte ohne Urteil."},
            {"min": 6, "text": "War es so schlimm wie erwartet? Meistens: Nein."},
            {"min": 8, "text": "Wiederhole morgen. Erwartung umschreiben: 'Es könnte auch leicht gehen.'"},
        ],
    },
    "hilflosigkeit": {
        "nr": 8,
        "name": "Erlernte Hilflosigkeit",
        "short": "Glaube, dass nichts hilft — also tust du nichts",
        "desc": "Du hast so oft gehört 'damit müssen Sie leben', dass du es glaubst. Aber dein Nervensystem ist plastisch. Es kann umlernen.",
        "body_zones": ["ganzer_koerper"],
        "signal": "'Nichts hilft', 'Ich habe alles versucht', passive Haltung, Therapie-Hopping",
        "reframe": "Du bist nicht hilflos. Du hast nur noch nicht den richtigen Hebel gefunden.",
        "protocol": [
            {"min": 0, "text": "Schreibe eine Liste: Was hast du alles schon versucht?"},
            {"min": 3, "text": "Markiere ehrlich: Was davon hast du wirklich konsequent durchgezogen (>4 Wochen)?"},
            {"min": 5, "text": "Wähle EINE Sache, die du ab heute 21 Tage durchziehst. Nur eine."},
            {"min": 7, "text": "Plane konkret: Wann? Wo? Wie lange? Schreibe es auf."},
            {"min": 9, "text": "Starte JETZT. Nicht morgen. Die erste Minute zählt am meisten."},
        ],
    },
    "soziale_isolation": {
        "nr": 9,
        "name": "Soziale Isolation",
        "short": "Rückzug verstärkt Schmerz und Depression",
        "desc": "Schmerz macht einsam. Du sagst Treffen ab, bleibst zuhause, ziehst dich zurück. Aber Isolation verstärkt Schmerz — soziale Verbindung lindert ihn.",
        "body_zones": ["brust", "bauch", "kopf"],
        "signal": "Absagen, Rückzug, 'Die verstehen das nicht', Einsamkeitsgefühl",
        "reframe": "Verbindung ist ein Schmerzmittel. Menschen in deiner Nähe aktivieren dein Sicherheitssystem.",
        "protocol": [
            {"min": 0, "text": "Wem hast du zuletzt abgesagt? Schreibe den Namen auf."},
            {"min": 2, "text": "Schreibe dieser Person JETZT eine kurze Nachricht. Nur ein 'Hey, wie geht's?'"},
            {"min": 4, "text": "Plane ein Treffen diese Woche. Kurz reicht. 30 Minuten Kaffee."},
            {"min": 6, "text": "Bereite einen Satz vor: 'Mir geht es gerade nicht so gut, aber ich bin froh, hier zu sein.'"},
            {"min": 8, "text": "Erlaube dir, nicht zu funktionieren. Einfach DA sein ist genug."},
        ],
    },
    "perfektionismus": {
        "nr": 10,
        "name": "Schmerz-Perfektionismus",
        "short": "Alles muss perfekt sein — auch die Heilung",
        "desc": "Du willst den perfekten Therapeuten, die perfekte Übung, das perfekte Protokoll. Aber Perfektionismus ist Vermeidung in Verkleidung.",
        "body_zones": ["nacken", "schulter", "kopf"],
        "signal": "'Erst wenn ich den richtigen Arzt finde', endloses Recherchieren, nie anfangen",
        "reframe": "Unperfektes Handeln schlägt perfektes Planen. Starte mit 'gut genug'.",
        "protocol": [
            {"min": 0, "text": "Was schiebst du auf, weil es 'noch nicht perfekt' ist?"},
            {"min": 2, "text": "Mache es jetzt — aber nur zu 70%. Bewusst unperfekt."},
            {"min": 4, "text": "Beobachte: Passiert etwas Schlimmes? Meistens: Nein."},
            {"min": 6, "text": "Schreibe auf: 'Done is better than perfect.'"},
            {"min": 8, "text": "Wiederhole morgen. Perfektionismus ist ein Muskel, der durch Nicht-Benutzen schrumpft."},
        ],
    },
    "gedankenkreisen": {
        "nr": 11,
        "name": "Gedankenkreisen (Rumination)",
        "short": "Endlosschleife von Schmerzgedanken",
        "desc": "Dein Kopf dreht sich im Kreis: Warum ich? Was wenn? Was habe ich falsch gemacht? Rumination hält dein Nervensystem im Schmerzmodus.",
        "body_zones": ["kopf", "nacken", "brust"],
        "signal": "Grübeln, Schlafprobleme, gleiche Gedanken immer wieder, 'Was wenn...'",
        "reframe": "Gedanken sind keine Fakten. Du kannst die Schleife unterbrechen.",
        "protocol": [
            {"min": 0, "text": "Erkenne die Schleife: 'Ich grüble gerade.' Sage es laut."},
            {"min": 1, "text": "Steh auf. Bewege dich. Wasche dir die Hände mit kaltem Wasser."},
            {"min": 3, "text": "Zähle rückwärts von 100 in 7er-Schritten: 100, 93, 86..."},
            {"min": 5, "text": "Dein Gehirn kann nicht gleichzeitig rechnen UND grübeln."},
            {"min": 7, "text": "Schreibe den Grübel-Gedanken auf Papier. Einmal. Dann weg damit. Er ist raus."},
        ],
    },
    "vergleich": {
        "nr": 12,
        "name": "Sozialer Vergleich",
        "short": "Andere haben es leichter — du hast Pech",
        "desc": "Du vergleichst dich mit Gesunden und fühlst dich benachteiligt. Oder mit anderen Schmerzpatienten — und dir geht es 'noch schlimmer'.",
        "body_zones": ["brust", "kopf"],
        "signal": "'Warum ich?', Neid auf Gesunde, Social-Media-Vermeidung oder -Sucht",
        "reframe": "Dein Weg ist deiner. Vergleich stiehlt Energie, die du für Heilung brauchst.",
        "protocol": [
            {"min": 0, "text": "Wann hast du dich zuletzt mit jemandem verglichen? Wer war es?"},
            {"min": 2, "text": "Was genau hast du verglichen? Gesundheit? Erfolg? Lebensfreude?"},
            {"min": 4, "text": "Du siehst 5% von deren Leben. Die anderen 95% kennst du nicht."},
            {"min": 6, "text": "Schreibe 3 Dinge auf, die DU kannst oder hast, trotz allem."},
            {"min": 8, "text": "Neuer Fokus: 'Ich vergleiche mich nur mit meinem gestrigen Ich.'"},
        ],
    },
    "uebergeneralisierung": {
        "nr": 13,
        "name": "Übergeneralisierung",
        "short": "Ein schlechter Tag = alles ist schlecht",
        "desc": "Ein Rückfall, ein schlechter Tag — und du schließt: 'Es wird nie besser.' Ein einzelnes Ereignis wird zum Gesamturteil.",
        "body_zones": ["ganzer_koerper"],
        "signal": "'Immer', 'nie', 'jedes Mal', Rückschlag = Beweis für Hoffnungslosigkeit",
        "reframe": "Ein schlechter Tag ist ein schlechter Tag. Nicht ein schlechtes Leben.",
        "protocol": [
            {"min": 0, "text": "Was ist heute passiert, das dich runterzieht?"},
            {"min": 2, "text": "Ist es WIRKLICH 'immer' so? Oder gab es auch gute Tage letzte Woche?"},
            {"min": 4, "text": "Ersetze 'immer' durch 'heute'. Ersetze 'nie' durch 'gerade nicht'."},
            {"min": 6, "text": "Schreibe 1 guten Moment der letzten 7 Tage auf. Er existiert."},
            {"min": 8, "text": "Morgen ist ein neuer Tag. Dieser hier definiert nicht alle anderen."},
        ],
    },
    "kontrollzwang": {
        "nr": 14,
        "name": "Kontrollzwang",
        "short": "Der Versuch, alles zu kontrollieren, erzeugt Anspannung",
        "desc": "Du willst jede Variable kontrollieren: Ernährung, Schlaf, Haltung, Temperatur. Aber Überkontrolle ist Stress — und Stress ist Schmerz.",
        "body_zones": ["nacken", "schulter", "kiefer"],
        "signal": "Rigide Routinen, Panik bei Abweichung, ständiges Optimieren",
        "reframe": "Kontrolle ist eine Illusion. Loslassen ist die wahre Stärke.",
        "protocol": [
            {"min": 0, "text": "Was versuchst du gerade zu kontrollieren? Schreibe es auf."},
            {"min": 2, "text": "Frage: Liegt das in meiner Kontrolle? Ja oder Nein?"},
            {"min": 4, "text": "Wenn Nein: Lass es los. Buchstäblich — öffne die Fäuste, atme aus."},
            {"min": 6, "text": "Wenn Ja: Was ist die EINE Sache, die du tun kannst? Nur eine."},
            {"min": 8, "text": "Alles andere? Nicht dein Problem. Nicht jetzt. Atme."},
        ],
    },
    "somatisierung": {
        "nr": 15,
        "name": "Somatisierung",
        "short": "Psychischer Stress wird zu körperlichem Schmerz",
        "desc": "Dein Körper spricht, was dein Mund nicht sagt. Stress bei der Arbeit? Rückenschmerz. Beziehungsprobleme? Migräne. Das ist kein Einbildung — es ist Neurobiologie.",
        "body_zones": ["unterer_ruecken", "kopf", "bauch", "brust"],
        "signal": "Schmerz bei Stress, keine organische Ursache, wechselnde Symptome",
        "reframe": "Dein Körper ist ehrlicher als dein Kopf. Höre auf die Botschaft, nicht nur auf den Schmerz.",
        "protocol": [
            {"min": 0, "text": "Was stresst dich gerade am meisten? Benenne es konkret."},
            {"min": 2, "text": "Wo spürst du diesen Stress im Körper? Zeige mit der Hand hin."},
            {"min": 4, "text": "Sage zum Schmerz: 'Ich höre dich. Was willst du mir sagen?'"},
            {"min": 6, "text": "Schreibe die Antwort auf, die kommt. Ohne Zensur."},
            {"min": 8, "text": "Was müsstest du im Leben ändern, damit dein Körper aufhört zu schreien?"},
        ],
    },
    "opferrolle": {
        "nr": 16,
        "name": "Opferrolle",
        "short": "Das Schicksal ist schuld — du kannst nichts tun",
        "desc": "Du fühlst dich als Opfer deines Körpers, der Ärzte, des Systems. Aber solange du in der Opferrolle bleibst, gibst du alle Macht ab.",
        "body_zones": ["ganzer_koerper"],
        "signal": "'Mir passiert immer alles', Schuldzuweisung an andere, Passivität",
        "reframe": "Du bist nicht verantwortlich für den Schmerz. Aber für deine Reaktion darauf.",
        "protocol": [
            {"min": 0, "text": "Wem oder was gibst du gerade die Schuld? Schreibe es auf."},
            {"min": 2, "text": "Jetzt die harte Frage: Was könntest DU anders machen?"},
            {"min": 4, "text": "Nicht alles — nur EINE Sache. Eine kleine Handlung, die in deiner Macht liegt."},
            {"min": 6, "text": "Schreibe: 'Ich übernehme Verantwortung für ___.'"},
            {"min": 8, "text": "Tu es. Jetzt. Nicht morgen. Verantwortung beginnt mit Handlung."},
        ],
    },
    "zukunftsangst": {
        "nr": 17,
        "name": "Zukunftsangst",
        "short": "Angst vor einer schmerzhaften Zukunft",
        "desc": "Du malst dir eine Zukunft voller Schmerz aus. Rollstuhl, Pflegefall, Arbeitsunfähigkeit. Aber die Zukunft existiert noch nicht — nur dieser Moment.",
        "body_zones": ["brust", "bauch", "kopf"],
        "signal": "'Was wenn es schlimmer wird?', Schlafstörungen, Panikattacken, Zukunftsszenarien",
        "reframe": "Die Zukunft ist nicht geschrieben. Du bist nur für JETZT zuständig.",
        "protocol": [
            {"min": 0, "text": "Welches Zukunftsszenario macht dir am meisten Angst? Schreibe es auf."},
            {"min": 2, "text": "Wie wahrscheinlich ist es wirklich? Auf einer Skala 1–10."},
            {"min": 4, "text": "Was kannst du HEUTE tun, damit es weniger wahrscheinlich wird?"},
            {"min": 6, "text": "Komm zurück ins Jetzt: Spüre deine Füße auf dem Boden. Du bist hier. Jetzt."},
            {"min": 8, "text": "Neuer Satz: 'Ich handle heute. Die Zukunft kümmere ich mich, wenn sie da ist.'"},
        ],
    },
    "sekundaerer_gewinn": {
        "nr": 18,
        "name": "Sekundärer Krankheitsgewinn",
        "short": "Schmerz hat auch Vorteile — unbewusst",
        "desc": "Schmerz kann Aufmerksamkeit bringen, Verantwortung abnehmen, Konflikte vermeiden. Solange der Gewinn bleibt, bleibt der Schmerz.",
        "body_zones": ["ganzer_koerper"],
        "signal": "Schmerz 'passt' immer, wenn Anforderungen da sind. Besserung macht Angst.",
        "reframe": "Ehrlichkeit mit dir selbst ist der erste Schritt. Was verlierst du, wenn der Schmerz geht?",
        "protocol": [
            {"min": 0, "text": "Stell dir vor, der Schmerz wäre morgen weg. Komplett. Was ändert sich?"},
            {"min": 3, "text": "Was müsstest du dann tun, was du jetzt nicht tun musst?"},
            {"min": 5, "text": "Sei ehrlich: Gibt es etwas, das der Schmerz dir 'erspart'?"},
            {"min": 7, "text": "Das ist kein Vorwurf. Es ist Neurobiologie. Dein Gehirn schützt dich."},
            {"min": 9, "text": "Neuer Deal mit dir selbst: 'Ich finde andere Wege, meine Bedürfnisse zu erfüllen.'"},
        ],
    },
}

ZEIS_MASTERCLASS = [
    {
        "nr": 1,
        "title": "Was Schmerz wirklich ist",
        "subtitle": "Neurowissenschaft für Nicht-Nerds",
        "content": """Schmerz entsteht im Gehirn, nicht im Gewebe. Das ist keine Meinung — das ist Neurowissenschaft.

Dein Gehirn bewertet ständig: Ist diese Empfindung gefährlich? Wenn ja, produziert es Schmerz. Wenn nein, ignoriert es das Signal. Deshalb kann ein Soldat im Krieg mit gebrochenem Bein weiterlaufen — und du vor Rückenschmerz nicht mehr aufstehen kannst, obwohl dein MRT normal ist.

**Die 3 Schlüssel-Erkenntnisse:**

1. **Schmerz ≠ Schaden.** 85% aller Rückenschmerzen haben keine strukturelle Ursache.
2. **Dein Nervensystem lernt Schmerz.** Wie ein Musiker sein Instrument — durch Wiederholung. Je öfter du Schmerz erlebst, desto besser wird dein Gehirn darin.
3. **Was gelernt wurde, kann umgelernt werden.** Neuroplastizität ist real. Dein Gehirn kann neue Pfade bilden.""",
    },
    {
        "nr": 2,
        "title": "Die 18 Schmerzverzerrungen",
        "subtitle": "Warum dein Kopf den Schmerz verstärkt",
        "content": """Kognitive Verzerrungen sind Denkfehler, die Schmerz verstärken. Sie sind nicht deine Schuld — sie sind evolutionär. Aber du kannst sie erkennen und entschärfen.

**Die 5 häufigsten:**
- **Katastrophisieren:** Das Schlimmste erwarten
- **Hypervigilanz:** Ständig den Körper scannen
- **Schwarz-Weiß:** Entweder gesund oder kaputt
- **Hilflosigkeit:** Glauben, dass nichts hilft
- **Nocebo:** Negative Erwartung erzeugt echten Schmerz

Im ZEIS-Scan findest du heraus, welche Verzerrungen bei DIR am stärksten sind. Erst wenn du den Feind kennst, kannst du ihn besiegen.""",
    },
    {
        "nr": 3,
        "title": "Der Body-Mind-Loop",
        "subtitle": "Wie Körper und Geist sich gegenseitig triggern",
        "content": """Schmerz ist nie nur körperlich oder nur psychisch. Er ist ein Loop:

**Gedanke** → Stress → Muskelspannung → **Schmerz** → Angst → mehr Stress → mehr Spannung → **mehr Schmerz**

Der Loop läuft automatisch. Aber du kannst ihn an JEDER Stelle unterbrechen:

1. **Am Gedanken:** Reframing (Verzerrung erkennen und umdeuten)
2. **Am Stress:** Atemtechniken, Vagusnerv-Stimulation
3. **An der Spannung:** Bewegung, progressive Muskelrelaxation
4. **Am Schmerz:** Graded Exposure, Desensibilisierung
5. **An der Angst:** Sicherheitssignale, soziale Verbindung

Du brauchst nicht alle 5. Du brauchst nur EINEN Hebel, der für dich funktioniert.""",
    },
    {
        "nr": 4,
        "title": "Dein Nervensystem verstehen",
        "subtitle": "Sympathikus, Parasympathikus und der Vagusnerv",
        "content": """Dein autonomes Nervensystem hat zwei Modi:

**Sympathikus (Gas):** Kampf, Flucht, Anspannung. Schmerz verstärkt.
**Parasympathikus (Bremse):** Ruhe, Verdauung, Heilung. Schmerz gelindert.

Der **Vagusnerv** ist dein direkter Zugang zum Parasympathikus. Er verläuft vom Hirnstamm durch Hals und Brust bis in den Bauch.

**Vagusnerv aktivieren — sofort:**
- Kaltes Wasser ins Gesicht (Tauchreflex)
- Langes Ausatmen (länger als Einatmen)
- Summen oder Singen (Vibration am Kehlkopf)
- Soziale Verbindung (Blickkontakt, Umarmung)

Wenn dein Vagus aktiv ist, kann dein Körper heilen. Wenn nicht, bleibt er im Alarmmodus.""",
    },
    {
        "nr": 5,
        "title": "Graded Exposure",
        "subtitle": "Schrittweise zurück ins Leben",
        "content": """Vermeidung macht Schmerz stärker. Graded Exposure ist das Gegenmittel.

**Prinzip:** Du setzt dich den gefürchteten Bewegungen/Situationen schrittweise aus. Nicht alles auf einmal. Nicht heroisch. Systematisch.

**Beispiel Rückenschmerz:**
1. Woche 1: 5 Minuten spazieren (auch wenn es zieht)
2. Woche 2: 10 Minuten spazieren + 5 Kniebeugen
3. Woche 3: 15 Minuten + leichtes Heben (2 kg)
4. Woche 4: 20 Minuten + normales Heben

**Die Regeln:**
- Starte unter deiner Schmerzgrenze
- Steigere um maximal 10-20% pro Woche
- NICHT am Schmerz orientieren, sondern am Plan
- Rückschläge sind normal — kein Grund aufzuhören""",
    },
    {
        "nr": 6,
        "title": "Schlaf & Schmerz",
        "subtitle": "Warum schlechter Schlaf alles schlimmer macht",
        "content": """Schlechter Schlaf senkt deine Schmerzschwelle um bis zu 40%. Das ist keine Metapher — das sind Messwerte.

**Der Teufelskreis:** Schmerz → schlechter Schlaf → niedrigere Schwelle → mehr Schmerz → noch schlechterer Schlaf.

**Die ZEIS-Schlafregeln:**
1. **Gleiche Zeit** — jeden Tag gleich aufstehen (auch am Wochenende)
2. **Kein Koffein** nach 14 Uhr
3. **Bildschirm-Stopp** 60 Minuten vor dem Schlafen
4. **Kühles Zimmer** — 16-18°C optimal
5. **Nicht im Bett liegen und grübeln** — nach 20 Minuten aufstehen, lesen, zurückkommen
6. **Schmerz-Tagebuch** nicht abends führen — das aktiviert das Schmerznetzwerk

Eine Nacht guter Schlaf kann mehr bewirken als jede Tablette.""",
    },
    {
        "nr": 7,
        "title": "Stress als Schmerzverstärker",
        "subtitle": "Cortisol, Adrenalin und ihre Rolle",
        "content": """Chronischer Stress hält dein Nervensystem im Alarmmodus. Cortisol und Adrenalin halten die Schmerzempfindlichkeit hoch.

**Stress-Signale erkennen:**
- Kiefer zusammengebissen
- Schultern hochgezogen
- Flache Atmung
- Konzentrationsprobleme
- Reizbarkeit

**Sofort-Interventionen:**
1. **Physiological Sigh:** 2× kurz einatmen durch die Nase, 1× lang aus durch den Mund
2. **Kälte-Exposition:** 30 Sekunden kaltes Wasser über die Unterarme
3. **Schütteln:** 2 Minuten den ganzen Körper schütteln — wie ein Hund nach dem Schwimmen

Stress ist nicht der Feind. Chronischer Stress ohne Erholung ist der Feind.""",
    },
    {
        "nr": 8,
        "title": "Selbstmitgefühl statt Selbstkritik",
        "subtitle": "Warum du aufhören musst, dich selbst fertigzumachen",
        "content": """Die härteste Stimme in deinem Kopf ist deine eigene. 'Du bist schwach', 'Stell dich nicht an', 'Andere schaffen das doch auch.'

Selbstkritik aktiviert das Bedrohungssystem — und damit Schmerz. Selbstmitgefühl aktiviert das Beruhigungssystem — und damit Heilung.

**Die 3 Komponenten (nach Kristin Neff):**
1. **Achtsamkeit:** Wahrnehmen, dass es dir schlecht geht (ohne Übertreibung)
2. **Gemeinsames Menschsein:** Andere leiden auch. Du bist nicht allein.
3. **Freundlichkeit:** Behandle dich wie einen guten Freund.

**Übung:** Lege die Hand aufs Herz und sage: 'Das ist gerade schwer. Anderen geht es auch so. Ich darf freundlich zu mir sein.'

Das ist nicht weich. Das ist Neurowissenschaft.""",
    },
]

ZEIS_BODY_ZONES = {
    "kopf": {"name": "Kopf", "x": 148, "y": 30, "w": 55, "h": 55},
    "nacken": {"name": "Nacken", "x": 148, "y": 85, "w": 45, "h": 30},
    "schulter": {"name": "Schultern", "x": 100, "y": 110, "w": 150, "h": 30},
    "brust": {"name": "Brust", "x": 125, "y": 140, "w": 100, "h": 50},
    "bauch": {"name": "Bauch", "x": 130, "y": 195, "w": 90, "h": 55},
    "unterer_ruecken": {"name": "Unterer Rücken", "x": 130, "y": 250, "w": 90, "h": 45},
    "knie": {"name": "Knie", "x": 120, "y": 340, "w": 110, "h": 30},
    "kiefer": {"name": "Kiefer", "x": 130, "y": 60, "w": 90, "h": 25},
    "ganzer_koerper": {"name": "Ganzer Körper", "x": 110, "y": 150, "w": 130, "h": 200},
}


# ---- ZEIS ROUTES ----

@app.get("/zeis", response_class=HTMLResponse)
async def zeis_landing(request: Request):
    types_grid = ""
    for key, t in ZEIS_TYPES.items():
        types_grid += f"""
        <a href="/zeis/protocol/{key}" style="text-decoration:none;color:inherit;">
          <div style="background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:10px;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <span style="color:#f59e0b;font-weight:700;">#{t['nr']}</span>
              <span class="small">→</span>
            </div>
            <div style="font-weight:600;margin:6px 0 4px;color:#f3f4f6;">{t['name']}</div>
            <div class="small">{t['short']}</div>
          </div>
        </a>"""

    body = f"""
      <div style="text-align:center;margin-bottom:20px;">
        <div style="font-size:48px;margin-bottom:8px;">🧠</div>
        <h1 style="margin:0;">ZEIS Protocol</h1>
        <p style="color:#f59e0b;font-size:14px;margin-top:4px;">18 Schmerzverzerrungen erkennen & auflösen</p>
      </div>

      <p>Dein Schmerz ist real — aber dein Gehirn verstärkt ihn. Das ZEIS Protocol hilft dir, die 18 häufigsten Schmerzverzerrungen zu erkennen und mit gezielten Self-Treatment Protokollen aufzulösen.</p>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:20px 0;">
        <a href="/zeis/scan" class="btn" style="text-align:center;">🔍 ZEIS Scan starten</a>
        <a href="/zeis/method" class="btn btn-outline" style="text-align:center;color:#f59e0b;">📖 Die Methode</a>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:20px;">
        <a href="/zeis/masterclass" class="btn btn-outline" style="text-align:center;">🎓 Masterclass</a>
        <a href="/zeis/daily" class="btn btn-outline" style="text-align:center;">📋 Tägliches Protokoll</a>
      </div>

      <div class="hr"></div>
      <h2>Die 18 Schmerzverzerrungen</h2>
      {types_grid}

      <div class="hr"></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
        <a href="/zeis/book/preview" class="btn btn-outline" style="text-align:center;">📖 Buch-Vorschau</a>
        <a href="/zeis/book/export" class="btn btn-outline" style="text-align:center;">⬇ Buch-Export (.md)</a>
      </div>

      <div style="height:16px;"></div>
      <a href="/" class="btn btn-outline" style="text-align:center;display:block;">← Zurück</a>
      <p class="small" style="text-align:center;margin-top:12px;">ZEIS Protocol — by Alexander Zeis</p>
    """
    return _page("ZEIS Protocol — Schmerzverzerrungen", body, request=request)


@app.get("/zeis/method", response_class=HTMLResponse)
async def zeis_method(request: Request):
    types_list = ""
    for key, t in ZEIS_TYPES.items():
        zones = ", ".join(ZEIS_BODY_ZONES[z]["name"] for z in t["body_zones"] if z in ZEIS_BODY_ZONES)
        types_list += f"""
        <div style="background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:12px;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div>
              <span style="color:#f59e0b;font-weight:700;font-size:13px;">Typ #{t['nr']}</span>
              <h2 style="margin:4px 0 6px;font-size:16px;">{t['name']}</h2>
            </div>
            <a href="/zeis/protocol/{key}" class="btn btn-outline" style="font-size:12px;padding:6px 12px;margin:0;width:auto;">Protokoll →</a>
          </div>
          <p style="margin:0 0 8px;font-size:14px;">{t['desc']}</p>
          <div class="small" style="margin-bottom:4px;"><b>Körperzonen:</b> {zones}</div>
          <div class="small" style="margin-bottom:4px;"><b>Signale:</b> {t['signal']}</div>
          <div style="background:rgba(245,158,11,.07);border:1px solid rgba(245,158,11,.2);border-radius:10px;padding:10px;margin-top:8px;">
            <div class="small" style="color:#f59e0b;font-weight:600;">Reframe:</div>
            <div style="font-size:13px;color:#e5e7eb;">{t['reframe']}</div>
          </div>
        </div>"""

    body = f"""
      <h1>📖 Die ZEIS-Methode</h1>
      <p style="color:#f59e0b;">18 Schmerzverzerrungen — erklärt, erkannt, aufgelöst</p>

      <div class="hr"></div>

      <h2>Was sind Schmerzverzerrungen?</h2>
      <p>Kognitive Verzerrungen sind systematische Denkfehler, die deinen Schmerz verstärken. Sie sind nicht deine Schuld — sie sind evolutionär programmiert. Aber du kannst sie erkennen und umprogrammieren.</p>
      <p>Das ZEIS Protocol identifiziert 18 spezifische Verzerrungsmuster, die bei Schmerzpatienten am häufigsten auftreten. Jede Verzerrung hat ein eigenes Self-Treatment Protokoll.</p>

      <div class="hr"></div>

      <h2>Alle 18 Typen im Detail</h2>
      {types_list}

      <div class="hr"></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
        <a href="/zeis/scan" class="btn" style="text-align:center;">🔍 Scan starten</a>
        <a href="/zeis" class="btn btn-outline" style="text-align:center;">← Zurück</a>
      </div>
      <p class="small" style="text-align:center;margin-top:12px;">ZEIS Protocol — by Alexander Zeis</p>
    """
    return _page("ZEIS Methode — 18 Typen", body, request=request)


@app.get("/zeis/scan", response_class=HTMLResponse)
async def zeis_scan(request: Request):
    questions = []
    for key, t in ZEIS_TYPES.items():
        questions.append({"key": key, "name": t["name"], "signal": t["signal"]})

    q_html = ""
    for i, q in enumerate(questions):
        q_html += f"""
        <div style="background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:12px;">
          <label style="font-size:14px;color:#f3f4f6;margin:0 0 8px;">
            <b>#{i+1}.</b> {q['name']}
          </label>
          <p class="small" style="margin:0 0 10px;">{q['signal']}</p>
          <div class="slider-wrap">
            <input type="range" name="{q['key']}" min="0" max="10" value="0"
                   oninput="document.getElementById('val_{q['key']}').textContent=this.value"
                   style="width:100%;">
            <div style="display:flex;justify-content:space-between;margin-top:4px;">
              <span class="small">Trifft nicht zu</span>
              <span class="slider-val" id="val_{q['key']}">0</span>
              <span class="small">Trifft voll zu</span>
            </div>
          </div>
        </div>"""

    body = f"""
      <h1>🔍 ZEIS Body-Mind Scan</h1>
      <p>Bewerte ehrlich, wie stark jede Verzerrung bei dir zutrifft (0 = gar nicht, 10 = extrem stark).</p>

      <div class="hr"></div>

      <form action="/zeis/scan" method="post">
        {q_html}
        <button type="submit" class="btn" style="margin-top:16px;">Scan auswerten →</button>
      </form>

      <div style="height:12px;"></div>
      <a href="/zeis" class="btn btn-outline" style="text-align:center;display:block;">← Zurück</a>
    """
    return _page("ZEIS Scan", body, request=request)


@app.post("/zeis/scan", response_class=HTMLResponse)
async def zeis_scan_result(request: Request):
    form = await request.form()
    results = []
    for key in ZEIS_TYPES:
        val = int(form.get(key, 0))
        if val > 0:
            results.append({"key": key, "val": val, "type": ZEIS_TYPES[key]})
    results.sort(key=lambda r: r["val"], reverse=True)

    top_types = results[:5]

    if not top_types:
        body = """
          <h1>🔍 Scan-Ergebnis</h1>
          <p>Du hast keine Verzerrung bewertet. Bitte fülle den Scan ehrlich aus.</p>
          <a href="/zeis/scan" class="btn" style="text-align:center;display:block;">Nochmal versuchen</a>
        """
        return _page("ZEIS Scan — Kein Ergebnis", body, request=request)

    total = sum(r["val"] for r in results)
    avg = round(total / len(results), 1) if results else 0

    result_html = ""
    for r in top_types:
        pct = int(r["val"] / 10 * 100)
        color = "#ef4444" if r["val"] >= 7 else "#f59e0b" if r["val"] >= 4 else "#22c55e"
        result_html += f"""
        <div style="background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:10px;">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <div>
              <span style="color:{color};font-weight:700;font-size:22px;">{r['val']}/10</span>
              <span style="margin-left:8px;font-weight:600;color:#f3f4f6;">{r['type']['name']}</span>
            </div>
            <a href="/zeis/protocol/{r['key']}" class="btn btn-outline" style="font-size:12px;padding:6px 12px;margin:0;width:auto;">Protokoll →</a>
          </div>
          <div style="height:4px;background:#1f2937;border-radius:999px;margin:8px 0;">
            <div style="height:4px;background:{color};border-radius:999px;width:{pct}%;"></div>
          </div>
          <p class="small" style="margin:4px 0 0;">{r['type']['short']}</p>
        </div>"""

    severity = "hoch" if avg >= 6 else "mittel" if avg >= 3 else "niedrig"
    sev_color = "#ef4444" if avg >= 6 else "#f59e0b" if avg >= 3 else "#22c55e"

    body = f"""
      <h1>🔍 Dein ZEIS-Profil</h1>

      <div style="text-align:center;margin:16px 0;">
        <div style="font-size:48px;font-weight:800;color:{sev_color};">{avg}</div>
        <div style="font-size:14px;color:{sev_color};font-weight:600;">Verzerrungsindex: {severity}</div>
        <p class="small">{len([r for r in results if r['val'] >= 5])} von 18 Verzerrungen aktiv (≥5)</p>
      </div>

      <div class="hr"></div>

      <h2>Deine Top-5 Verzerrungen</h2>
      {result_html}

      <div class="hr"></div>

      <h2>Empfehlung</h2>
      <div class="action-box">
        <p style="margin:0;color:#f3f4f6;">Starte mit dem Protokoll für <b>{top_types[0]['type']['name']}</b> — deine stärkste Verzerrung. Führe es 7 Tage lang täglich durch.</p>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:16px;">
        <a href="/zeis/protocol/{top_types[0]['key']}" class="btn" style="text-align:center;">Protokoll starten</a>
        <a href="/zeis/scan" class="btn btn-outline" style="text-align:center;">Scan wiederholen</a>
      </div>

      <div style="height:12px;"></div>
      <a href="/zeis" class="btn btn-outline" style="text-align:center;display:block;">← Zurück</a>
    """
    return _page("ZEIS Scan-Ergebnis", body, request=request)


@app.get("/zeis/masterclass", response_class=HTMLResponse)
async def zeis_masterclass(request: Request):
    modules_html = ""
    for m in ZEIS_MASTERCLASS:
        modules_html += f"""
        <a href="/zeis/masterclass/{m['nr']}" style="text-decoration:none;color:inherit;">
          <div style="background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:10px;display:flex;align-items:center;gap:14px;">
            <div style="min-width:44px;height:44px;background:rgba(245,158,11,.12);border-radius:12px;display:flex;align-items:center;justify-content:center;font-weight:800;color:#f59e0b;font-size:18px;">{m['nr']}</div>
            <div>
              <div style="font-weight:600;color:#f3f4f6;">{m['title']}</div>
              <div class="small">{m['subtitle']}</div>
            </div>
          </div>
        </a>"""

    body = f"""
      <h1>🎓 ZEIS Masterclass</h1>
      <p>8 Module für ein tiefes Verständnis von Schmerz, Gehirn und Heilung.</p>

      <div class="hr"></div>

      {modules_html}

      <div class="hr"></div>
      <a href="/zeis" class="btn btn-outline" style="text-align:center;display:block;">← Zurück</a>
      <p class="small" style="text-align:center;margin-top:12px;">ZEIS Masterclass — by Alexander Zeis</p>
    """
    return _page("ZEIS Masterclass", body, request=request)


@app.get("/zeis/masterclass/{nr}", response_class=HTMLResponse)
async def zeis_masterclass_module(nr: int, request: Request):
    module = None
    for m in ZEIS_MASTERCLASS:
        if m["nr"] == nr:
            module = m
            break
    if not module:
        return _page("Nicht gefunden", "<p>Modul nicht gefunden.</p><a href='/zeis/masterclass'>← Zurück</a>", request=request)

    content_html = module["content"].replace("\n\n", "</p><p>").replace("**", "<b>").replace("**", "</b>")
    # Simple markdown bold handling
    import re
    formatted = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', module["content"])
    formatted = formatted.replace("\n\n", "</p><p style='color:var(--muted);line-height:1.6;'>")
    formatted = formatted.replace("\n- ", "<br>• ")
    formatted = formatted.replace("\n1. ", "<br>1. ").replace("\n2. ", "<br>2. ").replace("\n3. ", "<br>3. ")
    formatted = formatted.replace("\n4. ", "<br>4. ").replace("\n5. ", "<br>5. ").replace("\n6. ", "<br>6. ")

    prev_link = f'<a href="/zeis/masterclass/{nr - 1}" class="btn btn-outline" style="text-align:center;">← Modul {nr - 1}</a>' if nr > 1 else '<span></span>'
    next_link = f'<a href="/zeis/masterclass/{nr + 1}" class="btn" style="text-align:center;">Modul {nr + 1} →</a>' if nr < len(ZEIS_MASTERCLASS) else '<span></span>'

    body = f"""
      <div class="small" style="color:#f59e0b;margin-bottom:4px;">Modul {module['nr']} von {len(ZEIS_MASTERCLASS)}</div>
      <h1>{module['title']}</h1>
      <p style="color:#f59e0b;font-size:14px;margin-top:-8px;">{module['subtitle']}</p>

      <div class="hr"></div>

      <div style="line-height:1.8;">
        <p style="color:var(--muted);line-height:1.6;">{formatted}</p>
      </div>

      <div class="hr"></div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
        {prev_link}
        {next_link}
      </div>

      <div style="height:12px;"></div>
      <a href="/zeis/masterclass" class="btn btn-outline" style="text-align:center;display:block;">← Alle Module</a>
    """
    return _page(f"ZEIS Masterclass — {module['title']}", body, request=request)


@app.get("/zeis/protocol/{type_key}", response_class=HTMLResponse)
async def zeis_protocol(type_key: str, request: Request):
    t = ZEIS_TYPES.get(type_key)
    if not t:
        return _page("Nicht gefunden", "<p>Verzerrungstyp nicht gefunden.</p><a href='/zeis'>← Zurück</a>", request=request)

    steps_html = ""
    for i, step in enumerate(t["protocol"]):
        steps_html += f"""
        <div id="step_{i}" class="protocol-step" style="display:{'block' if i == 0 else 'none'};background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:14px;padding:20px;margin-bottom:12px;">
          <div class="small" style="color:#f59e0b;margin-bottom:8px;">Minute {step['min']}</div>
          <p style="font-size:16px;color:#f3f4f6;line-height:1.6;margin:0;">{step['text']}</p>
        </div>"""

    zones = ", ".join(ZEIS_BODY_ZONES[z]["name"] for z in t["body_zones"] if z in ZEIS_BODY_ZONES)
    total_steps = len(t["protocol"])

    body = f"""
      <div class="small" style="color:#f59e0b;">Self-Treatment Protokoll</div>
      <h1>#{t['nr']} {t['name']}</h1>
      <p>{t['desc']}</p>

      <div style="display:flex;gap:8px;flex-wrap:wrap;margin:12px 0;">
        {"".join(f'<span class="pattern-tag">{ZEIS_BODY_ZONES[z]["name"]}</span>' for z in t["body_zones"] if z in ZEIS_BODY_ZONES)}
      </div>

      <div class="hr"></div>

      <div class="action-box">
        <div class="small" style="color:#f59e0b;font-weight:600;">Erkennungssignal:</div>
        <p style="margin:4px 0 0;color:#f3f4f6;">{t['signal']}</p>
      </div>

      <div style="background:rgba(34,197,94,.07);border:1px solid rgba(34,197,94,.3);border-radius:16px;padding:18px;margin:12px 0;">
        <div class="small" style="color:#22c55e;font-weight:600;">Reframe:</div>
        <p style="margin:4px 0 0;color:#f3f4f6;">{t['reframe']}</p>
      </div>

      <div class="hr"></div>

      <h2>Protokoll starten</h2>
      <p class="small">Klicke dich durch die {total_steps} Schritte. Nimm dir Zeit.</p>

      <div id="timer_display" style="text-align:center;margin:16px 0;">
        <span style="font-size:36px;font-weight:800;color:#f59e0b;" id="timer_val">00:00</span>
        <div class="small">Timer</div>
      </div>

      {steps_html}

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px;">
        <button onclick="prevStep()" class="btn btn-outline" id="prev_btn" style="opacity:0.3;" disabled>← Zurück</button>
        <button onclick="nextStep()" class="btn" id="next_btn">Weiter →</button>
      </div>

      <div id="done_section" style="display:none;text-align:center;margin-top:20px;">
        <div style="font-size:48px;">✅</div>
        <h2>Protokoll abgeschlossen</h2>
        <p>Du hast das Self-Treatment für <b>{t['name']}</b> durchgeführt. Wiederhole es morgen.</p>
        <a href="/zeis" class="btn" style="display:inline-block;margin-top:12px;">Zurück zum ZEIS Protocol</a>
      </div>

      <div class="hr"></div>
      <a href="/zeis" class="btn btn-outline" style="text-align:center;display:block;">← Zurück</a>

      <script>
      let step = 0;
      const total = {total_steps};
      let seconds = 0;
      let timerInterval = null;

      function startTimer() {{
        if (timerInterval) return;
        timerInterval = setInterval(() => {{
          seconds++;
          const m = String(Math.floor(seconds / 60)).padStart(2, '0');
          const s = String(seconds % 60).padStart(2, '0');
          document.getElementById('timer_val').textContent = m + ':' + s;
        }}, 1000);
      }}

      function nextStep() {{
        startTimer();
        if (step < total - 1) {{
          document.getElementById('step_' + step).style.display = 'none';
          step++;
          document.getElementById('step_' + step).style.display = 'block';
          document.getElementById('prev_btn').disabled = false;
          document.getElementById('prev_btn').style.opacity = '1';
          if (step === total - 1) {{
            document.getElementById('next_btn').textContent = 'Abschließen ✓';
          }}
        }} else {{
          document.getElementById('step_' + step).style.display = 'none';
          document.getElementById('next_btn').style.display = 'none';
          document.getElementById('prev_btn').style.display = 'none';
          document.getElementById('done_section').style.display = 'block';
          if (timerInterval) clearInterval(timerInterval);
        }}
      }}

      function prevStep() {{
        if (step > 0) {{
          document.getElementById('step_' + step).style.display = 'none';
          step--;
          document.getElementById('step_' + step).style.display = 'block';
          document.getElementById('next_btn').textContent = 'Weiter →';
          if (step === 0) {{
            document.getElementById('prev_btn').disabled = true;
            document.getElementById('prev_btn').style.opacity = '0.3';
          }}
        }}
      }}
      </script>
    """
    return _page(f"ZEIS Protokoll — {t['name']}", body, request=request)


@app.get("/zeis/daily", response_class=HTMLResponse)
async def zeis_daily(request: Request):
    options_html = ""
    for key, t in ZEIS_TYPES.items():
        options_html += f'<option value="{key}">{t["name"]}</option>'

    body = f"""
      <h1>📋 Tägliches ZEIS-Protokoll</h1>
      <p>Tracke deine Verzerrungen und Fortschritte täglich.</p>

      <div class="hr"></div>

      <form action="/zeis/daily" method="post">
        <label>Wie geht es dir gerade? (1–10)</label>
        <div class="slider-wrap">
          <input type="range" name="state" min="1" max="10" value="5"
                 oninput="document.getElementById('state_val').textContent=this.value">
          <div style="display:flex;justify-content:space-between;">
            <span class="small">Schlecht</span>
            <span class="slider-val" id="state_val">5</span>
            <span class="small">Sehr gut</span>
          </div>
        </div>

        <label>Schmerz-Level (0–10)</label>
        <div class="slider-wrap">
          <input type="range" name="pain" min="0" max="10" value="3"
                 oninput="document.getElementById('pain_val').textContent=this.value">
          <div style="display:flex;justify-content:space-between;">
            <span class="small">Kein Schmerz</span>
            <span class="slider-val" id="pain_val">3</span>
            <span class="small">Maximal</span>
          </div>
        </div>

        <label>Welche Verzerrung war heute am stärksten?</label>
        <select name="distortion">
          <option value="">— Wähle —</option>
          {options_html}
        </select>

        <label>Verzerrungsstärke (0–10)</label>
        <div class="slider-wrap">
          <input type="range" name="intensity" min="0" max="10" value="5"
                 oninput="document.getElementById('int_val').textContent=this.value">
          <div style="display:flex;justify-content:space-between;">
            <span class="small">Schwach</span>
            <span class="slider-val" id="int_val">5</span>
            <span class="small">Extrem</span>
          </div>
        </div>

        <label>Hast du ein Protokoll durchgeführt?</label>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
            <input type="radio" name="protocol_done" value="ja" style="width:auto;"> Ja
          </label>
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
            <input type="radio" name="protocol_done" value="nein" checked style="width:auto;"> Nein
          </label>
        </div>

        <label>Was hast du heute gelernt / erkannt?</label>
        <textarea name="insight" rows="3" placeholder="Deine Erkenntnis des Tages..."></textarea>

        <button type="submit" class="btn" style="margin-top:16px;">Eintrag speichern ✓</button>
      </form>

      <div style="height:12px;"></div>
      <a href="/zeis" class="btn btn-outline" style="text-align:center;display:block;">← Zurück</a>
    """
    return _page("Tägliches ZEIS-Protokoll", body, request=request)


@app.post("/zeis/daily", response_class=HTMLResponse)
async def zeis_daily_save(request: Request):
    form = await request.form()
    state = form.get("state", "5")
    pain = form.get("pain", "3")
    distortion = form.get("distortion", "")
    intensity = form.get("intensity", "5")
    protocol_done = form.get("protocol_done", "nein")
    insight = form.get("insight", "")

    dist_name = ZEIS_TYPES[distortion]["name"] if distortion in ZEIS_TYPES else "Keine gewählt"

    body = f"""
      <div style="text-align:center;">
        <div style="font-size:48px;margin-bottom:8px;">✅</div>
        <h1>Eintrag gespeichert</h1>
      </div>

      <div class="hr"></div>

      <div class="grid3" style="margin-bottom:16px;">
        <div class="kpi"><span class="small">Zustand</span><b>{state}/10</b></div>
        <div class="kpi"><span class="small">Schmerz</span><b>{pain}/10</b></div>
        <div class="kpi"><span class="small">Verzerrung</span><b>{intensity}/10</b></div>
      </div>

      <div style="background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:12px;">
        <div class="small">Hauptverzerrung:</div>
        <div style="font-weight:600;color:#f3f4f6;">{dist_name}</div>
      </div>

      <div style="background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:12px;">
        <div class="small">Protokoll durchgeführt:</div>
        <div style="font-weight:600;color:{'#22c55e' if protocol_done == 'ja' else '#ef4444'};">{'Ja ✓' if protocol_done == 'ja' else 'Nein ✗'}</div>
      </div>

      {"<div style='background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:12px;'><div class=small>Erkenntnis:</div><p style=margin:4px_0_0;color:#f3f4f6;>" + insight + "</p></div>" if insight else ""}

      <div class="hr"></div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
        <a href="/zeis/daily" class="btn" style="text-align:center;">Neuer Eintrag</a>
        <a href="/zeis" class="btn btn-outline" style="text-align:center;">← ZEIS Home</a>
      </div>
    """
    return _page("ZEIS — Eintrag gespeichert", body, request=request)


@app.get("/zeis/book/export")
async def zeis_book_export(request: Request):
    md = "# ZEIS Protocol — 18 Schmerzverzerrungen erkennen & auflösen\n\n"
    md += "**Von Alexander Zeis**\n\n"
    md += "---\n\n"

    # Part 1: Masterclass
    md += "# Teil 1: Masterclass\n\n"
    for m in ZEIS_MASTERCLASS:
        md += f"## Kapitel {m['nr']}: {m['title']}\n\n"
        md += f"*{m['subtitle']}*\n\n"
        md += m["content"] + "\n\n"
        md += "---\n\n"

    # Part 2: Die 18 Typen
    md += "# Teil 2: Die 18 Schmerzverzerrungen\n\n"
    for key, t in ZEIS_TYPES.items():
        zones = ", ".join(ZEIS_BODY_ZONES[z]["name"] for z in t["body_zones"] if z in ZEIS_BODY_ZONES)
        md += f"## Typ #{t['nr']}: {t['name']}\n\n"
        md += f"**{t['short']}**\n\n"
        md += f"{t['desc']}\n\n"
        md += f"**Körperzonen:** {zones}\n\n"
        md += f"**Erkennungssignal:** {t['signal']}\n\n"
        md += f"**Reframe:** {t['reframe']}\n\n"
        md += "### Self-Treatment Protokoll\n\n"
        for step in t["protocol"]:
            md += f"- **Minute {step['min']}:** {step['text']}\n"
        md += "\n---\n\n"

    # Part 3: Tägliches Protokoll Vorlage
    md += "# Teil 3: Tägliches Protokoll (Vorlage)\n\n"
    md += "| Datum | Zustand (1-10) | Schmerz (0-10) | Hauptverzerrung | Stärke (0-10) | Protokoll? | Erkenntnis |\n"
    md += "|-------|---------------|----------------|-----------------|---------------|------------|------------|\n"
    md += "| ___ | ___ | ___ | ___ | ___ | ___ | ___ |\n" * 7
    md += "\n---\n\n"
    md += "*ZEIS Protocol — by Alexander Zeis — Generiert am " + datetime.now().strftime("%d.%m.%Y") + "*\n"

    from starlette.responses import Response
    return Response(
        content=md.encode("utf-8"),
        media_type="text/markdown",
        headers={"Content-Disposition": "attachment; filename=ZEIS-Protocol-Buch.md"},
    )


@app.get("/zeis/book/preview", response_class=HTMLResponse)
async def zeis_book_preview(request: Request):
    import re

    chapters_html = ""

    # Masterclass chapters
    chapters_html += '<h2 style="color:#f59e0b;margin-top:24px;">Teil 1: Masterclass</h2>'
    for m in ZEIS_MASTERCLASS:
        formatted = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', m["content"])
        formatted = formatted.replace("\n\n", "</p><p style='color:var(--muted);line-height:1.6;font-size:14px;'>")
        formatted = formatted.replace("\n- ", "<br>• ")
        chapters_html += f"""
        <div style="background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:12px;">
          <div class="small" style="color:#f59e0b;">Kapitel {m['nr']}</div>
          <h2 style="font-size:16px;margin:4px 0;">{m['title']}</h2>
          <div class="small" style="margin-bottom:8px;">{m['subtitle']}</div>
          <p style="color:var(--muted);line-height:1.6;font-size:14px;">{formatted}</p>
        </div>"""

    # Type chapters
    chapters_html += '<h2 style="color:#f59e0b;margin-top:24px;">Teil 2: Die 18 Schmerzverzerrungen</h2>'
    for key, t in ZEIS_TYPES.items():
        zones = ", ".join(ZEIS_BODY_ZONES[z]["name"] for z in t["body_zones"] if z in ZEIS_BODY_ZONES)
        steps = "".join(f"<li><b>Min {s['min']}:</b> {s['text']}</li>" for s in t["protocol"])
        chapters_html += f"""
        <div style="background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:12px;">
          <div class="small" style="color:#f59e0b;">Typ #{t['nr']}</div>
          <h2 style="font-size:16px;margin:4px 0;">{t['name']}</h2>
          <p style="font-size:13px;color:var(--muted);margin:4px 0 8px;">{t['desc']}</p>
          <div class="small"><b>Zonen:</b> {zones}</div>
          <div class="small"><b>Signal:</b> {t['signal']}</div>
          <div style="background:rgba(245,158,11,.07);border-radius:10px;padding:10px;margin:8px 0;">
            <div class="small" style="color:#22c55e;"><b>Reframe:</b></div>
            <div style="font-size:13px;">{t['reframe']}</div>
          </div>
          <div class="small" style="margin-top:8px;"><b>Protokoll:</b></div>
          <ol style="font-size:13px;color:var(--muted);padding-left:20px;">{steps}</ol>
        </div>"""

    total_chapters = len(ZEIS_MASTERCLASS) + len(ZEIS_TYPES)

    body = f"""
      <div style="text-align:center;margin-bottom:20px;">
        <div style="font-size:48px;">📖</div>
        <h1>ZEIS Protocol — Das Buch</h1>
        <p style="color:#f59e0b;">Von Alexander Zeis</p>
        <p class="small">{total_chapters} Kapitel • 8 Masterclass-Module • 18 Protokolle</p>
      </div>

      <div class="hr"></div>

      <a href="/zeis/book/export" class="btn" style="text-align:center;display:block;margin-bottom:16px;">⬇ Als Markdown herunterladen</a>
      <p class="small" style="text-align:center;margin-bottom:16px;">Lokal mit Pandoc → PDF/EPUB konvertierbar</p>

      <div class="hr"></div>

      {chapters_html}

      <div class="hr"></div>
      <a href="/zeis/book/export" class="btn" style="text-align:center;display:block;">⬇ Buch herunterladen (.md)</a>
      <div style="height:12px;"></div>
      <a href="/zeis" class="btn btn-outline" style="text-align:center;display:block;">← Zurück</a>
      <p class="small" style="text-align:center;margin-top:12px;">ZEIS Protocol — by Alexander Zeis</p>
    """
    return _page("ZEIS Protocol — Buch-Vorschau", body, request=request)


# =========================================================
# MASTER CONTROL — Bordcomputer Dashboard
# =========================================================

@app.get("/master-control", response_class=HTMLResponse)
def master_control(request: Request, db=Depends(get_db)):
    t = require_therapist_login(request, db)
    now = _now_local()
    today = now.date().isoformat()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d")

    # --- Token Usage Stats ---
    all_usage = db.query(TokenUsage).all()
    today_usage = [u for u in all_usage if u.created_at and u.created_at.strftime("%Y-%m-%d") == today]
    month_usage = [u for u in all_usage if u.created_at and u.created_at.strftime("%Y-%m-%d") >= month_start]

    total_tokens_all = sum(u.total_tokens for u in all_usage)
    total_cost_all = sum(u.cost_usd for u in all_usage)
    total_calls_all = len(all_usage)
    success_calls = sum(1 for u in all_usage if u.success)
    error_calls = total_calls_all - success_calls

    tokens_today = sum(u.total_tokens for u in today_usage)
    cost_today = sum(u.cost_usd for u in today_usage)
    calls_today = len(today_usage)

    tokens_month = sum(u.total_tokens for u in month_usage)
    cost_month = sum(u.cost_usd for u in month_usage)
    calls_month = len(month_usage)

    input_total = sum(u.input_tokens for u in all_usage)
    output_total = sum(u.output_tokens for u in all_usage)

    # --- Per-Feature Breakdown ---
    feature_stats = {}
    for u in all_usage:
        f = u.feature
        if f not in feature_stats:
            feature_stats[f] = {"calls": 0, "tokens": 0, "cost": 0.0, "errors": 0}
        feature_stats[f]["calls"] += 1
        feature_stats[f]["tokens"] += u.total_tokens
        feature_stats[f]["cost"] += u.cost_usd
        if not u.success:
            feature_stats[f]["errors"] += 1

    feature_labels = {
        "signal_extraction": "Signal Extraction",
        "value_extraction": "Value Extraction",
        "coaching_impulse": "AI Coaching",
        "trend_insights": "Trend Insights",
        "evening_message": "Abend-Nachricht",
        "mastery_today": "Mastery Today",
        "music_analysis": "Music AI",
    }

    feature_rows = ""
    sorted_features = sorted(feature_stats.items(), key=lambda x: x[1]["cost"], reverse=True)
    for feat, stats in sorted_features:
        label = feature_labels.get(feat, feat)
        err_tag = f"<span style='color:#fecaca;margin-left:6px;'>({stats['errors']} err)</span>" if stats["errors"] > 0 else ""
        pct = (stats["cost"] / total_cost_all * 100) if total_cost_all > 0 else 0
        bar_w = max(2, int(pct))
        feature_rows += f"""
        <div style="padding:10px 0;border-bottom:1px solid var(--line);">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <div>
              <b style="font-size:14px;">{label}</b>{err_tag}
              <div class="small">{stats['calls']} Calls • {stats['tokens']:,} Tokens</div>
            </div>
            <div style="text-align:right;">
              <b style="color:#f59e0b;">${stats['cost']:.4f}</b>
              <div class="small">{pct:.1f}%</div>
            </div>
          </div>
          <div style="height:4px;background:#1f2937;border-radius:2px;margin-top:6px;">
            <div style="height:4px;background:linear-gradient(90deg,#f59e0b,#ef4444);border-radius:2px;width:{bar_w}%;"></div>
          </div>
        </div>"""

    # --- System Stats ---
    total_patients = db.query(Patient).count()
    total_checkins = db.query(CheckIn).count()
    total_therapists = db.query(Therapist).count()
    total_outcomes = db.query(Outcome).count()
    total_logins = db.query(LoginEvent).count()
    checkins_today = db.query(CheckIn).filter(CheckIn.local_day == today).count()

    # Active patients (last 7 days)
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    active_patients_week = db.query(CheckIn.patient_id).filter(
        CheckIn.local_day >= week_ago
    ).distinct().count()

    # Average score
    from sqlalchemy import func as sa_func
    avg_score_row = db.query(sa_func.avg(CheckIn.score)).scalar()
    avg_score = round(avg_score_row, 1) if avg_score_row else 0

    # Risk distribution
    high_risk = db.query(CheckIn).filter(CheckIn.risk_level == "high").count()
    med_risk = db.query(CheckIn).filter(CheckIn.risk_level == "medium").count()
    low_risk = db.query(CheckIn).filter(CheckIn.risk_level == "low").count()

    # Pattern distribution (top 5)
    pattern_counts = {}
    all_checkins_patterns = db.query(CheckIn.pattern_code).filter(CheckIn.pattern_code.isnot(None)).all()
    for (pc,) in all_checkins_patterns:
        pattern_counts[pc] = pattern_counts.get(pc, 0) + 1
    top_patterns = sorted(pattern_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    pattern_html = ""
    for pc, cnt in top_patterns:
        label = PATTERNS.get(pc, pc)
        pattern_html += f"""<span class="tag" style="margin-bottom:4px;">{label}: {cnt}</span> """

    # --- Recent AI calls (last 20) ---
    recent_ai = db.query(TokenUsage).order_by(TokenUsage.created_at.desc()).limit(20).all()
    recent_rows = ""
    for u in recent_ai:
        label = feature_labels.get(u.feature, u.feature)
        ts = u.created_at.strftime("%d.%m %H:%M") if u.created_at else "–"
        status_dot = "<span style='color:#22c55e;'>●</span>" if u.success else "<span style='color:#ef4444;'>●</span>"
        recent_rows += f"""
        <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid rgba(255,255,255,.04);font-size:13px;">
          <div>{status_dot} {label}</div>
          <div style="color:var(--muted);">{u.total_tokens:,} tok • ${u.cost_usd:.4f}</div>
          <div class="small">{ts}</div>
        </div>"""

    # --- Daily usage chart (last 14 days, text-based) ---
    daily_data = {}
    for u in all_usage:
        if u.created_at:
            day = u.created_at.strftime("%Y-%m-%d")
            if day not in daily_data:
                daily_data[day] = {"tokens": 0, "cost": 0.0, "calls": 0}
            daily_data[day]["tokens"] += u.total_tokens
            daily_data[day]["cost"] += u.cost_usd
            daily_data[day]["calls"] += 1

    chart_html = ""
    for i in range(13, -1, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        day_label = (now - timedelta(days=i)).strftime("%d.%m")
        dd = daily_data.get(d, {"tokens": 0, "cost": 0.0, "calls": 0})
        max_tokens = max((daily_data.get((now - timedelta(days=j)).strftime("%Y-%m-%d"), {}).get("tokens", 0) for j in range(14)), default=1) or 1
        bar_h = max(2, int(dd["tokens"] / max_tokens * 60))
        is_today = "border:1px solid #f59e0b;" if d == today else ""
        chart_html += f"""
        <div style="display:flex;flex-direction:column;align-items:center;flex:1;min-width:0;">
          <div class="small" style="font-size:10px;margin-bottom:2px;">{dd['calls']}</div>
          <div style="width:100%;max-width:24px;height:{bar_h}px;background:linear-gradient(180deg,#f59e0b,#7c3aed);border-radius:4px;{is_today}"></div>
          <div class="small" style="font-size:9px;margin-top:3px;">{day_label}</div>
        </div>"""

    # --- Uptime / System ---
    services = []
    services.append(("AI (Anthropic)", "online" if ANTHROPIC_API_KEY else "offline"))
    services.append(("Stripe", "online" if STRIPE_SECRET_KEY else "offline"))
    services.append(("Twilio/WhatsApp", "online" if _twilio_enabled() else "offline"))
    services.append(("SMTP/Email", "online" if SMTP_HOST else "offline"))
    services.append(("Datenbank", "online"))

    services_html = ""
    for sname, status in services:
        color = "#22c55e" if status == "online" else "#6b7280"
        services_html += f"""
        <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid rgba(255,255,255,.04);">
          <span style="font-size:13px;">{sname}</span>
          <span style="color:{color};font-size:13px;font-weight:600;">● {status.upper()}</span>
        </div>"""

    # Estimated monthly cost projection
    days_elapsed = now.day
    if days_elapsed > 0 and cost_month > 0:
        projected_monthly = cost_month / days_elapsed * 30
    else:
        projected_monthly = 0.0

    body = f"""
      <div style="text-align:center;margin:0 0 20px">
        <div style="font-size:11px;color:#6b7280;letter-spacing:3px;">MASTER CONTROL</div>
        <h1 style="font-size:28px;margin:6px 0;">Bordcomputer</h1>
        <p class="small">{now.strftime('%d.%m.%Y %H:%M')} • Eingeloggt als <b>{t.name}</b></p>
      </div>

      <!-- SYSTEM STATUS -->
      <div class="card" style="margin-bottom:16px;padding:16px;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">
          <span style="font-size:20px;">🛰</span>
          <b style="font-size:15px;">System Status</b>
        </div>
        {services_html}
      </div>

      <!-- TOKEN KPIs -->
      <div class="grid3" style="margin-bottom:16px;">
        <div class="kpi" style="text-align:center;background:rgba(245,158,11,.05);border-color:rgba(245,158,11,.2);">
          <div class="small">HEUTE</div>
          <b style="color:#f59e0b;font-size:18px;">${cost_today:.4f}</b>
          <div class="small">{tokens_today:,} tok • {calls_today} calls</div>
        </div>
        <div class="kpi" style="text-align:center;background:rgba(99,102,241,.05);border-color:rgba(99,102,241,.2);">
          <div class="small">MONAT</div>
          <b style="color:#a5b4fc;font-size:18px;">${cost_month:.4f}</b>
          <div class="small">{tokens_month:,} tok • {calls_month} calls</div>
        </div>
        <div class="kpi" style="text-align:center;background:rgba(34,197,94,.05);border-color:rgba(34,197,94,.2);">
          <div class="small">GESAMT</div>
          <b style="color:#22c55e;font-size:18px;">${total_cost_all:.4f}</b>
          <div class="small">{total_tokens_all:,} tok • {total_calls_all} calls</div>
        </div>
      </div>

      <!-- TOKEN DETAIL -->
      <div class="card" style="margin-bottom:16px;padding:16px;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">
          <span style="font-size:20px;">🔢</span>
          <b style="font-size:15px;">Token Breakdown</b>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;">
          <div>
            <div class="small">Input Tokens</div>
            <b>{input_total:,}</b>
          </div>
          <div>
            <div class="small">Output Tokens</div>
            <b>{output_total:,}</b>
          </div>
          <div>
            <div class="small">Fehler</div>
            <b style="color:{'#fecaca' if error_calls > 0 else '#22c55e'};">{error_calls}</b>
            <span class="small"> / {total_calls_all}</span>
          </div>
        </div>
        <div style="height:8px"></div>
        <div class="small">Erfolgsrate: <b style="color:#22c55e;">{(success_calls / total_calls_all * 100) if total_calls_all > 0 else 100:.1f}%</b></div>
        <div class="small">Prognose Monat: <b style="color:#f59e0b;">${projected_monthly:.4f}</b></div>
      </div>

      <!-- DAILY CHART -->
      <div class="card" style="margin-bottom:16px;padding:16px;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">
          <span style="font-size:20px;">📊</span>
          <b style="font-size:15px;">Letzte 14 Tage</b>
        </div>
        <div style="display:flex;align-items:flex-end;gap:2px;height:80px;padding-top:10px;">
          {chart_html}
        </div>
        <div class="small" style="text-align:center;margin-top:6px;">Calls pro Tag (Balkenhöhe = Tokens)</div>
      </div>

      <!-- FEATURE BREAKDOWN -->
      <div class="card" style="margin-bottom:16px;padding:16px;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">
          <span style="font-size:20px;">⚡</span>
          <b style="font-size:15px;">Kosten nach Feature</b>
        </div>
        {feature_rows if feature_rows else "<p class='small'>Noch keine AI-Aufrufe.</p>"}
      </div>

      <!-- APP STATS -->
      <div class="card" style="margin-bottom:16px;padding:16px;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">
          <span style="font-size:20px;">📋</span>
          <b style="font-size:15px;">App-Statistiken</b>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;">
          <div class="kpi" style="text-align:center;padding:10px;">
            <div class="small">Patienten</div>
            <b>{total_patients}</b>
          </div>
          <div class="kpi" style="text-align:center;padding:10px;">
            <div class="small">Therapeuten</div>
            <b>{total_therapists}</b>
          </div>
          <div class="kpi" style="text-align:center;padding:10px;">
            <div class="small">Check-ins</div>
            <b>{total_checkins}</b>
          </div>
          <div class="kpi" style="text-align:center;padding:10px;">
            <div class="small">Heute</div>
            <b style="color:#f59e0b;">{checkins_today}</b>
          </div>
          <div class="kpi" style="text-align:center;padding:10px;">
            <div class="small">Aktiv (7d)</div>
            <b style="color:#22c55e;">{active_patients_week}</b>
          </div>
          <div class="kpi" style="text-align:center;padding:10px;">
            <div class="small">&#216; Score</div>
            <b>{avg_score}</b>
          </div>
        </div>
        <div style="height:10px"></div>
        <div class="small">Outcomes: {total_outcomes} • Logins: {total_logins}</div>
      </div>

      <!-- RISK DISTRIBUTION -->
      <div class="card" style="margin-bottom:16px;padding:16px;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">
          <span style="font-size:20px;">🎯</span>
          <b style="font-size:15px;">Risk-Verteilung</b>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;">
          <div style="text-align:center;padding:10px;background:rgba(34,197,94,.08);border-radius:12px;">
            <div class="small" style="color:#22c55e;">LOW</div>
            <b style="font-size:22px;color:#22c55e;">{low_risk}</b>
          </div>
          <div style="text-align:center;padding:10px;background:rgba(245,158,11,.08);border-radius:12px;">
            <div class="small" style="color:#f59e0b;">MEDIUM</div>
            <b style="font-size:22px;color:#f59e0b;">{med_risk}</b>
          </div>
          <div style="text-align:center;padding:10px;background:rgba(239,68,68,.08);border-radius:12px;">
            <div class="small" style="color:#ef4444;">HIGH</div>
            <b style="font-size:22px;color:#ef4444;">{high_risk}</b>
          </div>
        </div>
        <div style="height:10px"></div>
        <div class="small">Top Patterns: {pattern_html if pattern_html else 'Keine Daten'}</div>
      </div>

      <!-- RECENT AI CALLS -->
      <div class="card" style="margin-bottom:16px;padding:16px;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">
          <span style="font-size:20px;">🧠</span>
          <b style="font-size:15px;">Letzte AI-Aufrufe</b>
        </div>
        {recent_rows if recent_rows else "<p class='small'>Noch keine AI-Aufrufe aufgezeichnet.</p>"}
      </div>

      <div class="hr"></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
        <a href="/therapist" class="btn btn-outline" style="text-align:center;">Therapist Dashboard</a>
        <a href="/master-control/api" class="btn btn-outline" style="text-align:center;">JSON API</a>
      </div>
      <p class="small" style="text-align:center;margin-top:12px;">PTGO Master Control • {now.strftime('%Y')}</p>
    """
    return _page("Master Control — Bordcomputer", body, request=request)


MASTER_CONTROL_KEY = os.getenv("MASTER_CONTROL_KEY", hashlib.sha256((APP_SECRET + "master-control").encode()).hexdigest()[:32])


def _require_master_auth(request: Request, db):
    """Allow therapist session OR API key for master-control access."""
    # Check API key header first
    api_key = request.headers.get("x-master-key", "")
    if api_key and api_key == MASTER_CONTROL_KEY:
        return True
    # Fall back to therapist session
    try:
        require_therapist_login(request, db)
        return True
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/master-control/api")
def master_control_api(request: Request, db=Depends(get_db)):
    """JSON API endpoint for Master Control data. Supports CORS."""
    _require_master_auth(request, db)

    all_usage = db.query(TokenUsage).all()
    now = _now_local()
    today = now.date().isoformat()
    month_start = now.replace(day=1).strftime("%Y-%m-%d")

    today_usage = [u for u in all_usage if u.created_at and u.created_at.strftime("%Y-%m-%d") == today]
    month_usage = [u for u in all_usage if u.created_at and u.created_at.strftime("%Y-%m-%d") >= month_start]

    feature_stats = {}
    for u in all_usage:
        f = u.feature
        if f not in feature_stats:
            feature_stats[f] = {"calls": 0, "tokens": 0, "cost": 0.0, "errors": 0}
        feature_stats[f]["calls"] += 1
        feature_stats[f]["tokens"] += u.total_tokens
        feature_stats[f]["cost"] += round(u.cost_usd, 6)
        if not u.success:
            feature_stats[f]["errors"] += 1

    # Recent calls
    recent = db.query(TokenUsage).order_by(TokenUsage.created_at.desc()).limit(30).all()
    recent_list = [
        {
            "feature": u.feature, "tokens": u.total_tokens, "cost": round(u.cost_usd, 6),
            "success": u.success, "time": u.created_at.isoformat() if u.created_at else None,
            "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
        }
        for u in recent
    ]

    # Daily history (last 30 days)
    daily = {}
    for u in all_usage:
        if u.created_at:
            day = u.created_at.strftime("%Y-%m-%d")
            if day not in daily:
                daily[day] = {"tokens": 0, "cost": 0.0, "calls": 0, "errors": 0}
            daily[day]["tokens"] += u.total_tokens
            daily[day]["cost"] += round(u.cost_usd, 6)
            daily[day]["calls"] += 1
            if not u.success:
                daily[day]["errors"] += 1

    from starlette.responses import JSONResponse
    resp = JSONResponse({
        "timestamp": now.isoformat(),
        "today": {
            "tokens": sum(u.total_tokens for u in today_usage),
            "cost_usd": round(sum(u.cost_usd for u in today_usage), 6),
            "calls": len(today_usage),
        },
        "month": {
            "tokens": sum(u.total_tokens for u in month_usage),
            "cost_usd": round(sum(u.cost_usd for u in month_usage), 6),
            "calls": len(month_usage),
        },
        "all_time": {
            "tokens": sum(u.total_tokens for u in all_usage),
            "cost_usd": round(sum(u.cost_usd for u in all_usage), 6),
            "calls": len(all_usage),
            "input_tokens": sum(u.input_tokens for u in all_usage),
            "output_tokens": sum(u.output_tokens for u in all_usage),
            "errors": sum(1 for u in all_usage if not u.success),
        },
        "by_feature": feature_stats,
        "recent": recent_list,
        "daily": daily,
        "system": {
            "patients": db.query(Patient).count(),
            "therapists": db.query(Therapist).count(),
            "checkins": db.query(CheckIn).count(),
            "outcomes": db.query(Outcome).count(),
            "ai_enabled": bool(ANTHROPIC_API_KEY),
            "stripe_enabled": bool(STRIPE_SECRET_KEY),
            "twilio_enabled": _twilio_enabled(),
            "smtp_enabled": bool(SMTP_HOST),
        },
    })
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "x-master-key, content-type"
    return resp


@app.options("/master-control/api")
def master_control_api_cors():
    """CORS preflight for master control API."""
    from starlette.responses import Response
    resp = Response(status_code=204)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "x-master-key, content-type"
    return resp


# =========================================================
# PRODUCT SALES TRACKER
# =========================================================

def _sales_nav(active: str = "daily") -> str:
    """Navigation bar for the sales tracker pages."""
    tabs = [
        ("daily", "Heute", "/product"),
        ("weekly", "Woche", "/product/weekly"),
        ("monthly", "Monat", "/product/monthly"),
    ]
    items = ""
    for key, label, href in tabs:
        style = "background:var(--accent);color:#111827;font-weight:700;" if key == active else "color:var(--muted);"
        items += f'<a href="{href}" style="padding:8px 16px;border-radius:999px;font-size:14px;{style}">{label}</a>'
    return f'<div style="display:flex;gap:6px;margin-bottom:16px;">{items}</div>'


@app.get("/product", response_class=HTMLResponse)
def sales_daily(request: Request, db=Depends(get_db)):
    t = require_therapist_login(request, db)
    today = _now_local().date().isoformat()

    sales = (
        db.query(ProductSale)
        .filter(ProductSale.local_day == today)
        .all()
    )

    # Aggregate by product
    product_totals: Dict[str, Dict[str, Any]] = {}
    for s in sales:
        if s.product_name not in product_totals:
            product_totals[s.product_name] = {"qty": 0, "revenue": 0}
        product_totals[s.product_name]["qty"] += s.quantity
        product_totals[s.product_name]["revenue"] += s.quantity * s.price_cents

    # Sort by quantity descending
    sorted_products = sorted(product_totals.items(), key=lambda x: x[1]["qty"], reverse=True)

    total_qty = sum(v["qty"] for v in product_totals.values())
    total_revenue = sum(v["revenue"] for v in product_totals.values())

    # Daily goal: top product
    top_product_html = ""
    if sorted_products:
        top_name, top_data = sorted_products[0]
        top_product_html = f"""
        <div class="action-box" style="margin-bottom:16px">
            <p class="small" style="margin:0 0 4px">Tages-Bestseller</p>
            <div style="font-size:22px;font-weight:700;color:#f59e0b">{top_name}</div>
            <p class="small" style="margin:4px 0 0">{top_data['qty']}x verkauft &bull; {top_data['revenue']/100:.2f} &euro; Umsatz</p>
        </div>
        """

    # Product rows
    rows = ""
    for rank, (name, data) in enumerate(sorted_products, 1):
        pct = int(data["qty"] / max(total_qty, 1) * 100)
        rows += f"""
        <div class="kpi" style="margin-bottom:8px">
            <div style="display:flex;justify-content:space-between;align-items:center">
                <div>
                    <span style="color:var(--accent);font-weight:700;margin-right:8px">#{rank}</span>
                    <b>{name}</b>
                </div>
                <div style="text-align:right">
                    <b>{data['qty']}x</b>
                    <span class="small" style="margin-left:8px">{data['revenue']/100:.2f} &euro;</span>
                </div>
            </div>
            <div style="height:4px;background:#1f2937;border-radius:999px;margin-top:8px;">
                <div style="height:4px;background:#f59e0b;border-radius:999px;width:{pct}%"></div>
            </div>
        </div>
        """

    body = f"""
        <h1>Verkaufs-Tracker</h1>
        <p class="small">Eingeloggt als <b>{t.name}</b> &bull; <a href="/therapist">Dashboard</a> &bull; <a href="/therapist/logout">Logout</a></p>
        <div class="hr"></div>
        {_sales_nav("daily")}
        <h2>Heute &mdash; {today}</h2>
        {top_product_html}
        <div class="grid3" style="margin-bottom:16px">
            <div class="kpi"><span class="small">Produkte</span><b>{len(sorted_products)}</b></div>
            <div class="kpi"><span class="small">Verkauft</span><b>{total_qty}</b></div>
            <div class="kpi"><span class="small">Umsatz</span><b>{total_revenue/100:.2f}&euro;</b></div>
        </div>
        {rows if rows else "<p class='small'>Heute noch keine Verk&auml;ufe.</p>"}
        <div class="hr"></div>
        <h2>Verkauf erfassen</h2>
        <form method="post" action="/product/add">
            <label>Produktname</label>
            <input name="product_name" placeholder="z.B. Therapieband, &Ouml;l, Buch..." required>
            <div class="row">
                <div>
                    <label>Menge</label>
                    <input name="quantity" type="number" value="1" min="1" required>
                </div>
                <div>
                    <label>Preis (&euro;)</label>
                    <input name="price" type="number" step="0.01" min="0" placeholder="9.99" required>
                </div>
            </div>
            <button type="submit">Verkauf speichern</button>
        </form>
    """
    return _page("Verkaufs-Tracker", body, request=request)


@app.post("/product/add", response_class=HTMLResponse)
def sales_add(
    request: Request,
    product_name: str = Form(...),
    quantity: int = Form(...),
    price: float = Form(...),
    db=Depends(get_db),
):
    t = require_therapist_login(request, db)
    now = _now_local()
    sale = ProductSale(
        product_name=product_name.strip(),
        quantity=max(quantity, 1),
        price_cents=int(round(price * 100)),
        sold_at=now,
        local_day=now.date().isoformat(),
        therapist_id=t.id,
    )
    db.add(sale)
    db.commit()
    return RedirectResponse("/product", status_code=303)


@app.get("/product/weekly", response_class=HTMLResponse)
def sales_weekly(request: Request, db=Depends(get_db)):
    t = require_therapist_login(request, db)
    now = _now_local()
    week_start = (now - timedelta(days=now.weekday())).date()
    week_end = now.date()

    sales = (
        db.query(ProductSale)
        .filter(ProductSale.local_day >= week_start.isoformat())
        .filter(ProductSale.local_day <= week_end.isoformat())
        .all()
    )

    # Aggregate by product
    product_totals: Dict[str, Dict[str, Any]] = {}
    for s in sales:
        if s.product_name not in product_totals:
            product_totals[s.product_name] = {"qty": 0, "revenue": 0}
        product_totals[s.product_name]["qty"] += s.quantity
        product_totals[s.product_name]["revenue"] += s.quantity * s.price_cents

    sorted_products = sorted(product_totals.items(), key=lambda x: x[1]["qty"], reverse=True)
    total_qty = sum(v["qty"] for v in product_totals.values())
    total_revenue = sum(v["revenue"] for v in product_totals.values())

    # Daily breakdown for the week
    daily_breakdown = {}
    for s in sales:
        day = s.local_day
        if day not in daily_breakdown:
            daily_breakdown[day] = {"qty": 0, "revenue": 0, "top": {}}
        daily_breakdown[day]["qty"] += s.quantity
        daily_breakdown[day]["revenue"] += s.quantity * s.price_cents
        if s.product_name not in daily_breakdown[day]["top"]:
            daily_breakdown[day]["top"][s.product_name] = 0
        daily_breakdown[day]["top"][s.product_name] += s.quantity

    # Top product of the week
    top_html = ""
    if sorted_products:
        top_name, top_data = sorted_products[0]
        top_html = f"""
        <div class="action-box" style="margin-bottom:16px">
            <p class="small" style="margin:0 0 4px">Wochen-Bestseller</p>
            <div style="font-size:22px;font-weight:700;color:#f59e0b">{top_name}</div>
            <p class="small" style="margin:4px 0 0">{top_data['qty']}x verkauft &bull; {top_data['revenue']/100:.2f} &euro; Umsatz</p>
        </div>
        """

    # Product ranking
    rows = ""
    for rank, (name, data) in enumerate(sorted_products, 1):
        pct = int(data["qty"] / max(total_qty, 1) * 100)
        rows += f"""
        <div class="kpi" style="margin-bottom:8px">
            <div style="display:flex;justify-content:space-between;align-items:center">
                <div>
                    <span style="color:var(--accent);font-weight:700;margin-right:8px">#{rank}</span>
                    <b>{name}</b>
                </div>
                <div style="text-align:right">
                    <b>{data['qty']}x</b>
                    <span class="small" style="margin-left:8px">{data['revenue']/100:.2f} &euro;</span>
                </div>
            </div>
            <div style="height:4px;background:#1f2937;border-radius:999px;margin-top:8px;">
                <div style="height:4px;background:#f59e0b;border-radius:999px;width:{pct}%"></div>
            </div>
        </div>
        """

    # Daily breakdown rows
    daily_rows = ""
    for day in sorted(daily_breakdown.keys(), reverse=True):
        dd = daily_breakdown[day]
        top_prod = max(dd["top"].items(), key=lambda x: x[1]) if dd["top"] else ("—", 0)
        daily_rows += f"""
        <div class="kpi" style="margin-bottom:6px">
            <div style="display:flex;justify-content:space-between">
                <b>{day}</b>
                <span>{dd['qty']}x &bull; {dd['revenue']/100:.2f} &euro;</span>
            </div>
            <p class="small" style="margin:2px 0 0">Top: {top_prod[0]} ({top_prod[1]}x)</p>
        </div>
        """

    body = f"""
        <h1>Verkaufs-Tracker</h1>
        <p class="small">Eingeloggt als <b>{t.name}</b> &bull; <a href="/therapist">Dashboard</a> &bull; <a href="/therapist/logout">Logout</a></p>
        <div class="hr"></div>
        {_sales_nav("weekly")}
        <h2>Woche: {week_start.isoformat()} &mdash; {week_end.isoformat()}</h2>
        {top_html}
        <div class="grid3" style="margin-bottom:16px">
            <div class="kpi"><span class="small">Produkte</span><b>{len(sorted_products)}</b></div>
            <div class="kpi"><span class="small">Verkauft</span><b>{total_qty}</b></div>
            <div class="kpi"><span class="small">Umsatz</span><b>{total_revenue/100:.2f}&euro;</b></div>
        </div>
        <h2>Produkt-Ranking</h2>
        {rows if rows else "<p class='small'>Diese Woche noch keine Verk&auml;ufe.</p>"}
        <div class="hr"></div>
        <h2>Tages&uuml;bersicht</h2>
        {daily_rows if daily_rows else "<p class='small'>Keine Daten.</p>"}
    """
    return _page("Wochenansicht", body, request=request)


@app.get("/product/monthly", response_class=HTMLResponse)
def sales_monthly(request: Request, db=Depends(get_db)):
    t = require_therapist_login(request, db)
    now = _now_local()
    month_start = now.date().replace(day=1)
    month_end = now.date()
    month_name = now.strftime("%B %Y")

    sales = (
        db.query(ProductSale)
        .filter(ProductSale.local_day >= month_start.isoformat())
        .filter(ProductSale.local_day <= month_end.isoformat())
        .all()
    )

    # Aggregate by product
    product_totals: Dict[str, Dict[str, Any]] = {}
    for s in sales:
        if s.product_name not in product_totals:
            product_totals[s.product_name] = {"qty": 0, "revenue": 0}
        product_totals[s.product_name]["qty"] += s.quantity
        product_totals[s.product_name]["revenue"] += s.quantity * s.price_cents

    sorted_products = sorted(product_totals.items(), key=lambda x: x[1]["qty"], reverse=True)
    total_qty = sum(v["qty"] for v in product_totals.values())
    total_revenue = sum(v["revenue"] for v in product_totals.values())

    # Weekly breakdown
    weekly_breakdown: Dict[str, Dict[str, Any]] = {}
    for s in sales:
        sale_date = datetime.strptime(s.local_day, "%Y-%m-%d").date()
        week_num = sale_date.isocalendar()[1]
        week_key = f"KW {week_num}"
        if week_key not in weekly_breakdown:
            weekly_breakdown[week_key] = {"qty": 0, "revenue": 0, "top": {}}
        weekly_breakdown[week_key]["qty"] += s.quantity
        weekly_breakdown[week_key]["revenue"] += s.quantity * s.price_cents
        if s.product_name not in weekly_breakdown[week_key]["top"]:
            weekly_breakdown[week_key]["top"][s.product_name] = 0
        weekly_breakdown[week_key]["top"][s.product_name] += s.quantity

    # Monthly top product
    top_html = ""
    if sorted_products:
        top_name, top_data = sorted_products[0]
        top_html = f"""
        <div class="action-box" style="margin-bottom:16px">
            <p class="small" style="margin:0 0 4px">Monats-Bestseller</p>
            <div style="font-size:28px;font-weight:700;color:#f59e0b">{top_name}</div>
            <p class="small" style="margin:4px 0 0">{top_data['qty']}x verkauft &bull; {top_data['revenue']/100:.2f} &euro; Umsatz</p>
        </div>
        """

    # Full ranking
    rows = ""
    for rank, (name, data) in enumerate(sorted_products, 1):
        pct = int(data["qty"] / max(total_qty, 1) * 100)
        medal = ""
        if rank == 1:
            medal = " style='color:#fbbf24'"
        elif rank == 2:
            medal = " style='color:#94a3b8'"
        elif rank == 3:
            medal = " style='color:#b45309'"
        rows += f"""
        <div class="kpi" style="margin-bottom:8px">
            <div style="display:flex;justify-content:space-between;align-items:center">
                <div>
                    <span{medal}><b>#{rank}</b></span>
                    <b style="margin-left:8px">{name}</b>
                </div>
                <div style="text-align:right">
                    <b>{data['qty']}x</b>
                    <span class="small" style="margin-left:8px">{data['revenue']/100:.2f} &euro;</span>
                </div>
            </div>
            <div style="height:4px;background:#1f2937;border-radius:999px;margin-top:8px;">
                <div style="height:4px;background:linear-gradient(90deg,#f59e0b,#fbbf24);border-radius:999px;width:{pct}%"></div>
            </div>
        </div>
        """

    # Weekly breakdown rows
    weekly_rows = ""
    for wk in sorted(weekly_breakdown.keys()):
        wd = weekly_breakdown[wk]
        top_prod = max(wd["top"].items(), key=lambda x: x[1]) if wd["top"] else ("—", 0)
        weekly_rows += f"""
        <div class="kpi" style="margin-bottom:6px">
            <div style="display:flex;justify-content:space-between">
                <b>{wk}</b>
                <span>{wd['qty']}x &bull; {wd['revenue']/100:.2f} &euro;</span>
            </div>
            <p class="small" style="margin:2px 0 0">Top: {top_prod[0]} ({top_prod[1]}x)</p>
        </div>
        """

    # Daily goal: average per day
    days_elapsed = max((month_end - month_start).days + 1, 1)
    avg_per_day = total_qty / days_elapsed

    body = f"""
        <h1>Monats&uuml;bersicht</h1>
        <p class="small">Eingeloggt als <b>{t.name}</b> &bull; <a href="/therapist">Dashboard</a> &bull; <a href="/therapist/logout">Logout</a></p>
        <div class="hr"></div>
        {_sales_nav("monthly")}
        <h2>{month_name}</h2>
        {top_html}
        <div class="grid3" style="margin-bottom:16px">
            <div class="kpi"><span class="small">Produkte</span><b>{len(sorted_products)}</b></div>
            <div class="kpi"><span class="small">Gesamt</span><b>{total_qty}x</b></div>
            <div class="kpi"><span class="small">Umsatz</span><b>{total_revenue/100:.2f}&euro;</b></div>
        </div>
        <div class="kpi" style="margin-bottom:16px">
            <span class="small">Durchschnitt pro Tag</span>
            <b style="font-size:20px;color:#f59e0b">{avg_per_day:.1f} Verk&auml;ufe/Tag</b>
        </div>
        <h2>Produkt-Ranking</h2>
        {rows if rows else "<p class='small'>Dieser Monat noch keine Verk&auml;ufe.</p>"}
        <div class="hr"></div>
        <h2>Wochen&uuml;bersicht</h2>
        {weekly_rows if weekly_rows else "<p class='small'>Keine Daten.</p>"}
    """
    return _page("Monats\u00fcbersicht", body, request=request)


# =========================================================
# PAIN ANALYSIS ASSISTANT — AI-gestützter Schmerz-Analyse-Chat
# =========================================================

PAIN_ASSISTANT_SYSTEM_PROMPT = """Du bist ein hochpräziser Schmerz-Analyse-Assistent basierend auf praktischer manueller Erfahrung.

Dein Ziel ist es NICHT, Diagnosen im medizinischen Sinne zu stellen, sondern:
- das Schmerzbild klar einzuordnen
- Muster zu erkennen
- die Situation so zu strukturieren, dass eine gezielte manuelle oder einfache Intervention möglich wird

Du arbeitest wie ein erfahrener Praktiker:
- stellst gezielte, kurze Fragen
- gehst Schritt für Schritt vor
- vermeidest Überforderung
- denkst in Mustern, nicht in Theorie

ABLAUF — Gehe IMMER in dieser Reihenfolge vor:

STEP 1: Ort klären
- "Zeig mir genau, wo der Schmerz ist"
- Punkt / Linie / Fläche unterscheiden

STEP 2: Gefühl klären
- "Wie fühlt es sich an?"
  (ziehend / stechend / dumpf / Druck)

STEP 3: Trigger klären
- "Wann wird es schlimmer?"
  (Bewegung / Druck / Ruhe)

STEP 4: Verlauf
- "Seit wann ist es da?"
- plötzlich oder langsam gekommen?

STEP 5: Intensität / Veränderung
- besser / gleich / schlimmer

WICHTIGE REGELN:
- immer nur 1–2 Fragen gleichzeitig
- Fokus auf: Ort, Gefühl, Bewegung, Verlauf
- keine langen Erklärungen
- keine medizinischen Fachbegriffe
- keine Unsicherheit zeigen
- auf Antwort warten, dann nächste logische Frage

INTERNE EINORDNUNG (dem Patienten NICHT zeigen, nur für deine Fragesteuerung):

TYPE A: Linien-/Zugspannung — Schmerz zieht entlang einer Linie
TYPE B: Punktueller Schmerz — klar lokalisierbar, druckempfindlich
TYPE C: Gelenk-/Bewegungsproblem — bei Bewegung / Winkel spezifisch
TYPE D: Diffuse Spannung — großflächig, unklar

Nach jeder Antwort:
1. Kurz zusammenfassen: "Okay, ich sehe…"
2. Nächste präzise Frage stellen
3. Wenn genug Klarheit: einfache, sichere Handlung vorschlagen (leichte Druckanweisung, kleine Bewegung, Wahrnehmungsfokus)
4. Dann fragen: "Was passiert, wenn du das machst?"

NIEMALS:
- medizinische Diagnosen nennen
- komplexe Erklärungen geben
- 5 Dinge auf einmal sagen
- unsicher wirken

IMMER:
- klar
- ruhig
- direkt
- fokussiert

Ziel: maximale Klarheit in minimaler Zeit.

Starte das Gespräch mit einer freundlichen, kurzen Begrüßung und frage nach dem Schmerzort."""


@app.get("/pain-assistant", response_class=HTMLResponse)
def pain_assistant_page(request: Request, db=Depends(get_db)):
    """Pain Analysis Assistant — conversational chat UI."""
    pid = request.session.get("patient_id")
    if not pid:
        return RedirectResponse("/", status_code=303)

    # Clear conversation history on fresh page load
    request.session["pain_chat"] = "[]"

    body = """
    <h1>Schmerz-Analyse</h1>
    <p style="margin-bottom:16px">Beschreibe deinen Schmerz — ich führe dich Schritt für Schritt durch die Analyse.</p>
    <div class="hr"></div>

    <div id="chat-messages" style="min-height:120px;max-height:60vh;overflow-y:auto;padding:8px 0;">
      <div id="loading-initial" style="text-align:center;padding:20px;">
        <div style="display:inline-block;width:24px;height:24px;border:3px solid #1f2937;border-top-color:#f59e0b;border-radius:50%;animation:spin 0.8s linear infinite;"></div>
      </div>
    </div>

    <div class="hr"></div>

    <form id="chat-form" onsubmit="sendMessage(event)" style="display:flex;gap:8px;align-items:flex-end;">
      <textarea id="chat-input" rows="2" placeholder="Beschreibe deinen Schmerz..."
        style="flex:1;resize:none;font-size:15px;" disabled></textarea>
      <button type="submit" id="send-btn" disabled
        style="width:auto;min-width:60px;padding:12px 16px;margin-top:0;font-size:18px;">→</button>
    </form>

    <div id="voice-row" style="text-align:center;margin-top:10px;">
      <button type="button" id="voice-btn" onclick="toggleVoice()" disabled
        style="background:transparent;border:1px solid #263246;color:#94a3b8;width:auto;padding:10px 18px;font-size:14px;border-radius:12px;">
        🎤 Spracheingabe
      </button>
    </div>

    <p style="text-align:center;margin-top:14px;">
      <a href="/checkin/1">Check-in</a> •
      <a href="/progress">Progress</a> •
      <a href="/logout">Logout</a>
    </p>

    <style>
      @keyframes spin { to { transform: rotate(360deg); } }
      .msg { margin:10px 0; padding:12px 16px; border-radius:16px; max-width:85%; line-height:1.5; font-size:15px; word-wrap:break-word; }
      .msg-user { background:rgba(245,158,11,.12); border:1px solid rgba(245,158,11,.3); margin-left:auto; color:#e5e7eb; }
      .msg-ai { background:rgba(255,255,255,.04); border:1px solid #1f2937; margin-right:auto; color:#cbd5e1; }
      .msg-ai p { margin:4px 0; color:#cbd5e1; }
      .typing-dot { display:inline-block; width:8px; height:8px; background:#94a3b8; border-radius:50%; margin:0 2px; animation:blink 1.4s infinite both; }
      .typing-dot:nth-child(2) { animation-delay:0.2s; }
      .typing-dot:nth-child(3) { animation-delay:0.4s; }
      @keyframes blink { 0%,80%,100%{opacity:.2} 40%{opacity:1} }
      #chat-input:focus { border-color:#f59e0b; }
      .voice-active { border-color:#ef4444 !important; color:#ef4444 !important; animation:pulse-voice 1.5s ease-in-out infinite; }
      @keyframes pulse-voice { 0%,100%{box-shadow:0 0 0 0 rgba(239,68,68,.4)} 50%{box-shadow:0 0 0 8px rgba(239,68,68,0)} }
    </style>

    <script>
    const chatMessages = document.getElementById('chat-messages');
    const chatInput = document.getElementById('chat-input');
    const sendBtn = document.getElementById('send-btn');
    const voiceBtn = document.getElementById('voice-btn');
    let sending = false;
    let recognition = null;
    let isListening = false;

    // Load initial AI greeting
    (async function() {
      try {
        const resp = await fetch('/pain-assistant/chat', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({message: '__INIT__'})
        });
        const data = await resp.json();
        document.getElementById('loading-initial').remove();
        appendMessage(data.reply, 'ai');
        chatInput.disabled = false;
        sendBtn.disabled = false;
        voiceBtn.disabled = false;
        chatInput.focus();
      } catch(e) {
        document.getElementById('loading-initial').innerHTML =
          '<p style="color:#fecaca">Verbindung fehlgeschlagen. Bitte Seite neu laden.</p>';
      }
    })();

    function appendMessage(text, role) {
      const div = document.createElement('div');
      div.className = 'msg msg-' + role;
      div.style.display = 'flex';
      // Simple markdown: **bold**, newlines
      let html = text
        .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
        .replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>')
        .replace(/\\n/g, '<br>');
      div.innerHTML = html;
      chatMessages.appendChild(div);
      chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function showTyping() {
      const div = document.createElement('div');
      div.className = 'msg msg-ai';
      div.id = 'typing-indicator';
      div.innerHTML = '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>';
      chatMessages.appendChild(div);
      chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function removeTyping() {
      const el = document.getElementById('typing-indicator');
      if (el) el.remove();
    }

    async function sendMessage(e) {
      if (e) e.preventDefault();
      const text = chatInput.value.trim();
      if (!text || sending) return;

      sending = true;
      chatInput.value = '';
      chatInput.disabled = true;
      sendBtn.disabled = true;

      appendMessage(text, 'user');
      showTyping();

      try {
        const resp = await fetch('/pain-assistant/chat', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({message: text})
        });
        removeTyping();
        if (!resp.ok) {
          appendMessage('Fehler bei der Verbindung. Bitte versuche es erneut.', 'ai');
        } else {
          const data = await resp.json();
          appendMessage(data.reply, 'ai');
        }
      } catch(err) {
        removeTyping();
        appendMessage('Verbindung unterbrochen. Bitte versuche es erneut.', 'ai');
      }

      sending = false;
      chatInput.disabled = false;
      sendBtn.disabled = false;
      chatInput.focus();
    }

    // Enter to send (Shift+Enter for newline)
    chatInput.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    // Voice input via Web Speech API
    function toggleVoice() {
      if (!('webkitSpeechRecognition' in window || 'SpeechRecognition' in window)) {
        alert('Spracheingabe wird in diesem Browser nicht unterstützt.');
        return;
      }
      if (isListening) {
        recognition.stop();
        return;
      }
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      recognition = new SR();
      recognition.lang = 'de-DE';
      recognition.continuous = false;
      recognition.interimResults = true;

      recognition.onstart = () => {
        isListening = true;
        voiceBtn.classList.add('voice-active');
        voiceBtn.textContent = '🔴 Aufnahme...';
      };

      recognition.onresult = (event) => {
        let transcript = '';
        for (let i = 0; i < event.results.length; i++) {
          transcript += event.results[i][0].transcript;
        }
        chatInput.value = transcript;
      };

      recognition.onend = () => {
        isListening = false;
        voiceBtn.classList.remove('voice-active');
        voiceBtn.textContent = '🎤 Spracheingabe';
      };

      recognition.onerror = () => {
        isListening = false;
        voiceBtn.classList.remove('voice-active');
        voiceBtn.textContent = '🎤 Spracheingabe';
      };

      recognition.start();
    }
    </script>
    """
    return _page("PTGO • Schmerz-Analyse", body, request=request)


@app.post("/pain-assistant/chat")
async def pain_assistant_chat(request: Request, db=Depends(get_db)):
    """Handle chat messages for the Pain Analysis Assistant."""
    pid = request.session.get("patient_id")
    if not pid:
        raise HTTPException(status_code=401, detail="Nicht eingeloggt")

    if not ANTHROPIC_API_KEY:
        return {"reply": "Der Schmerz-Analyse-Assistent ist momentan nicht verfügbar. Bitte versuche es später erneut."}

    body = await request.json()
    user_message = body.get("message", "").strip()

    # Load conversation history from session
    try:
        history = json.loads(request.session.get("pain_chat", "[]"))
    except (json.JSONDecodeError, TypeError):
        history = []

    # Build messages for Claude API
    messages = list(history)

    if user_message == "__INIT__":
        # Initial greeting — no user message, just system prompt triggers first response
        pass
    else:
        messages.append({"role": "user", "content": user_message})

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5",
                "max_tokens": 500,
                "system": PAIN_ASSISTANT_SYSTEM_PROMPT,
                "messages": messages if messages else [{"role": "user", "content": "Hallo, ich habe Schmerzen und brauche Hilfe."}],
            },
            timeout=20,
        )
        resp.raise_for_status()
        resp_data = resp.json()
        _track_ai_usage("pain_assistant", resp_data, patient_id=pid)

        reply = resp_data["content"][0]["text"].strip()

        # Update conversation history
        if user_message == "__INIT__":
            # Add a synthetic user message for the init
            messages.append({"role": "user", "content": "Hallo, ich habe Schmerzen und brauche Hilfe."})
        messages.append({"role": "assistant", "content": reply})

        # Keep last 20 messages to avoid session bloat
        if len(messages) > 20:
            messages = messages[-20:]

        request.session["pain_chat"] = json.dumps(messages)

        return {"reply": reply}

    except Exception as e:
        _track_ai_error("pain_assistant", str(e), patient_id=pid)
        return {"reply": "Es ist ein Fehler aufgetreten. Bitte versuche es erneut."}


# =========================================================
# GAME — Snake
# =========================================================

@app.get("/game", response_class=HTMLResponse)
async def game_snake(request: Request):
    """A full Snake game with PTGO dark theme, mobile-friendly controls."""
    game_html = """
    <html><head>
      <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
      <title>PTGO Snake</title>
      <style>
        :root { --bg:#0b0f1a; --card:#0f172a; --muted:#94a3b8; --text:#e5e7eb; --accent:#f59e0b; --line:#1f2937; }
        * { margin:0; padding:0; box-sizing:border-box; }
        html, body { height:100%; overflow:hidden; }
        body {
          font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Inter,Arial,sans-serif;
          background:radial-gradient(1000px 600px at 50% -100px,#1f2a52,transparent),var(--bg);
          color:var(--text);
          display:flex; flex-direction:column; align-items:center; justify-content:center;
          touch-action:none;
        }
        .top-bar {
          position:absolute; top:12px; left:0; right:0;
          display:flex; align-items:center; justify-content:center; gap:24px;
          font-size:14px; color:var(--muted);
        }
        .top-bar .brand { font-weight:700; letter-spacing:.2px; color:var(--text); }
        .top-bar .score-display { font-weight:700; color:var(--accent); font-size:18px; }
        .top-bar .high-score { font-size:12px; color:var(--muted); }
        #game-container {
          position:relative;
          border:2px solid var(--line);
          border-radius:14px;
          overflow:hidden;
          box-shadow:0 20px 60px rgba(0,0,0,.5);
        }
        canvas { display:block; }
        #overlay {
          position:absolute; inset:0;
          display:flex; flex-direction:column; align-items:center; justify-content:center;
          background:rgba(11,15,26,.85);
          border-radius:14px;
          z-index:10;
        }
        #overlay.hidden { display:none; }
        #overlay h1 { font-size:28px; margin-bottom:8px; }
        #overlay p { color:var(--muted); margin-bottom:16px; font-size:14px; line-height:1.6; text-align:center; padding:0 20px; }
        #overlay .final-score { font-size:48px; font-weight:700; color:var(--accent); margin:8px 0; }
        .start-btn {
          background:linear-gradient(180deg,#fbbf24,#f59e0b);
          color:#111827; border:none; border-radius:14px;
          padding:14px 36px; font-weight:700; font-size:16px;
          cursor:pointer; margin-top:8px;
        }
        .start-btn:active { transform:scale(.97); }
        .controls {
          display:none; /* shown on touch devices */
          position:absolute; bottom:10px; left:0; right:0;
          justify-content:center; gap:6px;
        }
        @media (pointer:coarse) { .controls { display:flex; } }
        .ctrl-btn {
          width:56px; height:56px; border-radius:14px;
          background:rgba(255,255,255,.06); border:1px solid var(--line);
          color:var(--text); font-size:22px; cursor:pointer;
          display:flex; align-items:center; justify-content:center;
        }
        .ctrl-btn:active { background:rgba(245,158,11,.2); border-color:var(--accent); }
        .back-link {
          position:absolute; bottom:12px; left:0; right:0;
          text-align:center; font-size:12px;
        }
        .back-link a { color:var(--accent); text-decoration:none; }
      </style>
    </head>
    <body>
      <div class="top-bar">
        <span class="brand">PTGO Snake</span>
        <span>Score: <span class="score-display" id="score">0</span></span>
        <span class="high-score">Best: <span id="high-score">0</span></span>
      </div>

      <div id="game-container">
        <canvas id="canvas"></canvas>
        <div id="overlay">
          <h1>Snake</h1>
          <p>Pfeiltasten oder Swipe zum Steuern.<br>Sammle die goldenen Punkte!</p>
          <button class="start-btn" id="start-btn">Start</button>
        </div>
      </div>

      <div class="controls" id="controls">
        <button class="ctrl-btn" data-dir="up">&uarr;</button>
        <button class="ctrl-btn" data-dir="left">&larr;</button>
        <button class="ctrl-btn" data-dir="down">&darr;</button>
        <button class="ctrl-btn" data-dir="right">&rarr;</button>
      </div>

      <div class="back-link"><a href="/">&larr; Zur&uuml;ck zu PTGO</a></div>

      <script>
      (function(){
        const canvas = document.getElementById('canvas');
        const ctx = canvas.getContext('2d');
        const overlay = document.getElementById('overlay');
        const scoreEl = document.getElementById('score');
        const highScoreEl = document.getElementById('high-score');
        const container = document.getElementById('game-container');

        const CELL = 20;
        let COLS, ROWS, W, H;

        function resize() {
          const maxW = Math.min(window.innerWidth - 32, 480);
          const maxH = Math.min(window.innerHeight - 160, 480);
          COLS = Math.floor(maxW / CELL);
          ROWS = Math.floor(maxH / CELL);
          W = COLS * CELL;
          H = ROWS * CELL;
          canvas.width = W;
          canvas.height = H;
          container.style.width = W + 'px';
          container.style.height = H + 'px';
        }
        resize();

        let snake, dir, nextDir, food, score, highScore, speed, gameLoop, alive;
        highScore = parseInt(localStorage.getItem('ptgo_snake_hs') || '0');
        highScoreEl.textContent = highScore;

        function init() {
          const cx = Math.floor(COLS / 2);
          const cy = Math.floor(ROWS / 2);
          snake = [{x:cx,y:cy},{x:cx-1,y:cy},{x:cx-2,y:cy}];
          dir = {x:1,y:0};
          nextDir = {x:1,y:0};
          score = 0;
          speed = 120;
          alive = true;
          scoreEl.textContent = '0';
          placeFood();
        }

        function placeFood() {
          let pos;
          do {
            pos = {x:Math.floor(Math.random()*COLS), y:Math.floor(Math.random()*ROWS)};
          } while(snake.some(s=>s.x===pos.x&&s.y===pos.y));
          food = pos;
        }

        function draw() {
          // background
          ctx.fillStyle = '#0b0f1a';
          ctx.fillRect(0, 0, W, H);

          // grid lines (subtle)
          ctx.strokeStyle = 'rgba(255,255,255,.03)';
          ctx.lineWidth = 1;
          for (let x = 0; x <= W; x += CELL) {
            ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
          }
          for (let y = 0; y <= H; y += CELL) {
            ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
          }

          // food (golden glow)
          const fx = food.x * CELL + CELL/2;
          const fy = food.y * CELL + CELL/2;
          const glow = ctx.createRadialGradient(fx, fy, 2, fx, fy, CELL);
          glow.addColorStop(0, '#fbbf24');
          glow.addColorStop(0.6, 'rgba(245,158,11,.4)');
          glow.addColorStop(1, 'transparent');
          ctx.fillStyle = glow;
          ctx.fillRect(food.x*CELL, food.y*CELL, CELL, CELL);
          ctx.fillStyle = '#f59e0b';
          ctx.beginPath();
          ctx.arc(fx, fy, CELL/2 - 3, 0, Math.PI*2);
          ctx.fill();

          // snake
          snake.forEach((seg, i) => {
            const r = CELL/2 - 2;
            const sx = seg.x * CELL + CELL/2;
            const sy = seg.y * CELL + CELL/2;
            if (i === 0) {
              // head
              ctx.fillStyle = '#34d399';
              ctx.shadowColor = '#34d399';
              ctx.shadowBlur = 8;
            } else {
              const t = 1 - (i / snake.length) * 0.5;
              ctx.fillStyle = `rgba(52,211,153,${t})`;
              ctx.shadowBlur = 0;
            }
            ctx.beginPath();
            ctx.arc(sx, sy, r, 0, Math.PI*2);
            ctx.fill();
            ctx.shadowBlur = 0;
          });
        }

        function step() {
          if (!alive) return;
          dir = nextDir;
          const head = {x: snake[0].x + dir.x, y: snake[0].y + dir.y};

          // wall collision — wrap around
          if (head.x < 0) head.x = COLS - 1;
          if (head.x >= COLS) head.x = 0;
          if (head.y < 0) head.y = ROWS - 1;
          if (head.y >= ROWS) head.y = 0;

          // self collision
          if (snake.some(s => s.x === head.x && s.y === head.y)) {
            alive = false;
            gameOver();
            return;
          }

          snake.unshift(head);

          if (head.x === food.x && head.y === food.y) {
            score++;
            scoreEl.textContent = score;
            placeFood();
            // speed up slightly
            if (speed > 60) speed -= 2;
            clearInterval(gameLoop);
            gameLoop = setInterval(step, speed);
          } else {
            snake.pop();
          }

          draw();
        }

        function gameOver() {
          if (score > highScore) {
            highScore = score;
            localStorage.setItem('ptgo_snake_hs', highScore);
            highScoreEl.textContent = highScore;
          }
          overlay.classList.remove('hidden');
          overlay.innerHTML = `
            <h1>Game Over</h1>
            <div class="final-score">${score}</div>
            <p>Punkte gesammelt!${score > 0 && score >= highScore ? '<br>Neuer Highscore!' : ''}</p>
            <button class="start-btn" id="restart-btn">Nochmal</button>
          `;
          document.getElementById('restart-btn').addEventListener('click', startGame);
          clearInterval(gameLoop);
        }

        function startGame() {
          resize();
          init();
          overlay.classList.add('hidden');
          draw();
          gameLoop = setInterval(step, speed);
        }

        // keyboard controls
        document.addEventListener('keydown', e => {
          switch(e.key) {
            case 'ArrowUp':    case 'w': if (dir.y!==1)  nextDir={x:0,y:-1}; e.preventDefault(); break;
            case 'ArrowDown':  case 's': if (dir.y!==-1) nextDir={x:0,y:1};  e.preventDefault(); break;
            case 'ArrowLeft':  case 'a': if (dir.x!==1)  nextDir={x:-1,y:0}; e.preventDefault(); break;
            case 'ArrowRight': case 'd': if (dir.x!==-1) nextDir={x:1,y:0};  e.preventDefault(); break;
            case ' ': if (!alive) { startGame(); e.preventDefault(); } break;
          }
        });

        // touch swipe controls
        let touchStartX, touchStartY;
        canvas.addEventListener('touchstart', e => {
          touchStartX = e.touches[0].clientX;
          touchStartY = e.touches[0].clientY;
          e.preventDefault();
        }, {passive:false});
        canvas.addEventListener('touchmove', e => { e.preventDefault(); }, {passive:false});
        canvas.addEventListener('touchend', e => {
          if (!touchStartX) return;
          const dx = e.changedTouches[0].clientX - touchStartX;
          const dy = e.changedTouches[0].clientY - touchStartY;
          if (Math.abs(dx) < 10 && Math.abs(dy) < 10) return;
          if (Math.abs(dx) > Math.abs(dy)) {
            if (dx > 0 && dir.x !== -1) nextDir = {x:1,y:0};
            else if (dx < 0 && dir.x !== 1) nextDir = {x:-1,y:0};
          } else {
            if (dy > 0 && dir.y !== -1) nextDir = {x:0,y:1};
            else if (dy < 0 && dir.y !== 1) nextDir = {x:0,y:-1};
          }
        });

        // on-screen button controls
        document.querySelectorAll('.ctrl-btn').forEach(btn => {
          btn.addEventListener('click', () => {
            switch(btn.dataset.dir) {
              case 'up':    if (dir.y!==1)  nextDir={x:0,y:-1}; break;
              case 'down':  if (dir.y!==-1) nextDir={x:0,y:1};  break;
              case 'left':  if (dir.x!==1)  nextDir={x:-1,y:0}; break;
              case 'right': if (dir.x!==-1) nextDir={x:1,y:0};  break;
            }
          });
        });

        document.getElementById('start-btn').addEventListener('click', startGame);

        // initial draw
        init();
        draw();

        // handle window resize
        window.addEventListener('resize', () => {
          if (!alive) { resize(); draw(); }
        });
      })();
      </script>
    </body></html>
    """
    return HTMLResponse(game_html)
