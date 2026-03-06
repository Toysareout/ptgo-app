# app.py — Python 3.14.3 — FastAPI “Billionaire UI” MVP
# Features:
# - Patient onboarding (Name + Phone + Email)  ✅ NO email code required (Magic Link only)
# - WhatsApp via Twilio: daily reminder + patient results + therapist alerts
# - Magic-Link login: WhatsApp link logs patient in instantly (no password)
# - Progressive questions, patient score + guidance, clinician score + insights
# - Progress graphs (7/30), streak, 7-day compliance
# - Risk engine + early alerts for therapist
# - Multi-therapist accounts:
#   - Therapist register/login
#   - Assign patients to therapists
#
# DEPLOY:
# - Uvicorn behind Nginx (HTTPS)
# - systemd service
# - BASE_URL=https://app.ptgo.de

import os
import json
import time
import math
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

# Twilio WhatsApp (use requests API)
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "").strip()  # e.g. whatsapp:+14155238886
THERAPIST_WHATSAPP_TO = os.getenv("THERAPIST_WHATSAPP_TO", "").strip()  # e.g. +49... or whatsapp:+49...

# SMTP (optional; not required for Billionaire Magic-Link UX)
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASS = os.getenv("SMTP_PASS", "").strip()
SMTP_FROM = os.getenv("SMTP_FROM", "").strip()

# Stripe (optional)
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "").strip()

# Anthropic Claude AI
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

# Daily reminder loop
REMINDER_LOOP_SECONDS = int(os.getenv("REMINDER_LOOP_SECONDS", "30"))  # check every 30s
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

    # Legacy flag (kept for compatibility). In Billionaire mode we mark it True on onboarding.
    email_verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    verify_code_hash = Column(String(128), nullable=True)
    verify_code_expires_at = Column(DateTime, nullable=True)

    # Magic link login
    magic_token_hash = Column(String(128), nullable=True)
    magic_token_expires_at = Column(DateTime, nullable=True)

    # monetization
    subscription_active = Column(Boolean, default=False)
    stripe_customer_id = Column(String(128), nullable=True)

    # reminders
    reminder_enabled = Column(Boolean, default=True)
    reminder_time_local = Column(String(5), default="08:00")  # HH:MM in APP_TZ
    last_reminder_sent_on = Column(String(10), nullable=True)  # YYYY-MM-DD local

    therapist_id = Column(Integer, ForeignKey("therapists.id"), nullable=True)
    therapist = relationship("Therapist", back_populates="patients")

    checkins = relationship("CheckIn", back_populates="patient")


class CheckIn(Base):
    __tablename__ = "checkins"
    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), index=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    local_day = Column(String(10), index=True)  # YYYY-MM-DD in APP_TZ

    # raw answers (json)
    answers_json = Column(Text, nullable=False)

    # computed
    ptgo_score = Column(Integer, nullable=False, default=0)
    risk_level = Column(String(16), nullable=False, default="low")
    one_action = Column(Text, nullable=True)

    patient = relationship("Patient", back_populates="checkins")


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

app = FastAPI(title="PTGO Daily Loop")
app.add_middleware(SessionMiddleware, secret_key=APP_SECRET)


# =========================================================
# UTILS
# =========================================================

def _now_utc() -> datetime:
    return datetime.utcnow()

def _now_local() -> datetime:
    return datetime.now(ZoneInfo(APP_TZ))

def _clamp_int(v: int, lo: int, hi: int) -> int:
    return max(lo, min(int(v), hi))

def _hash_code(code: str) -> str:
    return hashlib.sha256((code + APP_SECRET).encode("utf-8")).hexdigest()

def _hash_magic(token: str) -> str:
    return hashlib.sha256((token + APP_SECRET + "MAGIC").encode("utf-8")).hexdigest()

def _hash_token(token: str) -> str:
    return hashlib.sha256((token + THERAPIST_TOKEN_SECRET).encode("utf-8")).hexdigest()

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


