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


Index("ix_checkins_patient_day", CheckIn.patient_id, CheckIn.local_day)
Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
        text = resp.json()["content"][0]["text"].strip().replace("```json","").replace("```","").strip()
        vals = json.loads(text)
        for k in ["daily_state","stress","sleep","body","craving","avoidance"]:
            if k in vals:
                data[k] = _clamp_int(vals[k], 0, 10)
    except Exception as e:
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
        text = resp.json()["content"][0]["text"].strip()
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
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
      <p class="small">Eingeloggt als <b>{t.name}</b> • <a href="/therapist/logout">logout</a></p>
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
        return resp.json()["content"][0]["text"].strip()
    except Exception as e:
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
        return resp.json()["content"][0]["text"].strip()
    except Exception as e:
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
            return resp.json()["content"][0]["text"].strip()
        except Exception as e:
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
                coaching_prompt = resp.json()["content"][0]["text"].strip()
            except Exception as e:
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
