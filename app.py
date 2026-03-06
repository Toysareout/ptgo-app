# app.py — PTGO v2 — FastAPI
# Modules: 1 (Conversational), 2 (Signal Extraction), 4 (Login Tracking),
#          5 (Pattern Engine), 6 (Action Library), 7 (Action Engine),
#          8 (Result Screen), 9 (Outcome Feedback)
#
# DEPLOY:
# - Uvicorn behind Nginx (HTTPS)
# - systemd service
# - BASE_URL=https://app.ptgo.de

import os
import json
import time
import secrets
import hashlib
import threading
import smtplib
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

import requests
from email.message import EmailMessage

from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
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
    reminder_enabled = Column(Boolean, default=True)
    reminder_time_local = Column(String(5), default="08:00")
    last_reminder_sent_on = Column(String(10), nullable=True)
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
                if not _should_send_reminder_now(p, now_local):
                    continue
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

    body = f"""
      <h1>Daily State Check</h1>
      <p>30 Sekunden. Ehrlich. Dann bekommst du dein Pattern + 1 klare Action.</p>
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
    body = f"""
      <h1>Wie geht es dir heute wirklich?</h1>
      <p>Sei ehrlich. Niemand außer dir und deinem Therapeuten sieht das.</p>
      <form method="post" action="/checkin/1">
        <label>Dein Tagesgefühl</label>
        <div class="slider-wrap">
          <input type="range" name="daily_state" min="0" max="10" value="5"
            oninput="document.getElementById('ds_val').textContent=this.value">
          <span class="slider-val" id="ds_val">5</span> / 10
        </div>
        <label>Was beschäftigt dich heute? (optional)</label>
        <textarea name="overall_text" rows="3" placeholder="In einem Satz oder frei..."></textarea>
        <button type="submit">Weiter →</button>
      </form>
    """
    return _page("PTGO • Check 1/5", body, request=request, step=1, total=5)


@app.post("/checkin/1", response_class=HTMLResponse)
def checkin_1_post(request: Request, daily_state: int = Form(5), overall_text: str = Form(""), db=Depends(get_db)):
    p = require_patient_login(request, db)
    request.session["ci_daily_state"] = daily_state
    request.session["ci_overall_text"] = overall_text.strip()
    return RedirectResponse("/checkin/2", status_code=303)


@app.get("/checkin/2", response_class=HTMLResponse)
def checkin_2(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)
    body = f"""
      <h1>Was fordert dich heute am meisten?</h1>
      <form method="post" action="/checkin/2">
        <div class="row">
          <div>
            <label>Stress</label>
            <div class="slider-wrap">
              <input type="range" name="stress" min="0" max="10" value="5"
                oninput="document.getElementById('st_val').textContent=this.value">
              <span class="slider-val" id="st_val">5</span>
            </div>
          </div>
          <div>
            <label>Schlaf letzte Nacht</label>
            <div class="slider-wrap">
              <input type="range" name="sleep" min="0" max="10" value="5"
                oninput="document.getElementById('sl_val').textContent=this.value">
              <span class="slider-val" id="sl_val">5</span>
            </div>
          </div>
        </div>
        <label>Kontext (optional)</label>
        <textarea name="context_text" rows="2" placeholder="Arbeit, Familie, Finanzen..."></textarea>
        <button type="submit">Weiter →</button>
      </form>
    """
    return _page("PTGO • Check 2/5", body, request=request, step=2, total=5)


@app.post("/checkin/2", response_class=HTMLResponse)
def checkin_2_post(request: Request, stress: int = Form(5), sleep: int = Form(5), context_text: str = Form(""), db=Depends(get_db)):
    p = require_patient_login(request, db)
    request.session["ci_stress"] = stress
    request.session["ci_sleep"] = sleep
    request.session["ci_context_text"] = context_text.strip()
    return RedirectResponse("/checkin/3", status_code=303)


@app.get("/checkin/3", response_class=HTMLResponse)
def checkin_3(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)
    body = f"""
      <h1>Gibt es körperliche Beschwerden?</h1>
      <form method="post" action="/checkin/3">
        <label>Körpergefühl</label>
        <div class="slider-wrap">
          <input type="range" name="body" min="0" max="10" value="5"
            oninput="document.getElementById('bo_val').textContent=this.value">
          <span class="slider-val" id="bo_val">5</span> / 10
        </div>
        <label>Wo spürst du etwas? (optional)</label>
        <textarea name="body_text" rows="2" placeholder="z.B. Nacken, Schultern, Rücken..."></textarea>
        <label>Region (optional)</label>
        <select name="pain_region">
          <option value="">– keine –</option>
          <option value="neck">Nacken</option>
          <option value="shoulder">Schultern</option>
          <option value="upper_back">Oberer Rücken</option>
          <option value="lower_back">Unterer Rücken</option>
          <option value="head">Kopf</option>
          <option value="chest">Brust</option>
          <option value="stomach">Bauch</option>
          <option value="legs">Beine</option>
        </select>
        <button type="submit">Weiter →</button>
      </form>
    """
    return _page("PTGO • Check 3/5", body, request=request, step=3, total=5)


@app.post("/checkin/3", response_class=HTMLResponse)
def checkin_3_post(request: Request, body: int = Form(5), body_text: str = Form(""), pain_region: str = Form(""), db=Depends(get_db)):
    p = require_patient_login(request, db)
    request.session["ci_body"] = body
    request.session["ci_body_text"] = body_text.strip()
    request.session["ci_pain_region"] = pain_region
    return RedirectResponse("/checkin/4", status_code=303)


@app.get("/checkin/4", response_class=HTMLResponse)
def checkin_4(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)
    body = f"""
      <h1>Gibt es Sorgen oder Dinge, die du vermeidest?</h1>
      <form method="post" action="/checkin/4">
        <div class="row">
          <div>
            <label>Craving / Impulse</label>
            <div class="slider-wrap">
              <input type="range" name="craving" min="0" max="10" value="0"
                oninput="document.getElementById('cr_val').textContent=this.value">
              <span class="slider-val" id="cr_val">0</span>
            </div>
          </div>
          <div>
            <label>Vermeidung</label>
            <div class="slider-wrap">
              <input type="range" name="avoidance" min="0" max="10" value="0"
                oninput="document.getElementById('av_val').textContent=this.value">
              <span class="slider-val" id="av_val">0</span>
            </div>
          </div>
        </div>
        <label>Was vermeidest du gerade? (optional)</label>
        <textarea name="mental_text" rows="2" placeholder="Offen und ehrlich..."></textarea>
        <button type="submit">Weiter →</button>
      </form>
    """
    return _page("PTGO • Check 4/5", body, request=request, step=4, total=5)


@app.post("/checkin/4", response_class=HTMLResponse)
def checkin_4_post(request: Request, craving: int = Form(0), avoidance: int = Form(0), mental_text: str = Form(""), db=Depends(get_db)):
    p = require_patient_login(request, db)
    request.session["ci_craving"] = craving
    request.session["ci_avoidance"] = avoidance
    request.session["ci_mental_text"] = mental_text.strip()
    return RedirectResponse("/checkin/5", status_code=303)


@app.get("/checkin/5", response_class=HTMLResponse)
def checkin_5(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)
    body = f"""
      <h1>Was wäre heute ein guter Tag für dich?</h1>
      <p>Was müsste passieren damit du heute Abend sagst: "War ein guter Tag"?</p>
      <form method="post" action="/checkin/5">
        <label>Dein Tagesziel</label>
        <textarea name="goal_text" rows="3" placeholder="In einem Satz..."></textarea>
        <button type="submit">Auswerten →</button>
      </form>
    """
    return _page("PTGO • Check 5/5", body, request=request, step=5, total=5)


@app.post("/checkin/5", response_class=HTMLResponse)
def checkin_5_post(request: Request, goal_text: str = Form(""), db=Depends(get_db)):
    p = require_patient_login(request, db)

    # Collect all data from session
    data = {
        "daily_state": request.session.pop("ci_daily_state", 5),
        "overall_text": request.session.pop("ci_overall_text", ""),
        "stress": request.session.pop("ci_stress", 5),
        "sleep": request.session.pop("ci_sleep", 5),
        "context_text": request.session.pop("ci_context_text", ""),
        "body": request.session.pop("ci_body", 5),
        "body_text": request.session.pop("ci_body_text", ""),
        "pain_region": request.session.pop("ci_pain_region", ""),
        "craving": request.session.pop("ci_craving", 0),
        "avoidance": request.session.pop("ci_avoidance", 0),
        "mental_text": request.session.pop("ci_mental_text", ""),
        "goal_text": goal_text.strip(),
    }

    # Modul 2 – Signal Extraction
    signals = extract_signals(data)

    # Modul 5 – Pattern Engine
    pattern_code, pattern_label = detect_pattern(data)

    # Modul 7 – Action Engine
    action_code, action = get_action(pattern_code)

    # Score
    score, risk = compute_score(data)

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

    # Therapist alert on high risk
    if risk == "high":
        try:
            tmsg = (
                f"⚠️ HIGH RISK\n"
                f"Score {score}/100 • Pattern: {pattern_label}\n"
                f"Link: {BASE_URL}/therapist/checkin/{c.id}"
            )
            send_whatsapp_to_therapist(p, p.therapist, tmsg)
        except Exception as e:
            print("[WARN] Therapist alert failed:", e)

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

    body = f"""
      <h1>Dein Ergebnis</h1>

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
      <p class="small">
        <a href="/checkin/1">Neuer Check</a> •
        <a href="/progress">Progress</a> •
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

    body = f"""
      <h1>Progress</h1>
      <p class="small">Letzte 30 Check-ins</p>
      <div class="hr"></div>
      {items if items else "<p class='small'>Noch keine Daten.</p>"}
      <div class="hr"></div>
      <p class="small"><a href="/checkin/1">Neuer Check</a></p>
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
# THERAPIST
# =========================================================

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