# =========================================================
# EMAIL (SMTP) — optional (not required for Magic-Link UX)
# =========================================================

def send_email(to_email: str, subject: str, body: str) -> None:
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS and SMTP_FROM):
        print("[DEV] SMTP not configured. Email suppressed.")
        print("To:", to_email)
        print("Subject:", subject)
        print(body)
        return

    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)

def send_email_verification(to_email: str, code: str) -> None:
    # kept for compatibility; not used in Billionaire flow
    body = f"Dein PTGO Bestätigungscode: {code}"
    send_email(to_email, "PTGO Bestätigungscode", body)


# =========================================================
# WHATSAPP (TWILIO)
# =========================================================

def _twilio_enabled() -> bool:
    return bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM)

def _send_whatsapp(to_e164_phone: str, message: str) -> None:
    if not _twilio_enabled():
        print("[DEV] WhatsApp ->", to_e164_phone)
        print(message)
        return

    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    data = {
        "From": TWILIO_WHATSAPP_FROM,
        "To": f"whatsapp:{to_e164_phone}",
        "Body": message.strip()
    }
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
        print(message)
        return

    prefix = f"[PTGO] {patient.name}: "
    _send_whatsapp(target, prefix + message)


# =========================================================
# UI HELPERS
# =========================================================

def _page(title: str, body_html: str, request: Optional[Request] = None) -> HTMLResponse:
    # Minimal “Billionaire UI” style.
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
      .card{background:linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,.02));border:1px solid var(--line);border-radius:18px;padding:18px 18px 14px;box-shadow:0 20px 60px rgba(0,0,0,.35);}
      h1{font-size:44px;line-height:1.05;margin:8px 0 12px;}
      h2{font-size:18px;margin:18px 0 10px;color:#f3f4f6}
      p{color:var(--muted);line-height:1.55}
      .hr{height:1px;background:var(--line);margin:18px 0;}
      label{display:block;color:#cbd5e1;font-size:13px;margin:10px 0 6px}
      input,select,textarea{width:100%;box-sizing:border-box;background:#0b1223;border:1px solid #263246;color:#e5e7eb;border-radius:12px;padding:12px 12px;font-size:16px;outline:none}
      input:focus,textarea:focus{border-color:#3b82f6}
      .row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
      button,.btn{display:inline-block;background:linear-gradient(180deg,#fbbf24,#f59e0b);color:#111827;border:none;border-radius:14px;padding:12px 16px;font-weight:700;font-size:16px;cursor:pointer;text-align:center}
      .btn{padding:12px 14px}
      .small{font-size:12px;color:var(--muted)}
      .code{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"Liberation Mono","Courier New",monospace;background:#0b1223;border:1px solid #263246;border-radius:12px;padding:10px;color:#e5e7eb;word-break:break-all}
      .grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
      .kpi{border:1px solid var(--line);border-radius:14px;padding:12px;background:rgba(255,255,255,.02)}
      .kpi b{display:block;font-size:20px;margin-top:4px}
      .tag{display:inline-block;font-size:12px;border:1px solid #374151;padding:4px 8px;border-radius:999px;color:#cbd5e1;margin-right:6px}
      .warn{color:#fecaca}
      .ok{color:#bbf7d0}
    </style>
    """

    top = f"""
      <div class="top">
        <div class="brand">PTGO Daily <span style="opacity:.5">•</span> Loop</div>
        <div class="pill">Verify</div>
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
          {body_html}
        </div>
      </div>
    </body></html>
    """
    return HTMLResponse(html)


# =========================================================
# SCORING ENGINE (PTGO Score → 1 Action via Claude AI)
# =========================================================

def _compute_score(answers: Dict[str, Any]) -> tuple:
    """Deterministic score + risk calculation (always runs, no API needed)."""
    mood     = _clamp_int(answers.get("mood", 5), 0, 10)
    sleep    = _clamp_int(answers.get("sleep", 5), 0, 10)
    body     = _clamp_int(answers.get("body", 5), 0, 10)
    stress   = _clamp_int(answers.get("stress", 5), 0, 10)
    craving  = _clamp_int(answers.get("craving", 0), 0, 10)
    avoidance= _clamp_int(answers.get("avoidance", 0), 0, 10)

    raw = (
        0.28 * mood +
        0.22 * sleep +
        0.18 * body +
        0.12 * (10 - stress) +
        0.10 * (10 - craving) +
        0.10 * (10 - avoidance)
    )
    ptgo_score = _clamp_int(int(round((raw / 10.0) * 100)), 0, 100)

    risk_points = 0
    if stress >= 8:   risk_points += 2
    if craving >= 7:  risk_points += 2
    if avoidance >= 7: risk_points += 2
    if mood <= 3:     risk_points += 2
    if sleep <= 3:    risk_points += 1

    if risk_points >= 6:
        risk = "high"
    elif risk_points >= 3:
        risk = "medium"
    else:
        risk = "low"

    return ptgo_score, risk


def _ai_action(answers: Dict[str, Any], ptgo_score: int, risk: str) -> str:
    """Call Claude API for a personalized 1-action recommendation. Falls back to static text on error."""
    if not ANTHROPIC_API_KEY:
        return _static_action(answers)

    note = (answers.get("note") or "").strip()
    prompt = (
        f"Du bist ein erfahrener Psychotherapeut mit Fokus auf PTBS, Sucht und posttraumatisches Wachstum.\n"
        f"Ein Patient hat heute seinen Daily State Check ausgefüllt:\n\n"
        f"- Stimmung: {answers.get('mood')}/10\n"
        f"- Schlaf: {answers.get('sleep')}/10\n"
        f"- Körper: {answers.get('body')}/10\n"
        f"- Stress: {answers.get('stress')}/10\n"
        f"- Craving: {answers.get('craving')}/10\n"
        f"- Vermeidung: {answers.get('avoidance')}/10\n"
        f"- PTGO Score: {ptgo_score}/100\n"
        f"- Risikolevel: {risk}\n"
        + (f"- Notiz des Patienten: \"{note}\"\n" if note else "") +
        f"\nGib dem Patienten GENAU 1 klare, konkrete Handlungsempfehlung für die nächsten 24 Stunden.\n"
        f"Regeln:\n"
        f"- Maximal 1-2 kurze Sätze\n"
        f"- Beginne mit 'Heute:'\n"
        f"- Immer 'du' verwenden, niemals 'Sie'\n"
        f"- Keine Diagnose, kein Medikament\n"
        f"- Praktisch und sofort umsetzbar\n"
        f"- Direkt und motivierend\n"
        f"- Auf Deutsch\n"
        f"Antworte NUR mit der Empfehlung, ohne Einleitung oder Erklärung."
    )

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
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        action = data["content"][0]["text"].strip()
        return action
    except Exception as e:
        print("[WARN] Claude API failed, using static fallback:", e)
        return _static_action(answers)


def _static_action(answers: Dict[str, Any]) -> str:
    """Fallback static actions if AI is unavailable."""
    sleep    = _clamp_int(answers.get("sleep", 5), 0, 10)
    stress   = _clamp_int(answers.get("stress", 5), 0, 10)
    craving  = _clamp_int(answers.get("craving", 0), 0, 10)
    avoidance= _clamp_int(answers.get("avoidance", 0), 0, 10)
    mood     = _clamp_int(answers.get("mood", 5), 0, 10)

    if sleep <= 4:
        return "Heute: 20 Min früher ins Bett + Handy 60 Min vor Schlaf aus (Wecker stellen)."
    elif stress >= 7:
        return "Heute: 3-Min Reset: 6 tiefe Atemzüge, dann 10-Min Spaziergang ohne Handy."
    elif craving >= 6:
        return "Heute: 1 Trigger vermeiden + 1 Ersatz: Wasser + 10 Liegestütze / 10 Kniebeugen."
    elif avoidance >= 6:
        return "Heute: 1 Mikro-Konfrontation: 5 Minuten an der Aufgabe arbeiten, dann stoppen (Timer)."
    elif mood <= 4:
        return "Heute: 1 Kontakt: 1 Person anrufen/Sprachnachricht senden (30 Sekunden ehrlich)."
    else:
        return "Heute: 15 Min Fokus-Block: wichtigste Sache (Timer) + danach kurz feiern (✅)."


def compute_score_and_action(answers: Dict[str, Any]) -> Dict[str, Any]:
    """
    Input: answers dict (values mostly 0-10 ints)
    Output:
      ptgo_score 0..100 (higher = better)
      risk_level: low | medium | high
      one_action: personalized AI recommendation for next 24h
    """
    ptgo_score, risk = _compute_score(answers)
    one_action = _ai_action(answers, ptgo_score, risk)
    return {"ptgo_score": ptgo_score, "risk_level": risk, "one_action": one_action}


# =========================================================
# REMINDERS (Daily WhatsApp)
# =========================================================

def _should_send_reminder_now(p: Patient, now_local: datetime) -> bool:
    if not p.reminder_enabled:
        return False

    # Already sent today?
    today = now_local.date().isoformat()
    if p.last_reminder_sent_on == today:
        return False

    # Parse time HH:MM
    try:
        hh, mm = (p.reminder_time_local or "08:00").split(":")
        target = now_local.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    except Exception:
        target = now_local.replace(hour=8, minute=0, second=0, microsecond=0)

    # send if within a small window after target time
    delta = (now_local - target).total_seconds()
    return 0 <= delta <= 60 * 6  # 6-minute window

def _patient_checked_in_today(db, p: Patient) -> bool:
    today = _now_local().date().isoformat()
    last = db.query(CheckIn).filter(CheckIn.patient_id == p.id, CheckIn.local_day == today).first()
    return bool(last)

def reminder_loop():
    while True:
        try:
            now_local = _now_local()
            db = SessionLocal()

            # Billionaire mode: do NOT gate on email_verified.
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
                    f"Dein Daily State Check wartet auf dich.\n"
                    f"30 Sekunden → PTGO Score → 1 Action.\n\n"
                    f"➡️ 1 Tap: {magic}"
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
    return {
        "ok": True,
        "time_utc": int(time.time()),
        "tz": APP_TZ,
        "base_url": BASE_URL,
        "twilio": _twilio_enabled(),
        "stripe": bool(STRIPE_SECRET_KEY and STRIPE_PRICE_ID),
    }


# =========================================================
# PATIENT — Billionaire UX (Magic Link Only)
# =========================================================

@app.get("/", response_class=HTMLResponse)
def index(request: Request, db=Depends(get_db)):
    if request.session.get("patient_id"):
        return RedirectResponse("/checkin", status_code=303)

    body = f"""
      <h1>Daily State Check</h1>
      <p>30 Sekunden pro Tag. Du bekommst sofort klare Empfehlungen. Dein Therapeut erhält strukturierte Hinweise.</p>
      <div class="hr"></div>

      <h2>Start (1-Tap Link per WhatsApp)</h2>
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
        <label>E-Mail (optional für später)</label>
        <input name="email" required placeholder="name@email.de" type="email">

        <div style="height:12px"></div>
        <button type="submit">Link per WhatsApp senden</button>

        <div style="height:10px"></div>
        <p class="small">
          Du bekommst deinen Zugang als 1-Tap Magic-Link per WhatsApp. Kein Passwort, kein Code.
        </p>
      </form>

      <div class="hr"></div>
      <p class="small">
        Therapeut? <a href="/therapist/login">Login</a>
      </p>
    """
    return _page("PTGO Daily • Start", body, request=request)


@app.post("/auth/start", response_class=HTMLResponse)
def auth_start(
    request: Request,
    name: str = Form(...),
    phone: str = Form(...),
    email: EmailStr = Form(...),
    db=Depends(get_db),
):
    """
    Billionaire UX:
    - No passwords
    - No email verification codes
    - Patient access happens via a 1-Tap Magic Link (WhatsApp/SMS)
    """
    name = name.strip()
    phone = phone.strip()
    email = str(email).strip().lower()

    patient = db.query(Patient).filter((Patient.phone == phone) | (Patient.email == email)).first()
    if not patient:
        patient = Patient(name=name, phone=phone, email=email)
        db.add(patient)
        db.commit()
        db.refresh(patient)
    else:
        patient.name = name
        patient.phone = phone
        patient.email = email
        db.commit()

    # Allow “continue now” on this device (session), but also send a Magic Link for 1-Tap access later.
    request.session["patient_id"] = patient.id

    # Treat onboarding as verified. (SMTP verification can be added later, but it is not required for Daily Loop.)
    if not patient.email_verified:
        patient.email_verified = True
        db.commit()

    magic = issue_magic_link(db, patient, ttl_minutes=60 * 24)

    # Send WhatsApp magic link (Twilio WhatsApp Sandbox or approved sender)
    try:
        msg = (
            f"Hallo {patient.name} 👋\n\n"
            f"Dein PTGO Daily Check (30 Sekunden)\n"
            f"→ PTGO Score\n"
            f"→ 1 Action\n\n"
            f"1 Tap: {magic}"
        )
        send_whatsapp_to_patient(patient, msg)
    except Exception as e:
        print("[WARN] WhatsApp send failed:", e)

    body = f"""
      <h1>Link gesendet ✅</h1>
      <p>Wir haben dir einen 1-Tap Link per WhatsApp geschickt.</p>
      <div style="height:10px"></div>
      <p class="small">Wenn du gerade am selben Gerät bist, kannst du auch direkt starten:</p>
      <p><a class="btn" href="{magic}">Jetzt starten</a></p>
      <div style="height:12px"></div>
      <p class="small">Oder kopiere den Link:</p>
      <div class="code">{magic}</div>
      <div style="height:14px"></div>
      <p><a href="/checkin">Zum Daily Check (Session)</a></p>
    """
    return _page("PTGO Daily • Link", body, request=request)


@app.post("/auth/verify", response_class=HTMLResponse)
def auth_verify(request: Request):
    # Deprecated: kept only so old links/forms don't 404.
    # Patient onboarding uses WhatsApp Magic Links now.
    return RedirectResponse("/checkin", status_code=303)


@app.get("/magic/{token}")
def magic_login(token: str, request: Request, db=Depends(get_db)):
    token_hash = _hash_magic(token.strip())
    patient = db.query(Patient).filter(Patient.magic_token_hash == token_hash).first()
    if not patient:
        raise HTTPException(status_code=401, detail="Invalid link")
    if not patient.magic_token_expires_at or _now_utc() > patient.magic_token_expires_at:
        raise HTTPException(status_code=401, detail="Link expired")

    request.session["patient_id"] = patient.id
    return RedirectResponse("/checkin", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# =========================================================
# THERAPIST AUTH + DASHBOARD
# =========================================================

def _hash_password(pw: str) -> str:
    return hashlib.sha256((pw + APP_SECRET + "PW").encode("utf-8")).hexdigest()

@app.get("/therapist/login", response_class=HTMLResponse)
def therapist_login_page(request: Request):
    body = """
      <h1>Therapist Login</h1>
      <form method="post" action="/therapist/login">
        <label>E-Mail</label>
        <input name="email" type="email" required>
        <label>Password</label>
        <input name="password" type="password" required>
        <div style="height:12px"></div>
        <button type="submit">Login</button>
      </form>
      <div class="hr"></div>
      <h2>Register</h2>
      <form method="post" action="/therapist/register">
        <label>Name</label>
        <input name="name" required>
        <label>E-Mail</label>
        <input name="email" type="email" required>
        <label>Phone (E.164)</label>
        <input name="phone" placeholder="+49...">
        <label>Password</label>
        <input name="password" type="password" required>
        <div style="height:12px"></div>
        <button type="submit">Create Account</button>
      </form>
      <div class="hr"></div>
      <p><a href="/">Back</a></p>
    """
    return _page("Therapist Login", body, request=request)

@app.post("/therapist/register", response_class=HTMLResponse)
def therapist_register(
    request: Request,
    name: str = Form(...),
    email: EmailStr = Form(...),
    phone: str = Form(""),
    password: str = Form(...),
    db=Depends(get_db),
):
    email = str(email).strip().lower()
    if db.query(Therapist).filter(Therapist.email == email).first():
        return _page("Therapist Register", "<h1>Fehler</h1><p>E-Mail existiert bereits.</p><p><a href='/therapist/login'>Zurück</a></p>", request=request)
    t = Therapist(
        name=name.strip(),
        email=email,
        phone=phone.strip() or None,
        password_hash=_hash_password(password),
    )
    db.add(t)
    db.commit()
    request.session["therapist_id"] = t.id
    return RedirectResponse("/therapist", status_code=303)

@app.post("/therapist/login", response_class=HTMLResponse)
def therapist_login(
    request: Request,
    email: EmailStr = Form(...),
    password: str = Form(...),
    db=Depends(get_db),
):
    email = str(email).strip().lower()
    t = db.query(Therapist).filter(Therapist.email == email).first()
    if not t or t.password_hash != _hash_password(password):
        return _page("Therapist Login", "<h1>Login fehlgeschlagen</h1><p><a href='/therapist/login'>Zurück</a></p>", request=request)
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
        last = (
            db.query(CheckIn)
            .filter(CheckIn.patient_id == p.id)
            .order_by(CheckIn.created_at.desc())
            .first()
        )
        if last:
            tag = f"<span class='tag'>Score {last.ptgo_score}</span><span class='tag'>Risk {last.risk_level}</span>"
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
      <p class="small">Logged in as <b>{t.name}</b> ({t.email}) • <a href="/therapist/logout">logout</a></p>
      <div class="hr"></div>

      <h2>Assign Patient</h2>
      <form method="post" action="/therapist/assign">
        <label>Patient Phone (E.164)</label>
        <input name="phone" placeholder="+49..." required>
        <div style="height:12px"></div>
        <button type="submit">Assign</button>
      </form>

      <div class="hr"></div>
      <h2>Patients</h2>
      {rows if rows else "<p class='small'>No patients yet.</p>"}
    """
    return _page("Therapist Dashboard", body, request=request)

@app.post("/therapist/assign", response_class=HTMLResponse)
def therapist_assign(request: Request, phone: str = Form(...), db=Depends(get_db)):
    t = require_therapist_login(request, db)
    phone = phone.strip()
    p = db.query(Patient).filter(Patient.phone == phone).first()
    if not p:
        return _page("Assign", "<h1>Patient nicht gefunden</h1><p>Bitte zuerst Patient onboarden.</p><p><a href='/therapist'>Back</a></p>", request=request)
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

    answers = json.loads(c.answers_json)

    body = f"""
      <h1>{p.name}</h1>
      <p class="small">{p.phone} • {p.email}</p>

      <div class="hr"></div>
      <div class="grid3">
        <div class="kpi"><span class="small">PTGO Score</span><b>{c.ptgo_score}</b></div>
        <div class="kpi"><span class="small">Risk</span><b>{c.risk_level}</b></div>
        <div class="kpi"><span class="small">Day</span><b>{c.local_day}</b></div>
      </div>

      <div class="hr"></div>
      <h2>1 Action</h2>
      <p>{c.one_action or ""}</p>

      <div class="hr"></div>
      <h2>Answers</h2>
      <div class="code">{json.dumps(answers, ensure_ascii=False, indent=2)}</div>

      <div style="height:16px"></div>
      <p><a href="/therapist">← Back</a></p>
    """
    return _page("Therapist • Checkin", body, request=request)


# =========================================================
# SETTINGS (patient)
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
          <option value="1" {"selected" if p.reminder_enabled else ""}>On</option>
          <option value="0" {"selected" if not p.reminder_enabled else ""}>Off</option>
        </select>

        <label>Reminder time (HH:MM)</label>
        <input name="time" value="{p.reminder_time_local or "08:00"}" placeholder="08:00">

        <div style="height:12px"></div>
        <button type="submit">Save</button>
      </form>

      <div class="hr"></div>
      <p><a href="/checkin">Back</a> • <a href="/logout">Logout</a></p>
    """
    return _page("PTGO Settings", body, request=request)

@app.post("/settings", response_class=HTMLResponse)
def settings_save(request: Request, enabled: str = Form("1"), time_str: str = Form("08:00"), db=Depends(get_db)):
    p = require_patient_login(request, db)
    p.reminder_enabled = (enabled == "1")
    p.reminder_time_local = (time_str or "08:00")[:5]
    db.commit()
    return RedirectResponse("/settings", status_code=303)


# =========================================================
# CHECKIN (Daily Check → PTGO Score → 1 Action)
# =========================================================

@app.get("/checkin", response_class=HTMLResponse)
def checkin_page(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)

    body = f"""
      <h1>Daily Check</h1>
      <p class="small">30 Sekunden. Ehrlich. Dann bekommst du 1 klare Action.</p>

      <div class="hr"></div>
      <form method="post" action="/checkin">
        <div class="row">
          <div>
            <label>Stimmung (0-10)</label>
            <input name="mood" type="number" min="0" max="10" value="5" required>
          </div>
          <div>
            <label>Schlaf (0-10)</label>
            <input name="sleep" type="number" min="0" max="10" value="5" required>
          </div>
        </div>

        <div class="row">
          <div>
            <label>Körper (0-10)</label>
            <input name="body" type="number" min="0" max="10" value="5" required>
          </div>
          <div>
            <label>Stress (0-10)</label>
            <input name="stress" type="number" min="0" max="10" value="5" required>
          </div>
        </div>

        <div class="row">
          <div>
            <label>Craving (0-10)</label>
            <input name="craving" type="number" min="0" max="10" value="0" required>
          </div>
          <div>
            <label>Vermeidung (0-10)</label>
            <input name="avoidance" type="number" min="0" max="10" value="0" required>
          </div>
        </div>

        <label>Notiz (optional)</label>
        <textarea name="note" rows="3" placeholder="Kurz & ehrlich..."></textarea>

        <div style="height:12px"></div>
        <button type="submit">Auswerten</button>
      </form>

      <div class="hr"></div>
      <p class="small">
        <a href="/progress">Progress</a> • <a href="/settings">Settings</a> • <a href="/logout">Logout</a>
      </p>
    """
    return _page("PTGO Daily • Check", body, request=request)


@app.post("/checkin", response_class=HTMLResponse)
def checkin_submit(
    request: Request,
    mood: int = Form(...),
    sleep: int = Form(...),
    body: int = Form(...),
    stress: int = Form(...),
    craving: int = Form(...),
    avoidance: int = Form(...),
    note: str = Form(""),
    db=Depends(get_db),
):
    p = require_patient_login(request, db)

    answers = {
        "mood": mood,
        "sleep": sleep,
        "body": body,
        "stress": stress,
        "craving": craving,
        "avoidance": avoidance,
        "note": (note or "").strip()
    }

    res = compute_score_and_action(answers)

    local_day = _now_local().date().isoformat()
    c = CheckIn(
        patient_id=p.id,
        local_day=local_day,
        answers_json=json.dumps(answers, ensure_ascii=False),
        ptgo_score=res["ptgo_score"],
        risk_level=res["risk_level"],
        one_action=res["one_action"],
    )
    db.add(c)
    db.commit()
    db.refresh(c)

    # Patient WhatsApp summary (optional)
    try:
        msg = (
            f"PTGO Ergebnis ✅\n\n"
            f"Score: {c.ptgo_score}/100\n"
            f"Risk: {c.risk_level}\n\n"
            f"1 Action:\n{c.one_action}\n\n"
            f"Details: {BASE_URL}/result/{c.id}"
        )
        send_whatsapp_to_patient(p, msg)
    except Exception as e:
        print("[WARN] WhatsApp patient result failed:", e)

    # Therapist alert on high risk
    therapist = p.therapist
    if c.risk_level in ("high",):
        try:
            tmsg = (
                f"⚠️ HIGH RISK Checkin\n"
                f"Score {c.ptgo_score}/100\n"
                f"Stress {answers.get('stress')}, Craving {answers.get('craving')}, Avoid {answers.get('avoidance')}\n"
                f"Link: {BASE_URL}/therapist/checkin/{c.id}"
            )
            send_whatsapp_to_therapist(p, therapist, tmsg)
        except Exception as e:
            print("[WARN] Therapist alert failed:", e)

    return RedirectResponse(f"/result/{c.id}", status_code=303)


@app.get("/result/{checkin_id}", response_class=HTMLResponse)
def result_page(checkin_id: int, request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)
    c = db.query(CheckIn).filter(CheckIn.id == checkin_id, CheckIn.patient_id == p.id).first()
    if not c:
        raise HTTPException(status_code=404)

    risk_color = "ok" if c.risk_level == "low" else ("warn" if c.risk_level == "high" else "")
    body = f"""
      <h1>Dein Ergebnis</h1>

      <div class="grid3">
        <div class="kpi"><span class="small">PTGO Score</span><b>{c.ptgo_score}</b></div>
        <div class="kpi"><span class="small">Risk</span><b class="{risk_color}">{c.risk_level}</b></div>
        <div class="kpi"><span class="small">Day</span><b>{c.local_day}</b></div>
      </div>

      <div class="hr"></div>
      <h2>1 Action (24h)</h2>
      <p>{c.one_action or ""}</p>

      <div class="hr"></div>
      <p class="small"><a href="/checkin">Next Check</a> • <a href="/progress">Progress</a></p>
    """
    return _page("PTGO Result", body, request=request)


@app.get("/progress", response_class=HTMLResponse)
def progress_page(request: Request, db=Depends(get_db)):
    p = require_patient_login(request, db)
    rows = (
        db.query(CheckIn)
        .filter(CheckIn.patient_id == p.id)
        .order_by(CheckIn.created_at.desc())
        .limit(30)
        .all()
    )

    items = ""
    for c in rows:
        items += f"""
          <div class="kpi" style="margin-bottom:10px">
            <div class="small">{c.local_day}</div>
            <b>{c.ptgo_score}</b>
            <div class="small">risk: {c.risk_level}</div>
            <div style="height:6px"></div>
            <a class="small" href="/result/{c.id}">open</a>
          </div>
        """

    body = f"""
      <h1>Progress</h1>
      <p class="small">Letzte 30 Check-ins</p>
      <div class="hr"></div>
      {items if items else "<p class='small'>Noch keine Daten.</p>"}
      <div class="hr"></div>
      <p class="small"><a href="/checkin">Back</a></p>
    """
    return _page("PTGO Progress", body, request=request)


# =========================================================
# PAY (optional, placeholder)
# =========================================================

@app.get("/pay", response_class=HTMLResponse)
def pay_page(request: Request):
    body = """
      <h1>Upgrade</h1>
      <p class="small">Stripe integration placeholder.</p>
      <div class="hr"></div>
      <p><a href="/checkin">Back</a></p>
    """
    return _page("Pay", body, request=request)

@app.post("/pay/start", response_class=HTMLResponse)
def pay_start():
    raise HTTPException(status_code=501, detail="Stripe not configured")

@app.get("/pay/success", response_class=HTMLResponse)
def pay_success(request: Request):
    return _page("Success", "<h1>Success</h1><p>Payment successful.</p><p><a href='/checkin'>Back</a></p>", request=request)

@app.get("/pay/cancel", response_class=HTMLResponse)
def pay_cancel(request: Request):
    return _page("Cancel", "<h1>Cancelled</h1><p>Payment cancelled.</p><p><a href='/checkin'>Back</a></p>", request=request)
