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

# Supabase dual-write (Chief Agent)
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", os.getenv("SUPABASE_SERVICE_KEY", "")).strip()


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


# =========================================================
# UNDERCOVER WEALTH SYSTEM — DB MODELS
# =========================================================

class WealthStream(Base):
    """Einkommensquelle / Revenue Stream."""
    __tablename__ = "wealth_streams"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    category = Column(String(64), nullable=False, default="active")  # active | passive | equity
    stream_type = Column(String(64), nullable=False, default="recurring")  # recurring | one-time | equity | license
    holding = Column(String(128), nullable=True)  # z.B. "PTGO Health", "THETOYSAREOUT", "AI Services"
    monthly_target = Column(Integer, nullable=False, default=0)  # Ziel in Cent
    monthly_actual = Column(Integer, nullable=False, default=0)  # Ist in Cent
    automation_level = Column(Integer, nullable=False, default=0)  # 0-100%
    status = Column(String(32), nullable=False, default="active")  # active | paused | planned | retired
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WealthAsset(Base):
    """Asset im Portfolio (IP, SaaS, Brand, Equity, Immobilie)."""
    __tablename__ = "wealth_assets"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    asset_type = Column(String(64), nullable=False, default="ip")  # ip | saas | brand | equity | real_estate | license
    holding = Column(String(128), nullable=True)
    current_value = Column(Integer, nullable=False, default=0)  # Wert in Cent
    monthly_revenue = Column(Integer, nullable=False, default=0)  # Monatl. Einnahmen in Cent
    growth_rate = Column(Float, nullable=False, default=0.0)  # Jährliche Wachstumsrate in %
    status = Column(String(32), nullable=False, default="active")  # active | developing | planned
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WealthSnapshot(Base):
    """Monatlicher Snapshot aller KPIs für Trendanalyse."""
    __tablename__ = "wealth_snapshots"
    id = Column(Integer, primary_key=True, index=True)
    period = Column(String(7), nullable=False, index=True)  # YYYY-MM
    total_revenue = Column(Integer, nullable=False, default=0)  # Cent
    passive_revenue = Column(Integer, nullable=False, default=0)  # Cent
    active_revenue = Column(Integer, nullable=False, default=0)  # Cent
    total_assets_value = Column(Integer, nullable=False, default=0)  # Cent
    automation_avg = Column(Integer, nullable=False, default=0)  # 0-100
    streams_count = Column(Integer, nullable=False, default=0)
    passive_ratio = Column(Float, nullable=False, default=0.0)  # 0-100%
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class WealthWeekly(Base):
    """Wöchentlicher Review — 5 Kernfragen."""
    __tablename__ = "wealth_weekly"
    id = Column(Integer, primary_key=True, index=True)
    week = Column(String(10), nullable=False, index=True)  # YYYY-WXX
    q1_passive_income = Column(Text, nullable=True)  # Was kam OHNE mein Zutun?
    q2_automated = Column(Text, nullable=True)  # Was habe ich automatisiert?
    q3_automation_progress = Column(Text, nullable=True)  # Welchen Stream näher an 100%?
    q4_visibility = Column(String(16), nullable=True)  # reduced | same | increased
    q5_in_vs_on = Column(Text, nullable=True)  # Stunden IN vs. AN dem System
    score = Column(Integer, nullable=True)  # Selbstbewertung 1-10
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# =========================================================
# ELITE PROGRAM — persönliches Selbstentwicklungs-Programm
# (Quellen: Jocko Willink, Cal Newport, Huberman, Attia, Naval,
#  Marcus Aurelius, Goggins, Mark Manson, David Deida, Chris Voss)
# =========================================================

class EliteProfile(Base):
    """Persönliches Elite-Programm — 1 Profil pro Session-Key."""
    __tablename__ = "elite_profiles"
    id = Column(Integer, primary_key=True, index=True)
    owner_key = Column(String(128), unique=True, nullable=False, index=True)
    display_name = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    level = Column(Integer, default=1)                    # 1..10
    streak_days = Column(Integer, default=0)
    longest_streak = Column(Integer, default=0)
    last_active_day = Column(String(10), nullable=True)
    one_skill = Column(String(128), nullable=True)
    keystone = Column(String(64), nullable=True)          # step_id die nie verpasst werden darf
    weight_kg = Column(Float, nullable=True)
    body_fat_pct = Column(Float, nullable=True)
    baseline_bench_kg = Column(Float, nullable=True)
    baseline_deadlift_kg = Column(Float, nullable=True)
    total_days_logged = Column(Integer, default=0)
    total_steps_completed = Column(Integer, default=0)
    total_steps_skipped = Column(Integer, default=0)
    last_level_change = Column(String(10), nullable=True)
    last_review_week = Column(String(10), nullable=True)


class EliteDay(Base):
    __tablename__ = "elite_days"
    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(Integer, ForeignKey("elite_profiles.id"), index=True, nullable=False)
    day = Column(String(10), index=True, nullable=False)
    steps_done_json = Column(Text, default="[]")
    steps_skipped_json = Column(Text, default="[]")
    metrics_json = Column(Text, default="{}")
    score = Column(Integer, default=0)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EliteWeeklyReview(Base):
    __tablename__ = "elite_weekly_reviews"
    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(Integer, ForeignKey("elite_profiles.id"), index=True, nullable=False)
    week = Column(String(10), index=True, nullable=False)   # YYYY-WXX
    wins = Column(Text, nullable=True)
    failures = Column(Text, nullable=True)
    next_focus = Column(Text, nullable=True)
    self_score = Column(Integer, nullable=True)             # 1..10
    completion_pct = Column(Integer, default=0)
    level_at_review = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)


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
# SUPABASE DUAL-WRITE (Chief Agent)
# =========================================================

def _supabase_push(table: str, data: dict):
    """Fire-and-forget push to Supabase. Never blocks the main app."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates",
            },
            json=data,
            timeout=5,
        )
    except Exception as e:
        print(f"[SUPABASE] Push to {table} failed: {e}")


def _supabase_upsert(table: str, data: dict):
    """Upsert to Supabase (for sync)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates",
            },
            json=data,
            timeout=10,
        )
        return r.status_code < 300
    except Exception:
        return False


def _sync_checkin_to_supabase(checkin: CheckIn, patient: Patient):
    """Push a check-in + patient to Supabase in background thread."""
    def _do():
        # Ensure patient exists in Supabase
        _supabase_upsert("patients", {
            "id": patient.id,
            "name": patient.name,
            "phone": patient.phone,
            "email": patient.email,
            "therapist_id": patient.therapist_id,
            "created_at": patient.created_at.isoformat() if patient.created_at else None,
        })
        # Push check-in
        _supabase_push("checkins", {
            "id": checkin.id,
            "patient_id": checkin.patient_id,
            "created_at": checkin.created_at.isoformat() if checkin.created_at else None,
            "local_day": checkin.local_day,
            "daily_state": checkin.daily_state,
            "overall_text": checkin.overall_text,
            "stress": checkin.stress,
            "sleep": checkin.sleep,
            "context_text": checkin.context_text,
            "body": checkin.body,
            "body_text": checkin.body_text,
            "pain_region": checkin.pain_region,
            "craving": checkin.craving,
            "avoidance": checkin.avoidance,
            "mental_text": checkin.mental_text,
            "goal_text": checkin.goal_text,
            "signals_json": checkin.signals_json,
            "pattern_code": checkin.pattern_code,
            "pattern_label": checkin.pattern_label,
            "action_code": checkin.action_code,
            "action_label": checkin.action_label,
            "action_text": checkin.action_text,
            "score": checkin.score,
            "risk_level": checkin.risk_level,
        })
    threading.Thread(target=_do, daemon=True).start()


def _sync_outcome_to_supabase(outcome: Outcome):
    """Push outcome to Supabase in background."""
    def _do():
        _supabase_push("outcomes", {
            "id": outcome.id,
            "checkin_id": outcome.checkin_id,
            "patient_id": outcome.patient_id,
            "rating": outcome.rating,
            "outcome_note": outcome.outcome_note,
            "created_at": outcome.created_at.isoformat() if outcome.created_at else None,
        })
    threading.Thread(target=_do, daemon=True).start()


# =========================================================
# APP
# =========================================================

app = FastAPI(title="PTGO Daily Loop v2")
app.add_middleware(SessionMiddleware, secret_key=APP_SECRET)


# =========================================================
# SKYCOACH AI — Gleitschirm-Fluganalyse als Sub-Mount
# =========================================================
# Lebt unter /skycoach. Eigene SQLite-DB (skycoach.db) und eigene Auth, damit
# PTGO-Daten unangetastet bleiben. Frontend-`dist/` wird vom selben Prozess
# ausgeliefert, sobald das Frontend einmal mit
# `SKYCOACH_BASE=/skycoach/ npm run build` gebaut wurde.

try:
    import sys as _sys
    _sys.path.insert(
        0,
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "skycoach", "backend"),
    )
    os.environ.setdefault("SKYCOACH_SECRET", APP_SECRET)
    os.environ.setdefault(
        "SKYCOACH_FRONTEND_DIST",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "skycoach", "frontend", "dist"),
    )
    from skycoach.main import app as _skycoach_app  # noqa: E402
    from skycoach.db import init_db as _init_skycoach_db  # noqa: E402

    _init_skycoach_db()
    app.mount("/skycoach", _skycoach_app)
    print("[skycoach] mounted at /skycoach")
except Exception as _e:
    print(f"[skycoach] not mounted: {_e}")


# =========================================================
# THETOYSAREOUT — served from local file
# =========================================================

_TTAO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "thetoysareout.html")
_LIVE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live.html")
_DASHBOARD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mein-dashboard.html")
_COACHING_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coaching.html")
_MINDSET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mindset.html")

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

@app.get("/mindset", response_class=HTMLResponse)
async def mindset_page():
    if os.path.exists(_MINDSET_PATH):
        return FileResponse(_MINDSET_PATH, media_type="text/html")
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

    # Supabase dual-write (Chief Agent)
    _sync_checkin_to_supabase(c, p)

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
    db.refresh(o)

    # Supabase dual-write (Chief Agent)
    _sync_outcome_to_supabase(o)

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
      <p class="small">Eingeloggt als <b>{t.name}</b> &bull; <a href="/therapist/chief-agent" style="color:#f59e0b;font-weight:700">🧠 KI-Chefagent</a> &bull; <a href="/product">Verkaufs-Tracker</a> &bull; <a href="/therapist/logout">logout</a></p>
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
# KI-CHEFAGENT — AI Chief Agent for Therapists
# =========================================================

def _chief_agent_collect_data(db, therapist_id: int) -> dict:
    """Collect and aggregate all patient data for the AI Chief Agent."""
    patients = db.query(Patient).filter(Patient.therapist_id == therapist_id).all()
    if not patients:
        return {"patients": [], "summary": {}}

    now = _now_local()
    today = now.strftime("%Y-%m-%d")
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")

    patient_data = []
    total_checkins_7d = 0
    all_patterns_7d = []
    all_scores_7d = []
    all_outcomes_7d = []
    risk_patients = []

    for p in patients:
        # Recent check-ins (last 7 days)
        recent = db.query(CheckIn).filter(
            CheckIn.patient_id == p.id,
            CheckIn.local_day >= week_ago,
        ).order_by(CheckIn.created_at.desc()).all()

        # Last check-in overall
        last = db.query(CheckIn).filter(CheckIn.patient_id == p.id).order_by(CheckIn.created_at.desc()).first()

        # All check-ins for trend
        all_checkins = db.query(CheckIn).filter(CheckIn.patient_id == p.id).order_by(CheckIn.created_at.desc()).limit(30).all()

        # Outcomes last 7 days
        outcomes = db.query(Outcome).filter(
            Outcome.patient_id == p.id,
            Outcome.created_at >= datetime.utcnow() - timedelta(days=7),
        ).all()

        scores = [c.score for c in recent if c.score is not None]
        patterns = [c.pattern_code for c in recent if c.pattern_code]
        actions = [c.action_code for c in recent if c.action_code]

        avg_score = round(sum(scores) / len(scores), 1) if scores else None
        trend_scores = [c.score for c in all_checkins[:14] if c.score is not None]
        score_trend = None
        if len(trend_scores) >= 4:
            first_half = sum(trend_scores[len(trend_scores)//2:]) / max(1, len(trend_scores) - len(trend_scores)//2)
            second_half = sum(trend_scores[:len(trend_scores)//2]) / max(1, len(trend_scores)//2)
            score_trend = "steigend" if second_half > first_half + 3 else "fallend" if second_half < first_half - 3 else "stabil"

        days_since_last = None
        if last:
            try:
                ld = datetime.strptime(last.local_day, "%Y-%m-%d")
                days_since_last = (now - ld.replace(tzinfo=now.tzinfo)).days
            except Exception:
                pass

        outcome_ratings = [o.rating for o in outcomes]

        pd_entry = {
            "name": p.name,
            "phone": p.phone,
            "checkins_7d": len(recent),
            "avg_score_7d": avg_score,
            "score_trend": score_trend,
            "last_score": last.score if last else None,
            "last_risk": last.risk_level if last else None,
            "last_pattern": last.pattern_label if last else None,
            "last_day": last.local_day if last else None,
            "days_since_last": days_since_last,
            "patterns_7d": patterns,
            "actions_7d": actions,
            "outcomes_7d": outcome_ratings,
            "stress_avg": round(sum(c.stress for c in recent if c.stress is not None) / max(1, len([c for c in recent if c.stress is not None])), 1) if recent else None,
            "sleep_avg": round(sum(c.sleep for c in recent if c.sleep is not None) / max(1, len([c for c in recent if c.sleep is not None])), 1) if recent else None,
            "craving_avg": round(sum(c.craving for c in recent if c.craving is not None) / max(1, len([c for c in recent if c.craving is not None])), 1) if recent else None,
        }
        patient_data.append(pd_entry)

        total_checkins_7d += len(recent)
        all_patterns_7d.extend(patterns)
        all_scores_7d.extend(scores)
        all_outcomes_7d.extend(outcome_ratings)

        if last and last.risk_level == "high":
            risk_patients.append(pd_entry)
        elif days_since_last and days_since_last >= 3:
            risk_patients.append(pd_entry)

    # Pattern frequency
    pattern_freq = {}
    for pat in all_patterns_7d:
        pattern_freq[pat] = pattern_freq.get(pat, 0) + 1

    # Outcome distribution
    outcome_dist = {"better": 0, "same": 0, "worse": 0}
    for o in all_outcomes_7d:
        if o in outcome_dist:
            outcome_dist[o] += 1

    summary = {
        "total_patients": len(patients),
        "active_patients_7d": len([p for p in patient_data if p["checkins_7d"] > 0]),
        "total_checkins_7d": total_checkins_7d,
        "avg_score_7d": round(sum(all_scores_7d) / len(all_scores_7d), 1) if all_scores_7d else None,
        "pattern_frequency": pattern_freq,
        "outcome_distribution": outcome_dist,
        "risk_patients": len(risk_patients),
    }

    return {
        "patients": patient_data,
        "risk_patients": risk_patients,
        "summary": summary,
        "today": today,
    }


def _chief_agent_briefing(data: dict) -> str:
    """Generate AI executive briefing from aggregated patient data."""
    if not ANTHROPIC_API_KEY:
        return "<p class='small' style='color:#fecaca'>Kein ANTHROPIC_API_KEY konfiguriert. KI-Analyse nicht verfügbar.</p>"

    summary = data.get("summary", {})
    patients = data.get("patients", [])
    risk_patients = data.get("risk_patients", [])

    patient_details = ""
    for p in patients:
        patient_details += (
            f"- {p['name']}: Score Ø{p['avg_score_7d']}, Trend {p['score_trend'] or '?'}, "
            f"Check-ins {p['checkins_7d']}, Letztes Pattern: {p['last_pattern'] or '–'}, "
            f"Stress Ø{p['stress_avg']}, Schlaf Ø{p['sleep_avg']}, Craving Ø{p['craving_avg']}, "
            f"Outcomes: {p['outcomes_7d']}, Letzter Check-in: {p['last_day'] or '–'} "
            f"({p['days_since_last']} Tage her)\n"
        )

    risk_details = ""
    for r in risk_patients:
        risk_details += f"- ⚠️ {r['name']}: Score {r['last_score']}, Risk {r['last_risk']}, {r['days_since_last']} Tage seit letztem Check-in\n"

    prompt = f"""Du bist der KI-Chefagent eines Therapeuten. Deine Aufgabe: Liefere ein Executive Briefing über alle Patienten.
Schreibe auf Deutsch. Sei direkt, konkret, handlungsorientiert. Kein Smalltalk.

DATEN:
- Patienten gesamt: {summary.get('total_patients', 0)}
- Aktive Patienten (7 Tage): {summary.get('active_patients_7d', 0)}
- Check-ins (7 Tage): {summary.get('total_checkins_7d', 0)}
- Durchschnitts-Score (7 Tage): {summary.get('avg_score_7d', '–')}
- Pattern-Häufigkeit: {json.dumps(summary.get('pattern_frequency', {}), ensure_ascii=False)}
- Outcome-Verteilung: {json.dumps(summary.get('outcome_distribution', {}), ensure_ascii=False)}
- Risiko-Patienten: {summary.get('risk_patients', 0)}

PATIENTEN-DETAILS:
{patient_details if patient_details else 'Keine Patientendaten vorhanden.'}

RISIKO-ALERTS:
{risk_details if risk_details else 'Keine Risiko-Patienten aktuell.'}

Erstelle ein strukturiertes Briefing mit diesen Abschnitten:

1. **LAGE-ÜBERBLICK** (2-3 Sätze Gesamtbild)
2. **SOFORT-HANDLUNGSBEDARF** (welche Patienten brauchen Aufmerksamkeit und warum)
3. **POSITIVE ENTWICKLUNGEN** (was läuft gut)
4. **MUSTER & TRENDS** (welche Patterns dominieren, was bedeutet das)
5. **EMPFEHLUNGEN** (3 konkrete nächste Schritte für den Therapeuten)

Formatiere als HTML mit <h3>, <p>, <ul>, <li> Tags. Keine Code-Blöcke. Maximal 500 Wörter."""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5", "max_tokens": 1500, "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        resp.raise_for_status()
        resp_data = resp.json()
        _track_ai_usage("chief_agent_briefing", resp_data)
        text = resp_data["content"][0]["text"].strip()
        return text
    except Exception as e:
        _track_ai_error("chief_agent_briefing", str(e))
        print("[WARN] Chief Agent briefing failed:", e)
        return "<p class='warn'>KI-Briefing konnte nicht erstellt werden. Bitte erneut versuchen.</p>"


def _chief_agent_answer(data: dict, question: str) -> str:
    """Answer a therapist question about their patient data."""
    if not ANTHROPIC_API_KEY:
        return "Kein ANTHROPIC_API_KEY konfiguriert."

    patients = data.get("patients", [])
    summary = data.get("summary", {})

    patient_info = ""
    for p in patients:
        patient_info += (
            f"- {p['name']}: Score Ø{p['avg_score_7d']}, Trend {p['score_trend'] or '?'}, "
            f"Check-ins {p['checkins_7d']}, Pattern: {p['last_pattern'] or '–'}, "
            f"Stress Ø{p['stress_avg']}, Schlaf Ø{p['sleep_avg']}, Craving Ø{p['craving_avg']}, "
            f"Outcomes: {p['outcomes_7d']}, Letzter Check-in: {p['last_day'] or '–'}\n"
        )

    prompt = f"""Du bist der KI-Chefagent eines Therapeuten. Du hast Zugang zu allen Patientendaten.
Beantworte die Frage des Therapeuten direkt, konkret und auf Deutsch. Kein Smalltalk.

ZUSAMMENFASSUNG:
- Patienten gesamt: {summary.get('total_patients', 0)}
- Aktive (7 Tage): {summary.get('active_patients_7d', 0)}
- Check-ins (7 Tage): {summary.get('total_checkins_7d', 0)}
- Score Ø: {summary.get('avg_score_7d', '–')}
- Patterns: {json.dumps(summary.get('pattern_frequency', {}), ensure_ascii=False)}
- Outcomes: {json.dumps(summary.get('outcome_distribution', {}), ensure_ascii=False)}

PATIENTEN:
{patient_info if patient_info else 'Keine Daten.'}

FRAGE DES THERAPEUTEN:
{question}

Antworte als HTML mit <p>, <ul>, <li>, <b> Tags. Maximal 300 Wörter. Sei präzise und handlungsorientiert."""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5", "max_tokens": 800, "messages": [{"role": "user", "content": prompt}]},
            timeout=20,
        )
        resp.raise_for_status()
        resp_data = resp.json()
        _track_ai_usage("chief_agent_chat", resp_data)
        return resp_data["content"][0]["text"].strip()
    except Exception as e:
        _track_ai_error("chief_agent_chat", str(e))
        return "<p class='warn'>Fehler bei der KI-Antwort. Bitte erneut versuchen.</p>"


@app.get("/therapist/chief-agent", response_class=HTMLResponse)
def chief_agent_dashboard(request: Request, db=Depends(get_db)):
    t = require_therapist_login(request, db)
    data = _chief_agent_collect_data(db, t.id)
    summary = data.get("summary", {})
    patients = data.get("patients", [])
    risk_patients = data.get("risk_patients", [])

    # KPI cards
    avg_score = summary.get("avg_score_7d")
    score_color = "#bbf7d0" if avg_score and avg_score >= 60 else "#fecaca" if avg_score and avg_score < 40 else "#f59e0b"

    kpi_html = f"""
    <div class="grid3" style="margin-bottom:20px">
      <div class="kpi"><span class="small">Patienten</span><b>{summary.get('total_patients', 0)}</b></div>
      <div class="kpi"><span class="small">Aktiv (7T)</span><b>{summary.get('active_patients_7d', 0)}</b></div>
      <div class="kpi"><span class="small">Check-ins (7T)</span><b>{summary.get('total_checkins_7d', 0)}</b></div>
    </div>
    <div class="grid3" style="margin-bottom:20px">
      <div class="kpi"><span class="small">Score Ø</span><b style="color:{score_color}">{avg_score or '–'}</b></div>
      <div class="kpi"><span class="small">Risiko-Pat.</span><b style="color:{'#fecaca' if summary.get('risk_patients', 0) > 0 else '#bbf7d0'}">{summary.get('risk_patients', 0)}</b></div>
      <div class="kpi"><span class="small">Outcomes</span><b style="font-size:14px">👍{summary.get('outcome_distribution', {}).get('better', 0)} 😐{summary.get('outcome_distribution', {}).get('same', 0)} 👎{summary.get('outcome_distribution', {}).get('worse', 0)}</b></div>
    </div>
    """

    # Pattern frequency bars
    pattern_freq = summary.get("pattern_frequency", {})
    max_freq = max(pattern_freq.values()) if pattern_freq else 1
    pattern_html = ""
    for code, count in sorted(pattern_freq.items(), key=lambda x: -x[1]):
        pct = int((count / max_freq) * 100)
        label = PATTERNS.get(code, code)
        pattern_html += f"""
        <div style="margin:6px 0">
          <div class="small" style="margin-bottom:2px">{label} ({count}x)</div>
          <div style="height:6px;background:#1f2937;border-radius:999px">
            <div style="height:6px;background:linear-gradient(90deg,#6366f1,#a78bfa);border-radius:999px;width:{pct}%"></div>
          </div>
        </div>
        """

    # Risk alerts
    risk_html = ""
    for r in risk_patients:
        risk_reason = []
        if r.get("last_risk") == "high":
            risk_reason.append("Hohes Risiko")
        if r.get("days_since_last") and r["days_since_last"] >= 3:
            risk_reason.append(f"{r['days_since_last']} Tage inaktiv")
        risk_html += f"""
        <div style="background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.3);border-radius:12px;padding:12px;margin:8px 0">
          <b style="color:#fecaca">{r['name']}</b>
          <span class="small"> — {', '.join(risk_reason)}</span>
          <div class="small" style="margin-top:4px">Score: {r.get('last_score', '–')} | Pattern: {r.get('last_pattern', '–')} | Letzter Check-in: {r.get('last_day', '–')}</div>
        </div>
        """

    # Patient overview table
    patient_rows = ""
    for p in sorted(patients, key=lambda x: x.get("avg_score_7d") or 999):
        s = p.get("avg_score_7d")
        sc = "#bbf7d0" if s and s >= 60 else "#fecaca" if s and s < 40 else "#f59e0b"
        trend_icon = "📈" if p.get("score_trend") == "steigend" else "📉" if p.get("score_trend") == "fallend" else "➡️"
        patient_rows += f"""
        <div class="kpi" style="margin-bottom:8px;display:grid;grid-template-columns:1fr auto;align-items:center">
          <div>
            <b>{p['name']}</b>
            <div class="small">Check-ins: {p['checkins_7d']} | Pattern: {p.get('last_pattern') or '–'}</div>
          </div>
          <div style="text-align:right">
            <span style="font-size:20px;font-weight:700;color:{sc}">{s or '–'}</span>
            <span style="font-size:16px">{trend_icon}</span>
          </div>
        </div>
        """

    # AI Briefing (loaded via iframe/fetch or inline)
    briefing = _chief_agent_briefing(data)

    body = f"""
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
        <h1 style="margin:0">🧠 KI-Chefagent</h1>
        <span class="pill" style="border-color:#6366f1;color:#a5b4fc">AI Powered</span>
      </div>
      <p class="small" style="margin-bottom:16px">
        Dein intelligenter Assistent — analysiert alle Patientendaten und liefert Insights direkt.
        <br><a href="/therapist">← Zum Dashboard</a>
      </p>

      <div class="hr"></div>

      <!-- KPIs -->
      <h2>📊 Überblick (letzte 7 Tage)</h2>
      {kpi_html}

      <div class="hr"></div>

      <!-- AI BRIEFING -->
      <h2>📋 KI-Briefing</h2>
      <div style="background:rgba(99,102,241,.06);border:1px solid rgba(99,102,241,.25);border-radius:16px;padding:20px;margin:12px 0;line-height:1.7">
        {briefing}
      </div>

      <div class="hr"></div>

      <!-- RISK ALERTS -->
      <h2>🚨 Sofort-Handlungsbedarf</h2>
      {risk_html if risk_html else "<p class='small' style='color:#bbf7d0'>Keine Risiko-Patienten aktuell. Alles im grünen Bereich.</p>"}

      <div class="hr"></div>

      <!-- PATTERN FREQUENCY -->
      <h2>🔍 Pattern-Häufigkeit (7 Tage)</h2>
      {pattern_html if pattern_html else "<p class='small'>Keine Patterns erkannt.</p>"}

      <div class="hr"></div>

      <!-- PATIENT RANKING -->
      <h2>👥 Patienten-Ranking (Score aufsteigend)</h2>
      {patient_rows if patient_rows else "<p class='small'>Keine Patienten zugewiesen.</p>"}

      <div class="hr"></div>

      <!-- SYNC BUTTON -->
      <h2>🔄 Daten-Sync</h2>
      <p class="small">Alle bestehenden Daten nach Supabase pushen — damit der Vercel-Chefagent sie auch hat.</p>
      <form method="post" action="/therapist/chief-agent/sync">
        <button type="submit" style="background:linear-gradient(180deg,#6366f1,#4f46e5);font-size:14px;padding:12px">Jetzt synchronisieren</button>
      </form>

      <div class="hr"></div>

      <!-- INTERACTIVE CHAT -->
      <h2>💬 Frag den Chefagenten</h2>
      <p class="small">Stelle Fragen zu deinen Patienten — der KI-Agent antwortet basierend auf allen Daten.</p>
      <div id="chat-history" style="margin:12px 0"></div>
      <form id="chief-chat-form" onsubmit="return askChief(event)">
        <textarea id="chief-q" rows="2" placeholder="z.B. Welcher Patient macht die besten Fortschritte? Wer braucht mehr Aufmerksamkeit?" style="margin-bottom:8px"></textarea>
        <button type="submit" id="chief-btn">Frage stellen</button>
      </form>

      <script>
      async function askChief(e) {{
        e.preventDefault();
        const q = document.getElementById('chief-q').value.trim();
        if (!q) return false;
        const btn = document.getElementById('chief-btn');
        const hist = document.getElementById('chat-history');
        btn.disabled = true;
        btn.textContent = 'Denke nach...';

        // Add question bubble
        hist.innerHTML += '<div style="background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.3);border-radius:12px;padding:12px;margin:8px 0"><b>Du:</b> ' + q.replace(/</g,'&lt;') + '</div>';

        try {{
          const resp = await fetch('/therapist/chief-agent/ask', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
            body: 'question=' + encodeURIComponent(q),
          }});
          const html = await resp.text();
          hist.innerHTML += '<div style="background:rgba(99,102,241,.06);border:1px solid rgba(99,102,241,.25);border-radius:12px;padding:12px;margin:8px 0;line-height:1.6"><b>🧠 Chefagent:</b><br>' + html + '</div>';
        }} catch(err) {{
          hist.innerHTML += '<div style="color:#fecaca;padding:8px">Fehler: ' + err.message + '</div>';
        }}

        document.getElementById('chief-q').value = '';
        btn.disabled = false;
        btn.textContent = 'Frage stellen';
        hist.scrollIntoView({{behavior:'smooth', block:'end'}});
        return false;
      }}
      </script>
    """
    return _page("KI-Chefagent", body, request=request)


@app.post("/therapist/chief-agent/ask", response_class=HTMLResponse)
def chief_agent_ask(request: Request, question: str = Form(...), db=Depends(get_db)):
    t = require_therapist_login(request, db)
    data = _chief_agent_collect_data(db, t.id)
    answer = _chief_agent_answer(data, question.strip())
    return HTMLResponse(answer)


@app.post("/therapist/chief-agent/sync", response_class=HTMLResponse)
def chief_agent_sync(request: Request, db=Depends(get_db)):
    """One-click sync: push ALL existing data to Supabase for the Chief Agent."""
    t = require_therapist_login(request, db)
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return HTMLResponse("<h2>Fehler</h2><p>SUPABASE_URL und SUPABASE_SERVICE_KEY muessen gesetzt sein.</p><p><a href='/therapist/chief-agent'>Zurueck</a></p>")

    # Sync therapist
    _supabase_upsert("therapists", {
        "id": t.id, "email": t.email, "name": t.name, "phone": t.phone or "",
        "password_hash": "SYNCED", "created_at": t.created_at.isoformat() if t.created_at else None,
    })

    # Sync all patients
    patients = db.query(Patient).filter(Patient.therapist_id == t.id).all()
    synced_patients = 0
    synced_checkins = 0
    synced_outcomes = 0

    for p in patients:
        ok = _supabase_upsert("patients", {
            "id": p.id, "name": p.name, "phone": p.phone, "email": p.email,
            "therapist_id": p.therapist_id,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })
        if ok:
            synced_patients += 1

        # Sync all check-ins for this patient
        checkins = db.query(CheckIn).filter(CheckIn.patient_id == p.id).all()
        for c in checkins:
            ok = _supabase_upsert("checkins", {
                "id": c.id, "patient_id": c.patient_id,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "local_day": c.local_day, "daily_state": c.daily_state,
                "overall_text": c.overall_text, "stress": c.stress, "sleep": c.sleep,
                "context_text": c.context_text, "body": c.body, "body_text": c.body_text,
                "pain_region": c.pain_region, "craving": c.craving, "avoidance": c.avoidance,
                "mental_text": c.mental_text, "goal_text": c.goal_text,
                "signals_json": c.signals_json, "pattern_code": c.pattern_code,
                "pattern_label": c.pattern_label, "action_code": c.action_code,
                "action_label": c.action_label, "action_text": c.action_text,
                "score": c.score, "risk_level": c.risk_level,
            })
            if ok:
                synced_checkins += 1

        # Sync outcomes
        outcomes = db.query(Outcome).filter(Outcome.patient_id == p.id).all()
        for o in outcomes:
            ok = _supabase_upsert("outcomes", {
                "id": o.id, "checkin_id": o.checkin_id, "patient_id": o.patient_id,
                "rating": o.rating, "outcome_note": o.outcome_note,
                "created_at": o.created_at.isoformat() if o.created_at else None,
            })
            if ok:
                synced_outcomes += 1

    body = f"""
      <h1>Sync abgeschlossen</h1>
      <div class="grid3">
        <div class="kpi"><span class="small">Patienten</span><b>{synced_patients}</b></div>
        <div class="kpi"><span class="small">Check-ins</span><b>{synced_checkins}</b></div>
        <div class="kpi"><span class="small">Outcomes</span><b>{synced_outcomes}</b></div>
      </div>
      <div class="hr"></div>
      <p>Alle Daten wurden nach Supabase gepusht. Der KI-Chefagent hat jetzt Zugriff.</p>
      <p><a href="/therapist/chief-agent">Zum Chefagent</a></p>
    """
    return _page("Sync Complete", body, request=request)


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
                "description": "In jeder Beziehung bestimmt die Person, die den Frame hält, die Realität dieser Beziehung. Dein Frame ist deine mentale Herkunft – deine Mission, deine Überzeugungen, dein Lebensentwurf. Wenn du deinen Frame aufgibst, lebst du im Frame eines anderen.",
                "daily_practice": "Beobachte heute jede Interaktion: Wer setzt den Frame? Übernimm bewusst die Führung. Wenn jemand deinen Frame testet, halte ruhig stand statt zu reagieren.",
                "metric": "frame_control",
            },
            {
                "rule": "Iron Rule #2: Mache deine Beziehung niemals zu deinem Lebenszweck",
                "description": "Eine Frau oder Beziehung darf niemals deine einzige Daseinsberechtigung sein. Der Mann, der seine gesamte Identität auf eine Beziehung aufbaut, verliert seine Attraktivität und seinen Frame. Deine Mission kommt zuerst – immer.",
                "daily_practice": "Überprüfe: Wie viel deiner Identität hängt an einer Person? Investiere heute bewusst Zeit in DEINE Mission, DEIN Wachstum, DEINE Ziele – unabhängig von jeder Beziehung.",
                "metric": "mission_priority",
            },
            {
                "rule": "Iron Rule #3: Jede Frau die dich warten lässt, ist das Warten nicht wert",
                "description": "Eine Frau, die echtes sexuelles Interesse an dir hat, wird Gelegenheiten schaffen, keine Hindernisse. Warten-lassen ist kein Zeichen von Qualität – es ist ein Zeichen mangelnden Interesses. Für den richtigen Mann gibt es keine Regeln.",
                "daily_practice": "Bewerte heute deine Investitionen: Wo wartest du auf jemanden, der nicht dieselbe Energie zurückgibt? Setze deine Zeit dort ein, wo echtes Interesse besteht.",
                "metric": "investment_awareness",
            },
            {
                "rule": "Iron Rule #4: Ziehe niemals unter keinen Umständen mit einer Frau zusammen, die nicht deine Ehefrau ist",
                "description": "Zusammenziehen ohne Commitment gibt alle Vorteile der Ehe ohne die Verpflichtung. Du verlierst Verhandlungsmacht, Unabhängigkeit und deinen mysteriösen Wert. Es normalisiert deine Präsenz und macht dich zum Möbelstück.",
                "daily_practice": "Reflektiere deine Wohnsituation und Unabhängigkeit. Schütze deinen persönlichen Raum als Grundlage deines Frames.",
                "metric": "independence",
            },
            {
                "rule": "Iron Rule #5: Überlasse niemals einer Frau die Kontrolle über Verhütung oder Familienplanung",
                "description": "Reproduktive Entscheidungen haben lebenslange Konsequenzen. Übernimm die volle Verantwortung für deine eigene Verhütung. Vertraue nicht auf die Aussagen anderer in einer so fundamentalen Frage.",
                "daily_practice": "Übernimm Eigenverantwortung in ALLEN Lebensbereichen. Überprüfe, wo du kritische Entscheidungen an andere delegierst.",
                "metric": "self_responsibility",
            },
            {
                "rule": "Iron Rule #6: Frauen sind grundlegend nicht in der Lage, einen Mann so zu lieben wie er es erwartet",
                "description": "Männer lieben idealistisch – bedingungslos und aufopfernd. Frauen lieben opportunistisch – pragmatisch und bedingt. Das ist keine Kritik, sondern Biologie. Weibliche Liebe basiert auf dem, was du BIST und DARSTELLST, nicht auf wer du im Herzen bist.",
                "daily_practice": "Akzeptiere diese Realität ohne Bitterkeit. Werde die beste Version deiner selbst – nicht für sie, sondern für DICH. Dein Wert bestimmt die Qualität deiner Beziehungen.",
                "metric": "realistic_expectations",
            },
            {
                "rule": "Iron Rule #7: Es ist immer besser neue Frauen kennenzulernen als alte Beziehungen wiederzubeleben",
                "description": "Der Versuch, eine Ex zurückzugewinnen, kommt immer aus einer Position der Schwäche. Du investierst Energie in eine gescheiterte Dynamik statt in neue Möglichkeiten. Nostalgie ist ein schlechter Berater – Abundance Mentality ist der Weg.",
                "daily_practice": "Lasse heute etwas Altes los, das nicht mehr funktioniert. Investiere die freigewordene Energie in neue Möglichkeiten – im sozialen Leben, in der Karriere, überall.",
                "metric": "forward_momentum",
            },
            {
                "rule": "Iron Rule #8: Lass sie selbst herausfinden warum sie nicht mit dir schlafen will – erkläre es ihr niemals",
                "description": "Wenn du einer Frau erklärst, warum sie dich nicht attraktiv findet, gibst du ihr das Werkzeug, dich zu manipulieren. Echte Attraktion braucht keine Erklärung. Zeige, demonstriere, SEI – aber erkläre dich nie.",
                "daily_practice": "Höre heute auf, dich zu erklären oder zu rechtfertigen. Lass deine Handlungen sprechen. Wer dich versteht, versteht dich – wer nicht, ist nicht dein Publikum.",
                "metric": "non_justification",
            },
            {
                "rule": "Iron Rule #9: Mache dich niemals selbst herunter – unter keinen Umständen",
                "description": "Selbstabwertung ist kein Humor und keine Bescheidenheit – es ist Selbstsabotage. Jedes Mal wenn du dich selbst herunterputzt, bestätigst du den niedrigen Wert, den andere dir zuschreiben wollen. Sei der Preis.",
                "daily_practice": "Achte heute auf jeden Moment, in dem du dich selbst abwertest – auch 'humorvoll'. Ersetze Selbstironie durch ruhiges Selbstvertrauen. Sprich über dich, wie du über deinen besten Freund sprechen würdest.",
                "metric": "self_regard",
            },
        ],
    },
    "books": {
        "rational_male_1": {
            "title": "The Rational Male – Buch 1 (2013)",
            "key_concepts": [
                "Hypergamie – der weibliche Instinkt zum Optimieren nach oben (Branch Swinging)",
                "SMV-Kurven (Sexual Market Value) – Männer peaken ~35-45, Frauen ~18-28",
                "Blue Pill Conditioning – gesellschaftliche Programmierung die Männer schwächt",
                "Oneitis – die Fixierung auf EINE Frau als größter AFC-Fehler",
                "Plate Theory – mehrere Optionen gleichzeitig als natürlicher Zustand",
                "Frame Control – wer den Frame hält, bestimmt die Beziehungsrealität",
                "AFC (Average Frustrated Chump) – der typische Beta-Mann und seine Fehler",
                "Alpha Fucks / Beta Bucks – die duale Sexualstrategie der Frau",
                "War Brides – Frauen passen sich emotional schneller an neue Realitäten an",
                "The Feminine Mystique – die Überhöhung des Weiblichen als Kontrollmechanismus",
                "Scarcity vs Abundance Mentality – Knappheitsdenken vs. Überflussdenken",
                "The Medium is the Message – WIE du kommunizierst ist wichtiger als WAS",
                "Female Solipsism – die weibliche Tendenz alles auf sich selbst zu beziehen",
                "Desire Cannot Be Negotiated – echte Attraktion kann nicht verhandelt werden",
            ],
        },
        "rational_male_2": {
            "title": "The Rational Male – Preventive Medicine (2015)",
            "key_concepts": [
                "SMV-Timeline im Detail – strategische Lebensphasen für Männer (20er, 30er, 40er)",
                "Die Epiphany Phase – Frauen ~28-32 wechseln die Strategie (Alpha → Beta-Suche)",
                "Open Hypergamy – Hypergamie wird im modernen Kontext offen gelebt und gefeiert",
                "Alpha Widow – Frau die emotional an einen früheren Alpha gebunden bleibt",
                "Die 5 Phasen der Red Pill Entwicklung: Denial → Anger → Bargaining → Depression → Acceptance",
                "Feminine Imperative – das gesellschaftliche System das weibliche Sexualstrategie priorisiert",
                "Social Conventions – ungeschriebene Regeln die Männer in Blue-Pill-Verhalten halten",
                "Preventive Medicine für junge Männer – früh die Dynamiken verstehen statt spät",
                "Zeitfenster-Strategien – wann investieren, wann ernten, wann aufbauen",
                "Beta Game vs Alpha Game – die fundamentalen Unterschiede im Beziehungsverhalten",
                "The Wall – der Punkt an dem weibliche SMV rapide sinkt und Strategien sich ändern",
                "Buffers – Schutzmechanismen die Männer vor Ablehnung bewahren sollen (aber schaden)",
            ],
        },
        "rational_male_3": {
            "title": "The Rational Male – Positive Masculinity (2017)",
            "key_concepts": [
                "Maskulinität ist kein Fehler – gegen die Pathologisierung männlicher Natur",
                "Konventionelle Attraktivität – bewusst aufbauen statt 'just be yourself'",
                "Mission vor Beziehung – dein Lebenszweck definiert deinen Wert",
                "Red Pill Parenting – Söhne UND Töchter mit realistischem Weltbild erziehen",
                "Komplementäre Geschlechterrollen vs egalitärer Gleichheitsmythos",
                "Authentic Masculinity vs Performance Masculinity – echt sein statt spielen",
                "Fatherlessness – die Krise der vaterlosen Generation und ihre Auswirkungen",
                "Tribalism und Male Spaces – Männer brauchen männliche Räume ohne Feminisierung",
                "The New Polyandry – wie moderne Dating-Kultur Frauen ermutigt mehrere Männer zu halten",
                "Positive Maskulinität als Gegenpol zur 'Toxic Masculinity' Narrative",
                "Intersexual Hierarchies – wie Männer und Frauen sich gegenseitig bewerten",
                "Red Pill Awareness in Langzeitbeziehungen – Frame halten über Jahrzehnte",
            ],
        },
        "rational_male_4": {
            "title": "The Rational Male – Religion (2019)",
            "key_concepts": [
                "Egalitarian vs Complementarian Christianity – zwei Modelle der Geschlechterrollen",
                "The Purple Pill – der Kompromiss-Versuch religiöser Männer (Red Pill + Blue Pill)",
                "Churchianity vs echte Spiritualität – wenn Kirche zum Feminine Imperative wird",
                "Blue Pill Conditioning durch religiöse Institutionen – 'Happy Wife, Happy Life'",
                "Das 'Good Man' Narrativ als Manipulation – Pflicht ohne Gegenwert",
                "Headship und männliche Führung – biblische Führung vs moderne Unterwerfung",
                "Sexual Marketplace innerhalb religiöser Gemeinschaften",
                "Moralische Integrität ohne Blue Pill Conditioning – Ethik mit offenen Augen",
                "Purpose-driven Leadership – spirituelle Mission als Frame-Fundament",
                "Traditionelle Werte im modernen Kontext – was funktioniert, was nicht",
                "Red Pill Women – Frauen die die Dynamiken verstehen und bewusst wählen",
                "Intersexual Dynamics in der Gemeinde – wie Kirchen oft Beta-Verhalten belohnen",
            ],
        },
        "rational_male_5": {
            "title": "The Rational Male – The Players Handbook (2022)",
            "key_concepts": [
                "Game als erlernbare Fähigkeit – nicht angeboren, sondern trainierbar",
                "Approach Anxiety überwinden – systematische Desensibilisierung",
                "Social Proof in der Praxis – Demonstration statt Behauptung",
                "Push/Pull Dynamik – Spannung aufbauen und halten",
                "Text Game – die Kunst der digitalen Kommunikation",
                "Date Logistics – Planung und Führung von Anfang an",
                "Kino Escalation – physische Eskalation lesen und führen",
                "LMR (Last Minute Resistance) – verstehen und kalibrieren",
                "Shit Tests erkennen und bestehen – Agree & Amplify, Amused Mastery, Ignore",
                "Inner Game – Mindset als Fundament für äußeren Erfolg",
                "Plates vs LTR – wann und wie man von Optionen zu Commitment übergeht",
                "Post-Wall Game – Attraktivität als reifer Mann maximieren",
            ],
        },
    },
    "glossary": {
        "hypergamie": {
            "term": "Hypergamie",
            "definition": "Der weibliche Instinkt, sich mit dem Mann höchsten verfügbaren Werts zu paaren. Kein moralisches Urteil – evolutionäre Biologie.",
        },
        "smv": {
            "term": "SMV (Sexual Market Value)",
            "definition": "Dein sexueller Marktwert. Kombination aus Aussehen, Status, Game und Ressourcen. Männer peaken ~35-45, Frauen ~18-28.",
        },
        "frame": {
            "term": "Frame",
            "definition": "Deine mentale Realität. Wer den Frame kontrolliert, bestimmt die Dynamik jeder Interaktion und Beziehung.",
        },
        "oneitis": {
            "term": "Oneitis",
            "definition": "Die krankhafte Fixierung auf EINE Frau als 'die Einzige'. Zerstört Abundance Mentality und Frame.",
        },
        "alpha_widow": {
            "term": "Alpha Widow",
            "definition": "Eine Frau, die emotional an einen früheren Alpha-Partner gebunden bleibt und jeden neuen Mann an ihm misst.",
        },
        "war_brides": {
            "term": "War Brides",
            "definition": "Das weibliche Talent, sich emotional schnell an neue Realitäten anzupassen. Erklärt schnelles 'Weitergehen' nach Trennungen.",
        },
        "the_wall": {
            "term": "The Wall",
            "definition": "Der Punkt (~30-32) an dem weibliche SMV rapide sinkt. Führt zur Epiphany Phase und Strategiewechsel.",
        },
        "epiphany_phase": {
            "term": "Epiphany Phase",
            "definition": "Frauen ~28-32 erkennen, dass ihre SMV sinkt und wechseln von Alpha-Partnern zu Beta-Providern (Strategiewechsel).",
        },
        "plate_theory": {
            "term": "Plate Theory",
            "definition": "Mehrere romantische Optionen gleichzeitig pflegen. Nicht um zu täuschen, sondern um Abundance Mentality zu leben.",
        },
        "afc": {
            "term": "AFC (Average Frustrated Chump)",
            "definition": "Der durchschnittliche Beta-Mann, der Blue-Pill-Strategien verfolgt (nett sein, warten, investieren) und frustriert scheitert.",
        },
        "shit_test": {
            "term": "Shit Test / Fitness Test",
            "definition": "Unbewusste Tests die Frauen einsetzen um die Frame-Stärke und den Wert eines Mannes zu prüfen.",
        },
        "feminine_imperative": {
            "term": "Feminine Imperative",
            "definition": "Das gesellschaftliche System das weibliche Sexualstrategie als Norm priorisiert und männliche Interessen unterordnet.",
        },
        "blue_pill": {
            "term": "Blue Pill",
            "definition": "Die gesellschaftliche Konditionierung: 'Sei einfach du selbst', 'Die Richtige kommt schon', 'Happy Wife Happy Life'. Hält Männer in passivem Verhalten.",
        },
        "abundance_mentality": {
            "term": "Abundance Mentality",
            "definition": "Die innere Überzeugung, dass es viele Optionen gibt. Das Gegenteil von Scarcity Mindset und Oneitis. Grundlage für Frame.",
        },
        "amused_mastery": {
            "term": "Amused Mastery",
            "definition": "Die Fähigkeit, Shit Tests und Provokationen mit souveränem Humor zu begegnen statt emotional zu reagieren.",
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
          <div style="font-size:11px;color:#6b7280">Cheat Sheet</div>
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

    # --- IRON RULES: compact cheat-sheet rows ---
    rules_html = ""
    for rule in ROLLO_TOMASSI["core_principles"]["iron_rules"]:
        rules_html += f"""
        <details style="border:1px solid rgba(239,68,68,.18);border-radius:12px;margin-bottom:8px;background:rgba(239,68,68,.03)">
          <summary style="padding:12px 14px;cursor:pointer;list-style:none;display:flex;justify-content:space-between;align-items:center">
            <b style="color:#fca5a5;font-size:13px;flex:1">{rule['rule']}</b>
            <span style="color:#4b5563;font-size:11px;margin-left:8px">▼</span>
          </summary>
          <div style="padding:0 14px 12px">
            <p style="font-size:12px;margin:0 0 8px;color:#d1d5db">{rule['description']}</p>
            <div style="font-size:11px;color:#f59e0b">→ {rule['daily_practice']}</div>
          </div>
        </details>"""

    # --- BOOKS: compact, 5 top concepts per book, no overlap with glossary ---
    book_colors = ["#ef4444", "#f59e0b", "#22c55e", "#6366f1", "#ec4899"]
    books_html = ""
    for i, (key, book) in enumerate(ROLLO_TOMASSI["books"].items()):
        color = book_colors[i % len(book_colors)]
        # Show max 5 most distinctive concepts per book for cheat-sheet brevity
        top_concepts = book["key_concepts"][:5]
        concepts = "".join(f"<li style='font-size:11px;margin:2px 0;color:#94a3b8'>{c}</li>" for c in top_concepts)
        more = f"<li style='font-size:11px;color:#4b5563;margin:2px 0'>+ {len(book['key_concepts']) - 5} weitere</li>" if len(book["key_concepts"]) > 5 else ""
        books_html += f"""
        <div style="border-left:3px solid {color};padding:10px 14px;margin-bottom:10px;background:rgba(255,255,255,.02);border-radius:0 10px 10px 0">
          <b style="color:{color};font-size:12px">{book['title']}</b>
          <ul style="margin:6px 0 0;padding-left:14px">{concepts}{more}</ul>
        </div>"""

    # --- SMV: horizontal compact bars ---
    smv_html = ""
    pillar_icons = {"physique": "💪", "status": "👑", "game": "🎯", "resources": "💰"}
    for key, pillar in ROLLO_TOMASSI["smv_pillars"].items():
        pct = int(pillar["weight"] * 100)
        icon = pillar_icons.get(key, "•")
        actions_short = " · ".join(a.split("(")[0].strip() for a in pillar["actions"][:3])
        smv_html += f"""
        <div style="margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
            <span style="font-size:12px;color:#a5b4fc;font-weight:600">{icon} {pillar['label']}</span>
            <span style="font-size:11px;color:#f59e0b;font-weight:700">{pct}%</span>
          </div>
          <div style="height:4px;background:#1f2937;border-radius:999px">
            <div style="height:4px;background:#6366f1;border-radius:999px;width:{pct}%"></div>
          </div>
          <div style="font-size:10px;color:#6b7280;margin-top:3px">{actions_short}</div>
        </div>"""

    # --- GLOSSARY: compact 2-column grid ---
    glossary_html = ""
    for key, entry in ROLLO_TOMASSI["glossary"].items():
        glossary_html += f"""
        <div style="padding:6px 0;border-bottom:1px solid #111827">
          <span style="color:#fca5a5;font-size:11px;font-weight:700">{entry['term']}</span>
          <span style="font-size:10px;color:#6b7280"> — {entry['definition']}</span>
        </div>"""

    body = f"""
      <div style="text-align:center;margin:8px 0 12px">
        <div style="font-size:36px;line-height:1">🔴</div>
        <div style="font-size:10px;color:#6b7280;margin-top:4px;letter-spacing:3px">CHEAT SHEET</div>
      </div>
      <h1 style="text-align:center;font-size:20px;margin:0 0 4px">The Rational Male</h1>
      <p style="text-align:center;font-size:11px;color:#6b7280;margin:0 0 16px">Rollo Tomassi · 5 Bücher · 9 Iron Rules · Buchgetreu</p>

      <h2 style="color:#fca5a5;font-size:14px;margin:0 0 8px;letter-spacing:1px">⚔️ IRON RULES</h2>
      {rules_html}

      <div style="height:1px;background:#1f2937;margin:16px 0"></div>
      <h2 style="color:#a5b4fc;font-size:14px;margin:0 0 10px;letter-spacing:1px">📊 SMV – DEIN MARKTWERT</h2>
      <div style="background:rgba(99,102,241,.04);border:1px solid rgba(99,102,241,.15);border-radius:12px;padding:14px">
        {smv_html}
      </div>

      <div style="height:1px;background:#1f2937;margin:16px 0"></div>
      <h2 style="font-size:14px;margin:0 0 10px;letter-spacing:1px">📚 BÜCHER – KEY TAKEAWAYS</h2>
      {books_html}

      <div style="height:1px;background:#1f2937;margin:16px 0"></div>
      <h2 style="color:#f59e0b;font-size:14px;margin:0 0 8px;letter-spacing:1px">🔑 GLOSSAR</h2>
      <div style="background:rgba(255,255,255,.02);border:1px solid #1f2937;border-radius:12px;padding:10px 14px">
        {glossary_html}
      </div>

      <div style="height:1px;background:#1f2937;margin:16px 0"></div>
      <p style="text-align:center;font-size:11px;color:#4b5563;margin:0 0 8px">
        <a href="/mastery">← Hub</a> · <a href="/mastery/tagesplan">Tagesplan</a> · <a href="/mastery/income">Income</a>
      </p>
    """
    return _page("PTGO • Rollo Tomassi Cheat Sheet", body, request=request)


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
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;">
        <a href="/therapist" class="btn btn-outline" style="text-align:center;">Therapist Dashboard</a>
        <a href="/wealth" class="btn btn-outline" style="text-align:center;">Wealth System</a>
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
# UNDERCOVER WEALTH SYSTEM — ROUTES
# =========================================================

def _wealth_page(title: str, body_html: str, active_tab: str = "dashboard") -> HTMLResponse:
    """Page wrapper for wealth system with tab navigation."""
    tabs = [
        ("dashboard", "Dashboard", "/wealth"),
        ("streams", "Streams", "/wealth/streams"),
        ("assets", "Assets", "/wealth/assets"),
        ("weekly", "Weekly", "/wealth/weekly"),
    ]
    tab_html = ""
    for key, label, href in tabs:
        active = "background:rgba(245,158,11,.15);border-color:rgba(245,158,11,.4);color:#f59e0b;" if key == active_tab else ""
        tab_html += f'<a href="{href}" style="display:inline-block;padding:8px 14px;border-radius:999px;font-size:13px;font-weight:600;border:1px solid var(--line);color:var(--muted);text-decoration:none;{active}">{label}</a> '

    nav = f"""
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:18px;">
      <div style="font-weight:700;font-size:18px;">Wealth System</div>
      <a href="/master-control" style="font-size:12px;color:var(--muted);text-decoration:none;">&larr; Master Control</a>
    </div>
    <div style="margin-bottom:18px;display:flex;gap:6px;flex-wrap:wrap;">{tab_html}</div>
    """

    css = """
    <style>
      :root { --bg:#0b0f1a; --card:#0f172a; --muted:#94a3b8; --text:#e5e7eb; --accent:#f59e0b; --line:#1f2937; }
      html,body{height:100%;}
      body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Inter,Arial,sans-serif;background:radial-gradient(1000px 600px at 50% -100px,#1f2a52,transparent),var(--bg);color:var(--text);}
      a{color:var(--accent);text-decoration:none}
      .wrap{max-width:900px;margin:0 auto;padding:26px 16px 60px;}
      .card{background:linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,.02));border:1px solid var(--line);border-radius:18px;padding:24px 20px 20px;box-shadow:0 20px 60px rgba(0,0,0,.35);margin-bottom:16px;}
      h1{font-size:28px;line-height:1.1;margin:0 0 12px;}
      h2{font-size:18px;margin:18px 0 10px;color:#f3f4f6}
      p{color:var(--muted);line-height:1.6}
      .hr{height:1px;background:var(--line);margin:18px 0;}
      label{display:block;color:#cbd5e1;font-size:13px;margin:14px 0 6px}
      input,select,textarea{width:100%;box-sizing:border-box;background:#0b1223;border:1px solid #263246;color:#e5e7eb;border-radius:12px;padding:12px;font-size:16px;outline:none}
      input:focus,textarea:focus{border-color:#f59e0b}
      .row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
      button,.btn{display:inline-block;background:linear-gradient(180deg,#fbbf24,#f59e0b);color:#111827;border:none;border-radius:14px;padding:14px 20px;font-weight:700;font-size:16px;cursor:pointer;text-align:center;width:100%;margin-top:8px;}
      .btn-outline{background:transparent;border:1px solid var(--line);color:var(--muted);width:auto;padding:10px 16px;font-size:14px;}
      .btn-sm{padding:8px 14px;font-size:13px;width:auto;margin-top:0;border-radius:10px;}
      .small{font-size:12px;color:var(--muted)}
      .grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
      .grid4{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:10px}
      .kpi{border:1px solid var(--line);border-radius:14px;padding:14px;background:rgba(255,255,255,.02)}
      .kpi b{display:block;font-size:22px;margin-top:4px}
      .tag{display:inline-block;font-size:11px;border:1px solid #374151;padding:3px 8px;border-radius:999px;color:#cbd5e1;margin-right:4px}
      @media(max-width:600px){.grid3,.grid4{grid-template-columns:1fr 1fr;}}
    </style>
    """

    html = f"""
    <html><head>
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>{title}</title>
      {css}
    </head>
    <body>
      <div class="wrap">
        {nav}
        {body_html}
      </div>
    </body></html>
    """
    return HTMLResponse(html)


def _fmt_eur(cents: int) -> str:
    """Format cents as EUR string."""
    if cents >= 100_00:
        return f"{cents / 100:,.0f} EUR".replace(",", ".")
    return f"{cents / 100:,.2f} EUR".replace(",", ".")


def _automation_bar(level: int) -> str:
    """Render a small automation progress bar."""
    color = "#22c55e" if level >= 80 else "#f59e0b" if level >= 40 else "#ef4444"
    return f"""<div style="display:flex;align-items:center;gap:8px;">
      <div style="flex:1;height:6px;background:#1f2937;border-radius:3px;">
        <div style="height:6px;background:{color};border-radius:3px;width:{level}%;"></div>
      </div>
      <span style="font-size:12px;color:{color};font-weight:600;">{level}%</span>
    </div>"""


# --- DASHBOARD ---

@app.get("/wealth", response_class=HTMLResponse)
async def wealth_dashboard(request: Request, db=Depends(get_db)):
    t = require_therapist_login(request, db)

    streams = db.query(WealthStream).filter(WealthStream.status != "retired").all()
    assets = db.query(WealthAsset).filter(WealthAsset.status != "retired").all()

    # KPIs
    total_monthly_target = sum(s.monthly_target for s in streams)
    total_monthly_actual = sum(s.monthly_actual for s in streams)
    passive_actual = sum(s.monthly_actual for s in streams if s.category == "passive")
    active_actual = sum(s.monthly_actual for s in streams if s.category == "active")
    equity_actual = sum(s.monthly_actual for s in streams if s.category == "equity")
    active_streams = [s for s in streams if s.status == "active"]
    avg_automation = round(sum(s.automation_level for s in active_streams) / len(active_streams)) if active_streams else 0
    passive_ratio = round(passive_actual / total_monthly_actual * 100) if total_monthly_actual > 0 else 0
    total_asset_value = sum(a.current_value for a in assets)
    pct_target = round(total_monthly_actual / total_monthly_target * 100) if total_monthly_target > 0 else 0

    # Holdings breakdown
    holdings = {}
    for s in streams:
        h = s.holding or "Sonstige"
        if h not in holdings:
            holdings[h] = {"target": 0, "actual": 0, "streams": 0}
        holdings[h]["target"] += s.monthly_target
        holdings[h]["actual"] += s.monthly_actual
        holdings[h]["streams"] += 1

    holdings_html = ""
    for h_name, h_data in sorted(holdings.items(), key=lambda x: x[1]["actual"], reverse=True):
        h_pct = round(h_data["actual"] / total_monthly_actual * 100) if total_monthly_actual > 0 else 0
        holdings_html += f"""
        <div style="padding:10px 0;border-bottom:1px solid var(--line);">
          <div style="display:flex;justify-content:space-between;">
            <div><b style="font-size:14px;">{h_name}</b> <span class="small">{h_data['streams']} Streams</span></div>
            <div style="text-align:right;">
              <b style="color:#f59e0b;">{_fmt_eur(h_data['actual'])}</b>
              <div class="small">Ziel: {_fmt_eur(h_data['target'])}</div>
            </div>
          </div>
          <div style="height:4px;background:#1f2937;border-radius:2px;margin-top:6px;">
            <div style="height:4px;background:linear-gradient(90deg,#f59e0b,#22c55e);border-radius:2px;width:{min(h_pct, 100)}%;"></div>
          </div>
        </div>"""

    # Stream list (top 5 by actual)
    top_streams = sorted(streams, key=lambda s: s.monthly_actual, reverse=True)[:5]
    stream_rows = ""
    for s in top_streams:
        cat_colors = {"active": "#3b82f6", "passive": "#22c55e", "equity": "#a855f7"}
        cat_color = cat_colors.get(s.category, "#94a3b8")
        stream_rows += f"""
        <div style="padding:10px 0;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;">
          <div>
            <b style="font-size:14px;">{s.name}</b>
            <div><span class="tag" style="border-color:{cat_color};color:{cat_color};">{s.category}</span>
            <span class="small">{s.holding or ''}</span></div>
          </div>
          <div style="text-align:right;">
            <b style="color:#f59e0b;">{_fmt_eur(s.monthly_actual)}</b>
            <div class="small">Auto: {s.automation_level}%</div>
          </div>
        </div>"""

    body = f"""
    <div class="card">
      <h1>Wealth Dashboard</h1>
      <p>Unternehmens-KPIs &amp; Einkommensströme</p>

      <div class="grid4" style="margin-top:16px;">
        <div class="kpi"><span class="small">Monatl. Umsatz</span><b style="color:#f59e0b;">{_fmt_eur(total_monthly_actual)}</b>
          <div class="small">{pct_target}% vom Ziel</div></div>
        <div class="kpi"><span class="small">Passiv-Quote</span><b style="color:#22c55e;">{passive_ratio}%</b>
          <div class="small">{_fmt_eur(passive_actual)} passiv</div></div>
        <div class="kpi"><span class="small">Automation</span><b style="color:#3b82f6;">{avg_automation}%</b>
          {_automation_bar(avg_automation)}</div>
        <div class="kpi"><span class="small">Asset-Wert</span><b>{_fmt_eur(total_asset_value)}</b>
          <div class="small">{len(assets)} Assets</div></div>
      </div>
    </div>

    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <h2 style="margin:0;">Holdings</h2>
      </div>
      {holdings_html if holdings_html else '<p>Noch keine Streams angelegt.</p>'}
    </div>

    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <h2 style="margin:0;">Top Streams</h2>
        <a href="/wealth/streams" class="btn-sm btn-outline">Alle &rarr;</a>
      </div>
      {stream_rows if stream_rows else '<p>Noch keine Streams angelegt.</p>'}
    </div>

    <div class="card">
      <h2 style="margin-top:0;">Revenue Split</h2>
      <div class="grid3">
        <div class="kpi"><span class="small" style="color:#3b82f6;">Active</span><b>{_fmt_eur(active_actual)}</b></div>
        <div class="kpi"><span class="small" style="color:#22c55e;">Passive</span><b>{_fmt_eur(passive_actual)}</b></div>
        <div class="kpi"><span class="small" style="color:#a855f7;">Equity</span><b>{_fmt_eur(equity_actual)}</b></div>
      </div>
    </div>
    """

    return _wealth_page("Wealth Dashboard", body, active_tab="dashboard")


# --- STREAMS ---

@app.get("/wealth/streams", response_class=HTMLResponse)
async def wealth_streams(request: Request, db=Depends(get_db)):
    t = require_therapist_login(request, db)
    streams = db.query(WealthStream).order_by(WealthStream.monthly_actual.desc()).all()

    rows = ""
    for s in streams:
        cat_colors = {"active": "#3b82f6", "passive": "#22c55e", "equity": "#a855f7"}
        cat_color = cat_colors.get(s.category, "#94a3b8")
        status_colors = {"active": "#22c55e", "paused": "#f59e0b", "planned": "#3b82f6", "retired": "#6b7280"}
        st_color = status_colors.get(s.status, "#94a3b8")
        pct = round(s.monthly_actual / s.monthly_target * 100) if s.monthly_target > 0 else 0
        rows += f"""
        <div style="padding:14px 0;border-bottom:1px solid var(--line);">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div>
              <b style="font-size:15px;">{s.name}</b>
              <div style="margin-top:4px;">
                <span class="tag" style="border-color:{cat_color};color:{cat_color};">{s.category}</span>
                <span class="tag" style="border-color:{st_color};color:{st_color};">{s.status}</span>
                <span class="tag">{s.stream_type}</span>
                {f'<span class="small">{s.holding}</span>' if s.holding else ''}
              </div>
            </div>
            <div style="text-align:right;">
              <b style="color:#f59e0b;font-size:18px;">{_fmt_eur(s.monthly_actual)}</b>
              <div class="small">Ziel: {_fmt_eur(s.monthly_target)} ({pct}%)</div>
            </div>
          </div>
          <div style="margin-top:8px;">
            <div class="small" style="margin-bottom:4px;">Automation</div>
            {_automation_bar(s.automation_level)}
          </div>
          {f'<div class="small" style="margin-top:6px;color:#cbd5e1;">{s.notes}</div>' if s.notes else ''}
          <div style="margin-top:8px;display:flex;gap:6px;">
            <a href="/wealth/streams/{s.id}/edit" class="btn-sm btn-outline">Bearbeiten</a>
            <form method="post" action="/wealth/streams/{s.id}/delete" style="margin:0;"><button type="submit" class="btn-sm btn-outline" style="color:#fecaca;border-color:#7f1d1d;" onclick="return confirm('Stream löschen?')">Löschen</button></form>
          </div>
        </div>"""

    body = f"""
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <h1 style="margin:0;">Income Streams</h1>
        <a href="/wealth/streams/new" class="btn-sm" style="text-decoration:none;">+ Neuer Stream</a>
      </div>
      <div class="small" style="margin-top:4px;">{len(streams)} Streams</div>
      <div style="margin-top:12px;">
        {rows if rows else '<p>Noch keine Streams angelegt.</p>'}
      </div>
    </div>
    """
    return _wealth_page("Income Streams", body, active_tab="streams")


@app.get("/wealth/streams/new", response_class=HTMLResponse)
async def wealth_stream_new(request: Request, db=Depends(get_db)):
    t = require_therapist_login(request, db)
    body = _wealth_stream_form("Neuer Stream", "/wealth/streams/create")
    return _wealth_page("Neuer Stream", body, active_tab="streams")


@app.get("/wealth/streams/{stream_id}/edit", response_class=HTMLResponse)
async def wealth_stream_edit(stream_id: int, request: Request, db=Depends(get_db)):
    t = require_therapist_login(request, db)
    s = db.query(WealthStream).filter(WealthStream.id == stream_id).first()
    if not s:
        raise HTTPException(404, "Stream not found")
    body = _wealth_stream_form("Stream bearbeiten", f"/wealth/streams/{s.id}/update", s)
    return _wealth_page("Stream bearbeiten", body, active_tab="streams")


def _wealth_stream_form(title: str, action: str, s=None) -> str:
    return f"""
    <div class="card">
      <h1>{title}</h1>
      <form method="post" action="{action}">
        <label>Name</label>
        <input name="name" required value="{s.name if s else ''}">
        <div class="row">
          <div>
            <label>Kategorie</label>
            <select name="category">
              <option value="active" {'selected' if s and s.category == 'active' else ''}>Active</option>
              <option value="passive" {'selected' if s and s.category == 'passive' else ''}>Passive</option>
              <option value="equity" {'selected' if s and s.category == 'equity' else ''}>Equity</option>
            </select>
          </div>
          <div>
            <label>Typ</label>
            <select name="stream_type">
              <option value="recurring" {'selected' if s and s.stream_type == 'recurring' else ''}>Recurring</option>
              <option value="one-time" {'selected' if s and s.stream_type == 'one-time' else ''}>One-Time</option>
              <option value="equity" {'selected' if s and s.stream_type == 'equity' else ''}>Equity</option>
              <option value="license" {'selected' if s and s.stream_type == 'license' else ''}>License</option>
            </select>
          </div>
        </div>
        <label>Holding / Unternehmen</label>
        <input name="holding" value="{s.holding if s and s.holding else ''}">
        <div class="row">
          <div>
            <label>Monatl. Ziel (EUR)</label>
            <input name="monthly_target" type="number" step="0.01" value="{s.monthly_target / 100 if s else '0'}">
          </div>
          <div>
            <label>Monatl. Ist (EUR)</label>
            <input name="monthly_actual" type="number" step="0.01" value="{s.monthly_actual / 100 if s else '0'}">
          </div>
        </div>
        <label>Automation Level (0-100%)</label>
        <input name="automation_level" type="number" min="0" max="100" value="{s.automation_level if s else 0}">
        <label>Status</label>
        <select name="status">
          <option value="active" {'selected' if s and s.status == 'active' else ''}>Active</option>
          <option value="paused" {'selected' if s and s.status == 'paused' else ''}>Paused</option>
          <option value="planned" {'selected' if s and s.status == 'planned' else ''}>Planned</option>
          <option value="retired" {'selected' if s and s.status == 'retired' else ''}>Retired</option>
        </select>
        <label>Notizen</label>
        <textarea name="notes" rows="3">{s.notes if s and s.notes else ''}</textarea>
        <button type="submit">Speichern</button>
      </form>
      <div style="margin-top:12px;text-align:center;">
        <a href="/wealth/streams" class="small">&larr; Zurück</a>
      </div>
    </div>
    """


@app.post("/wealth/streams/create", response_class=HTMLResponse)
async def wealth_stream_create(request: Request, db=Depends(get_db),
                                name: str = Form(...), category: str = Form("active"),
                                stream_type: str = Form("recurring"), holding: str = Form(""),
                                monthly_target: float = Form(0), monthly_actual: float = Form(0),
                                automation_level: int = Form(0), status: str = Form("active"),
                                notes: str = Form("")):
    t = require_therapist_login(request, db)
    s = WealthStream(
        name=name, category=category, stream_type=stream_type,
        holding=holding or None,
        monthly_target=int(monthly_target * 100), monthly_actual=int(monthly_actual * 100),
        automation_level=max(0, min(100, automation_level)), status=status,
        notes=notes or None,
    )
    db.add(s)
    db.commit()
    return RedirectResponse("/wealth/streams", status_code=303)


@app.post("/wealth/streams/{stream_id}/update", response_class=HTMLResponse)
async def wealth_stream_update(stream_id: int, request: Request, db=Depends(get_db),
                                name: str = Form(...), category: str = Form("active"),
                                stream_type: str = Form("recurring"), holding: str = Form(""),
                                monthly_target: float = Form(0), monthly_actual: float = Form(0),
                                automation_level: int = Form(0), status: str = Form("active"),
                                notes: str = Form("")):
    t = require_therapist_login(request, db)
    s = db.query(WealthStream).filter(WealthStream.id == stream_id).first()
    if not s:
        raise HTTPException(404, "Stream not found")
    s.name = name
    s.category = category
    s.stream_type = stream_type
    s.holding = holding or None
    s.monthly_target = int(monthly_target * 100)
    s.monthly_actual = int(monthly_actual * 100)
    s.automation_level = max(0, min(100, automation_level))
    s.status = status
    s.notes = notes or None
    db.commit()
    return RedirectResponse("/wealth/streams", status_code=303)


@app.post("/wealth/streams/{stream_id}/delete")
async def wealth_stream_delete(stream_id: int, request: Request, db=Depends(get_db)):
    t = require_therapist_login(request, db)
    s = db.query(WealthStream).filter(WealthStream.id == stream_id).first()
    if s:
        db.delete(s)
        db.commit()
    return RedirectResponse("/wealth/streams", status_code=303)


# --- ASSETS ---

@app.get("/wealth/assets", response_class=HTMLResponse)
async def wealth_assets(request: Request, db=Depends(get_db)):
    t = require_therapist_login(request, db)
    assets = db.query(WealthAsset).order_by(WealthAsset.current_value.desc()).all()

    total_value = sum(a.current_value for a in assets)
    total_monthly = sum(a.monthly_revenue for a in assets)

    rows = ""
    for a in assets:
        type_colors = {"ip": "#f59e0b", "saas": "#3b82f6", "brand": "#a855f7", "equity": "#22c55e", "real_estate": "#ef4444", "license": "#06b6d4"}
        t_color = type_colors.get(a.asset_type, "#94a3b8")
        pct_of_total = round(a.current_value / total_value * 100) if total_value > 0 else 0
        rows += f"""
        <div style="padding:14px 0;border-bottom:1px solid var(--line);">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div>
              <b style="font-size:15px;">{a.name}</b>
              <div style="margin-top:4px;">
                <span class="tag" style="border-color:{t_color};color:{t_color};">{a.asset_type}</span>
                <span class="tag">{a.status}</span>
                {f'<span class="small">{a.holding}</span>' if a.holding else ''}
              </div>
            </div>
            <div style="text-align:right;">
              <b style="color:#f59e0b;font-size:18px;">{_fmt_eur(a.current_value)}</b>
              <div class="small">{pct_of_total}% Portfolio</div>
            </div>
          </div>
          <div class="row" style="margin-top:8px;">
            <div class="small">Monatl. Revenue: <b style="color:#22c55e;">{_fmt_eur(a.monthly_revenue)}</b></div>
            <div class="small">Wachstum: <b>{a.growth_rate}% p.a.</b></div>
          </div>
          {f'<div class="small" style="margin-top:6px;color:#cbd5e1;">{a.notes}</div>' if a.notes else ''}
          <div style="margin-top:8px;display:flex;gap:6px;">
            <a href="/wealth/assets/{a.id}/edit" class="btn-sm btn-outline">Bearbeiten</a>
            <form method="post" action="/wealth/assets/{a.id}/delete" style="margin:0;"><button type="submit" class="btn-sm btn-outline" style="color:#fecaca;border-color:#7f1d1d;" onclick="return confirm('Asset löschen?')">Löschen</button></form>
          </div>
        </div>"""

    body = f"""
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <h1 style="margin:0;">Asset Portfolio</h1>
        <a href="/wealth/assets/new" class="btn-sm" style="text-decoration:none;">+ Neues Asset</a>
      </div>
      <div class="grid3" style="margin-top:14px;">
        <div class="kpi"><span class="small">Gesamtwert</span><b style="color:#f59e0b;">{_fmt_eur(total_value)}</b></div>
        <div class="kpi"><span class="small">Monatl. Revenue</span><b style="color:#22c55e;">{_fmt_eur(total_monthly)}</b></div>
        <div class="kpi"><span class="small">Assets</span><b>{len(assets)}</b></div>
      </div>
      <div style="margin-top:12px;">
        {rows if rows else '<p>Noch keine Assets angelegt.</p>'}
      </div>
    </div>
    """
    return _wealth_page("Asset Portfolio", body, active_tab="assets")


@app.get("/wealth/assets/new", response_class=HTMLResponse)
async def wealth_asset_new(request: Request, db=Depends(get_db)):
    t = require_therapist_login(request, db)
    body = _wealth_asset_form("Neues Asset", "/wealth/assets/create")
    return _wealth_page("Neues Asset", body, active_tab="assets")


@app.get("/wealth/assets/{asset_id}/edit", response_class=HTMLResponse)
async def wealth_asset_edit(asset_id: int, request: Request, db=Depends(get_db)):
    t = require_therapist_login(request, db)
    a = db.query(WealthAsset).filter(WealthAsset.id == asset_id).first()
    if not a:
        raise HTTPException(404, "Asset not found")
    body = _wealth_asset_form("Asset bearbeiten", f"/wealth/assets/{a.id}/update", a)
    return _wealth_page("Asset bearbeiten", body, active_tab="assets")


def _wealth_asset_form(title: str, action: str, a=None) -> str:
    return f"""
    <div class="card">
      <h1>{title}</h1>
      <form method="post" action="{action}">
        <label>Name</label>
        <input name="name" required value="{a.name if a else ''}">
        <div class="row">
          <div>
            <label>Typ</label>
            <select name="asset_type">
              <option value="ip" {'selected' if a and a.asset_type == 'ip' else ''}>IP</option>
              <option value="saas" {'selected' if a and a.asset_type == 'saas' else ''}>SaaS</option>
              <option value="brand" {'selected' if a and a.asset_type == 'brand' else ''}>Brand</option>
              <option value="equity" {'selected' if a and a.asset_type == 'equity' else ''}>Equity</option>
              <option value="real_estate" {'selected' if a and a.asset_type == 'real_estate' else ''}>Real Estate</option>
              <option value="license" {'selected' if a and a.asset_type == 'license' else ''}>License</option>
            </select>
          </div>
          <div>
            <label>Status</label>
            <select name="status">
              <option value="active" {'selected' if a and a.status == 'active' else ''}>Active</option>
              <option value="developing" {'selected' if a and a.status == 'developing' else ''}>Developing</option>
              <option value="planned" {'selected' if a and a.status == 'planned' else ''}>Planned</option>
            </select>
          </div>
        </div>
        <label>Holding / Unternehmen</label>
        <input name="holding" value="{a.holding if a and a.holding else ''}">
        <div class="row">
          <div>
            <label>Aktueller Wert (EUR)</label>
            <input name="current_value" type="number" step="0.01" value="{a.current_value / 100 if a else '0'}">
          </div>
          <div>
            <label>Monatl. Revenue (EUR)</label>
            <input name="monthly_revenue" type="number" step="0.01" value="{a.monthly_revenue / 100 if a else '0'}">
          </div>
        </div>
        <label>Wachstumsrate (% p.a.)</label>
        <input name="growth_rate" type="number" step="0.1" value="{a.growth_rate if a else 0}">
        <label>Notizen</label>
        <textarea name="notes" rows="3">{a.notes if a and a.notes else ''}</textarea>
        <button type="submit">Speichern</button>
      </form>
      <div style="margin-top:12px;text-align:center;">
        <a href="/wealth/assets" class="small">&larr; Zurück</a>
      </div>
    </div>
    """


@app.post("/wealth/assets/create", response_class=HTMLResponse)
async def wealth_asset_create(request: Request, db=Depends(get_db),
                               name: str = Form(...), asset_type: str = Form("ip"),
                               holding: str = Form(""), current_value: float = Form(0),
                               monthly_revenue: float = Form(0), growth_rate: float = Form(0),
                               status: str = Form("active"), notes: str = Form("")):
    t = require_therapist_login(request, db)
    a = WealthAsset(
        name=name, asset_type=asset_type, holding=holding or None,
        current_value=int(current_value * 100), monthly_revenue=int(monthly_revenue * 100),
        growth_rate=growth_rate, status=status, notes=notes or None,
    )
    db.add(a)
    db.commit()
    return RedirectResponse("/wealth/assets", status_code=303)


@app.post("/wealth/assets/{asset_id}/update", response_class=HTMLResponse)
async def wealth_asset_update(asset_id: int, request: Request, db=Depends(get_db),
                               name: str = Form(...), asset_type: str = Form("ip"),
                               holding: str = Form(""), current_value: float = Form(0),
                               monthly_revenue: float = Form(0), growth_rate: float = Form(0),
                               status: str = Form("active"), notes: str = Form("")):
    t = require_therapist_login(request, db)
    a = db.query(WealthAsset).filter(WealthAsset.id == asset_id).first()
    if not a:
        raise HTTPException(404, "Asset not found")
    a.name = name
    a.asset_type = asset_type
    a.holding = holding or None
    a.current_value = int(current_value * 100)
    a.monthly_revenue = int(monthly_revenue * 100)
    a.growth_rate = growth_rate
    a.status = status
    a.notes = notes or None
    db.commit()
    return RedirectResponse("/wealth/assets", status_code=303)


@app.post("/wealth/assets/{asset_id}/delete")
async def wealth_asset_delete(asset_id: int, request: Request, db=Depends(get_db)):
    t = require_therapist_login(request, db)
    a = db.query(WealthAsset).filter(WealthAsset.id == asset_id).first()
    if a:
        db.delete(a)
        db.commit()
    return RedirectResponse("/wealth/assets", status_code=303)


# --- WEEKLY REVIEW ---

@app.get("/wealth/weekly", response_class=HTMLResponse)
async def wealth_weekly(request: Request, db=Depends(get_db)):
    t = require_therapist_login(request, db)
    now = datetime.now(ZoneInfo("Europe/Berlin"))
    current_week = now.strftime("%Y-W%V")

    reviews = db.query(WealthWeekly).order_by(WealthWeekly.created_at.desc()).limit(12).all()
    current = next((r for r in reviews if r.week == current_week), None)

    form_html = f"""
    <div class="card">
      <h1>Weekly Review</h1>
      <p>Woche: <b>{current_week}</b></p>
      <form method="post" action="/wealth/weekly/save">
        <input type="hidden" name="week" value="{current_week}">

        <label>1. Was kam OHNE mein Zutun rein? (Passive Income)</label>
        <textarea name="q1_passive_income" rows="2">{current.q1_passive_income if current and current.q1_passive_income else ''}</textarea>

        <label>2. Was habe ich diese Woche automatisiert?</label>
        <textarea name="q2_automated" rows="2">{current.q2_automated if current and current.q2_automated else ''}</textarea>

        <label>3. Welchen Stream habe ich näher an 100% Automation gebracht?</label>
        <textarea name="q3_automation_progress" rows="2">{current.q3_automation_progress if current and current.q3_automation_progress else ''}</textarea>

        <label>4. Meine öffentliche Sichtbarkeit diese Woche?</label>
        <select name="q4_visibility">
          <option value="reduced" {'selected' if current and current.q4_visibility == 'reduced' else ''}>Reduziert</option>
          <option value="same" {'selected' if current and current.q4_visibility == 'same' else ''}>Gleich</option>
          <option value="increased" {'selected' if current and current.q4_visibility == 'increased' else ''}>Erhöht</option>
        </select>

        <label>5. Stunden IN vs. AN dem System gearbeitet?</label>
        <textarea name="q5_in_vs_on" rows="2">{current.q5_in_vs_on if current and current.q5_in_vs_on else ''}</textarea>

        <label>Selbstbewertung (1-10)</label>
        <input name="score" type="number" min="1" max="10" value="{current.score if current and current.score else ''}">

        <label>Notizen</label>
        <textarea name="notes" rows="2">{current.notes if current and current.notes else ''}</textarea>

        <button type="submit">Speichern</button>
      </form>
    </div>
    """

    past_html = ""
    for r in reviews:
        if r.week == current_week:
            continue
        score_color = "#22c55e" if r.score and r.score >= 7 else "#f59e0b" if r.score and r.score >= 4 else "#ef4444"
        vis_labels = {"reduced": "Reduziert", "same": "Gleich", "increased": "Erhöht"}
        past_html += f"""
        <div style="padding:12px 0;border-bottom:1px solid var(--line);">
          <div style="display:flex;justify-content:space-between;">
            <b>{r.week}</b>
            <span style="color:{score_color};font-weight:700;">{r.score or '-'}/10</span>
          </div>
          <div class="small" style="margin-top:4px;">
            Sichtbarkeit: {vis_labels.get(r.q4_visibility, '-')}
            {f' | {r.q1_passive_income[:60]}...' if r.q1_passive_income and len(r.q1_passive_income) > 60 else f' | {r.q1_passive_income}' if r.q1_passive_income else ''}
          </div>
        </div>"""

    if past_html:
        past_html = f"""
        <div class="card">
          <h2 style="margin-top:0;">Vergangene Reviews</h2>
          {past_html}
        </div>"""

    return _wealth_page("Weekly Review", form_html + past_html, active_tab="weekly")


@app.post("/wealth/weekly/save")
async def wealth_weekly_save(request: Request, db=Depends(get_db),
                              week: str = Form(...),
                              q1_passive_income: str = Form(""),
                              q2_automated: str = Form(""),
                              q3_automation_progress: str = Form(""),
                              q4_visibility: str = Form("same"),
                              q5_in_vs_on: str = Form(""),
                              score: int = Form(None),
                              notes: str = Form("")):
    t = require_therapist_login(request, db)
    existing = db.query(WealthWeekly).filter(WealthWeekly.week == week).first()
    if existing:
        existing.q1_passive_income = q1_passive_income or None
        existing.q2_automated = q2_automated or None
        existing.q3_automation_progress = q3_automation_progress or None
        existing.q4_visibility = q4_visibility
        existing.q5_in_vs_on = q5_in_vs_on or None
        existing.score = score
        existing.notes = notes or None
    else:
        r = WealthWeekly(
            week=week,
            q1_passive_income=q1_passive_income or None,
            q2_automated=q2_automated or None,
            q3_automation_progress=q3_automation_progress or None,
            q4_visibility=q4_visibility,
            q5_in_vs_on=q5_in_vs_on or None,
            score=score,
            notes=notes or None,
        )
        db.add(r)
    db.commit()
    return RedirectResponse("/wealth/weekly", status_code=303)


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


# =========================================================
# PATIENT COMMUNICATION ANALYZER — Chris Voss Elite Engine
# =========================================================
# Upload WhatsApp-Exports → KI-Analyse mit Chris Voss Techniken
# → Skill-Verbesserung → Pricing-Optimierung → Musk-Check

import re as _re
import io as _io


# ---------- WhatsApp Chat Parser ----------

def _parse_whatsapp_export(raw_text: str) -> list[dict]:
    """Parse WhatsApp exported .txt into structured messages.
    Supports formats:
      - [DD.MM.YY, HH:MM:SS] Name: Message
      - DD.MM.YY, HH:MM - Name: Message
      - DD/MM/YYYY, HH:MM - Name: Message
    """
    patterns = [
        _re.compile(r'\[(\d{1,2}\.\d{1,2}\.\d{2,4},\s*\d{1,2}:\d{2}(?::\d{2})?)\]\s*([^:]+):\s*(.+)', _re.DOTALL),
        _re.compile(r'(\d{1,2}\.\d{1,2}\.\d{2,4},\s*\d{1,2}:\d{2})\s*-\s*([^:]+):\s*(.+)', _re.DOTALL),
        _re.compile(r'(\d{1,2}/\d{1,2}/\d{2,4},\s*\d{1,2}:\d{2})\s*-\s*([^:]+):\s*(.+)', _re.DOTALL),
    ]
    messages = []
    current = None
    for line in raw_text.split('\n'):
        matched = False
        for pat in patterns:
            m = pat.match(line.strip())
            if m:
                if current:
                    messages.append(current)
                current = {
                    "timestamp": m.group(1).strip(),
                    "sender": m.group(2).strip(),
                    "text": m.group(3).strip(),
                }
                matched = True
                break
        if not matched and current and line.strip():
            current["text"] += "\n" + line.strip()
    if current:
        messages.append(current)
    return messages


def _build_conversation_summary(messages: list[dict]) -> dict:
    """Build stats from parsed messages."""
    if not messages:
        return {"total": 0, "senders": {}, "avg_length": 0, "duration_days": 0}
    senders = {}
    total_len = 0
    for m in messages:
        s = m["sender"]
        senders.setdefault(s, {"count": 0, "total_chars": 0, "questions": 0, "emojis": 0})
        senders[s]["count"] += 1
        senders[s]["total_chars"] += len(m["text"])
        if "?" in m["text"]:
            senders[s]["questions"] += 1
        total_len += len(m["text"])
    return {
        "total": len(messages),
        "senders": senders,
        "avg_length": total_len // max(len(messages), 1),
    }


# ---------- Chris Voss Analysis Dimensions ----------

CHRIS_VOSS_DIMENSIONS = {
    "tactical_empathy": {
        "label": "Taktische Empathie",
        "description": "Verständnis der Perspektive des Gegenübers demonstrieren, ohne zuzustimmen",
        "techniques": ["Labeling", "Mirroring", "Akkusations-Audit"],
    },
    "calibrated_questions": {
        "label": "Kalibrierte Fragen",
        "description": "Offene Fragen, die den anderen zum Nachdenken bringen (Wie...? Was...?)",
        "techniques": ["How-Fragen", "What-Fragen", "Implementierungs-Fragen"],
    },
    "mirroring": {
        "label": "Spiegelung",
        "description": "Die letzten 1-3 Wörter wiederholen, um Rapport aufzubauen",
        "techniques": ["Wort-Spiegel", "Ton-Spiegel", "Emotions-Spiegel"],
    },
    "labeling": {
        "label": "Labeling",
        "description": "'Es scheint als ob...' / 'Es klingt so als...' — Emotionen benennen",
        "techniques": ["Emotions-Label", "Situations-Label", "Dynamik-Label"],
    },
    "accusation_audit": {
        "label": "Akkusations-Audit",
        "description": "Negative Erwartungen vorwegnehmen und ansprechen",
        "techniques": ["Vorwegnahme", "Entwaffnung", "Reset"],
    },
    "no_oriented": {
        "label": "Nein-orientierte Fragen",
        "description": "Fragen stellen, auf die 'Nein' die gewünschte Antwort ist",
        "techniques": ["Ist es lächerlich...?", "Haben Sie aufgegeben...?", "Wäre es falsch...?"],
    },
    "late_night_dj": {
        "label": "Late-Night-DJ-Stimme",
        "description": "Ruhiger, tiefer, kontrollierter Ton — Vertrauen durch Stimme",
        "techniques": ["Ton-Kontrolle", "Pausen", "Verlangsamung"],
    },
    "black_swan": {
        "label": "Black Swans",
        "description": "Unbekannte Informationen entdecken, die alles verändern",
        "techniques": ["Überraschende Erkenntnisse", "Versteckte Motive", "Unausgesprochenes"],
    },
}

PRICING_PRINCIPLES = {
    "value_anchor": "Wert-Anker: Preis am transformativen Ergebnis messen, nicht an Zeit",
    "loss_aversion": "Verlust-Aversion: Was kostet es, NICHT zu handeln?",
    "scarcity": "Knappheit: Begrenzte Verfügbarkeit erzeugt Handlungsdruck",
    "social_proof": "Social Proof: Ergebnisse anderer zeigen (anonymisiert)",
    "price_bracketing": "Price Bracketing: 3 Optionen — der mittlere Preis wirkt rational",
    "outcome_pricing": "Outcome Pricing: Preis am Ergebnis koppeln, nicht an Input",
}


# ---------- Claude API Calls ----------

def _analyze_communication_voss(messages: list[dict], summary: dict) -> dict:
    """Call Claude to analyze communication with Chris Voss framework."""
    if not ANTHROPIC_API_KEY:
        return {"error": "ANTHROPIC_API_KEY nicht konfiguriert"}

    # Build conversation excerpt (max ~4000 chars for context)
    conv_text = ""
    for m in messages[-80:]:  # last 80 messages for analysis
        conv_text += f"[{m['timestamp']}] {m['sender']}: {m['text']}\n"
    if len(conv_text) > 6000:
        conv_text = conv_text[-6000:]

    sender_stats = ""
    for name, stats in summary["senders"].items():
        sender_stats += f"- {name}: {stats['count']} Nachrichten, {stats['total_chars']} Zeichen, {stats['questions']} Fragen\n"

    prompt = f"""Du bist ein Elite-Kommunikationscoach, der auf Chris Voss' Verhandlungstechniken spezialisiert ist
(aus "Never Split the Difference"). Analysiere den folgenden WhatsApp-Chatverlauf zwischen einem Therapeuten
und einem Patienten.

KOMMUNIKATIONSSTATISTIK:
{sender_stats}
Gesamt: {summary['total']} Nachrichten

CHATVERLAUF:
{conv_text}

Analysiere EXAKT nach diesen 8 Chris Voss Dimensionen und bewerte jede von 0-100:

1. TAKTISCHE EMPATHIE — Zeigt der Therapeut echtes Verständnis für die Welt des Patienten?
2. KALIBRIERTE FRAGEN — Nutzt er "Wie...?" und "Was...?" Fragen statt geschlossener Fragen?
3. SPIEGELUNG (Mirroring) — Wiederholt er Schlüsselwörter des Patienten?
4. LABELING — Benennt er Emotionen mit "Es scheint als ob..." / "Es klingt so als..."?
5. AKKUSATIONS-AUDIT — Nimmt er negative Erwartungen vorweg?
6. NEIN-ORIENTIERTE FRAGEN — Nutzt er Fragen, bei denen "Nein" die gewünschte Antwort ist?
7. LATE-NIGHT-DJ-STIMME — Ist der Ton ruhig, kontrolliert, vertrauensbildend (auch schriftlich erkennbar)?
8. BLACK SWANS — Entdeckt er versteckte Informationen, die alles verändern?

Für JEDE Dimension:
- Score (0-100)
- Was gut gemacht wurde (konkrete Beispiele aus dem Chat)
- Was verbessert werden kann (konkrete Formulierungsvorschläge)
- ELITE-LEVEL Vorschlag: Wie würde ein Weltklasse-Verhandler hier kommunizieren?

Zusätzlich:
- GESAMTSCORE (0-100)
- TOP 3 STÄRKEN
- TOP 3 VERBESSERUNGSPOTENZIALE mit konkreten Formulierungen
- PRICING INSIGHT: Wie kann die Kommunikation den wahrgenommenen Wert maximieren?
- EINZIGARTIGKEIT: Was macht diese Kommunikation einzigartig und was fehlt noch zur Weltklasse?

Antworte als JSON:
{{
  "overall_score": <int>,
  "dimensions": {{
    "tactical_empathy": {{"score": <int>, "good": "<text>", "improve": "<text>", "elite": "<text>"}},
    "calibrated_questions": {{"score": <int>, "good": "<text>", "improve": "<text>", "elite": "<text>"}},
    "mirroring": {{"score": <int>, "good": "<text>", "improve": "<text>", "elite": "<text>"}},
    "labeling": {{"score": <int>, "good": "<text>", "improve": "<text>", "elite": "<text>"}},
    "accusation_audit": {{"score": <int>, "good": "<text>", "improve": "<text>", "elite": "<text>"}},
    "no_oriented": {{"score": <int>, "good": "<text>", "improve": "<text>", "elite": "<text>"}},
    "late_night_dj": {{"score": <int>, "good": "<text>", "improve": "<text>", "elite": "<text>"}},
    "black_swan": {{"score": <int>, "good": "<text>", "improve": "<text>", "elite": "<text>"}}
  }},
  "top_strengths": ["<text>", "<text>", "<text>"],
  "top_improvements": ["<text>", "<text>", "<text>"],
  "pricing_insight": "<text>",
  "uniqueness": "<text>",
  "concrete_scripts": ["<formulierung1>", "<formulierung2>", "<formulierung3>"]
}}

NUR JSON ausgeben, kein weiterer Text."""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5", "max_tokens": 4000, "messages": [{"role": "user", "content": prompt}]},
            timeout=60,
        )
        if resp.status_code == 200:
            rj = resp.json()
            _track_ai_usage("communication_voss_analysis", rj)
            text = rj.get("content", [{}])[0].get("text", "{}")
            # Extract JSON from response
            json_match = _re.search(r'\{[\s\S]*\}', text)
            if json_match:
                return json.loads(json_match.group())
            return {"error": "KI-Antwort konnte nicht geparst werden", "raw": text[:500]}
        else:
            _track_ai_error("communication_voss_analysis", f"HTTP {resp.status_code}")
            return {"error": f"API Fehler: {resp.status_code}"}
    except Exception as e:
        _track_ai_error("communication_voss_analysis", str(e))
        return {"error": str(e)}


def _musk_check(analysis: dict, messages: list[dict]) -> dict:
    """Ask Claude: Would Elon Musk build it differently?"""
    if not ANTHROPIC_API_KEY:
        return {"error": "ANTHROPIC_API_KEY nicht konfiguriert"}

    analysis_summary = json.dumps(analysis, ensure_ascii=False)[:3000]

    prompt = f"""Du bist Elon Musk. Du hast gerade diese Kommunikationsanalyse eines Therapeuten gesehen:

{analysis_summary}

Der Therapeut hat {len(messages)} WhatsApp-Nachrichten mit Patienten ausgetauscht.

Beantworte BRUTAL EHRLICH aus Elon Musks Perspektive:

1. FIRST PRINCIPLES: Was stimmt an der grundlegenden Annahme nicht? Was würdest du von Grund auf anders machen?
2. 10X THINKING: Wie kann die Kommunikation nicht 10% besser, sondern 10x besser werden?
3. AUTOMATION: Welche Teile der Kommunikation können/sollten automatisiert werden?
4. SCALE: Wie kann dieser Therapeut von 1:1 zu 1:N skalieren ohne Qualitätsverlust?
5. PRICING: Was wäre der Preis, wenn die Ergebnisse 10x besser wären? Wie kommt man dahin?
6. SPEED: Was dauert zu lange? Wo wird Zeit verschwendet?
7. KILLER FEATURE: Was fehlt komplett, das alles verändern würde?
8. FINAL VERDICT: Würde ich investieren? Ja/Nein und warum?

Antworte als JSON:
{{
  "first_principles": "<text>",
  "ten_x_thinking": "<text>",
  "automation": "<text>",
  "scale": "<text>",
  "pricing": "<text>",
  "speed": "<text>",
  "killer_feature": "<text>",
  "final_verdict": "<text>",
  "invest": <true/false>,
  "one_line": "<Ein Satz der alles zusammenfasst>"
}}

NUR JSON."""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5", "max_tokens": 2000, "messages": [{"role": "user", "content": prompt}]},
            timeout=60,
        )
        if resp.status_code == 200:
            rj = resp.json()
            _track_ai_usage("communication_musk_check", rj)
            text = rj.get("content", [{}])[0].get("text", "{}")
            json_match = _re.search(r'\{[\s\S]*\}', text)
            if json_match:
                return json.loads(json_match.group())
            return {"error": "Musk-Check konnte nicht geparst werden"}
        else:
            _track_ai_error("communication_musk_check", f"HTTP {resp.status_code}")
            return {"error": f"API Fehler: {resp.status_code}"}
    except Exception as e:
        _track_ai_error("communication_musk_check", str(e))
        return {"error": str(e)}


# ---------- Routes ----------

@app.get("/kommunikation", response_class=HTMLResponse)
async def communication_analyzer_home(request: Request):
    """Hauptseite — WhatsApp-Export hochladen."""
    tid = request.session.get("therapist_id")
    if not tid:
        return RedirectResponse("/therapist/login", 303)

    body = """
    <h1 style="font-size:28px;">Kommunikations-Analyse</h1>
    <p style="margin-bottom:6px;color:#f59e0b;font-size:13px;font-weight:600">CHRIS VOSS × ELITE LEVEL × MUSK CHECK</p>
    <p>Lade deinen WhatsApp-Chatverlauf hoch und erhalte eine tiefgreifende Analyse deiner therapeutischen Kommunikation.</p>

    <div class="hr"></div>

    <h2>So geht's</h2>
    <p>1. Öffne WhatsApp → Chat → ⋮ Mehr → Chat exportieren → Ohne Medien</p>
    <p>2. Die exportierte .txt-Datei hier hochladen</p>
    <p>3. KI analysiert nach 8 Chris Voss Dimensionen</p>
    <p>4. Elon Musk prüft das Ergebnis</p>

    <div class="hr"></div>

    <form action="/kommunikation/analyze" method="post" enctype="multipart/form-data">
      <label>WhatsApp-Export (.txt)</label>
      <input type="file" name="chat_file" accept=".txt,.csv,.text" required
             style="padding:14px;background:rgba(245,158,11,.07);border:1px solid rgba(245,158,11,.3);cursor:pointer;">

      <label style="margin-top:16px">Dein Name im Chat (damit die KI weiß, wer du bist)</label>
      <input type="text" name="therapist_name" placeholder="z.B. Sascha" required>

      <label style="margin-top:16px">Kontext (optional)</label>
      <textarea name="context" rows="3" placeholder="z.B. Patient mit chronischen Schmerzen, 3. Behandlungsmonat..."></textarea>

      <button type="submit" style="margin-top:20px;">Analyse starten</button>
    </form>

    <div class="hr"></div>
    <p class="small" style="text-align:center">
      Powered by Chris Voss Framework × Claude AI × Musk Protocol<br>
      <a href="/therapist">← Zurück zum Dashboard</a>
    </p>
    """
    return _page("Kommunikations-Analyse", body, request)


@app.post("/kommunikation/analyze", response_class=HTMLResponse)
async def communication_analyze(request: Request):
    """Parse upload, run Voss analysis + Musk check, show results."""
    tid = request.session.get("therapist_id")
    if not tid:
        return RedirectResponse("/therapist/login", 303)

    form = await request.form()
    chat_file = form.get("chat_file")
    therapist_name = form.get("therapist_name", "").strip()
    context = form.get("context", "").strip()

    if not chat_file or not hasattr(chat_file, "read"):
        return _page("Fehler", "<h1>Keine Datei hochgeladen</h1><p><a href='/kommunikation'>Zurück</a></p>", request)

    # Read uploaded file
    try:
        raw_bytes = await chat_file.read()
        # Try UTF-8 first, fallback to latin-1
        try:
            raw_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raw_text = raw_bytes.decode("latin-1")
    except Exception as e:
        return _page("Fehler", f"<h1>Datei konnte nicht gelesen werden</h1><p>{e}</p><p><a href='/kommunikation'>Zurück</a></p>", request)

    # Parse messages
    messages = _parse_whatsapp_export(raw_text)
    if not messages:
        return _page("Fehler", "<h1>Keine Nachrichten erkannt</h1><p>Bitte exportiere den Chat als .txt Datei aus WhatsApp.</p><p><a href='/kommunikation'>Zurück</a></p>", request)

    summary = _build_conversation_summary(messages)

    # Identify senders
    senders = list(summary["senders"].keys())
    therapist_msgs = sum(1 for m in messages if therapist_name.lower() in m["sender"].lower()) if therapist_name else 0
    patient_msgs = len(messages) - therapist_msgs

    # Run both analyses
    voss_analysis = _analyze_communication_voss(messages, summary)
    musk_result = _musk_check(voss_analysis, messages)

    # Build results page
    error_html = ""
    if "error" in voss_analysis:
        error_html = f'<div style="background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.4);border-radius:12px;padding:14px;margin:12px 0;"><p style="color:#fca5a5;">{voss_analysis["error"]}</p></div>'

    # Stats section
    stats_html = f"""
    <div class="grid3" style="margin:16px 0">
      <div class="kpi"><span class="small">Nachrichten</span><b>{summary['total']}</b></div>
      <div class="kpi"><span class="small">Teilnehmer</span><b>{len(senders)}</b></div>
      <div class="kpi"><span class="small">Ø Länge</span><b>{summary['avg_length']} Z.</b></div>
    </div>
    """

    # Sender breakdown
    sender_html = ""
    for name, stats in summary["senders"].items():
        is_therapist = therapist_name.lower() in name.lower() if therapist_name else False
        tag = ' <span style="color:#f59e0b;font-size:11px">(DU)</span>' if is_therapist else ''
        pct = round(stats["count"] / max(summary["total"], 1) * 100)
        sender_html += f"""
        <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--line)">
          <div><b>{name}</b>{tag}</div>
          <div class="small">{stats['count']} Nachrichten ({pct}%) · {stats['questions']} Fragen</div>
        </div>
        """

    # Overall Score
    overall_score = voss_analysis.get("overall_score", 0)
    score_color = "#ef4444" if overall_score < 40 else "#f59e0b" if overall_score < 70 else "#22c55e"
    score_html = f"""
    <div style="text-align:center;margin:24px 0">
      <div style="font-size:64px;font-weight:800;color:{score_color}">{overall_score}</div>
      <div class="small">VOSS ELITE SCORE / 100</div>
    </div>
    """

    # Dimensions
    dims_html = ""
    dimensions = voss_analysis.get("dimensions", {})
    for key, dim_data in dimensions.items():
        if not isinstance(dim_data, dict):
            continue
        dim_info = CHRIS_VOSS_DIMENSIONS.get(key, {})
        dim_score = dim_data.get("score", 0)
        dim_color = "#ef4444" if dim_score < 40 else "#f59e0b" if dim_score < 70 else "#22c55e"
        bar_width = max(dim_score, 2)
        dims_html += f"""
        <div style="margin:16px 0;padding:16px;background:rgba(255,255,255,.02);border:1px solid var(--line);border-radius:14px">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div><b>{dim_info.get('label', key)}</b></div>
            <div style="font-size:20px;font-weight:700;color:{dim_color}">{dim_score}</div>
          </div>
          <div style="height:4px;background:#1f2937;border-radius:999px;margin:8px 0">
            <div style="height:4px;background:{dim_color};border-radius:999px;width:{bar_width}%"></div>
          </div>
          <div style="font-size:13px;color:#6b7280;margin-bottom:6px">{dim_info.get('description', '')}</div>
          <div style="margin-top:8px">
            <div style="font-size:13px;margin:4px 0"><span style="color:#22c55e">✓</span> {dim_data.get('good', '—')}</div>
            <div style="font-size:13px;margin:4px 0"><span style="color:#f59e0b">↑</span> {dim_data.get('improve', '—')}</div>
            <div style="font-size:13px;margin:4px 0;padding:8px;background:rgba(99,102,241,.1);border-radius:8px;border:1px solid rgba(99,102,241,.3)">
              <span style="color:#a5b4fc">★ ELITE:</span> {dim_data.get('elite', '—')}
            </div>
          </div>
        </div>
        """

    # Top Strengths & Improvements
    strengths = voss_analysis.get("top_strengths", [])
    improvements = voss_analysis.get("top_improvements", [])
    scripts = voss_analysis.get("concrete_scripts", [])

    strengths_html = "".join(f'<div style="padding:8px 0;border-bottom:1px solid var(--line);font-size:14px"><span style="color:#22c55e">✓</span> {s}</div>' for s in strengths)
    improvements_html = "".join(f'<div style="padding:8px 0;border-bottom:1px solid var(--line);font-size:14px"><span style="color:#f59e0b">↑</span> {s}</div>' for s in improvements)
    scripts_html = "".join(f'<div style="padding:10px;margin:6px 0;background:rgba(245,158,11,.07);border:1px solid rgba(245,158,11,.2);border-radius:10px;font-size:14px;font-style:italic">"{s}"</div>' for s in scripts)

    # Pricing Insight
    pricing_html = ""
    pricing_insight = voss_analysis.get("pricing_insight", "")
    if pricing_insight:
        pricing_html = f"""
        <div style="margin:20px 0">
          <h2>Pricing-Optimierung</h2>
          <div class="action-box">
            <p style="color:#e5e7eb;font-size:14px">{pricing_insight}</p>
          </div>
        </div>
        """

    # Uniqueness
    uniqueness_html = ""
    uniqueness = voss_analysis.get("uniqueness", "")
    if uniqueness:
        uniqueness_html = f"""
        <div style="margin:20px 0">
          <h2>Einzigartigkeit</h2>
          <div style="padding:16px;background:rgba(99,102,241,.08);border:1px solid rgba(99,102,241,.3);border-radius:14px">
            <p style="color:#e5e7eb;font-size:14px">{uniqueness}</p>
          </div>
        </div>
        """

    # Musk Check Section
    musk_html = ""
    if "error" not in musk_result:
        invest = musk_result.get("invest", False)
        invest_color = "#22c55e" if invest else "#ef4444"
        invest_text = "JA — ICH INVESTIERE" if invest else "NEIN — NOCH NICHT"
        one_line = musk_result.get("one_line", "")

        musk_sections = ""
        musk_keys = [
            ("first_principles", "First Principles"),
            ("ten_x_thinking", "10X Thinking"),
            ("automation", "Automation"),
            ("scale", "Scale"),
            ("pricing", "Pricing"),
            ("speed", "Speed"),
            ("killer_feature", "Killer Feature"),
            ("final_verdict", "Final Verdict"),
        ]
        for mk, ml in musk_keys:
            val = musk_result.get(mk, "")
            if val:
                musk_sections += f"""
                <div style="padding:12px 0;border-bottom:1px solid var(--line)">
                  <div style="font-size:12px;color:#f59e0b;font-weight:700;letter-spacing:1px;margin-bottom:4px">{ml.upper()}</div>
                  <div style="font-size:14px;color:#e5e7eb">{val}</div>
                </div>
                """

        musk_html = f"""
        <div style="margin:30px 0">
          <div class="hr"></div>
          <div style="text-align:center;margin:20px 0">
            <div style="font-size:12px;color:#6b7280;letter-spacing:2px;margin-bottom:8px">MUSK PROTOCOL</div>
            <h2 style="font-size:24px;margin:0">Würde Elon investieren?</h2>
            <div style="font-size:32px;font-weight:800;color:{invest_color};margin:12px 0">{invest_text}</div>
            <p style="font-style:italic;color:#94a3b8">"{one_line}"</p>
          </div>
          {musk_sections}
        </div>
        """
    elif "error" in musk_result:
        musk_html = f'<div style="margin:20px 0"><h2>Musk Check</h2><p style="color:#fca5a5">{musk_result["error"]}</p></div>'

    # Assemble full page
    body = f"""
    <h1 style="font-size:28px;">Analyse-Ergebnis</h1>
    <p class="small" style="color:#f59e0b;font-weight:600">CHRIS VOSS × ELITE LEVEL × MUSK CHECK</p>

    {error_html}

    <div class="hr"></div>
    <h2>Chat-Statistik</h2>
    {stats_html}
    {sender_html}

    <div class="hr"></div>
    {score_html}

    <div class="hr"></div>
    <h2>8 Dimensionen — Chris Voss Framework</h2>
    {dims_html}

    <div class="hr"></div>
    <h2>Top 3 Stärken</h2>
    {strengths_html}

    <div class="hr"></div>
    <h2>Top 3 Verbesserungen</h2>
    {improvements_html}

    <div class="hr"></div>
    <h2>Konkrete Formulierungen zum Üben</h2>
    {scripts_html}

    {pricing_html}
    {uniqueness_html}
    {musk_html}

    <div class="hr"></div>
    <div style="text-align:center;margin:20px 0">
      <a href="/kommunikation" class="btn" style="display:inline-block;width:auto;padding:14px 24px;">Neue Analyse starten</a>
    </div>
    <p class="small" style="text-align:center"><a href="/therapist">← Dashboard</a></p>
    """
    return _page("Analyse-Ergebnis", body, request)


# =========================================================
# ELITE PROGRAM — Persönliches Selbstentwicklungs-Programm
# Quellen: Jocko Willink, Cal Newport, Andrew Huberman, Peter Attia,
#          Naval Ravikant, Marcus Aurelius, David Goggins, Mark Manson,
#          David Deida, Chris Voss, Alex Hormozi, James Clear, Viktor Frankl
# =========================================================

ELITE_STEPS = [
    # ── MORGEN-BLOCK ──
    {
        "id": "wake_early", "time": "05:30", "pillar": "mind",
        "title": "Aufstehen — Füße auf den Boden",
        "icon": "⚡", "min_level": 1, "keystone": True,
        "why": "Der erste Sieg des Tages bestimmt alle anderen. Wer den Wecker verhandelt, verhandelt alles. (Jocko Willink: 'Discipline Equals Freedom')",
        "how": "Wecker klingelt → 3 Sekunden → Füße auf den Boden → Bett machen. Kein Snooze. Kein Nachdenken. Bett machen = erster abgeschlossener Auftrag.",
        "metric": None,
    },
    {
        "id": "no_phone_90", "time": "05:31", "pillar": "mind",
        "title": "Kein Handy — erste 90 Minuten",
        "icon": "📵", "min_level": 1, "keystone": False,
        "why": "Dein Gehirn ist morgens im Theta/Alpha-Übergang — maximal formbar. Wer als Erstes Social Media checkt, trainiert Reaktivität statt Intention. (Cal Newport: 'Digital Minimalism')",
        "how": "Handy bleibt im anderen Raum oder in einer Schublade. Flugmodus bis 07:00. Kein Check, kein Scroll, keine Nachrichten. Du bestimmst die Agenda des Tages — nicht dein Feed.",
        "metric": None,
    },
    {
        "id": "water_salt", "time": "05:35", "pillar": "body",
        "title": "500 ml Wasser + Prise Salz",
        "icon": "💧", "min_level": 1, "keystone": False,
        "why": "Nach 7–8 Stunden Schlaf bist du dehydriert. Elektrolyte (Na+) verbessern Nervenleitung und Energielevel sofort. (Huberman Lab: 'Optimizing Hydration')",
        "how": "500 ml stilles Wasser + 1 Prise Meersalz oder Himalaya-Salz. Kein Kaffee in den ersten 90 Minuten — Adenosin muss erst natural clearen.",
        "metric": None,
    },
    {
        "id": "sunlight", "time": "05:40", "pillar": "body",
        "title": "10 Min Sonnenlicht — draußen",
        "icon": "☀️", "min_level": 1, "keystone": False,
        "why": "Morgenlicht (>100.000 Lux) setzt den zirkadianen Cortisol-Peak und programmiert Melatonin-Release 14–16 h später. Kein Fenster, kein Supplement ersetzt das. (Huberman: 'Master Your Sleep')",
        "how": "Rausgehen. Keine Sonnenbrille. Richtung Sonne schauen (nicht direkt rein). 10 Minuten. Bei Wolken: 20 Minuten. Kombinierbar mit Spaziergang.",
        "metric": None,
    },
    {
        "id": "cold_shower", "time": "05:55", "pillar": "body",
        "title": "Kaltdusche — 2 Minuten",
        "icon": "🧊", "min_level": 2, "keystone": False,
        "why": "+250 % Dopamin-Baseline für 3–5 Stunden. Kein Supplement, kein Kaffee kommt an diesen Effekt ran. Gleichzeitig: Willenskraft-Training — du tust bewusst etwas Unangenehmes. (Huberman: 'Deliberate Cold Exposure')",
        "how": "Letzte 2 Minuten der Dusche: Wasser auf kalt. So kalt es geht. Atme kontrolliert. Nicht zusammenzucken. Steh aufrecht. Nach 30 Sekunden wird es leichter — dein Körper adaptiert.",
        "metric": None,
    },
    {
        "id": "meditation", "time": "06:00", "pillar": "mind",
        "title": "Meditation — Gedanken beobachten",
        "icon": "🧠", "min_level": 1, "keystone": True,
        "why": "Du trainierst die Fähigkeit, zwischen Reiz und Reaktion eine Lücke zu setzen. Das ist die Grundlage von Selbstkontrolle, Charisma und emotionaler Stärke. (Marcus Aurelius: 'Du hast Macht über deinen Geist — nicht über äußere Ereignisse.')",
        "how": "Sitz aufrecht. Timer stellen. Atem beobachten. Gedanken kommen — lass sie gehen, zurück zum Atem. Nicht 'entspannen' — üben. L1–L3: 5 Min. L4–L6: 10 Min. L7+: 20 Min.",
        "metric": {"key": "meditation_min", "label": "Minuten meditiert", "type": "number", "unit": "min"},
    },
    # ── LEISTUNGS-BLOCK ──
    {
        "id": "deep_work", "time": "06:15", "pillar": "money",
        "title": "Deep Work — Dein EINE Ding",
        "icon": "🎯", "min_level": 1, "keystone": True,
        "why": "Die produktivsten Menschen der Welt arbeiten 3–4 Stunden in Deep Work pro Tag — der Rest ist Verwaltung. 90 Minuten ununterbrochene Fokusarbeit schlagen 8 Stunden Multitasking. (Cal Newport: 'Deep Work')",
        "how": "Tür zu. Handy im anderen Raum. Browser: nur das eine Tab. Eine Aufgabe, die dein Leben voranbringt. Kein E-Mail, kein Chat. Timer auf 90 Min. Arbeite, bis er klingelt.",
        "metric": {"key": "deep_work_min", "label": "Deep-Work Minuten", "type": "number", "unit": "min"},
    },
    {
        "id": "eat_the_frog", "time": "06:15", "pillar": "money",
        "title": "Eat the Frog — Härteste Aufgabe zuerst",
        "icon": "🐸", "min_level": 2, "keystone": False,
        "why": "Willenskraft ist morgens am höchsten und nimmt über den Tag ab (Baumeister: 'Ego Depletion'). Wer die schwierigste Sache zuerst erledigt, gewinnt den Tag bis Mittag.",
        "how": "Gestern Abend hast du die eine härteste Aufgabe für heute definiert. Die machst du JETZT — vor Meetings, vor E-Mails, vor allem anderen. Erledigt = Freiheit für den Rest des Tages.",
        "metric": None,
    },
    {
        "id": "skill_30", "time": "08:00", "pillar": "money",
        "title": "Skill-Training — Dein EINER Skill",
        "icon": "🔧", "min_level": 3, "keystone": False,
        "why": "Spezifisches Wissen + Hebel = Wohlstand. Du brauchst EINEN Skill, den du auf Weltklasse-Niveau beherrschst. Nicht drei, nicht fünf. Einen. (Naval: 'Spezifisches Wissen kann nicht trainiert, nur entdeckt werden.')",
        "how": "30 Min gezielte Übung (Deliberate Practice — Ericsson). Nicht lesen, nicht Videos, nicht 'recherchieren'. MACHEN. Code schreiben. Texte schreiben. Verkaufsgespräche führen. Output produzieren.",
        "metric": {"key": "skill_min", "label": "Skill-Training Minuten", "type": "number", "unit": "min"},
    },
    # ── KÖRPER-BLOCK ──
    {
        "id": "workout", "time": "17:00", "pillar": "body",
        "title": "Training — Stärker werden",
        "icon": "🏋️", "min_level": 2, "keystone": True,
        "why": "Krafttraining ist das stärkste Anti-Aging-Mittel (Attia: 'Outlive'). Es verändert Hormonprofil, Körperhaltung, Selbstbild und wie andere dich wahrnehmen — gleichzeitig.",
        "how": "Mo: Push (Bankdrücken, Schulterdrücken, Trizeps) | Di: Zone 2 Cardio 45 Min | Mi: Pull (Kreuzheben, Rudern, Bizeps) | Do: Zone 2 | Fr: Legs (Kniebeuge, RDL) | Sa: VO2max 4×4 Min",
        "metric": {"key": "workout_type", "label": "Training heute", "type": "select", "options": ["Push", "Pull", "Legs", "Cardio Z2", "VO2max", "Mobility", "Pause"]},
    },
    {
        "id": "steps_10k", "time": "ganztags", "pillar": "body",
        "title": "10.000 Schritte",
        "icon": "🚶", "min_level": 2, "keystone": False,
        "why": "Zone 2 Grundbewegung. Verbessert Insulinsensitivität, Kreativität (Stanford: Walking + Kreativität +60%) und kardiovaskuläre Basis. Telefonieren im Gehen. Meetings im Gehen.",
        "how": "Tracke deine Schritte. Jeder Anruf: aufstehen, gehen. Jede Pause: 10 Min Spaziergang. Treppe statt Aufzug. Immer.",
        "metric": {"key": "steps", "label": "Schritte heute", "type": "number", "unit": ""},
    },
    {
        "id": "protein", "time": "ganztags", "pillar": "body",
        "title": "Protein-Ziel — 1.8 g/kg",
        "icon": "🥩", "min_level": 1, "keystone": False,
        "why": "Muskelaufbau, Sättigung, Thermogenese. Ohne ausreichend Protein ist Training verschwendet. (Layne Norton, Peter Attia: Protein als wichtigster Makronährstoff für Langlebigkeit + Körperkomposition)",
        "how": "Beispiel 80 kg: 144 g Protein/Tag. Auf 3 Mahlzeiten: ~48 g pro Mahlzeit. Hähnchen, Fisch, Eier, Magerquark, Whey. Tracke es heute — nach 2 Wochen weißt du es auswendig.",
        "metric": {"key": "protein_g", "label": "Protein (g)", "type": "number", "unit": "g"},
    },
    {
        "id": "no_liquid_cal", "time": "ganztags", "pillar": "body",
        "title": "Keine Flüssigkalorien",
        "icon": "🚫", "min_level": 1, "keystone": False,
        "why": "Softdrinks, Säfte, Alkohol — 500+ leere Kalorien täglich, keine Sättigung. Eliminierung allein reicht für messbare Körperfettreduktion in 4 Wochen.",
        "how": "Wasser. Schwarzer Kaffee. Tee. Sonst nichts. Kein 'aber ein Glas Saft'. Kein 'nur ein Bier'. 90 Tage. Ausnahmslos.",
        "metric": None,
    },
    # ── GELD / SYSTEME ──
    {
        "id": "outreach", "time": "10:00", "pillar": "money",
        "title": "5 Outreach-Aktionen — Sichtbarkeit",
        "icon": "📤", "min_level": 4, "keystone": False,
        "why": "Geld folgt Aufmerksamkeit. Wer nicht sichtbar ist, existiert nicht. 5 Kontakte pro Tag = 150/Monat = dein Netzwerk explodiert. (Hormozi: '$100M Leads')",
        "how": "5 DMs, 5 E-Mails, 5 Kommentare — oder Mischung. Nicht spammen. Wert liefern. Frage stellen. Hilfe anbieten. Verbindung aufbauen. Jeden. Einzelnen. Tag.",
        "metric": {"key": "outreach_count", "label": "Outreach-Aktionen", "type": "number", "unit": ""},
    },
    {
        "id": "finance_check", "time": "20:00", "pillar": "money",
        "title": "Finanzen geprüft — Was gemessen wird, wird besser",
        "icon": "💰", "min_level": 3, "keystone": False,
        "why": "Die meisten Menschen wissen nicht, was sie ausgeben. Bewusstsein allein reduziert Konsum um 15–20 %. (Housel: 'Psychology of Money': Reichtum = was du NICHT ausgibst.)",
        "how": "Öffne dein Konto. Prüfe die Transaktionen des Tages. Frage bei jeder Ausgabe: 'Bringt mich das meinem Ziel näher?' Wenn nein → eliminieren.",
        "metric": None,
    },
    # ── SOZIAL / CHARISMA ──
    {
        "id": "real_conversation", "time": "tagsüber", "pillar": "social",
        "title": "Eine echte Begegnung — volle Präsenz",
        "icon": "👤", "min_level": 2, "keystone": False,
        "why": "Charisma ist keine Eigenschaft — es ist Aufmerksamkeit. Wer einem Menschen das Gefühl gibt, der einzige Mensch im Raum zu sein, ist magnetisch. (David Deida: Präsenz als maskulinste Qualität. Mark Manson: echte Verletzlichkeit > Performance)",
        "how": "Ein Gespräch heute: Handy weg. Augenkontakt halten. Mehr fragen als erzählen. Namen merken. Zuhören, um zu verstehen — nicht um zu antworten. Pause vor der Antwort = Stärke.",
        "metric": None,
    },
    {
        "id": "active_listen", "time": "tagsüber", "pillar": "social",
        "title": "Mirror + Label — Chris Voss Technik",
        "icon": "🪞", "min_level": 5, "keystone": False,
        "why": "FBI-Verhandler nutzen Mirroring (letzte 3 Worte wiederholen) und Labeling ('Es scheint, als ob…'), um sofort Vertrauen und Verbindung aufzubauen. Funktioniert überall. (Voss: 'Never Split the Difference')",
        "how": "In einem Gespräch heute: 1× Mirror (letzte Worte des anderen leicht fragend wiederholen) + 1× Label ('Es klingt, als wäre dir X wichtig.'). Beobachte die Reaktion.",
        "metric": None,
    },
    {
        "id": "gratitude_spoken", "time": "tagsüber", "pillar": "social",
        "title": "Dankbarkeit ausgesprochen — zu einem Menschen",
        "icon": "🤝", "min_level": 4, "keystone": False,
        "why": "Nicht gedacht — ausgesprochen. Echter Dank vertieft Beziehungen messbar und verändert deine eigene Neurochemie (Präfrontaler Cortex-Aktivierung). (Huberman: Gratitude Practice, Gottman: 5:1 Ratio)",
        "how": "Einer Person heute sagen: 'Ich wollte dir sagen, dass [konkretes Verhalten] mir [konkreten Effekt] gegeben hat. Danke.' Nicht per Text — persönlich oder Anruf.",
        "metric": None,
    },
    # ── ABEND-BLOCK ──
    {
        "id": "reading_30", "time": "20:00", "pillar": "mind",
        "title": "30 Min Lesen — Bücher, keine Feeds",
        "icon": "📖", "min_level": 1, "keystone": False,
        "why": "Buffett liest 5 Stunden täglich. Munger: 'Geh jeden Abend klüger ins Bett, als du aufgestanden bist.' 30 Min/Tag = ~25 Bücher/Jahr. Das trennt dich von 95% der Menschen.",
        "how": "Physisches Buch oder E-Reader (kein Tablet mit Notifications). Sachbuch, Philosophie, Biografie. Nicht 'Lesen' auf Twitter. Stift bereithalten — markiere 1 Idee pro Session.",
        "metric": {"key": "read_min", "label": "Minuten gelesen", "type": "number", "unit": "min"},
    },
    {
        "id": "journal_evening", "time": "21:00", "pillar": "mind",
        "title": "Abend-Journal — 3 Fragen",
        "icon": "📝", "min_level": 1, "keystone": True,
        "why": "Marcus Aurelius schrieb jeden Abend. Das 'Meditations' war sein privates Journal. Reflexion ohne Aufschreiben ist Illusion — du vergisst 90 % bis morgen.",
        "how": "3 Fragen, handschriftlich: 1) Was habe ich heute GUT gemacht? 2) Wo war ich feige / habe ausgewichen? 3) Was ist morgen mein Frog (härteste Aufgabe)?",
        "metric": None,
    },
    {
        "id": "sleep_prep", "time": "21:30", "pillar": "body",
        "title": "Schlaf-Vorbereitung — Handy raus",
        "icon": "🌙", "min_level": 1, "keystone": False,
        "why": "Schlaf ist das stärkste legale Leistungsmittel. 7–8 h = +20 % Testosteron, +35 % Wachstumshormon, emotionale Regulation, Gedächtniskonsolidierung. Alles andere wird irrelevant ohne Schlaf. (Attia: 'Outlive', Walker: 'Why We Sleep')",
        "how": "21:30: Handy in anderem Raum (Wecker = echter Wecker). Licht dimmen. Kein Bildschirm. Zimmer: 18°C, dunkel, still. 22:00 Licht aus. Nicht verhandelbar.",
        "metric": {"key": "sleep_h", "label": "Stunden geschlafen (letzte Nacht)", "type": "number", "unit": "h"},
    },
    # ── ADVANCED / LEVEL 5+ ──
    {
        "id": "breathwork", "time": "06:10", "pillar": "mind",
        "title": "Box-Breathing — 5 Minuten",
        "icon": "🫁", "min_level": 5, "keystone": False,
        "why": "Navy SEALs nutzen Box-Breathing (4-4-4-4) für Stress-Inokulierung. Aktiviert Parasympathikus, senkt Herzfrequenz, schärft Fokus. Vor Deep Work = Leistungsboost. (Mark Divine: 'Unbeatable Mind')",
        "how": "4 Sek einatmen → 4 Sek halten → 4 Sek ausatmen → 4 Sek halten. 5 Minuten. Aufrecht sitzen. Augen geschlossen. Zähle die Zyklen.",
        "metric": None,
    },
    {
        "id": "memento_mori", "time": "06:30", "pillar": "mind",
        "title": "Memento Mori — Du stirbst",
        "icon": "💀", "min_level": 6, "keystone": False,
        "why": "Die Stoiker: 'Meditatio Mortis'. Steve Jobs: 'Remembering that you are going to die is the best way I know to avoid the trap of thinking you have something to lose.' 60 Sekunden Konfrontation mit der eigenen Endlichkeit = maximale Klarheit über Prioritäten.",
        "how": "60 Sekunden still. Stell dir vor, du hast noch 1 Jahr. Was fällt sofort weg? Was bleibt? Heute: handle nach dieser Klarheit.",
        "metric": None,
    },
    {
        "id": "kill_distraction", "time": "ganztags", "pillar": "mind",
        "title": "1 Ablenkung eliminieren — permanent",
        "icon": "✂️", "min_level": 3, "keystone": False,
        "why": "Jede Ablenkung, die du eliminierst, gibt dir Kapazität zurück — nicht additiv, sondern multiplikativ. Ein Nein zu Junk = Ja zu Deep Work. (Buffett: 'The difference between successful people and really successful people is that really successful people say no to almost everything.')",
        "how": "Identifiziere die EINE Sache, die dich heute am meisten Zeit gekostet hätte (Instagram, YouTube, Junk-Snack, toxischer Chat). Eliminiere sie für heute. Wenn sie 7 Tage überlebt: permanent weg.",
        "metric": None,
    },
    # ── ROLLO TOMASSI INTEGRATION ──
    {
        "id": "iron_rule_daily", "time": "06:20", "pillar": "social",
        "title": "Iron Rule des Tages — Frame halten",
        "icon": "🔴", "min_level": 2, "keystone": True,
        "why": "Tomassi Iron Rule #1: Frame ist alles. In jeder Interaktion bestimmt die Person mit dem stärkeren Frame die Realität. Frame = deine Mission, deine Überzeugungen, dein Lebensentwurf. Wer seinen Frame aufgibt, lebt im Frame eines anderen. (Rollo Tomassi: 'The Rational Male')",
        "how": "Lies die heutige Iron Rule (rotiert automatisch). Beobachte JEDE Interaktion heute: Wer setzt den Frame? Bei Tests: ruhig standhalten, nicht emotional reagieren. Amused Mastery = souveräner Humor statt Reaktivität. Tägliche Übung im Detail auf der Systemseite.",
        "metric": {"key": "frame_score", "label": "Frame-Kontrolle heute (1-10)", "type": "number", "unit": ""},
    },
    {
        "id": "smv_invest", "time": "ganztags", "pillar": "social",
        "title": "SMV Investment — Marktwert steigern",
        "icon": "📈", "min_level": 3, "keystone": False,
        "why": "SMV (Sexual Market Value) = Physique (25%) + Status (30%) + Game (25%) + Ressourcen (20%). Männer peaken 35-45. Jeder Tag ohne bewusste Investition ist verschwendetes Kapital. (Tomassi: 'Desire cannot be negotiated — it must be inspired.')",
        "how": "Wähle EINE SMV-Aktion heute: Physique (Training, Kleidung, Körpersprache), Status (Karriere, Social Proof, Führung), Game (mit Fremden sprechen, Push/Pull üben), Ressourcen (Investieren, Einkommen steigern). Dokumentiere was du gewählt hast.",
        "metric": {"key": "smv_action", "label": "SMV-Aktion heute", "type": "select", "options": ["Physique", "Status", "Game", "Ressourcen"]},
    },
    {
        "id": "abundance_check", "time": "21:00", "pillar": "social",
        "title": "Abundance Check — Kein Oneitis",
        "icon": "♾️", "min_level": 4, "keystone": False,
        "why": "Oneitis = krankhafte Fixierung auf EINE Person/EINEN Kunden/EINE Einkommensquelle. Zerstört Frame und Verhandlungsmacht in JEDEM Lebensbereich. Abundance Mentality ist die Grundlage für Stärke. (Tomassi: 'Scarcity denken = schwach handeln.')",
        "how": "Ehrliche Frage: Wo bist du heute in Scarcity gefallen? Wo hast du an EINER Option gehangen, statt Optionen zu bauen? Relationships, Business, Social — überall gilt: wer Optionen hat, verhandelt aus Stärke.",
        "metric": None,
    },
    # ── ELON MUSK / FIRST PRINCIPLES ──
    {
        "id": "first_principles", "time": "06:15", "pillar": "money",
        "title": "First Principles — Denke von Null",
        "icon": "🚀", "min_level": 3, "keystone": False,
        "why": "Musk: 'Ich denke nicht in Analogien — ich zerlege Probleme in ihre physikalischen Grundbestandteile und baue von dort.' Das ist der Grund, warum er Raketen, E-Autos und Gehirn-Interfaces gleichzeitig baut. Analogie-Denken kopiert. First Principles erfindet.",
        "how": "Nimm EINE Annahme in deinem Leben/Business, die 'alle so machen'. Frage 3×: Warum? Was, wenn das Gegenteil stimmt? Was wäre die optimale Lösung, wenn es keine Regeln gäbe? Schreibe die Antwort auf.",
        "metric": None,
    },
    {
        "id": "time_boxing", "time": "08:00", "pillar": "money",
        "title": "Time Boxing — 5-Minuten-Blöcke (Musk)",
        "icon": "⏱️", "min_level": 4, "keystone": False,
        "why": "Musk blockt seinen Tag in 5-Minuten-Einheiten. Nicht weil er neurotisch ist — sondern weil Parkinson's Law gilt: Arbeit dehnt sich auf die verfügbare Zeit aus. Enge Zeitfenster = maximale Intensität = 10× Output.",
        "how": "Blocke die nächsten 4 Stunden in konkrete Einheiten. Jede Aufgabe bekommt eine Zeitgrenze. Timer sichtbar. Wenn die Zeit um ist, nächste Aufgabe — auch wenn nicht fertig. Morgen optimierst du.",
        "metric": {"key": "timeboxed_hours", "label": "Time-boxed Stunden heute", "type": "number", "unit": "h"},
    },
    {
        "id": "build_not_consume", "time": "ganztags", "pillar": "money",
        "title": "Bauen, nicht konsumieren — Builder Mindset",
        "icon": "🔨", "min_level": 2, "keystone": False,
        "why": "Musk: 'Builder profitieren, Konsumenten werden ersetzt.' Naval: 'Du wirst nicht reich, indem du deine Zeit vermietest. Du musst Equity besitzen.' Jede Stunde, die du konsumierst (Scroll, Netflix, News), ist eine Stunde, die du nicht baust.",
        "how": "Tracke heute brutal ehrlich: Wie viele Stunden hast du GEBAUT (Code, Content, Produkt, Skill, Beziehung) vs. KONSUMIERT (Scroll, Netflix, News, passives Zuschauen)? Ziel: 80% Build.",
        "metric": {"key": "build_hours", "label": "Build-Stunden heute", "type": "number", "unit": "h"},
    },
    # ── INCOME ENGINE INTEGRATION ──
    {
        "id": "revenue_action", "time": "09:00", "pillar": "money",
        "title": "Revenue-Aktion — Geld verdienen JETZT",
        "icon": "💵", "min_level": 3, "keystone": False,
        "why": "Hormozi: 'Die ersten 4 Stunden deines Tages gehören der Umsatzgenerierung. Alles andere ist Ablenkung.' Keine Strategie ersetzt Verkaufen. Kein Plan ersetzt Pitchen. Umsatz heilt alle Wunden.",
        "how": "EINE direkte Revenue-Aktion: Kunde anrufen, Angebot schreiben, Pitch senden, Produkt listen, Service verkaufen. Nicht 'planen' — MACHEN. Am Ende des Tages: Hast du Geld bewegt? Ja oder Nein.",
        "metric": None,
    },
    {
        "id": "leverage_build", "time": "15:00", "pillar": "money",
        "title": "Hebel bauen — Code, Content, Capital, People",
        "icon": "⚙️", "min_level": 5, "keystone": False,
        "why": "Naval's 4 Hebel: Code (skaliert unendlich), Media/Content (skaliert unendlich), Capital (Geld arbeiten lassen), People (Team multipliziert). Ohne Hebel tauschst du Zeit gegen Geld — das skaliert nie.",
        "how": "30 Min heute in EINEN Hebel investieren: SaaS/Tool bauen (Code), Content-Piece erstellen (Media), investieren/sparen (Capital), oder delegieren/outsourcen (People). Welchen Hebel hast du heute bewegt?",
        "metric": {"key": "leverage_type", "label": "Hebel heute", "type": "select", "options": ["Code", "Content/Media", "Capital", "People/Team", "keiner"]},
    },
]

# Welche Schritte pro Level sichtbar
def _elite_steps_for_level(level: int) -> list:
    return [s for s in ELITE_STEPS if s["min_level"] <= level]


# Tages-Score berechnen
def _elite_compute_score(steps_for_today: list, done_ids: list, skipped_ids: list) -> int:
    if not steps_for_today:
        return 0
    keystone_steps = [s for s in steps_for_today if s.get("keystone")]
    normal_steps = [s for s in steps_for_today if not s.get("keystone")]
    keystone_done = sum(1 for s in keystone_steps if s["id"] in done_ids)
    normal_done = sum(1 for s in normal_steps if s["id"] in done_ids)
    keystone_total = len(keystone_steps) or 1
    normal_total = len(normal_steps) or 1
    # Keystone-Schritte zählen doppelt
    raw = (keystone_done / keystone_total) * 60 + (normal_done / normal_total) * 40
    return min(100, max(0, int(round(raw))))


# Streak-Logik
def _elite_update_streak(profile: EliteProfile, today_str: str):
    yesterday = (datetime.strptime(today_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    if profile.last_active_day == yesterday:
        profile.streak_days += 1
    elif profile.last_active_day != today_str:
        profile.streak_days = 1
    profile.last_active_day = today_str
    if profile.streak_days > (profile.longest_streak or 0):
        profile.longest_streak = profile.streak_days
    profile.total_days_logged = (profile.total_days_logged or 0) + 1


# Level-Progression
def _elite_check_level(db, profile: EliteProfile):
    last_7 = db.query(EliteDay).filter(
        EliteDay.profile_id == profile.id
    ).order_by(EliteDay.day.desc()).limit(7).all()
    if len(last_7) < 5:
        return
    avg_score = sum(d.score for d in last_7) / len(last_7)
    last_change = profile.last_level_change or "2000-01-01"
    days_since = (datetime.utcnow() - datetime.strptime(last_change, "%Y-%m-%d")).days
    if days_since < 7:
        return
    today = _now_local().date().isoformat()
    if avg_score >= 80 and profile.level < 10:
        profile.level += 1
        profile.last_level_change = today
    elif avg_score < 35 and profile.level > 1:
        profile.level -= 1
        profile.last_level_change = today


# Session-Key
def _elite_owner_key(request: Request) -> str:
    key = request.session.get("elite_key")
    if not key:
        key = secrets.token_hex(16)
        request.session["elite_key"] = key
    return key


# Profil laden / erstellen
def _elite_get_profile(db, request: Request) -> EliteProfile:
    key = _elite_owner_key(request)
    profile = db.query(EliteProfile).filter(EliteProfile.owner_key == key).first()
    if not profile:
        profile = EliteProfile(owner_key=key, level=1, streak_days=0, longest_streak=0,
                               total_days_logged=0, total_steps_completed=0, total_steps_skipped=0)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


# Heutigen Tag laden / erstellen
def _elite_get_day(db, profile: EliteProfile) -> EliteDay:
    today = _now_local().date().isoformat()
    day = db.query(EliteDay).filter(
        EliteDay.profile_id == profile.id, EliteDay.day == today
    ).first()
    if not day:
        day = EliteDay(profile_id=profile.id, day=today)
        db.add(day)
        db.commit()
        db.refresh(day)
    return day


# ── Elite-spezifischer Page-Wrapper ──
def _elite_page(title: str, body_html: str, step: int = 0, total: int = 0) -> HTMLResponse:
    progress_bar = ""
    if total > 0:
        pct = int((step / total) * 100) if total else 0
        progress_bar = f"""
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px">
          <div style="flex:1;height:6px;background:#1f2937;border-radius:999px;overflow:hidden">
            <div style="height:6px;background:linear-gradient(90deg,#f59e0b,#ef4444);border-radius:999px;width:{pct}%;transition:width .5s ease"></div>
          </div>
          <span style="font-size:13px;color:#f59e0b;font-weight:700;white-space:nowrap">{step}/{total}</span>
        </div>
        """

    nav = """
    <div style="display:flex;gap:8px;margin-top:24px;flex-wrap:wrap;justify-content:center">
      <a href="/elite" style="font-size:11px;color:#6b7280;text-decoration:none;padding:6px 10px;border:1px solid #1f2937;border-radius:8px">Übersicht</a>
      <a href="/elite/system" style="font-size:11px;color:#a5b4fc;text-decoration:none;padding:6px 10px;border:1px solid rgba(99,102,241,.3);border-radius:8px">System</a>
      <a href="/elite/income" style="font-size:11px;color:#86efac;text-decoration:none;padding:6px 10px;border:1px solid rgba(34,197,94,.3);border-radius:8px">Income</a>
      <a href="/elite/status" style="font-size:11px;color:#6b7280;text-decoration:none;padding:6px 10px;border:1px solid #1f2937;border-radius:8px">Status</a>
      <a href="/elite/weekly" style="font-size:11px;color:#6b7280;text-decoration:none;padding:6px 10px;border:1px solid #1f2937;border-radius:8px">Review</a>
    </div>
    """

    css = """
    <style>
      :root { --bg:#0a0a0f; --card:#0f111a; --muted:#6b7280; --text:#e5e7eb; --accent:#f59e0b; --line:#1f2937; --red:#ef4444; --green:#22c55e; }
      *{box-sizing:border-box}
      html,body{height:100%;margin:0}
      body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,sans-serif;background:radial-gradient(ellipse at 50% 0%,#1a1030 0%,#0a0a0f 70%);color:var(--text);min-height:100vh}
      a{color:var(--accent);text-decoration:none}
      .wrap{max-width:480px;margin:0 auto;padding:20px 16px 60px}
      .card{background:linear-gradient(180deg,rgba(255,255,255,.04),rgba(255,255,255,.01));border:1px solid var(--line);border-radius:20px;padding:28px 22px;box-shadow:0 24px 80px rgba(0,0,0,.5)}
      h1{font-size:28px;line-height:1.15;margin:0 0 8px;font-weight:800}
      h2{font-size:18px;margin:20px 0 10px;color:#f3f4f6}
      p{color:var(--muted);line-height:1.6;margin:8px 0}
      .hr{height:1px;background:var(--line);margin:20px 0}
      .icon-big{font-size:48px;margin-bottom:12px;display:block}
      .pillar-tag{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;padding:4px 10px;border-radius:999px;margin-bottom:12px}
      .pillar-body{background:rgba(34,197,94,.12);color:#4ade80;border:1px solid rgba(34,197,94,.3)}
      .pillar-money{background:rgba(245,158,11,.12);color:#fbbf24;border:1px solid rgba(245,158,11,.3)}
      .pillar-mind{background:rgba(99,102,241,.12);color:#a5b4fc;border:1px solid rgba(99,102,241,.3)}
      .pillar-social{background:rgba(236,72,153,.12);color:#f9a8d4;border:1px solid rgba(236,72,153,.3)}
      .btn-done{display:block;width:100%;background:linear-gradient(180deg,#22c55e,#16a34a);color:#fff;border:none;border-radius:14px;padding:16px;font-weight:700;font-size:17px;cursor:pointer;margin-top:16px;letter-spacing:.3px}
      .btn-done:active{transform:scale(.97)}
      .btn-skip{display:block;width:100%;background:transparent;color:var(--muted);border:1px solid var(--line);border-radius:14px;padding:12px;font-size:14px;cursor:pointer;margin-top:8px}
      .btn-primary{display:block;width:100%;background:linear-gradient(180deg,#fbbf24,#f59e0b);color:#111;border:none;border-radius:14px;padding:16px;font-weight:700;font-size:17px;cursor:pointer;margin-top:16px}
      .btn-outline{display:inline-block;background:transparent;border:1px solid var(--line);color:var(--muted);border-radius:10px;padding:8px 14px;font-size:13px;cursor:pointer}
      input[type=number],input[type=text],select,textarea{width:100%;background:#0b1223;border:1px solid #263246;color:#e5e7eb;border-radius:12px;padding:12px;font-size:16px;outline:none;margin-top:6px}
      input:focus,select:focus,textarea:focus{border-color:#f59e0b}
      label{display:block;color:#cbd5e1;font-size:13px;margin:12px 0 4px;font-weight:600}
      .streak-fire{font-size:22px;font-weight:800;color:#f59e0b;display:flex;align-items:center;gap:6px}
      .level-badge{display:inline-flex;align-items:center;gap:6px;background:linear-gradient(180deg,rgba(245,158,11,.15),rgba(239,68,68,.1));border:1px solid rgba(245,158,11,.3);color:#fbbf24;padding:6px 14px;border-radius:999px;font-weight:700;font-size:14px}
      .step-row{display:flex;align-items:center;gap:12px;padding:12px;border:1px solid var(--line);border-radius:14px;margin:6px 0;cursor:pointer;transition:all .2s}
      .step-row:hover{border-color:#374151;background:rgba(255,255,255,.02)}
      .step-done{border-color:rgba(34,197,94,.3);background:rgba(34,197,94,.05)}
      .step-done .step-check{color:#22c55e}
      .step-skipped{border-color:rgba(107,114,128,.3);opacity:.5}
      .step-check{width:28px;height:28px;border-radius:50%;border:2px solid var(--line);display:flex;align-items:center;justify-content:center;font-size:14px;flex-shrink:0}
      .step-done .step-check{border-color:#22c55e;background:rgba(34,197,94,.1)}
      .kpi-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:16px 0}
      .kpi{border:1px solid var(--line);border-radius:14px;padding:14px;background:rgba(255,255,255,.02);text-align:center}
      .kpi b{display:block;font-size:24px;margin-top:4px;color:var(--accent)}
      .kpi span{font-size:12px;color:var(--muted)}
      .heatmap{display:grid;grid-template-columns:repeat(7,1fr);gap:3px;margin:12px 0}
      .heatmap div{aspect-ratio:1;border-radius:4px;min-width:0}
      @media(max-width:400px){.kpi-grid{grid-template-columns:1fr}}
    </style>
    """

    html = f"""
    <html><head>
      <meta name="viewport" content="width=device-width,initial-scale=1.0">
      <title>{title} — Elite Program</title>
      {css}
    </head>
    <body>
      <div class="wrap">
        <div class="card">
          {progress_bar}
          {body_html}
        </div>
        {nav}
      </div>
    </body></html>
    """
    return HTMLResponse(html)


# =========================================================
# ELITE ROUTES
# =========================================================

# ── Landing / Übersicht ──
@app.get("/elite", response_class=HTMLResponse)
def elite_landing(request: Request, db=Depends(get_db)):
    profile = _elite_get_profile(db, request)
    day = _elite_get_day(db, profile)
    steps = _elite_steps_for_level(profile.level)
    done_ids = json.loads(day.steps_done_json or "[]")
    skipped_ids = json.loads(day.steps_skipped_json or "[]")
    done_count = len(done_ids)
    total = len(steps)
    remaining = total - done_count - len(skipped_ids)
    score = _elite_compute_score(steps, done_ids, skipped_ids)

    # Nächster unerledigter Step
    next_idx = None
    for i, s in enumerate(steps):
        if s["id"] not in done_ids and s["id"] not in skipped_ids:
            next_idx = i
            break

    # Level bar
    level_pct = min(100, int(score * 1.0))
    streak_text = f"🔥 {profile.streak_days} Tage" if profile.streak_days > 0 else "Starte heute"

    if next_idx is not None:
        cta = f'<a href="/elite/step/{next_idx}" class="btn-primary" style="display:block;text-align:center;margin-top:20px">Weiter → {steps[next_idx]["icon"]} {steps[next_idx]["title"]}</a>'
    elif remaining <= 0 and done_count > 0:
        cta = '<a href="/elite/complete" class="btn-primary" style="display:block;text-align:center;margin-top:20px">Tag abschließen ✓</a>'
    else:
        cta = '<a href="/elite/step/0" class="btn-primary" style="display:block;text-align:center;margin-top:20px">Tag starten →</a>'

    # Step list
    step_list = ""
    for i, s in enumerate(steps):
        if s["id"] in done_ids:
            cls = "step-row step-done"
            check = "✓"
        elif s["id"] in skipped_ids:
            cls = "step-row step-skipped"
            check = "–"
        else:
            cls = "step-row"
            check = ""
        step_list += f"""
        <a href="/elite/step/{i}" style="text-decoration:none;color:inherit">
          <div class="{cls}">
            <div class="step-check">{check}</div>
            <div style="flex:1;min-width:0">
              <div style="font-size:13px;color:var(--muted)">{s['time']} · {s['icon']}</div>
              <div style="font-size:15px;font-weight:600;color:#e5e7eb">{s['title']}</div>
            </div>
          </div>
        </a>
        """

    # ── Iron Rule des Tages (Rollo) ──
    day_of_year = _now_local().timetuple().tm_yday
    rollo_rules = ROLLO_TOMASSI["core_principles"]["iron_rules"]
    todays_rule = rollo_rules[day_of_year % len(rollo_rules)]

    iron_rule_html = f"""
    <div style="background:rgba(239,68,68,.06);border:1px solid rgba(239,68,68,.25);border-radius:14px;padding:14px;margin:12px 0">
      <div style="font-size:10px;color:#f87171;letter-spacing:1.5px;font-weight:700;margin-bottom:4px">🔴 IRON RULE DES TAGES</div>
      <div style="font-size:14px;font-weight:700;color:#fca5a5">{todays_rule['rule']}</div>
      <div style="font-size:12px;color:#9ca3af;margin-top:6px;line-height:1.5">{todays_rule['description'][:150]}…</div>
      <div style="font-size:11px;color:#f59e0b;margin-top:6px">→ {todays_rule['daily_practice'][:120]}</div>
    </div>
    """

    # ── Aktueller Musk-Zeitblock ──
    now = _now_local()
    current_block = None
    for block in BILLIONAIRE_DAILY_PLAN["schedule"]:
        bh, bm = block["time"].split(":")
        block_time = now.replace(hour=int(bh), minute=int(bm), second=0)
        if now >= block_time:
            current_block = block

    musk_html = ""
    if current_block:
        musk_html = f"""
    <div style="background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.25);border-radius:14px;padding:14px;margin:0 0 12px">
      <div style="font-size:10px;color:#f59e0b;letter-spacing:1.5px;font-weight:700;margin-bottom:4px">🚀 JETZT — MUSK-BLOCK</div>
      <div style="font-size:14px;font-weight:700;color:#fbbf24">{current_block['block']}</div>
      <div style="font-size:12px;color:#9ca3af;margin-top:4px">{current_block['action'][:130]}</div>
    </div>
    """

    # ── Income Phase ──
    total_days_in = profile.total_days_logged or 0
    if total_days_in < 90:
        phase_name = "Phase 1: Foundation"
        phase_target = "500–2.000€/Tag"
        phase_color = "#22c55e"
    elif total_days_in < 365:
        phase_name = "Phase 2: Scale"
        phase_target = "2.000–5.000€/Tag"
        phase_color = "#f59e0b"
    else:
        phase_name = "Phase 3: Compound"
        phase_target = "5.000–50.000€/Tag"
        phase_color = "#ef4444"

    body = f"""
    <div style="text-align:center;margin-bottom:16px">
      <div class="level-badge">Level {profile.level}</div>
      <div class="streak-fire" style="justify-content:center;margin-top:10px">{streak_text}</div>
      <p style="font-size:13px;margin-top:4px">Longest: {profile.longest_streak or 0} Tage · Tag {total_days_in}</p>
    </div>

    <div class="kpi-grid">
      <div class="kpi"><span>Score heute</span><b>{score}%</b></div>
      <div class="kpi"><span>Erledigt</span><b>{done_count}/{total}</b></div>
      <div class="kpi"><span>Income Phase</span><b style="font-size:13px;color:{phase_color}">{phase_name}</b></div>
      <div class="kpi"><span>Ziel</span><b style="font-size:14px">{phase_target}</b></div>
    </div>

    {cta}

    {iron_rule_html}
    {musk_html}

    <div class="hr"></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:16px">
      <a href="/elite/system" style="display:block;padding:12px;background:rgba(99,102,241,.06);border:1px solid rgba(99,102,241,.2);border-radius:12px;text-align:center;text-decoration:none">
        <div style="font-size:20px">⚡</div>
        <div style="font-size:12px;font-weight:700;color:#a5b4fc;margin-top:4px">Komplett-System</div>
        <div style="font-size:10px;color:#6b7280">Rollo + Musk + Naval</div>
      </a>
      <a href="/elite/income" style="display:block;padding:12px;background:rgba(34,197,94,.06);border:1px solid rgba(34,197,94,.2);border-radius:12px;text-align:center;text-decoration:none">
        <div style="font-size:20px">💰</div>
        <div style="font-size:12px;font-weight:700;color:#86efac;margin-top:4px">Income Engine</div>
        <div style="font-size:10px;color:#6b7280">Phasen + Hebel</div>
      </a>
    </div>

    <h2>Heutiger Plan</h2>
    {step_list}
    """
    return _elite_page("Elite Program", body)


# ── Onboarding ──
@app.get("/elite/setup", response_class=HTMLResponse)
def elite_setup(request: Request, db=Depends(get_db)):
    profile = _elite_get_profile(db, request)
    body = f"""
    <div style="text-align:center;margin-bottom:16px">
      <span style="font-size:56px">🏔️</span>
      <h1>Elite Program</h1>
      <p>90 Tage. 3 Säulen. Kein Ausweg.</p>
    </div>

    <div class="hr"></div>
    <p style="color:#e5e7eb;font-size:14px;line-height:1.7">
      <b>Körper</b> — Kraft, Schlaf, Ernährung, Kälte<br>
      <b>Geld</b> — Deep Work, Skill, Sichtbarkeit, Systeme<br>
      <b>Geist</b> — Meditation, Lesen, Journal, Stoa<br>
      <b>Charisma</b> — Präsenz, Zuhören, Verbindung
    </p>

    <div class="hr"></div>
    <p style="font-size:13px">Du startest auf <b>Level 1</b> mit den Grundlagen. Jede Woche mit &gt;80 % Score → Level Up. Mehr Schritte, höherer Standard. Level 10 = Elite.</p>

    <form method="post" action="/elite/setup">
      <label>Dein Name (optional)</label>
      <input type="text" name="name" value="{profile.display_name or ''}" placeholder="Wie soll ich dich nennen?">

      <label>Dein EINER Skill — was willst du meistern?</label>
      <input type="text" name="skill" value="{profile.one_skill or ''}" placeholder="z.B. Programmieren, Copywriting, Sales, Video...">

      <button type="submit" class="btn-primary">Commitment — Ich bin bereit</button>
    </form>
    """
    return _elite_page("Setup", body)


@app.post("/elite/setup", response_class=HTMLResponse)
def elite_setup_save(request: Request, name: str = Form(""), skill: str = Form(""), db=Depends(get_db)):
    profile = _elite_get_profile(db, request)
    if name.strip():
        profile.display_name = name.strip()
    if skill.strip():
        profile.one_skill = skill.strip()
    db.commit()
    return RedirectResponse("/elite", status_code=303)


# ── Heute: Tagesübersicht ──
@app.get("/elite/today", response_class=HTMLResponse)
def elite_today(request: Request, db=Depends(get_db)):
    return RedirectResponse("/elite", status_code=303)


# ── Step Slideshow ──
@app.get("/elite/step/{idx}", response_class=HTMLResponse)
def elite_step_view(idx: int, request: Request, db=Depends(get_db)):
    profile = _elite_get_profile(db, request)
    day = _elite_get_day(db, profile)
    steps = _elite_steps_for_level(profile.level)

    if idx < 0 or idx >= len(steps):
        return RedirectResponse("/elite", status_code=303)

    step = steps[idx]
    done_ids = json.loads(day.steps_done_json or "[]")
    skipped_ids = json.loads(day.steps_skipped_json or "[]")
    already_done = step["id"] in done_ids
    already_skipped = step["id"] in skipped_ids

    pillar_cls = f"pillar-{step['pillar']}"
    pillar_label = {"body": "KÖRPER", "money": "GELD", "mind": "GEIST", "social": "CHARISMA"}[step["pillar"]]

    # Metric input
    metric_html = ""
    metrics = json.loads(day.metrics_json or "{}")
    if step.get("metric"):
        m = step["metric"]
        saved = metrics.get(m["key"], "")
        if m["type"] == "number":
            metric_html = f"""
            <label>{m['label']}</label>
            <div style="display:flex;gap:8px;align-items:center">
              <input type="number" name="metric_value" value="{saved}" placeholder="0" style="flex:1" inputmode="decimal">
              <span style="color:var(--muted);font-size:14px">{m.get('unit','')}</span>
            </div>
            """
        elif m["type"] == "select":
            opts = "".join(f'<option value="{o}" {"selected" if saved == o else ""}>{o}</option>' for o in m["options"])
            metric_html = f"""
            <label>{m['label']}</label>
            <select name="metric_value">{opts}</select>
            """

    # Status badge
    status_html = ""
    if already_done:
        status_html = '<div style="text-align:center;padding:10px;background:rgba(34,197,94,.1);border:1px solid rgba(34,197,94,.3);border-radius:12px;color:#4ade80;font-weight:700;margin-bottom:12px">✓ Erledigt</div>'
    elif already_skipped:
        status_html = '<div style="text-align:center;padding:10px;background:rgba(107,114,128,.1);border:1px solid rgba(107,114,128,.3);border-radius:12px;color:#9ca3af;margin-bottom:12px">– Übersprungen</div>'

    # Nav arrows
    prev_btn = f'<a href="/elite/step/{idx-1}" class="btn-outline">← Zurück</a>' if idx > 0 else '<span></span>'
    next_btn = f'<a href="/elite/step/{idx+1}" class="btn-outline">Weiter →</a>' if idx < len(steps) - 1 else '<a href="/elite/complete" class="btn-outline">Abschluss →</a>'

    keystone_badge = '<span style="font-size:11px;background:rgba(239,68,68,.15);color:#fca5a5;border:1px solid rgba(239,68,68,.3);padding:3px 8px;border-radius:999px;margin-left:8px">KEYSTONE</span>' if step.get("keystone") else ""

    body = f"""
    <div style="text-align:center">
      <span class="icon-big">{step['icon']}</span>
      <span class="pillar-tag {pillar_cls}">{pillar_label}</span>{keystone_badge}
      <h1>{step['title']}</h1>
      <p style="font-size:13px;color:#94a3b8">{step['time']}</p>
    </div>

    {status_html}

    <div class="hr"></div>

    <h2>Warum</h2>
    <p style="font-size:14px;color:#cbd5e1;line-height:1.7">{step['why']}</p>

    <h2>Wie — konkret</h2>
    <p style="font-size:14px;color:#e5e7eb;line-height:1.7">{step['how']}</p>

    <div class="hr"></div>

    <form method="post" action="/elite/step/{idx}">
      {metric_html}
      <button type="submit" name="action" value="done" class="btn-done">✓ Erledigt</button>
      <button type="submit" name="action" value="skip" class="btn-skip">Übersprungen</button>
    </form>

    <div style="display:flex;justify-content:space-between;align-items:center;margin-top:20px">
      {prev_btn}
      {next_btn}
    </div>
    """
    return _elite_page(step["title"], body, step=idx + 1, total=len(steps))


@app.post("/elite/step/{idx}", response_class=HTMLResponse)
def elite_step_submit(idx: int, request: Request, action: str = Form("done"), metric_value: str = Form(""), db=Depends(get_db)):
    profile = _elite_get_profile(db, request)
    day = _elite_get_day(db, profile)
    steps = _elite_steps_for_level(profile.level)

    if idx < 0 or idx >= len(steps):
        return RedirectResponse("/elite", status_code=303)

    step = steps[idx]
    done_ids = json.loads(day.steps_done_json or "[]")
    skipped_ids = json.loads(day.steps_skipped_json or "[]")
    metrics = json.loads(day.metrics_json or "{}")

    if action == "done":
        if step["id"] not in done_ids:
            done_ids.append(step["id"])
        if step["id"] in skipped_ids:
            skipped_ids.remove(step["id"])
        profile.total_steps_completed = (profile.total_steps_completed or 0) + 1
    else:
        if step["id"] not in skipped_ids:
            skipped_ids.append(step["id"])
        if step["id"] in done_ids:
            done_ids.remove(step["id"])
        profile.total_steps_skipped = (profile.total_steps_skipped or 0) + 1

    if step.get("metric") and metric_value.strip():
        metrics[step["metric"]["key"]] = metric_value.strip()

    day.steps_done_json = json.dumps(done_ids)
    day.steps_skipped_json = json.dumps(skipped_ids)
    day.metrics_json = json.dumps(metrics)
    day.score = _elite_compute_score(steps, done_ids, skipped_ids)
    db.commit()

    # Nächster unerledigter Step
    for i in range(idx + 1, len(steps)):
        if steps[i]["id"] not in done_ids and steps[i]["id"] not in skipped_ids:
            return RedirectResponse(f"/elite/step/{i}", status_code=303)
    # Alle erledigt → Complete
    return RedirectResponse("/elite/complete", status_code=303)


# ── Tag abschließen ──
@app.get("/elite/complete", response_class=HTMLResponse)
def elite_complete(request: Request, db=Depends(get_db)):
    profile = _elite_get_profile(db, request)
    day = _elite_get_day(db, profile)
    steps = _elite_steps_for_level(profile.level)
    done_ids = json.loads(day.steps_done_json or "[]")
    skipped_ids = json.loads(day.steps_skipped_json or "[]")
    score = _elite_compute_score(steps, done_ids, skipped_ids)

    day.score = score
    today_str = _now_local().date().isoformat()
    _elite_update_streak(profile, today_str)
    _elite_check_level(db, profile)
    db.commit()

    # Score Rating
    if score >= 90:
        rating = "ELITE 🔥"
        color = "#22c55e"
        msg = "Weltklasse. Genau so. Jeden Tag."
    elif score >= 70:
        rating = "STARK 💪"
        color = "#f59e0b"
        msg = "Solider Tag. Keystone-Habits gehalten. Weiter."
    elif score >= 50:
        rating = "OK ⚡"
        color = "#eab308"
        msg = "Basis steht, aber da geht mehr. Was hat dich aufgehalten?"
    else:
        rating = "SCHWACH ⚠️"
        color = "#ef4444"
        msg = "Kein Problem — aber morgen wird besser. Was war der Blocker?"

    # Done / Missed summary
    done_list = ""
    missed_list = ""
    for s in steps:
        if s["id"] in done_ids:
            done_list += f'<div style="color:#4ade80;font-size:14px;padding:3px 0">✓ {s["icon"]} {s["title"]}</div>'
        elif s["id"] in skipped_ids:
            missed_list += f'<div style="color:#9ca3af;font-size:14px;padding:3px 0">– {s["icon"]} {s["title"]}</div>'
        else:
            missed_list += f'<div style="color:#ef4444;font-size:14px;padding:3px 0">✗ {s["icon"]} {s["title"]}</div>'

    body = f"""
    <div style="text-align:center">
      <span style="font-size:64px;display:block;margin-bottom:8px">{"🏆" if score >= 90 else "📊"}</span>
      <h1 style="color:{color}">{rating}</h1>
      <p style="font-size:14px;color:#e5e7eb">{msg}</p>
    </div>

    <div class="kpi-grid">
      <div class="kpi"><span>Score</span><b style="color:{color}">{score}%</b></div>
      <div class="kpi"><span>Streak</span><b>🔥 {profile.streak_days}</b></div>
      <div class="kpi"><span>Level</span><b>{profile.level}</b></div>
      <div class="kpi"><span>Gesamt Tage</span><b>{profile.total_days_logged}</b></div>
    </div>

    <div class="hr"></div>
    <h2 style="color:#4ade80">Erledigt</h2>
    {done_list or '<p style="font-size:14px">—</p>'}

    {f'<div class="hr"></div><h2 style="color:#ef4444">Verpasst</h2>{missed_list}' if missed_list else ''}

    <div class="hr"></div>

    <form method="post" action="/elite/complete">
      <label>Notizen / Reflexion (optional)</label>
      <textarea name="notes" rows="3" placeholder="Was habe ich heute gelernt? Was war der Blocker?">{day.notes or ''}</textarea>
      <button type="submit" class="btn-primary">Tag speichern</button>
    </form>

    <div style="text-align:center;margin-top:16px">
      <a href="/elite" style="font-size:14px;color:var(--muted)">← Zurück zur Übersicht</a>
    </div>
    """
    return _elite_page("Tag abgeschlossen", body)


@app.post("/elite/complete", response_class=HTMLResponse)
def elite_complete_save(request: Request, notes: str = Form(""), db=Depends(get_db)):
    profile = _elite_get_profile(db, request)
    day = _elite_get_day(db, profile)
    day.notes = notes.strip() if notes.strip() else day.notes
    db.commit()
    return RedirectResponse("/elite", status_code=303)


# ── Status / Metriken / Level-System ──
@app.get("/elite/status", response_class=HTMLResponse)
def elite_status(request: Request, db=Depends(get_db)):
    profile = _elite_get_profile(db, request)
    steps = _elite_steps_for_level(profile.level)

    # Letzte 30 Tage
    last_30 = db.query(EliteDay).filter(
        EliteDay.profile_id == profile.id
    ).order_by(EliteDay.day.desc()).limit(30).all()
    last_30.reverse()

    avg_score = int(sum(d.score for d in last_30) / len(last_30)) if last_30 else 0
    best_score = max((d.score for d in last_30), default=0)

    # Heatmap (letzte 28 Tage)
    heatmap_html = ""
    today = _now_local().date()
    day_scores = {d.day: d.score for d in last_30}
    for i in range(27, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        sc = day_scores.get(d, -1)
        if sc < 0:
            color = "#1f2937"
        elif sc >= 80:
            color = "#22c55e"
        elif sc >= 60:
            color = "#84cc16"
        elif sc >= 40:
            color = "#eab308"
        elif sc > 0:
            color = "#ef4444"
        else:
            color = "#1f2937"
        heatmap_html += f'<div style="background:{color}" title="{d}: {sc}%"></div>'

    # Pillar breakdown
    pillar_done = {"body": 0, "money": 0, "mind": 0, "social": 0}
    pillar_total = {"body": 0, "money": 0, "mind": 0, "social": 0}
    for d in last_30:
        done_ids = json.loads(d.steps_done_json or "[]")
        for s in steps:
            pillar_total[s["pillar"]] = pillar_total.get(s["pillar"], 0) + 1
            if s["id"] in done_ids:
                pillar_done[s["pillar"]] = pillar_done.get(s["pillar"], 0) + 1

    pillar_html = ""
    pillar_names = {"body": ("KÖRPER", "pillar-body", "🏋️"), "money": ("GELD", "pillar-money", "💰"), "mind": ("GEIST", "pillar-mind", "🧠"), "social": ("CHARISMA", "pillar-social", "👤")}
    for p_key in ["body", "money", "mind", "social"]:
        name, cls, icon = pillar_names[p_key]
        pct = int(pillar_done[p_key] / pillar_total[p_key] * 100) if pillar_total[p_key] else 0
        pillar_html += f"""
        <div style="border:1px solid var(--line);border-radius:14px;padding:14px;background:rgba(255,255,255,.02)">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <span class="pillar-tag {cls}">{icon} {name}</span>
            <span style="font-size:18px;font-weight:700;color:{'#22c55e' if pct >= 70 else '#f59e0b' if pct >= 40 else '#ef4444'}">{pct}%</span>
          </div>
          <div style="height:4px;background:#1f2937;border-radius:999px;margin-top:8px">
            <div style="height:4px;background:{'#22c55e' if pct >= 70 else '#f59e0b' if pct >= 40 else '#ef4444'};border-radius:999px;width:{pct}%"></div>
          </div>
        </div>
        """

    # Level progression info
    next_level_info = ""
    if profile.level < 10:
        next_steps = [s for s in ELITE_STEPS if s["min_level"] == profile.level + 1]
        if next_steps:
            ns_list = "".join(f'<div style="font-size:13px;color:#cbd5e1;padding:2px 0">{s["icon"]} {s["title"]}</div>' for s in next_steps)
            next_level_info = f"""
            <div class="hr"></div>
            <h2>Level {profile.level + 1} — Neue Schritte</h2>
            <p style="font-size:13px">Erreiche 80 % Durchschnitt über 7 Tage für Level Up.</p>
            {ns_list}
            """

    # Metrics history (latest)
    metrics_html = ""
    if last_30:
        latest_metrics = json.loads(last_30[-1].metrics_json or "{}")
        if latest_metrics:
            metrics_html = '<div class="hr"></div><h2>Letzte Metriken</h2>'
            for k, v in latest_metrics.items():
                label = k.replace("_", " ").title()
                metrics_html += f'<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line);font-size:14px"><span style="color:var(--muted)">{label}</span><span style="font-weight:600">{v}</span></div>'

    body = f"""
    <div style="text-align:center;margin-bottom:16px">
      <div class="level-badge" style="font-size:18px;padding:10px 20px">Level {profile.level}</div>
      <div class="streak-fire" style="justify-content:center;margin-top:12px">🔥 {profile.streak_days} Tage Streak</div>
      <p style="font-size:12px">Rekord: {profile.longest_streak or 0} Tage | Gesamt: {profile.total_days_logged or 0} Tage</p>
    </div>

    <div class="kpi-grid">
      <div class="kpi"><span>∅ Score (30T)</span><b>{avg_score}%</b></div>
      <div class="kpi"><span>Best Score</span><b>{best_score}%</b></div>
      <div class="kpi"><span>Steps erledigt</span><b>{profile.total_steps_completed or 0}</b></div>
      <div class="kpi"><span>Skill</span><b style="font-size:14px">{profile.one_skill or '—'}</b></div>
    </div>

    <div class="hr"></div>
    <h2>Letzte 28 Tage</h2>
    <div class="heatmap">{heatmap_html}</div>
    <p style="font-size:11px;display:flex;gap:12px;justify-content:center">
      <span><span style="display:inline-block;width:10px;height:10px;background:#22c55e;border-radius:2px"></span> 80+</span>
      <span><span style="display:inline-block;width:10px;height:10px;background:#84cc16;border-radius:2px"></span> 60+</span>
      <span><span style="display:inline-block;width:10px;height:10px;background:#eab308;border-radius:2px"></span> 40+</span>
      <span><span style="display:inline-block;width:10px;height:10px;background:#ef4444;border-radius:2px"></span> &lt;40</span>
      <span><span style="display:inline-block;width:10px;height:10px;background:#1f2937;border-radius:2px"></span> kein Eintrag</span>
    </p>

    <div class="hr"></div>
    <h2>Säulen-Analyse</h2>
    <div style="display:grid;gap:10px">{pillar_html}</div>

    {next_level_info}
    {metrics_html}

    <div style="text-align:center;margin-top:24px">
      <a href="/elite/setup" class="btn-outline">Profil bearbeiten</a>
    </div>
    """
    return _elite_page("Status", body)


# ── Wöchentliches Review ──
@app.get("/elite/weekly", response_class=HTMLResponse)
def elite_weekly(request: Request, db=Depends(get_db)):
    profile = _elite_get_profile(db, request)
    today = _now_local().date()
    week_str = today.strftime("%Y-W%W")

    existing = db.query(EliteWeeklyReview).filter(
        EliteWeeklyReview.profile_id == profile.id,
        EliteWeeklyReview.week == week_str
    ).first()

    # Letzte 7 Tage Score
    last_7 = db.query(EliteDay).filter(
        EliteDay.profile_id == profile.id
    ).order_by(EliteDay.day.desc()).limit(7).all()
    avg_7 = int(sum(d.score for d in last_7) / len(last_7)) if last_7 else 0
    days_active_7 = len(last_7)

    body = f"""
    <div style="text-align:center;margin-bottom:16px">
      <span style="font-size:48px;display:block">📋</span>
      <h1>Wöchentliches Review</h1>
      <p>KW: {week_str}</p>
    </div>

    <div class="kpi-grid">
      <div class="kpi"><span>∅ Score 7T</span><b>{avg_7}%</b></div>
      <div class="kpi"><span>Aktive Tage</span><b>{days_active_7}/7</b></div>
    </div>

    <div class="hr"></div>

    <form method="post" action="/elite/weekly">
      <input type="hidden" name="week" value="{week_str}">

      <label>🏆 Was habe ich diese Woche gut gemacht?</label>
      <textarea name="wins" rows="3" placeholder="Siege, Durchbrüche, Disziplin-Momente...">{existing.wins if existing else ''}</textarea>

      <label>💀 Wo war ich feige / habe ausgewichen?</label>
      <textarea name="failures" rows="3" placeholder="Ehrlich. Kein Bullshit.">{existing.failures if existing else ''}</textarea>

      <label>🎯 Fokus nächste Woche — WAS ist die EINE Sache?</label>
      <textarea name="next_focus" rows="3" placeholder="Ein Ziel. Nicht drei.">{existing.next_focus if existing else ''}</textarea>

      <label>Selbst-Bewertung 1–10</label>
      <input type="number" name="self_score" min="1" max="10" value="{existing.self_score if existing else ''}" placeholder="Ehrlich: 1 = Totalausfall, 10 = Weltklasse">

      <button type="submit" class="btn-primary">Review speichern</button>
    </form>

    <div class="hr"></div>
    <h2>Vergangene Reviews</h2>
    """

    past = db.query(EliteWeeklyReview).filter(
        EliteWeeklyReview.profile_id == profile.id
    ).order_by(EliteWeeklyReview.week.desc()).limit(8).all()

    if past:
        for r in past:
            body += f"""
            <div style="border:1px solid var(--line);border-radius:12px;padding:14px;margin:8px 0;background:rgba(255,255,255,.02)">
              <div style="display:flex;justify-content:space-between;font-size:14px">
                <span style="font-weight:600">{r.week}</span>
                <span style="color:{'#22c55e' if (r.self_score or 0) >= 7 else '#f59e0b' if (r.self_score or 0) >= 5 else '#ef4444'};font-weight:700">{r.self_score or '—'}/10</span>
              </div>
              <p style="font-size:13px;margin-top:6px;color:#cbd5e1">{(r.next_focus or '—')[:120]}</p>
            </div>
            """
    else:
        body += '<p style="font-size:14px">Noch keine Reviews.</p>'

    return _elite_page("Weekly Review", body)


@app.post("/elite/weekly", response_class=HTMLResponse)
def elite_weekly_save(request: Request, week: str = Form(""), wins: str = Form(""), failures: str = Form(""), next_focus: str = Form(""), self_score: int = Form(0), db=Depends(get_db)):
    profile = _elite_get_profile(db, request)
    today = _now_local().date()
    week_str = week or today.strftime("%Y-W%W")

    existing = db.query(EliteWeeklyReview).filter(
        EliteWeeklyReview.profile_id == profile.id,
        EliteWeeklyReview.week == week_str
    ).first()

    last_7 = db.query(EliteDay).filter(
        EliteDay.profile_id == profile.id
    ).order_by(EliteDay.day.desc()).limit(7).all()
    completion_pct = int(sum(d.score for d in last_7) / len(last_7)) if last_7 else 0

    if existing:
        existing.wins = wins.strip()
        existing.failures = failures.strip()
        existing.next_focus = next_focus.strip()
        existing.self_score = max(1, min(10, self_score)) if self_score else None
        existing.completion_pct = completion_pct
    else:
        review = EliteWeeklyReview(
            profile_id=profile.id,
            week=week_str,
            wins=wins.strip(),
            failures=failures.strip(),
            next_focus=next_focus.strip(),
            self_score=max(1, min(10, self_score)) if self_score else None,
            completion_pct=completion_pct,
            level_at_review=profile.level,
        )
        db.add(review)

    _elite_check_level(db, profile)
    profile.last_review_week = week_str
    db.commit()
    return RedirectResponse("/elite/weekly", status_code=303)


# ── ELITE SYSTEM — Komplett-Framework (Rollo + Musk + Naval + Income) ──
@app.get("/elite/system", response_class=HTMLResponse)
def elite_system(request: Request, db=Depends(get_db)):
    profile = _elite_get_profile(db, request)

    # Today's Iron Rule
    day_of_year = _now_local().timetuple().tm_yday
    rules = ROLLO_TOMASSI["core_principles"]["iron_rules"]
    todays_rule = rules[day_of_year % len(rules)]

    # All Iron Rules
    rules_html = ""
    for i, rule in enumerate(rules):
        is_today = (i == day_of_year % len(rules))
        border = "border:2px solid #ef4444" if is_today else "border:1px solid rgba(239,68,68,.15)"
        badge = '<span style="font-size:10px;background:#ef4444;color:#fff;padding:2px 8px;border-radius:99px;margin-left:8px">HEUTE</span>' if is_today else ''
        rules_html += f"""
        <details style="{border};border-radius:12px;margin-bottom:8px;background:rgba(239,68,68,.03)" {"open" if is_today else ""}>
          <summary style="padding:12px 14px;cursor:pointer;list-style:none;display:flex;align-items:center">
            <b style="color:#fca5a5;font-size:13px;flex:1">{rule['rule']}</b>{badge}
          </summary>
          <div style="padding:0 14px 14px">
            <p style="font-size:13px;margin:0 0 8px;color:#d1d5db;line-height:1.6">{rule['description']}</p>
            <div style="font-size:12px;color:#f59e0b;background:rgba(245,158,11,.06);padding:8px 12px;border-radius:8px;margin-top:8px">
              <b>Tägliche Praxis:</b> {rule['daily_practice']}
            </div>
          </div>
        </details>"""

    # SMV Pillars
    smv_html = ""
    pillar_icons = {"physique": "💪", "status": "👑", "game": "🎯", "resources": "💰"}
    pillar_colors = {"physique": "#22c55e", "status": "#f59e0b", "game": "#6366f1", "resources": "#eab308"}
    for key, pillar in ROLLO_TOMASSI["smv_pillars"].items():
        pct = int(pillar["weight"] * 100)
        icon = pillar_icons.get(key, "•")
        color = pillar_colors.get(key, "#6b7280")
        actions = "".join(f'<div style="font-size:11px;color:#94a3b8;padding:2px 0">• {a}</div>' for a in pillar["actions"])
        smv_html += f"""
        <div style="border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:8px;background:rgba(255,255,255,.02)">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
            <span style="font-size:14px;font-weight:700;color:{color}">{icon} {pillar['label']}</span>
            <span style="font-size:16px;font-weight:800;color:{color}">{pct}%</span>
          </div>
          <div style="height:5px;background:#1f2937;border-radius:99px;margin-bottom:8px">
            <div style="height:5px;background:{color};border-radius:99px;width:{pct}%"></div>
          </div>
          {actions}
        </div>"""

    # Musk Schedule
    now = _now_local()
    musk_html = ""
    cat_colors = {
        "health": "#22c55e", "build": "#f59e0b", "revenue": "#ef4444",
        "review": "#6366f1", "communication": "#94a3b8", "learning": "#a855f7",
        "scale": "#ec4899", "network": "#06b6d4", "brand": "#f97316",
        "relationships": "#e879f9", "wind_down": "#6b7280", "sleep": "#334155",
    }
    for i_b, block in enumerate(BILLIONAIRE_DAILY_PLAN["schedule"]):
        bh, bm = block["time"].split(":")
        block_time = now.replace(hour=int(bh), minute=int(bm), second=0)
        is_current = False
        next_b = BILLIONAIRE_DAILY_PLAN["schedule"][i_b + 1] if i_b + 1 < len(BILLIONAIRE_DAILY_PLAN["schedule"]) else None
        if next_b:
            nh, nm = next_b["time"].split(":")
            is_current = block_time <= now < now.replace(hour=int(nh), minute=int(nm), second=0)
        else:
            is_current = now >= block_time
        color = cat_colors.get(block["category"], "#6b7280")
        border = f"2px solid {color}" if is_current else "1px solid #1f2937"
        bg = f"rgba(255,255,255,.04)" if is_current else "transparent"
        here = f'<div style="font-size:10px;color:{color};font-weight:700;margin-top:4px">← DU BIST HIER</div>' if is_current else ""
        musk_html += f"""
        <div style="border:{border};border-radius:10px;padding:10px 12px;margin-bottom:6px;background:{bg}">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <span style="font-size:12px;color:{color};font-weight:700">{block['time']}</span>
            <span style="font-size:13px;font-weight:600;color:#e5e7eb">{block['block']}</span>
            <span style="font-size:10px;color:#6b7280">{block['duration']}</span>
          </div>
          <div style="font-size:11px;color:#9ca3af;margin-top:4px">{block['action'][:100]}</div>
          {here}
        </div>"""

    # Naval's 4 Lever
    lever_html = ""
    lever_icons = {"Code (Software)": "💻", "Media (Content)": "📱", "Capital (Geld)": "💰", "People (Team)": "👥"}
    for lever in INCOME_STRATEGY["leverage_types"]["levers"]:
        icon = lever_icons.get(lever["name"], "⚙️")
        lever_html += f"""
        <div style="border:1px solid var(--line);border-radius:12px;padding:12px;background:rgba(255,255,255,.02)">
          <div style="font-size:20px;text-align:center">{icon}</div>
          <div style="font-size:13px;font-weight:700;color:#e5e7eb;text-align:center;margin:4px 0">{lever['name']}</div>
          <div style="font-size:11px;color:#9ca3af;text-align:center">{lever['description'][:80]}</div>
        </div>"""

    # Glossary (compact)
    glossary_html = ""
    for key, entry in list(ROLLO_TOMASSI["glossary"].items())[:8]:
        glossary_html += f"""
        <div style="padding:6px 0;border-bottom:1px solid #111827">
          <span style="color:#fca5a5;font-size:12px;font-weight:700">{entry['term']}</span>
          <span style="font-size:11px;color:#6b7280"> — {entry['definition'][:100]}</span>
        </div>"""

    body = f"""
    <div style="text-align:center;margin-bottom:16px">
      <span style="font-size:48px;display:block">⚡</span>
      <h1 style="font-size:22px;margin-bottom:4px">Das Komplett-System</h1>
      <p style="font-size:12px;color:#6b7280;margin:0">Rollo Tomassi + Elon Musk + Naval Ravikant + Hormozi</p>
      <div class="level-badge" style="margin-top:10px">Level {profile.level}</div>
    </div>

    <!-- SEKTION 1: ROLLO TOMASSI — IRON RULES -->
    <div class="hr"></div>
    <h2 style="color:#fca5a5;font-size:16px;display:flex;align-items:center;gap:8px">
      <span style="font-size:22px">🔴</span> Rollo Tomassi — Iron Rules
    </h2>
    <p style="font-size:12px;color:#6b7280;margin-bottom:12px">Aus 'The Rational Male' (5 Bücher). Frame = Realität. Wer den Frame hält, bestimmt alles.</p>
    {rules_html}

    <!-- SEKTION 2: SMV — SEXUAL MARKET VALUE -->
    <div class="hr"></div>
    <h2 style="color:#a5b4fc;font-size:16px;display:flex;align-items:center;gap:8px">
      <span style="font-size:22px">📊</span> SMV — Dein Marktwert
    </h2>
    <p style="font-size:12px;color:#6b7280;margin-bottom:12px">4 Säulen. Männer peaken 35–45. Investiere JETZT — jeder Tag zählt.</p>
    {smv_html}

    <!-- SEKTION 3: MUSK — TAGESSTRUKTUR -->
    <div class="hr"></div>
    <h2 style="color:#fbbf24;font-size:16px;display:flex;align-items:center;gap:8px">
      <span style="font-size:22px">🚀</span> Elon Musk — Tagesstruktur
    </h2>
    <p style="font-size:12px;color:#6b7280;margin-bottom:12px">Time-Boxing in 5-Min-Einheiten. First Principles. Impact/Stunde maximieren.</p>
    {musk_html}

    <!-- SEKTION 4: NAVAL — 4 HEBEL -->
    <div class="hr"></div>
    <h2 style="color:#86efac;font-size:16px;display:flex;align-items:center;gap:8px">
      <span style="font-size:22px">💰</span> Naval Ravikant — 4 Hebel zum Reichtum
    </h2>
    <p style="font-size:12px;color:#6b7280;margin-bottom:12px">'Du wirst nicht reich, indem du deine Zeit vermietest. Du musst Equity besitzen.'</p>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">{lever_html}</div>

    <!-- SEKTION 5: GLOSSAR -->
    <div class="hr"></div>
    <h2 style="color:#f59e0b;font-size:16px;display:flex;align-items:center;gap:8px">
      <span style="font-size:22px">🔑</span> Glossar
    </h2>
    <div style="background:rgba(255,255,255,.02);border:1px solid #1f2937;border-radius:12px;padding:12px 14px">
      {glossary_html}
    </div>

    <!-- SEKTION 6: DIE FORMEL -->
    <div class="hr"></div>
    <div style="text-align:center;padding:20px;background:linear-gradient(180deg,rgba(245,158,11,.08),rgba(239,68,68,.04));border:1px solid rgba(245,158,11,.2);border-radius:16px;margin:12px 0">
      <div style="font-size:11px;color:#f59e0b;letter-spacing:2px;font-weight:700;margin-bottom:8px">DIE FORMEL</div>
      <div style="font-size:18px;font-weight:800;color:#e5e7eb;line-height:1.4">
        Frame × Körper × Skill × Hebel = Unaufhaltbar
      </div>
      <div style="font-size:12px;color:#9ca3af;margin-top:8px;line-height:1.5">
        Frame (Rollo) → Wer du BIST<br>
        Körper (Huberman/Attia) → Wie du WIRKST<br>
        Skill (Newport/Ericsson) → Was du KANNST<br>
        Hebel (Naval/Musk) → Wie du SKALIERST
      </div>
    </div>

    <div style="text-align:center;margin-top:16px">
      <a href="/elite" class="btn-primary" style="display:inline-block;width:auto;padding:14px 24px">← Zurück zum Tagesplan</a>
    </div>
    """
    return _elite_page("Komplett-System", body)


# ── INCOME ENGINE — Phasen + Strategie ──
@app.get("/elite/income", response_class=HTMLResponse)
def elite_income(request: Request, db=Depends(get_db)):
    profile = _elite_get_profile(db, request)
    total_days = profile.total_days_logged or 0

    # Phase determination
    phases_html = ""
    phase_colors = ["#22c55e", "#f59e0b", "#ef4444"]
    phase_icons = ["🌱", "📈", "🏆"]
    for i, phase in enumerate(INCOME_STRATEGY["phases"]):
        color = phase_colors[i]
        icon = phase_icons[i]
        is_current = (i == 0 and total_days < 90) or (i == 1 and 90 <= total_days < 365) or (i == 2 and total_days >= 365)
        border = f"2px solid {color}" if is_current else "1px solid var(--line)"
        actions = "".join(f'<div style="font-size:12px;color:#cbd5e1;padding:3px 0;line-height:1.5">• {a}</div>' for a in phase["actions"])
        current_badge = f'<span style="font-size:10px;background:{color};color:#000;padding:2px 8px;border-radius:99px;font-weight:700">DU BIST HIER</span>' if is_current else ''
        phases_html += f"""
        <div style="border:{border};border-radius:16px;padding:16px;margin-bottom:12px;background:rgba(255,255,255,.02)">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
            <span style="font-size:15px;font-weight:700;color:{color}">{icon} {phase['phase']}</span>
            {current_badge}
          </div>
          <div style="font-size:13px;color:#fbbf24;font-weight:600;margin-bottom:4px">Ziel: {phase['target']}</div>
          <div style="font-size:12px;color:#9ca3af;margin-bottom:8px">Fokus: {phase['focus']}</div>
          {actions}
          <div style="font-size:11px;color:#6b7280;margin-top:8px;padding-top:8px;border-top:1px solid var(--line)">
            KPI: {phase['weekly_kpi']}
          </div>
        </div>"""

    # Sofort-Aktionen
    today_actions = "".join(f'<div style="font-size:13px;color:#e5e7eb;padding:4px 0">✓ {a}</div>' for a in INCOME_STRATEGY["immediate_actions"]["today"])
    week_actions = "".join(f'<div style="font-size:13px;color:#cbd5e1;padding:4px 0">→ {a}</div>' for a in INCOME_STRATEGY["immediate_actions"]["this_week"])

    # Elon Prediction
    elon_html = "".join(f'<div style="font-size:12px;color:#cbd5e1;padding:3px 0;line-height:1.5">• {p}</div>' for p in INCOME_STRATEGY["elon_prediction"]["analysis"])

    # Lever
    lever_html = ""
    for lever in INCOME_STRATEGY["leverage_types"]["levers"]:
        lever_html += f"""
        <div style="border:1px solid var(--line);border-radius:12px;padding:12px;background:rgba(255,255,255,.02)">
          <div style="font-size:14px;font-weight:700;color:#fbbf24">{lever['name']}</div>
          <div style="font-size:11px;color:#9ca3af;margin:4px 0">{lever['description']}</div>
          <div style="font-size:12px;color:#22c55e;font-weight:600">→ {lever['action']}</div>
        </div>"""

    # Reality Check
    reality = "".join(f'<div style="font-size:12px;color:#fca5a5;padding:2px 0">⚠️ {f}</div>' for f in INCOME_STRATEGY["reality_check"]["facts"])

    body = f"""
    <div style="text-align:center;margin-bottom:16px">
      <span style="font-size:48px;display:block">💰</span>
      <h1 style="font-size:22px;margin-bottom:4px">Income Engine</h1>
      <p style="font-size:12px;color:#6b7280;margin:0">Dein Weg zu finanziellem Durchbruch — Tag {total_days}</p>
    </div>

    <!-- Reality Check -->
    <div style="background:rgba(239,68,68,.05);border:1px solid rgba(239,68,68,.2);border-radius:14px;padding:14px;margin-bottom:16px">
      <div style="font-size:12px;font-weight:700;color:#fca5a5;margin-bottom:8px">⚠️ REALITY CHECK</div>
      {reality}
    </div>

    <!-- Phasen -->
    <h2 style="font-size:16px">📋 Die 3 Phasen</h2>
    {phases_html}

    <!-- Elon's Vorhersage -->
    <div class="hr"></div>
    <h2 style="font-size:16px">🚀 Elon Musk — Warum JETZT</h2>
    <div style="background:rgba(245,158,11,.05);border:1px solid rgba(245,158,11,.2);border-radius:14px;padding:14px">
      <div style="font-size:13px;font-weight:700;color:#fbbf24;margin-bottom:8px">{INCOME_STRATEGY['elon_prediction']['title']}</div>
      {elon_html}
    </div>

    <!-- 4 Hebel -->
    <div class="hr"></div>
    <h2 style="font-size:16px">⚙️ Die 4 Hebel (Naval Ravikant)</h2>
    <div style="display:grid;gap:8px">{lever_html}</div>

    <!-- Sofort-Aktionen -->
    <div class="hr"></div>
    <h2 style="font-size:16px">🎯 Was du HEUTE tust</h2>
    <div style="background:rgba(34,197,94,.05);border:1px solid rgba(34,197,94,.2);border-radius:14px;padding:14px;margin-bottom:12px">
      {today_actions}
    </div>

    <h2 style="font-size:16px">📅 Diese Woche</h2>
    <div style="background:rgba(245,158,11,.05);border:1px solid rgba(245,158,11,.2);border-radius:14px;padding:14px">
      {week_actions}
    </div>

    <div style="text-align:center;margin-top:20px">
      <a href="/elite" class="btn-primary" style="display:inline-block;width:auto;padding:14px 24px">← Zurück zum Tagesplan</a>
    </div>
    """
    return _elite_page("Income Engine", body)


# =========================================================
# ALEX — BLUE ELECTRIC LIFE
# Lebensdesign-System (Artist / Athlete / Father)
# =========================================================

ALEX = {
    "name": "Alex",
    "subtitle": "Blue Electric Life — zwischen München, Wien, Bergen und offenen Straßen",
    "leitsatz": "Ich bin kein Mann, der vor dem Leben flieht. Ich bin ein Mann, der sein Leben wieder zu Kunst macht.",
    "schlusssatz": "Ein Mann mit offenen Straßen im Herzen, Musik in den Händen, Ruhe im Nervensystem und einer Tochter, die weiß: Mein Vater ist frei — aber er bleibt.",
    "frame_rule": "Dieses Leben darf niemals toxisch-alpha, fake-spirituell, eskapistisch oder Instagram-künstlich werden. Es muss sich anfühlen wie: ein Mann baut sich langsam, ehrlich und stilvoll zurück ins Leben.",

    "pillars": [
        {"icon": "🜂", "key": "tochter",      "title": "Tochter / Wurzeln",        "lede": "Heiliger Anker. Alle zwei Wochen volle Präsenz. Sie erlebt einen stabilen, warmen, freien Vater."},
        {"icon": "✦",  "key": "ptgo",         "title": "PTGO / Mission",            "lede": "Keine Therapie-Stunden. Transformations-Kunst. Premium, ruhig, präzise, skalierbar."},
        {"icon": "♪",  "key": "musik",        "title": "Musik / Kunst",             "lede": "Kein Nebenprojekt — Markenkern. Soul, Rock, Blues, deutschsprachige Tiefe. Täglich."},
        {"icon": "❖",  "key": "koerper",      "title": "Körper / Gesundheit",       "lede": "Nicht Influencer. Pantherenergie: ruhig stark, geschmeidig, sinnlich, wach."},
        {"icon": "△",  "key": "freiheit",     "title": "Freiheit / Van / Fliegen", "lede": "Van und Gleitschirm sind Nervensystem-Regulation, nicht Flucht. Bewegte Stille."},
        {"icon": "◐",  "key": "stil",         "title": "Stil / Ästhetik",           "lede": "Schwarzes Leder, warme Räume, Tattoos im Goldlicht. Zeitlose Rock-Soul-Männlichkeit."},
        {"icon": "❤︎",  "key": "frauen",       "title": "Frauen / Verbindung",       "lede": "Ergänzung, nie Zentrum. Aus voller eigener Welt heraus lieben — nicht aus Bedürftigkeit."},
        {"icon": "≈",  "key": "nervensystem", "title": "Nervensystem / Ruhe",       "lede": "Atmen, Natur, Musik, Ordnung, Schönheit. Nie wieder gegen das eigene Nervensystem leben."},
    ],

    "week": [
        {"day": "Montag",     "mode": "München · Deep Work · Soul",        "weekday": 0,
         "items": [
            "06:00 — Aufstehen. Wasser, kalte Luft, Fenster auf. Kein Handy für 60 Min.",
            "06:20 — Mobility, Push-ups, Schattenboxen, Atmung. Pantherenergie, kein Studio-Ego.",
            "07:00 — Espresso, Früchte, Protein. Curtis Mayfield leise im Hintergrund.",
            "08:00–12:00 — Deep Work: PTGO, Musik, Texte, Systeme. Kein Chaos.",
            "12:30 — Clean Lunch, dann Spaziergang. Sonne statt Dauerreize.",
            "14:00–17:00 — Sessions, Calls, Business. Ruhig, präsent, nicht gehetzt.",
            "17:30 — Golden Hour: Isar oder Tegernsee. Nervensystempflege.",
            "20:00 — Dusche, warmes Licht, Piano oder Gitarre, schreiben.",
            "22:30 — Lesen. Schlafvorbereitung. Keine digitale Hölle."]},
        {"day": "Dienstag",   "mode": "Business + Musik",                   "weekday": 1,
         "items": [
            "06:30 — Morgenroutine wie Montag, etwas weicher.",
            "08:30–12:00 — PTGO-Systeme, Automatisierung, KI-Tools.",
            "13:00 — Krafttraining oder Schwimmen. Sauna danach.",
            "15:00–18:00 — Content, Masterclass, Calls.",
            "20:00 — Musikproduktion oder Songwriting. Mindestens 60 Min.",
            "22:30 — Abendroutine, früh ins Bett."]},
        {"day": "Mittwoch",   "mode": "Reset Day · Wohnung zurückerobern",  "weekday": 2,
         "items": [
            "Der wichtigste Tag der Woche. Freiheit braucht Ordnung.",
            "Vormittag — Admin, Buchhaltung, Termine, Tochterplanung, Wien-Vorbereitung.",
            "Mittag — Wohnung 83083: ein klar definierter Bereich pro Mittwoch (Bad / Küche / Schlafzimmer / Musikecke / Kleiderschrank).",
            "Ein Karton raus. Eine Schublade leer. Eine Fläche sichtbar. Nicht perfekt, aber atmungsfähig.",
            "Nachmittag — Wäsche, Körperpflege, Tasche packen für Wien.",
            "Bei Bedarf: Reinigungskraft, Entrümpler, Handwerker. Verantwortung > Chaos romantisieren.",
            "Abends — Training oder Spaziergang, dann Piano/Gitarre."]},
        {"day": "Donnerstag", "mode": "Wien · Anreise · Creative Gentleman", "weekday": 3,
         "items": [
            "Vorabend-Anreise per Bahn, wenn möglich. Schwarzer Mantel, Lederjacke, Notizbuch.",
            "Minimalistisches Gepäck. Eine Tasche, ein Anzug, drei Hemden, ein Buch.",
            "Hotel oder feste Unterkunft. Duschen, Spaziergang, gutes Abendessen.",
            "Spät: Kaffeehaus, Jazzbar oder Hotelzimmer mit Lampe, Musik, Schreiben.",
            "Keine billige Ablenkung. Wien = Inspirationsquelle, nicht Belastung."]},
        {"day": "Freitag",    "mode": "Wien Deep Presence / Umgangs-Freitag: Tochter ab 13:00", "weekday": 4,
         "items": [
            "Umgangs-Wochenende (alle 2 Wochen): Vormittag fokussiert, ab 13:00 Tochter abholen — kein Wien.",
            "Freie Wochen: Wien — präsent, fokussiert, ruhig. Sauna/Gym am Nachmittag.",
            "Freitag Nacht (Wien): warme Lampen, Hemd halb offen, Musik über Lautsprecher.",
            "Notizen aus Wien sammeln — für Musik, PTGO, Texte.",
            "Abendzug oder Übernachtung. Keine Hetze."]},
        {"day": "Samstag",    "mode": "Umgangs-Samstag (bis 18:30) / sonst Freiheit", "weekday": 5,
         "items": [
            "Umgangs-Wochenende (alle 2 Wochen): Tochter bis Samstag 18:30, dann warme Übergabe.",
            "Danach bzw. an freien Wochenenden: Van, Berge, Paragliding, Musik, Natur.",
            "Klar entscheiden, was heute ist. Keine Mischmodi."]},
        {"day": "Sonntag",    "mode": "Reflexion · Vorbereitung · Ruhe",     "weekday": 6,
         "items": [
            "Spaziergang. Tagebuch. Wochenrückblick (3 Punkte: Würde, Wachstum, Korrektur).",
            "Wäsche fertig, Tasche gepackt, Mo-Vormittag schon klar.",
            "Abends — Piano oder Lesen. Früh ins Bett."]},
    ],

    "weekend_a_tochter": {
        "title": "Umgangs-Wochenende — Tochter (Fr 13:00 – Sa 18:30, alle 2 Wochen)",
        "rituale": [
            "Gemeinsames Frühstück ohne Eile.",
            "Natur: See, Wald, Park, Tegernsee, Isar.",
            "Musik: ein Lied zusammen hören, ein Lied zusammen erfinden.",
            "Kochen mit ihr — kleine Aufgaben, große Würde.",
            "Lesen am Abend, ruhige Stimme.",
            "Eine kleine Tradition pro Wochenende (gleicher Bäcker, gleicher Platz)."],
        "nicht": [
            "Kein Dauerhandy.",
            "Kein gestresster Vater, kein Überprogramm.",
            "Keine Schuldgefühle kompensieren mit Geschenken.",
            "Keine Dating-Energie, keine fremden Frauen im Bild.",
            "Kein Van-Drama, kein Reise-Stress an diesem Wochenende."],
        "ziel": "Sie erlebt einen Vater, der stabil, warm, präsent, frei — und da ist."
    },
    "weekend_b_freiheit": {
        "title": "Wochenende B — Freiheit",
        "optionen": ["Van-Tour", "Paragliding (Tegernsee / Kössen / Gardasee)", "Dolomiten / Annecy / Südtirol",
                     "Musik-Retreat", "Sauna-Wochenende", "Kreativtage allein", "Sonntag Long Drive"],
        "leitsatz": "Bewegte Stille. Kein Eskapismus, kein Influencer-Roadtrip."
    },

    "day_blocks": [
        {"t1": "06:00", "t2": "07:00", "label": "Morgen-Anker",  "h": 6,  "items": [
            "Aufstehen, kein Handy, Wasser, Fenster öffnen, Licht reinlassen.",
            "5 Min Atmung. 10 Min Mobility. 20 Min Kraft/Körper.",
            "5 Min Vision: heute soll sich wie was anfühlen?",
            "Musik: Soul, Funk, Rock, Jazz — warme analoge Sounds."]},
        {"t1": "07:00", "t2": "08:00", "label": "Frühstück & Übergang", "h": 7, "items": [
            "Espresso. Früchte. Eier oder Protein.",
            "Kein Doomscroll. Erst nach dem ersten Deep-Work-Block."]},
        {"t1": "08:00", "t2": "12:00", "label": "Deep Work I",   "h": 8,  "items": [
            "PTGO / Masterclass / Musik / Texte / KI-Systeme.",
            "Phone in anderem Raum. Ein Ziel pro Block, schriftlich vorher festgelegt.",
            "Drei Sitzungen à 75 Min mit kurzen Pausen."]},
        {"t1": "12:00", "t2": "14:00", "label": "Clean Lunch & Walk", "h": 12, "items": [
            "Gemüse, Fisch oder hochwertige Alternative, kein Zucker, wenig Brot.",
            "20-Min Spaziergang. Sonne ins Gesicht. Keine Reize."]},
        {"t1": "14:00", "t2": "17:00", "label": "Sessions / Business", "h": 14, "items": [
            "Sessions, Calls, Strategie, Premium-Angebote.",
            "Ruhig, präsent, nicht gehetzt."]},
        {"t1": "17:30", "t2": "19:30", "label": "Golden Hour",   "h": 17, "items": [
            "Tegernsee, Isar, oder Spaziergang.",
            "Fahrt mit Musik, offenes Fenster, Sonnenuntergang."]},
        {"t1": "20:00", "t2": "22:00", "label": "Musik / Schreiben", "h": 20, "items": [
            "Warmlicht, Piano oder Gitarre.",
            "Tagebuch: 3 Zeilen. Was war Würde, was war Wachstum, was war Korrektur?"]},
        {"t1": "22:00", "t2": "23:00", "label": "Schlafvorbereitung", "h": 22, "items": [
            "Dusche, Duft, Lesen.",
            "Kein Bildschirm im Bett. Vorhang zu, Raum kühl."]},
    ],

    "koerper": {
        "kern": "Nicht Bodybuilder. Pantherenergie — geschmeidig, stark, ruhig.",
        "wochen": [
            "Mo — Mobility + Push-ups + Schattenboxen (30 Min)",
            "Di — Krafttraining schwer (60 Min) + Sauna",
            "Mi — Spaziergang lang + Atemarbeit",
            "Do — Reisemodus: Push-ups, Mobility im Hotelzimmer",
            "Fr — Sauna oder Schwimmen",
            "Sa — Natur lange Bewegung",
            "So — Ruhe, leichte Mobility"],
        "ernaehrung": [
            "Wasser zuerst. Espresso danach.",
            "Früchte, Eier oder Protein.",
            "Gemüse, Fisch, Fleisch oder hochwertige vegetarische Alternative.",
            "Wenig Zucker, wenig Alkohol, kein emotionales Essen.",
            "Sinnlich essen — Teller, Licht, Musik."]
    },

    "wien": {
        "modus": "Creative Gentleman Work Mode — keine Belastung, keine Hetze.",
        "anreise": [
            "Vorabend-Anreise per Bahn, wenn möglich. Sonst früher Frühzug.",
            "Schwarzer Mantel oder Lederjacke. Notizbuch. Buch. Kopfhörer.",
            "Gepäck: eine Tasche, drei Hemden, zwei Hosen, ein Anzug, ein Buch."],
        "unterkunft": [
            "Feste Unterkunft oder ein Hotel, das du immer wieder buchst.",
            "Ein Ort, der dich kennt. Vorzimmer für deinen Wien-Modus.",
            "Sauberes Bad, gutes Licht, ruhige Lage."],
        "rituale": [
            "Espresso im selben Café.",
            "Spaziergang durch eine schöne Straße zu Arbeitsbeginn.",
            "Mittagspause mit echter Mahlzeit, nicht to-go.",
            "Nach der Arbeit: duschen, laufen, gutes Essen.",
            "Abends: Kaffeehaus, Jazzbar, Schreiben.",
            "Freitag Nacht: Hotel-Energy — Lampen, Musik, Notizen."],
        "inspiration": ["Architektur", "Musik der Stadt", "alte Welt-Eleganz", "Texte und PTGO-Ideen", "Stille zwischen den Terminen"],
        "donts": ["Keine sinnlose Ablenkung", "Kein Dating-Modus", "Kein Übernachtsaufen", "Keine Hotellobby-Doom-Scrolls"]
    },

    "muenchen": {
        "ist_zustand": "Wohnung 83083 ist heute eher Lagerhalle alter Dinge. Sie wird wieder Männerraum, Musikraum, Vaterraum.",
        "atmosphaere": ["Holz und schwere Stoffe", "warme Lampen statt Deckenlicht", "Lederdetails, dunkle Farben",
                         "gute Lautsprecher sichtbar platziert", "Instrumente im Blickfeld", "Duft (warm, holzig, rauchig)",
                         "Pflanzen, klares Bad, gutes Bett, Ordnung"],
        "funktion": ["Vater-Ort: ihre Ecke, ihre Decke, ihre Bücher",
                     "Musik-Ort: Gitarre, Piano, Interface, Kopfhörer immer griffbereit",
                     "PTGO-Ort: ein Schreibtisch, eine Lampe, ein Sessel für Calls",
                     "Ruhe-Ort: ein Sessel am Fenster, leer und still",
                     "Dating-Ort nur, wenn es wirklich passt — nicht als Standard"],
        "reset_wochen": [
            "Woche 1 — Bad: leer machen, schrubben, Duft, neue Handtücher.",
            "Woche 2 — Küche: Schubladen aussortieren, Essentials sichtbar, alles andere weg.",
            "Woche 3 — Schlafzimmer: nur Bett, Licht, Nachttisch, Buch. Nichts auf dem Boden.",
            "Woche 4 — Musikecke einrichten: Instrumente, Kabel, Sound auf Punkt.",
            "Woche 5 — Kleiderschrank: 80% raus. Style-Guide leben.",
            "Woche 6 — Eingang, Flur, Schuhe — der erste Eindruck deines Lebens."],
        "hilfe": ["Reinigungskraft 1× pro Woche, fix gebucht.",
                  "Entrümpler einmalig für die großen Sachen.",
                  "Handwerker für Licht, Regale, Akustik — nicht selber machen wollen."]
    },

    "van": {
        "vehikel": "Mercedes Sprinter oder gleichwertig. Dunkles Exterieur (schwarz oder anthrazit).",
        "vibe": ["Dunkles Holz", "Warmes Licht (2700K)", "Cleanes Bett, schwere Decke",
                 "Espresso-Setup", "Mini-Studio (Audio-Interface, Mic, Kopfhörer)",
                 "Gute Lautsprecher", "Stauraum für Paragliding-Ausrüstung",
                 "Laptop-Arbeitsplatz", "Duft", "Kamera", "Gitarre"],
        "nicht": ["Kein billiger Camping-Look", "Keine Aufkleber", "Keine Klimbim-Optik", "Kein YouTuber-Vanlife-Klischee"],
        "frequenz": "1–2× pro Monat 3–5 Tage. Nicht öfter — sonst wird Freiheit zur Flucht.",
        "touren": [
            "Tegernsee — Wochenend-Reset, 1h Anfahrt.",
            "Kössen — Paragliding Klassiker, 1,5h.",
            "Gardasee — Wärme & Wasser, 4h.",
            "Dolomiten — Berge & Stille, 5h.",
            "Annecy — Fliegen am See, 7h.",
            "Südfrankreich / Italien — längere Solo-Touren 5+ Tage."],
        "ritual": ["Espresso zuerst", "Eine Seite Tagebuch", "30 Min Gitarre",
                   "Spaziergang", "Arbeit nur 2–3 Stunden", "Sonnenuntergang draußen"]
    },

    "paragliding": {
        "funktion": "Nervensystem-Regulation. Meditation in Bewegung. Kein Sport-Ego.",
        "kalender": [
            ("Jan–Feb", "Theorie, Wetterstudium, Materialcheck, Indoor-Training."),
            ("März",    "Saisonstart Tegernsee — kurze Flüge, Routine reaktivieren."),
            ("April",   "Kössen — Höhenmeter und Sicherheit aufbauen."),
            ("Mai–Juni","Gardasee + Dolomiten — Thermik, längere Flüge."),
            ("Juli",    "Annecy — Akro & Höhe."),
            ("August",  "Tochter-Wochenenden priorisieren, kürzere Flüge."),
            ("Sept",    "Dolomiten Soaring, lange Touren."),
            ("Okt",     "Tegernsee letzte Flüge, Saison-Reflexion."),
            ("Nov–Dez", "Material-Service, Theorie, mentale Vorbereitung."),
            ("Highlight","Ölüdeniz (Türkei) — 1× pro Jahr als Reise.")],
        "routine": [
            "Wetter 24h vorher checken (Windyty, DHV, lokaler Wetterdienst).",
            "Gear-Check am Vorabend: Schirm, Gurtzeug, Retter, Helm, Funk.",
            "Körper-Check morgens: Schlaf, Atmung, mentaler Zustand.",
            "Briefing-Ritual am Startplatz, keine Hetze.",
            "Nach Landung: 5 Min still sitzen, dann Notiz im Logbuch."],
        "gear": ["Schirm + Reserve (Service-Intervalle einhalten)", "Helm, Handschuhe, Gurtzeug",
                 "Vario, Funkgerät", "Wetterapp", "Sonnencreme, Wasser, Riegel", "Pilotenbuch"]
    },

    "ptgo": {
        "positionierung": "Premium-Kunstform. Keine Therapiestunden — Transformationserlebnisse.",
        "elemente": ["Manuelle Präzision", "Musik im Raum", "Stimme & Präsenz",
                     "Ritualisierter Beginn und Abschluss", "Premium-Preise", "Klare Grenzen"],
        "produkte": [
            "1:1 High Impact Session — limitiert, hochpreisig.",
            "PTGO Masterclass — gruppenbasiert, mehrere Wochen.",
            "Musikbasierte Körper-/Nervensystem-Releases (Audio-Programme).",
            "KI-gestützte Programme (Daily Check-in, Pattern Engine).",
            "Retreats — 2–4× pro Jahr, klein, sorgfältig kuratiert.",
            "Membership — kontinuierliche Begleitung, niederpreisig wiederkehrend.",
            "Premium Online-System — skaliertes High-End-Programm."],
        "marke": ["Klar", "Tief", "Ruhig", "Transformierend", "Niemals billig", "Niemals laut"],
        "rhythmus": ["Mo–Di Deep Work + Calls", "Do–Fr Wien-Sessions",
                     "Monatlich 1 Content-Schub", "Quartalsweise 1 Retreat oder Launch"]
    },

    "musik": {
        "sound": ["Soul", "Rock", "Funk", "Blues", "deutschsprachige Tiefe",
                  "warme Gitarren", "Piano", "analoge Atmosphäre",
                  "verletzliche Männlichkeit", "Freiheit, Nacht, Straße, Liebe, Feuer, Vater, Natur"],
        "ritual": "Täglich 30–90 Min Musik. Mindestens einmal pro Woche eine Session in echter Tiefe (2–3 Std).",
        "formate": ["Songs (fertig produziert)", "Voice Notes (roh, ehrlich)", "Live-Improvisationen (Video)",
                    "PTGO-Sounds (für Sessions, Retreats)", "Masterclass-Beds (Hintergrund-Atmos)",
                    "YouTube/Instagram-Ausschnitte (kurz, atmosphärisch)"],
        "setup": ["Gitarre + Piano sichtbar in München-Base", "Audio-Interface fest am Schreibtisch",
                  "Kondensator-Mikro + Akustik-Behandlung", "Eine DAW, die du beherrschst — nicht drei",
                  "Mobiler Setup für Van und Wien (Interface, Mic, Kopfhörer)"]
    },

    "style": {
        "kleidung": [
            "Schwarze Boots (1 hoch, 1 niedrig).",
            "Lederjacke (eine, gute, die altert).",
            "Leinenhemden (creme, weiß, schwarz).",
            "Gute Jeans (dunkel, schmal, ehrlich).",
            "Schwarze Shirts (dick, kein Logo).",
            "Mantel für Wien (lang, schwer, schwarz).",
            "Sonnenbrille (klassisch, nicht laut).",
            "Schmuck (Silber, dezent, dauerhaft getragen)."],
        "farben": ["Schwarz", "Weiß", "Creme", "Cognac", "Dunkelbraun", "Silber", "Gold", "Oliv", "Denim"],
        "duefte": ["Warm", "Holzig", "Rauchig", "Sinnlich — niemals süß oder generisch"],
        "nicht": ["Funktionsjacken ohne Stil", "Billige Sneaker", "Überladene Outfits",
                  "Fitness-Bro-Look", "Hype-Sneaker, Hype-Marken", "Logos im Gesicht des Outfits"],
        "tattoo_regel": "Tattoos sichtbar, aber nie bemüht. Sie erzählen — sie posieren nicht."
    },

    "frauen": {
        "grundsatz": "Frauen sind Ergänzung, nicht Zentrum. Sie ersetzen nicht die Mission.",
        "regeln": [
            "Nicht jagen.",
            "Nicht klammern.",
            "Nicht emotional betteln.",
            "Nicht kontrollieren.",
            "Keine Dauerdiskussionen.",
            "Keine Beziehung als Identitätsersatz."],
        "stattdessen": [
            "Eigene Welt, eigene Routine.",
            "Ruhige Kommunikation.",
            "Klare Grenzen, klare Wünsche.",
            "Sinnliche Atmosphäre statt Druck.",
            "Führung ohne Härte, Wärme ohne Bedürftigkeit."],
        "pia": [
            "Keine emotionalen Kämpfe mehr.",
            "Keine Verschmelzung, keine Chaos-Abhängigkeit.",
            "Keine Familiensystem-Übernahme, keine Retterrolle.",
            "Ruhe. Präsenz. Grenzen. Eigene Base. Eigene Identität.",
            "Freundlich, aber nicht selbstaufgebend."],
        "leitsatz": "Ich bin offen für Liebe, aber ich verrate mich nicht mehr dafür."
    },

    "tochter": {
        "grundsatz": "Heiliger Anker. Alle zwei Wochen: Freitag 13:00 bis Samstag 18:30 — volle, ruhige Präsenz in München.",
        "rituale": [
            "Frühstück ohne Eile — kein Handy am Tisch.",
            "Ein Naturort pro Wochenende (See, Wald, Berg).",
            "Eine Musikminute zusammen (Lied hören oder erfinden).",
            "Eine kleine Tradition (Bäcker, Spielplatz, Eisdiele).",
            "Lesen abends, ruhige Stimme.",
            "Kleines Abenteuer — Museum, Boot, kleines Konzert.",
            "Kochen mit ihr — sie hat eine kleine Aufgabe."],
        "nicht": ["Nebenbei-Handy", "Gestresster Vater", "Schuldgefühle kompensieren",
                  "Überprogramm", "Dating-Energie", "Familiensystem-Drama anderer Frauen"],
        "ziel": "Sie erlebt einen Vater, der stabil, warm, präsent, frei — und da ist."
    },

    "monthly": [
        "Habe ich meine Tochter wirklich präsent gesehen?",
        "Habe ich PTGO konkret weitergebaut?",
        "Habe ich Musik gemacht — nicht nur geplant?",
        "Habe ich trainiert und meinen Körper gepflegt?",
        "Habe ich Natur wirklich erlebt (nicht nur durchquert)?",
        "Habe ich keine Frau zum Zentrum meines Lebens gemacht?",
        "Habe ich mein Nervensystem geschützt?",
        "Habe ich Geld intelligent bewegt (Übersicht, Sparen, Investieren)?",
        "Habe ich Schönheit in mein Leben gebracht?",
        "Habe ich Freiheit und Verantwortung verbunden — nicht gegeneinander gestellt?",
    ],

    "year": [
        {"phase": "Jan–März", "title": "Studio & Strategie",   "items": ["Wien-Rhythmus festziehen", "PTGO-Premium-Angebot fertig", "Körper-Basis stabil", "Musik: 1 Song fertig"]},
        {"phase": "Apr–Juni", "title": "Bergluft & Build",     "items": ["Paragliding-Saison startet", "Van recherchieren / probieren", "Masterclass-Launch vorbereiten", "Gardasee-Tour"]},
        {"phase": "Juli–Aug", "title": "Tochter & Sonne",      "items": ["Lange Tochter-Wochenenden", "Reisen mit ihr (1 kleines Abenteuer)", "Musik draußen", "weniger Termine"]},
        {"phase": "Sept–Okt", "title": "Schreiben & Retreats", "items": ["1 Retreat", "Tegernsee-Phase", "Musik: 2. Song produziert", "PTGO-Skalierung Phase 2"]},
        {"phase": "Nov–Dez", "title": "Reflexion & Wien",      "items": ["Wien-Wochen verdichten", "Musikproduktion fokussiert", "Jahresplanung", "Ruhe & Lesen"]},
    ],

    "daily_checklist": [
        "60 Min ohne Handy nach dem Aufstehen.",
        "Wasser vor allem anderen.",
        "10 Min Mobility oder Bewegung.",
        "Espresso bewusst getrunken — nicht nebenbei.",
        "1 Deep-Work-Block ohne Ablenkung (mindestens 90 Min).",
        "Spaziergang an der frischen Luft.",
        "Clean Lunch ohne Bildschirm.",
        "Mindestens 15 Min Musik (Gitarre, Piano, Hören, Schreiben).",
        "Mindestens 3 Sätze Tagebuch (Würde / Wachstum / Korrektur).",
        "Eine Geste der Schönheit (Licht, Duft, Musik, Ordnung).",
        "Frauen nicht zum Zentrum gemacht.",
        "Nervensystem geschützt — keine emotionalen Sümpfe.",
        "Vor 23:00 Schirme aus.",
    ],

    "roadmap": [
        {"phase": "Tag 1–30 — Foundation", "lede": "Wohnung, Körper, Routine. Das Fundament.", "items": [
            "Wohnung 83083: Bad, Küche, Schlafzimmer freigeräumt.",
            "Reinigungskraft fix gebucht (1× pro Woche).",
            "Entrümpler einmalig durchgeführt.",
            "Morgen-Anker etabliert: 06:00, Wasser, Atmung, Mobility.",
            "Rauchen reduziert oder ersetzt durch Atemarbeit + Spaziergang.",
            "1 Deep-Work-Block jeden Werktag (90 Min minimum).",
            "Style-Reset: Kleiderschrank 80% raus, Essentials da."]},
        {"phase": "Tag 31–60 — Build", "lede": "PTGO Premium, Musik-Output, Van-Recherche.", "items": [
            "PTGO Premium-Angebot finalisiert (Preisliste, Beschreibung, Buchungsweg).",
            "1 Song roh fertig (Voice Notes oder erste Skizze produziert).",
            "Wien-Rhythmus etabliert: Do/Fr fest, gleiches Hotel.",
            "Paragliding-Saison gestartet, erste Flüge.",
            "Van-Recherche: 3 konkrete Modelle, 1 Testfahrt.",
            "Masterclass-Outline geschrieben."]},
        {"phase": "Tag 61–90 — Expand", "lede": "Skalierung, Sichtbarkeit, Vater-Tiefe.", "items": [
            "Masterclass-Launch oder Pilot durchgeführt.",
            "1 Song produziert und veröffentlicht (auch nur klein).",
            "Van entschieden oder konkret gemietet/getestet.",
            "1 Retreat geplant oder durchgeführt.",
            "Tochter-Tradition stabil etabliert.",
            "Pia-Dynamik geklärt — Ruhe, Grenzen, Klarheit.",
            "Jahresplan für die nächsten 9 Monate steht."]}
    ],

    "notion": {
        "tree": [
            "ALEX · Blue Electric Life",
            "├── 00 · Heute",
            "│   ├── Morgen-Anker",
            "│   ├── Deep-Work-Ziel",
            "│   └── 3 Zeilen Tagebuch",
            "├── 01 · Tochter",
            "│   ├── Rituale",
            "│   ├── Kalender (gerade KW)",
            "│   └── Ideen / kleine Abenteuer",
            "├── 02 · PTGO",
            "│   ├── Produkte",
            "│   ├── Pipeline & Calls",
            "│   ├── Masterclass / Retreats",
            "│   └── Marke & Texte",
            "├── 03 · Musik",
            "│   ├── Songs (DB)",
            "│   ├── Voice Notes",
            "│   ├── Setlists & Skizzen",
            "│   └── Releases",
            "├── 04 · Körper",
            "│   ├── Wochenplan",
            "│   ├── Ernährung",
            "│   └── Sauna / Cold / Schlaf",
            "├── 05 · Freiheit",
            "│   ├── Van (Setup, Touren-DB)",
            "│   ├── Paragliding (Flüge-DB, Gear)",
            "│   └── Reise-Karte",
            "├── 06 · Stil",
            "│   ├── Garderobe (DB)",
            "│   ├── Düfte",
            "│   └── Looks (Bilder)",
            "├── 07 · Frauen",
            "│   ├── Frame",
            "│   ├── Pia",
            "│   └── Kommunikations-Templates",
            "├── 08 · Nervensystem",
            "│   ├── Atem-Protokolle",
            "│   ├── Cold / Sauna Tracker",
            "│   └── Tagebuch (DB)",
            "├── 09 · München-Base",
            "│   ├── Reset-Wochenplan",
            "│   ├── Helfer (Reinigung, Entrümpler)",
            "│   └── Wunschliste (Möbel, Sound)",
            "├── 10 · Wien-Modus",
            "│   ├── Hotels & Anfahrt",
            "│   ├── Orte (Cafés, Bars, Spaziergänge)",
            "│   └── Notizen-Speicher",
            "├── 11 · 90-Tage-Roadmap",
            "└── 99 · Jahresvision",
        ],
        "datenbanken": [
            ("Tagebuch", "Datum · Zustand 0–10 · Würde · Wachstum · Korrektur · Tag-Tag"),
            ("Songs", "Titel · Status · BPM · Tonart · Lyrics-Link · Audio-Link · Notiz"),
            ("Flüge", "Datum · Ort · Dauer · Bedingungen · Notiz · Bewertung"),
            ("Tochter-Erinnerungen", "Datum · Ort · was wir gemacht haben · Foto · ihr Lieblingsmoment"),
            ("PTGO-Klient:innen", "Name · Status · letzter Kontakt · Pipeline-Stufe · nächste Aktion"),
            ("Garderobe", "Stück · Kategorie · Farbe · letztes Mal getragen · Pflege"),
        ],
        "tags": ["#tiefe", "#freiheit", "#tochter", "#wien", "#musik", "#korrektur", "#wuerde", "#wachstum"],
    },

    "no_chaos": [
        "Eine Frau zerstört dauerhaft dein Nervensystem.",
        "Du vernachlässigst deine Arbeit für Drama.",
        "Du verpasst deine Tochter emotional.",
        "Du reagierst nur noch, statt zu gestalten.",
        "Du bist permanent müde.",
        "Du erklärst dich, statt zu leben.",
        "Du wohnst in fremdem Chaos.",
        "Du verlierst Zugang zu deiner Kreativität.",
    ],
    "no_chaos_antwort": [
        "Rückzug.",
        "Ordnung in deinem Raum.",
        "Training.",
        "Musik.",
        "Natur.",
        "Klarheit — auch wenn sie unbequem ist.",
    ],

    # Umgang: alle zwei Wochen Freitag 13:00 – Samstag 18:30. Anker = kommendes Umgangs-Wochenende.
    "custody": {
        "anchor": "2026-05-22",
        "from": "Freitag 13:00",
        "to": "Samstag 18:30",
        "rhythm": "alle zwei Wochen",
        "city": "München",
    },
}

# Nachgetragen für 1:1-Treue zum Original-Auftrag (Abschnitt 13 + Identitäts-Leitsatz v1).
ALEX["leitsatz_v1"] = "Ich bin kein Mann, der flieht. Ich bin ein Mann, der bewusst lebt."
ALEX["nervensystem"] = {
    "intro": "Alex lebt nie wieder gegen sein eigenes Nervensystem. Schönheit ist keine Dekoration — sie ist Regulation.",
    "braucht": ["Ruhe", "Natur", "Schönheit", "Ordnung", "Musik", "körperliche Bewegung",
                "Freiheit", "klare Grenzen", "Luft", "Wasser", "Rhythmus"],
    "nicht": ["Dauerchaos", "Lärm", "emotionale Überladung", "fremde Familiensysteme",
              "emotionaler Druck", "vollgestellte Räume", "Unordnung", "Reaktivität"],
    "tools": ["Atmung", "Spaziergänge", "Sauna", "kaltes Wasser", "Musik", "Van-Auszeiten",
              "Paragliding", "Tagebuch", "Training", "Natur", "klare Kommunikation"],
}


_ALEX_CSS = """
<style>
  :root{
    --bg:#0b0805; --bg2:#160e08; --ink:#e9e4dc; --muted:#9b8f80;
    --line:#2a1f17; --line2:#3a2a1d;
    --gold:#c8821e; --gold2:#e2a04a; --cream:#f0e7d5;
    --cognac:#8a4f23; --leather:#1a120c; --red:#a02c2c; --green:#5f8c5a;
  }
  *{box-sizing:border-box}
  html,body{margin:0;padding:0;background:var(--bg);color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,'Inter','Segoe UI',Roboto,sans-serif;-webkit-font-smoothing:antialiased}
  body{background:
    radial-gradient(1000px 600px at 80% -200px, rgba(200,130,30,.10), transparent 60%),
    radial-gradient(800px 500px at 10% 100%, rgba(138,79,35,.08), transparent 60%),
    var(--bg);
    min-height:100vh}
  a{color:var(--gold2);text-decoration:none}
  a:hover{color:var(--cream)}
  .wrap{max-width:1080px;margin:0 auto;padding:32px 22px 80px}

  .hero{padding:8px 0 24px;border-bottom:1px solid var(--line)}
  .eyebrow{font-size:11px;letter-spacing:3px;color:var(--gold);text-transform:uppercase;font-weight:700}
  .title{font-family:'Cormorant Garamond','Playfair Display',Georgia,serif;font-size:56px;line-height:1.02;
    margin:8px 0 6px;letter-spacing:.5px;color:var(--cream);font-weight:600}
  .title em{font-style:italic;color:var(--gold2)}
  .sub{color:var(--muted);font-size:15px;max-width:760px;line-height:1.6;margin:8px 0 0}
  .leitsatz{margin:22px 0 6px;padding:18px 22px;border-left:3px solid var(--gold);background:linear-gradient(90deg,rgba(200,130,30,.07),transparent);
    font-family:'Cormorant Garamond',Georgia,serif;font-size:21px;color:var(--cream);font-style:italic;line-height:1.5}
  .schluss{margin-top:16px;padding:14px 18px;border:1px solid var(--line2);border-radius:14px;background:rgba(0,0,0,.25);
    color:var(--cream);font-family:'Cormorant Garamond',Georgia,serif;font-style:italic;font-size:17px;line-height:1.6}

  .tabs{display:flex;gap:6px;overflow-x:auto;padding:18px 22px 14px;margin:6px -22px 22px;
    border-bottom:1px solid var(--line);scrollbar-width:thin}
  .tabs::-webkit-scrollbar{height:6px}
  .tabs::-webkit-scrollbar-thumb{background:var(--line2);border-radius:3px}
  .tab{flex:0 0 auto;background:transparent;border:1px solid var(--line);color:var(--muted);
    padding:9px 14px;border-radius:999px;font-size:13px;cursor:pointer;letter-spacing:.3px;
    font-family:inherit;white-space:nowrap;transition:all .15s}
  .tab:hover{border-color:var(--gold);color:var(--cream)}
  .tab.active{background:linear-gradient(180deg,rgba(200,130,30,.16),rgba(200,130,30,.06));
    border-color:var(--gold);color:var(--cream);box-shadow:0 0 0 1px rgba(200,130,30,.25)}

  .panel{display:none;animation:fade .25s ease}
  .panel.active{display:block}
  @keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}

  h2.section{font-family:'Cormorant Garamond',Georgia,serif;font-size:30px;color:var(--cream);
    margin:0 0 6px;font-weight:600;letter-spacing:.5px}
  .section-sub{color:var(--muted);font-size:14px;margin:0 0 22px;line-height:1.6}

  .card{background:linear-gradient(180deg,rgba(255,255,255,.025),rgba(0,0,0,.15));
    border:1px solid var(--line);border-radius:18px;padding:20px 22px;margin:0 0 14px;
    box-shadow:0 20px 60px rgba(0,0,0,.35)}
  .card h3{margin:0 0 10px;font-family:'Cormorant Garamond',Georgia,serif;font-size:22px;color:var(--cream);font-weight:600;letter-spacing:.3px}
  .card .meta{font-size:11px;letter-spacing:2px;color:var(--gold);text-transform:uppercase;font-weight:700}
  .card p{color:var(--muted);font-size:14px;line-height:1.65;margin:6px 0}
  .card ul{margin:8px 0 0;padding:0;list-style:none}
  .card ul li{padding:7px 0 7px 22px;color:var(--ink);font-size:14.5px;line-height:1.55;
    position:relative;border-bottom:1px solid rgba(58,42,29,.4)}
  .card ul li:last-child{border-bottom:none}
  .card ul li::before{content:'';position:absolute;left:6px;top:14px;width:6px;height:6px;
    border-radius:50%;background:var(--gold)}
  .card.dim ul li::before{background:var(--cognac)}

  .grid{display:grid;gap:14px}
  .grid.cols-2{grid-template-columns:repeat(2,1fr)}
  .grid.cols-3{grid-template-columns:repeat(3,1fr)}
  .grid.cols-4{grid-template-columns:repeat(4,1fr)}
  @media(max-width:780px){.grid.cols-2,.grid.cols-3,.grid.cols-4{grid-template-columns:1fr}}

  .pillar{padding:18px 18px;border:1px solid var(--line);border-radius:16px;background:rgba(0,0,0,.2);transition:all .2s}
  .pillar:hover{border-color:var(--gold);background:rgba(200,130,30,.04);transform:translateY(-1px)}
  .pillar .icn{font-size:22px;color:var(--gold2);font-family:Georgia,serif;line-height:1}
  .pillar .tt{font-family:'Cormorant Garamond',Georgia,serif;font-size:19px;color:var(--cream);margin:8px 0 4px;font-weight:600}
  .pillar .ld{font-size:13px;color:var(--muted);line-height:1.55}

  .day{border:1px solid var(--line);border-radius:14px;padding:14px 16px;background:rgba(0,0,0,.18)}
  .day.today{border-color:var(--gold);background:linear-gradient(180deg,rgba(200,130,30,.10),rgba(0,0,0,.2));
    box-shadow:0 0 0 1px rgba(200,130,30,.25)}
  .day .hdr{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:8px}
  .day .dn{font-family:'Cormorant Garamond',Georgia,serif;font-size:20px;color:var(--cream);font-weight:600}
  .day .md{font-size:11px;color:var(--gold);letter-spacing:1.5px;text-transform:uppercase}
  .day .now-tag{font-size:10px;background:var(--gold);color:#000;padding:2px 8px;border-radius:99px;font-weight:700;letter-spacing:1px}

  .block{display:grid;grid-template-columns:90px 1fr;gap:14px;padding:12px 14px;border:1px solid var(--line);
    border-radius:12px;background:rgba(0,0,0,.18);margin-bottom:8px}
  .block.now{border-color:var(--gold);background:linear-gradient(90deg,rgba(200,130,30,.12),rgba(0,0,0,.2))}
  .block .tm{font-family:'Cormorant Garamond',Georgia,serif;color:var(--gold2);font-size:16px;font-weight:600;letter-spacing:.5px}
  .block .lb{font-size:14px;color:var(--cream);font-weight:600;margin-bottom:4px}
  .block .it{font-size:13px;color:var(--muted);line-height:1.55}
  .block .it div{padding:2px 0}

  .check-list{display:flex;flex-direction:column;gap:2px}
  .check{display:flex;align-items:flex-start;gap:12px;padding:11px 14px;border:1px solid var(--line);
    border-radius:11px;background:rgba(0,0,0,.18);cursor:pointer;transition:all .15s;user-select:none}
  .check:hover{border-color:var(--line2);background:rgba(255,255,255,.02)}
  .check input{appearance:none;width:18px;height:18px;border:1.5px solid var(--line2);border-radius:5px;
    background:transparent;flex-shrink:0;cursor:pointer;margin-top:1px;position:relative;transition:all .15s}
  .check input:checked{background:var(--gold);border-color:var(--gold)}
  .check input:checked::after{content:'';position:absolute;left:5px;top:1px;width:5px;height:10px;
    border:solid #000;border-width:0 2px 2px 0;transform:rotate(45deg)}
  .check input:checked + span{color:var(--muted);text-decoration:line-through;text-decoration-color:var(--cognac)}
  .check span{font-size:14px;color:var(--ink);line-height:1.5}
  .check-head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px;gap:14px}
  .check-head .meter{font-family:'Cormorant Garamond',Georgia,serif;font-size:22px;color:var(--gold2);font-weight:600}
  .bar{height:4px;background:var(--line);border-radius:99px;margin:6px 0 14px;overflow:hidden}
  .bar > div{height:100%;background:linear-gradient(90deg,var(--cognac),var(--gold));border-radius:99px;
    width:0;transition:width .3s ease}

  .tag-row{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
  .tag{display:inline-block;font-size:11.5px;padding:4px 10px;border-radius:99px;
    border:1px solid var(--line2);color:var(--cream);background:rgba(0,0,0,.25);letter-spacing:.3px}
  .tag.gold{border-color:var(--gold);color:var(--gold2)}
  .tag.warn{border-color:rgba(160,44,44,.5);color:#e08585}

  .tree{font-family:'JetBrains Mono','SF Mono',Menlo,monospace;font-size:13px;color:var(--cream);
    background:rgba(0,0,0,.35);border:1px solid var(--line);border-radius:12px;padding:18px 20px;
    line-height:1.7;white-space:pre;overflow-x:auto}
  .db-row{display:grid;grid-template-columns:1fr 2fr;gap:14px;padding:10px 0;border-bottom:1px solid var(--line)}
  .db-row:last-child{border-bottom:none}
  .db-row b{color:var(--gold2);font-family:'Cormorant Garamond',Georgia,serif;font-size:16px;font-weight:600}
  .db-row span{color:var(--muted);font-size:13px;font-family:'JetBrains Mono','SF Mono',Menlo,monospace}

  .quote{margin:24px 0;padding:18px 22px;border-left:2px solid var(--gold);
    font-family:'Cormorant Garamond',Georgia,serif;font-style:italic;font-size:19px;color:var(--cream);line-height:1.55}

  .twin{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  @media(max-width:780px){.twin{grid-template-columns:1fr}}
  .col h4{margin:0 0 8px;font-family:'Cormorant Garamond',Georgia,serif;font-size:18px;color:var(--cream);font-weight:600}
  .col h4.danger{color:#e08585}
  .col h4.ok{color:#9bd194}

  .footnote{margin-top:30px;padding-top:18px;border-top:1px solid var(--line);color:var(--muted);font-size:12px;text-align:center;line-height:1.6}

  /* Heute · geführte Tages-Timeline */
  .now-hero{background:linear-gradient(180deg,rgba(200,130,30,.13),rgba(0,0,0,.22));border:1px solid var(--gold);
    border-radius:18px;padding:20px 22px;margin-bottom:14px}
  .now-eyebrow{font-size:11px;letter-spacing:2px;color:var(--gold);text-transform:uppercase;font-weight:700}
  .now-line{font-size:22px;color:var(--cream);margin:10px 0 14px;font-family:'Cormorant Garamond',Georgia,serif;line-height:1.25}
  .now-line b{color:var(--gold2);font-weight:600}
  .now-progress{font-size:12px;color:var(--muted);margin-top:10px;display:flex;justify-content:space-between}
  .streak{margin-top:10px;font-size:13px;color:var(--gold2);font-weight:700;letter-spacing:.3px}
  .weekend-status{margin-top:8px;font-size:12.5px;font-weight:600;letter-spacing:.2px;padding-top:8px;border-top:1px solid rgba(200,130,30,.25)}
  .weekend-status.custody{color:var(--gold2)}
  .weekend-status.free{color:var(--cream)}
  .day-plan{display:none}
  .day-plan.active{display:block}
  .ics-btn{display:block;width:100%;background:linear-gradient(180deg,var(--gold2),var(--gold));color:#1a120c;
    border:none;border-radius:14px;padding:15px;font-size:15px;font-weight:700;cursor:pointer;letter-spacing:.3px;font-family:inherit}
  .ics-btn:active{transform:scale(.99)}
  .ics-hint{font-size:12px;color:var(--muted);text-align:center;margin:10px 4px 20px;line-height:1.55}
  .tl{border:1px solid var(--line);border-radius:14px;margin-bottom:8px;background:rgba(0,0,0,.18);overflow:hidden;transition:all .2s}
  .tl-head{display:flex;align-items:center;gap:12px;width:100%;background:transparent;border:none;cursor:pointer;
    padding:15px 16px;text-align:left;font-family:inherit}
  .tl-time{font-family:'Cormorant Garamond',Georgia,serif;font-size:18px;color:var(--gold2);font-weight:600;flex:0 0 56px}
  .tl-label{font-size:15px;color:var(--cream);font-weight:600;flex:1}
  .tl-state{flex:0 0 auto;font-size:11px}
  .tl-body{display:none;padding:2px 16px 14px}
  .tl.open .tl-body{display:block}
  .tl.now{border-color:var(--gold);box-shadow:0 0 0 1px rgba(200,130,30,.3);background:linear-gradient(180deg,rgba(200,130,30,.10),rgba(0,0,0,.2))}
  .tl.now .tl-label{color:var(--gold2)}
  .tl.past{opacity:.5}
  .tl.done{opacity:.6}
  .tl.done .tl-state::after{content:'\\2713 erledigt';color:var(--green)}
  .tl.now .tl-state::after{content:'JETZT';color:var(--gold);font-weight:700;letter-spacing:1px}
  .tl.later:not(.done) .tl-state::after{content:'sp\\00e4ter';color:var(--muted)}
  .step{display:flex;align-items:flex-start;gap:12px;padding:9px 0;cursor:pointer;user-select:none;border-bottom:1px solid rgba(58,42,29,.4)}
  .step:last-child{border-bottom:none}
  .step input{appearance:none;width:19px;height:19px;border:1.5px solid var(--line2);border-radius:5px;background:transparent;
    flex-shrink:0;cursor:pointer;margin-top:1px;position:relative}
  .step input:checked{background:var(--gold);border-color:var(--gold)}
  .step input:checked::after{content:'';position:absolute;left:5px;top:1px;width:5px;height:10px;
    border:solid #000;border-width:0 2px 2px 0;transform:rotate(45deg)}
  .step input:checked + span{color:var(--muted);text-decoration:line-through;text-decoration-color:var(--cognac)}
  .step span{font-size:14px;color:var(--ink);line-height:1.5}
</style>
"""


_ALEX_JS = r"""
<script>
(function(){
  var tabs = document.querySelectorAll('[data-tab]');
  var panels = document.querySelectorAll('[data-panel]');
  function show(name){
    tabs.forEach(function(t){ t.classList.toggle('active', t.dataset.tab === name); });
    panels.forEach(function(p){ p.classList.toggle('active', p.dataset.panel === name); });
    history.replaceState(null, '', '#' + name);
    var active = document.querySelector('[data-tab="' + name + '"]');
    if (active && active.scrollIntoView) active.scrollIntoView({block:'nearest', inline:'center', behavior:'smooth'});
    window.scrollTo({top:0});
  }
  tabs.forEach(function(t){ t.addEventListener('click', function(){ show(t.dataset.tab); }); });
  var hash = location.hash.slice(1);
  if (hash && document.querySelector('[data-tab="' + hash + '"]')) show(hash);

  function todayKey(){ return new Date().toISOString().slice(0,10); }
  function monthKey(){ return new Date().toISOString().slice(0,7); }
  function bind(id, scope){
    var root = document.getElementById(id);
    if (!root) return;
    var boxes = root.querySelectorAll('input[type=checkbox]');
    var storageKey = id + ':' + (scope === 'day' ? todayKey() : scope === 'month' ? monthKey() : 'persistent');
    var saved;
    try { saved = JSON.parse(localStorage.getItem(storageKey) || '[]'); } catch(e){ saved = []; }
    var meter = root.querySelector('.meter');
    var bar = root.querySelector('.bar > div');
    function refresh(){
      var done = 0;
      boxes.forEach(function(b){ if (b.checked) done++; });
      if (meter) meter.textContent = done + ' / ' + boxes.length;
      if (bar) bar.style.width = (boxes.length ? (done / boxes.length * 100) : 0) + '%';
    }
    boxes.forEach(function(b, i){
      b.checked = saved.indexOf(i) !== -1;
      b.addEventListener('change', function(){
        var arr = [];
        boxes.forEach(function(x, j){ if (x.checked) arr.push(j); });
        localStorage.setItem(storageKey, JSON.stringify(arr));
        refresh();
      });
    });
    refresh();
  }
  bind('daily-checklist', 'day');
  bind('monthly-checklist', 'month');
  bind('roadmap-checklist', 'persistent');

  var dow = (new Date().getDay() + 6) % 7;
  var dayEl = document.querySelector('.day[data-weekday="' + dow + '"]');
  if (dayEl){
    dayEl.classList.add('today');
    var tag = dayEl.querySelector('.now-tag');
    if (tag) tag.style.display = 'inline-block';
  }
  var hr = new Date().getHours();
  var blocks = document.querySelectorAll('.block[data-hour]');
  var current = null;
  blocks.forEach(function(b){
    var h = parseInt(b.dataset.hour, 10);
    if (h >= 0 && h <= hr) current = b;
  });
  if (current) current.classList.add('now');
})();
</script>

<script>
// Heute · geführte, tagesspezifische Timeline
(function(){
  var now = new Date();
  var hr = now.getHours();
  var dow = (now.getDay() + 6) % 7; // Mo=0 … So=6
  var wd = ['Sonntag','Montag','Dienstag','Mittwoch','Donnerstag','Freitag','Samstag'];
  var mo = ['Januar','Februar','März','April','Mai','Juni','Juli','August','September','Oktober','November','Dezember'];
  var modes = window.ALEX_MODES || [];
  function setText(id, t){ var e = document.getElementById(id); if (e) e.textContent = t; }
  function iso(d){ return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0'); }

  setText('today-date', wd[now.getDay()] + ', ' + now.getDate() + '. ' + mo[now.getMonth()]);
  setText('today-mode', modes[dow] || '');
  setText('now-clock', String(hr).padStart(2,'0') + ':' + String(now.getMinutes()).padStart(2,'0') + ' Uhr');

  // Umgangs-Rhythmus: alle zwei Wochen Fr 13:00 – Sa 18:30, ab Anker-Freitag
  var cfg = window.ALEX_CUSTODY || {anchor:'2026-05-22'};
  var ap = cfg.anchor.split('-');
  var ANCHOR = new Date(+ap[0], +ap[1]-1, +ap[2]); // Anker-Freitag, lokale Mitternacht
  function strip(d){ return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }
  function daysBetween(a, b){ return Math.round((a - b) / 86400000); }
  function isCustodyFriday(friday){ return ((daysBetween(friday, ANCHOR) % 14) + 14) % 14 === 0; }
  function upcomingFriday(d){ var x = strip(d); var w = (x.getDay()+6)%7; x.setDate(x.getDate() + ((4 - w) + 7) % 7); return x; }

  var isCustody = false, currentWeekend = false, theFriday;
  if (dow === 4){ theFriday = strip(now); isCustody = isCustodyFriday(theFriday); currentWeekend = true; }
  else if (dow === 5){ theFriday = strip(now); theFriday.setDate(theFriday.getDate() - 1); isCustody = isCustodyFriday(theFriday); currentWeekend = true; }
  else { theFriday = upcomingFriday(now); isCustody = isCustodyFriday(theFriday); currentWeekend = false; }

  var ws = document.getElementById('weekend-status');
  if (ws){
    if (isCustody){
      ws.textContent = currentWeekend ? '\\uD83D\\uDD3A Umgangswochenende l\\u00e4uft \\u2014 Tochter bis Sa 18:30'
                                       : '\\uD83D\\uDD3A Dieses Wochenende: Umgang \\u2014 Tochter Fr 13:00 \\u2192 Sa 18:30';
      ws.className = 'weekend-status custody';
    } else {
      ws.textContent = currentWeekend ? '\\u25B3 Freies Wochenende \\u2014 Freiheit'
                                       : '\\u25B3 Dieses Wochenende: frei \\u2014 Van, Berge, Musik';
      ws.className = 'weekend-status free';
    }
  }

  // Heutigen Wochentag-Plan zeigen (Umgangs-Variante an Umgangswochenenden)
  function pickPlan(){
    var sel = '.day-plan[data-weekday="' + dow + '"]';
    return document.querySelector(sel + '[data-custody="' + (isCustody ? '1' : '0') + '"]')
        || document.querySelector(sel + '[data-custody="any"]')
        || document.querySelector(sel);
  }
  var plan = pickPlan();
  if (plan) plan.classList.add('active');
  var tl = plan ? plan.querySelectorAll('.tl') : [];
  var allBoxes = plan ? plan.querySelectorAll('input[type=checkbox]') : [];
  if (!tl.length) return;

  var curIdx = -1;
  tl.forEach(function(b){ if (parseInt(b.dataset.hour, 10) <= hr) curIdx = parseInt(b.dataset.idx, 10); });

  var key = 'alex-heute:' + iso(now);
  var saved; try { saved = JSON.parse(localStorage.getItem(key) || '{}'); } catch(e){ saved = {}; }

  function blockDone(b){
    var bx = b.querySelectorAll('input[type=checkbox]');
    if (!bx.length) return false;
    for (var i = 0; i < bx.length; i++){ if (!bx[i].checked) return false; }
    return true;
  }
  function computeStreak(){
    var s; try { s = JSON.parse(localStorage.getItem('alex-streak') || '[]'); } catch(e){ s = []; }
    var set = {}; s.forEach(function(d){ set[d] = 1; });
    var streak = 0, day = new Date();
    if (!set[iso(day)]) day.setDate(day.getDate() - 1); // gestern zählt, falls heute noch offen
    while (set[iso(day)]) { streak++; day.setDate(day.getDate() - 1); }
    return streak;
  }
  function refresh(){
    var done = 0; allBoxes.forEach(function(x){ if (x.checked) done++; });
    setText('day-meter', done + ' / ' + allBoxes.length + ' Schritte');
    var bar = document.getElementById('day-bar');
    if (bar) bar.style.width = (allBoxes.length ? done / allBoxes.length * 100 : 0) + '%';
    tl.forEach(function(b){ b.classList.toggle('done', blockDone(b)); });

    if (allBoxes.length && done === allBoxes.length){
      var s; try { s = JSON.parse(localStorage.getItem('alex-streak') || '[]'); } catch(e){ s = []; }
      if (s.indexOf(iso(now)) < 0){ s.push(iso(now)); localStorage.setItem('alex-streak', JSON.stringify(s)); }
    }
    var st = computeStreak();
    setText('streak', st > 0 ? ('\\uD83D\\uDD25 ' + st + (st === 1 ? ' Tag' : ' Tage') + ' Serie') : 'Serie startet heute');
  }

  tl.forEach(function(b){
    var idx = parseInt(b.dataset.idx, 10);
    b.classList.remove('past','now','later','open');
    if (idx < curIdx) b.classList.add('past');
    else if (idx === curIdx){ b.classList.add('now','open'); }
    else b.classList.add('later');
    b.querySelectorAll('input[type=checkbox]').forEach(function(input, j){
      var k = idx + ':' + j;
      input.checked = !!saved[k];
      input.addEventListener('change', function(){
        saved[k] = input.checked;
        localStorage.setItem(key, JSON.stringify(saved));
        refresh();
      });
    });
    b.querySelector('.tl-head').addEventListener('click', function(){ b.classList.toggle('open'); });
  });

  var cur = plan.querySelector('.tl.now .tl-label');
  setText('now-label', cur ? cur.textContent : 'Tag ausklingen lassen');
  refresh();

  var btn = document.getElementById('ics-btn');
  if (btn){
    btn.addEventListener('click', function(){
      var ics = window.ALEX_ICS || '';
      var blob = new Blob([ics], {type:'text/calendar'});
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'alex-blue-electric-life.ics';
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
    });
  }
})();
</script>
"""


def _alex_page(body_html: str) -> HTMLResponse:
    html = f"""<!doctype html>
<html lang="de"><head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Alex · Blue Electric Life</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,600;1,400;1,600&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  {_ALEX_CSS}
</head><body>
  <div class="wrap">
    {body_html}
  </div>
  {_ALEX_JS}
</body></html>"""
    return HTMLResponse(html)


def _alex_pillars_html() -> str:
    cards = ""
    for p in ALEX["pillars"]:
        cards += f"""
        <div class="pillar">
          <div class="icn">{p['icon']}</div>
          <div class="tt">{p['title']}</div>
          <div class="ld">{p['lede']}</div>
        </div>"""
    return f'<div class="grid cols-4">{cards}</div>'


def _alex_week_html() -> str:
    days = ""
    for d in ALEX["week"]:
        items = "".join(f"<li>{x}</li>" for x in d["items"])
        days += f"""
        <div class="day" data-weekday="{d['weekday']}">
          <div class="hdr">
            <div>
              <div class="dn">{d['day']} <span class="now-tag" style="display:none;margin-left:8px">heute</span></div>
              <div class="md">{d['mode']}</div>
            </div>
          </div>
          <ul>{items}</ul>
        </div>"""
    return f'<div class="grid cols-2">{days}</div>'


def _alex_weekend_html() -> str:
    a = ALEX["weekend_a_tochter"]
    b = ALEX["weekend_b_freiheit"]
    a_rit = "".join(f"<li>{x}</li>" for x in a["rituale"])
    a_nicht = "".join(f"<li>{x}</li>" for x in a["nicht"])
    b_opt = "".join(f"<li>{x}</li>" for x in b["optionen"])
    return f"""
    <div class="twin">
      <div class="card">
        <div class="meta">Wochenende A</div>
        <h3>{a['title']}</h3>
        <div class="col"><h4 class="ok">Rituale</h4><ul>{a_rit}</ul></div>
        <div class="col" style="margin-top:14px"><h4 class="danger">Nicht</h4><ul>{a_nicht}</ul></div>
        <p style="margin-top:14px;color:var(--cream);font-style:italic">{a['ziel']}</p>
      </div>
      <div class="card">
        <div class="meta">Wochenende B</div>
        <h3>{b['title']}</h3>
        <ul>{b_opt}</ul>
        <p style="margin-top:14px;color:var(--cream);font-style:italic">{b['leitsatz']}</p>
      </div>
    </div>"""


def _alex_day_blocks_html() -> str:
    blocks = ""
    for b in ALEX["day_blocks"]:
        items = "".join(f"<div>{x}</div>" for x in b["items"])
        blocks += f"""
        <div class="block" data-hour="{b['h']}">
          <div class="tm">{b['t1']}<br><span style="font-size:12px;color:var(--muted)">bis {b['t2']}</span></div>
          <div>
            <div class="lb">{b['label']}</div>
            <div class="it">{items}</div>
          </div>
        </div>"""
    return blocks


def _hm(t):
    h, m = (int(x) for x in t.split(":"))
    return h, m


def _blocks_from_day_blocks():
    out = []
    for b in ALEX["day_blocks"]:
        h, m = _hm(b["t1"])
        out.append({"t1": b["t1"], "h": h, "m": m, "label": b["label"], "items": b["items"]})
    return out


# Tagesspezifische Pläne (0=Montag … 6=Sonntag). Mo/Di = der tiefe Soul-Deep-Work-Tag.
def _alex_day_plans():
    deep = _blocks_from_day_blocks()
    plans = {0: deep, 1: deep}

    plans[2] = [  # Mittwoch — Reset Day
        {"t1": "06:30", "label": "Morgen-Anker (sanft)", "items": ["Wasser, Fenster, Licht, kein Handy.", "10 Min Mobility, Atmung.", "Espresso, Früchte."]},
        {"t1": "08:30", "label": "Admin & Buchhaltung", "items": ["Rechnungen, Belege, Überblick Geld.", "Posteingang leeren — Inbox Zero.", "Offene Entscheidungen treffen, nicht aufschieben."]},
        {"t1": "10:30", "label": "Tochter- & Wien-Planung", "items": ["Tochter-Wochenende konkret planen.", "Wien Do/Fr vorbereiten: Hotel, Termine, Zug.", "Ein kleines Abenteuer für sie aussuchen."]},
        {"t1": "12:00", "label": "Wohnungs-Reset (ein Bereich)", "items": ["Heute genau EIN Bereich (Bad / Küche / Schlafzimmer / Musikecke / Schrank).", "Ein Karton raus. Eine Fläche frei. Atmungsfähig statt perfekt.", "Bei Bedarf Reinigungskraft / Entrümpler einplanen."]},
        {"t1": "15:00", "label": "Wäsche & Körperpflege", "items": ["Wäsche, Bügeln, Kleidung für Wien bereitlegen.", "Haare, Bart, Nägel, Haut. Gepflegt = präsent.", "Schuhe putzen."]},
        {"t1": "17:00", "label": "Training / Spaziergang", "items": ["Langer Spaziergang oder Krafttraining.", "Sauna, wenn möglich.", "Nervensystem runterfahren."]},
        {"t1": "18:30", "label": "Tasche packen für Wien", "items": ["Minimalistisch: 3 Hemden, 2 Hosen, Mantel/Lederjacke.", "Notizbuch, Buch, Kopfhörer, Ladegeräte.", "Alles bereit an der Tür."]},
        {"t1": "20:00", "label": "Piano / Gitarre", "items": ["30–60 Min Musik. Ohne Ziel, nur Klang.", "Eine Voice Note aufnehmen."]},
        {"t1": "22:30", "label": "Schlafvorbereitung", "items": ["Dusche, Duft, Lesen. Kein Bildschirm im Bett."]},
    ]

    plans[3] = [  # Donnerstag — Wien Anreise
        {"t1": "06:30", "label": "Morgen-Anker", "items": ["Wasser, Atmung, kurze Mobility.", "Espresso, Protein."]},
        {"t1": "07:30", "label": "Anreise Wien (Bahn)", "items": ["Schwarzer Mantel / Lederjacke, Notizbuch, Buch.", "Im Zug: schreiben, lesen, Musik — kein Doomscroll.", "Eine PTGO- oder Song-Idee festhalten."]},
        {"t1": "11:00", "label": "Ankommen & einchecken", "items": ["Hotel/Unterkunft, das dich kennt.", "Kurzer Spaziergang durch eine schöne Straße.", "Espresso im Stammcafé."]},
        {"t1": "14:00", "label": "Arbeit in Wien", "items": ["Präsent, fokussiert, ruhig.", "Sessions / Calls / Deep Work."]},
        {"t1": "18:00", "label": "Duschen & gutes Essen", "items": ["Duschen, frisch machen.", "Echte Mahlzeit, bewusst, nicht to-go."]},
        {"t1": "20:30", "label": "Kaffeehaus / Jazz / Schreiben", "items": ["Kaffeehaus oder Jazzbar.", "Notizen: Architektur, Stil, Texte, Ideen.", "Keine billige Ablenkung, kein Dating-Modus."]},
        {"t1": "22:30", "label": "Schlafvorbereitung", "items": ["Warmes Licht, Lesen, früh ins Bett."]},
    ]

    plans[4] = [  # Freitag — Wien Deep Presence
        {"t1": "07:00", "label": "Morgen-Anker im Hotel", "items": ["Wasser, Atmung, Mobility.", "Push-ups im Zimmer.", "Espresso, kurzer Spaziergang."]},
        {"t1": "09:00", "label": "Arbeit in Wien", "items": ["Tiefste Präsenz der Woche.", "Sessions / Strategie."]},
        {"t1": "13:00", "label": "Clean Lunch", "items": ["Leicht, hochwertig, ohne Bildschirm."]},
        {"t1": "14:00", "label": "Arbeit / Sessions", "items": ["Letzter Arbeitsblock.", "Loose Ends schließen."]},
        {"t1": "17:00", "label": "Sauna oder Gym", "items": ["Körper regulieren.", "Hitze, Kälte, Atem."]},
        {"t1": "19:00", "label": "Hotel-Energy", "items": ["Warme Lampen, Hemd halb offen, Musik über Lautsprecher.", "Die Stadt leuchtet draußen — du lebst bewusst drin."]},
        {"t1": "21:00", "label": "Wien-Notizen sichern", "items": ["Beste Ideen der Woche für Musik & PTGO festhalten.", "Eine Sache, die du mitnimmst."]},
        {"t1": "22:30", "label": "Schlaf oder Abendzug", "items": ["Übernachten oder ruhig heimfahren."]},
    ]

    plans[5] = [  # Samstag — Wochenende A (Tochter) / B (Freiheit)
        {"t1": "08:00", "label": "Langsames Frühstück", "items": ["Kein Eile, kein Handy am Tisch.", "A: mit Tochter · B: allein, still, Espresso draußen."]},
        {"t1": "10:00", "label": "Natur / Bewegung", "items": ["A: See, Wald, Spielplatz mit Tochter.", "B: Van, Berge, Paragliding, lange Bewegung."]},
        {"t1": "13:00", "label": "Mittag", "items": ["A: zusammen kochen, kleine Aufgabe für sie.", "B: gutes Essen unterwegs."]},
        {"t1": "14:30", "label": "Abenteuer", "items": ["A: Museum, Boot, kleines Konzert, See.", "B: Tour, Flug, Gardasee/Dolomiten, Sauna."]},
        {"t1": "18:00", "label": "Ruhiger Abend", "items": ["A: Abendessen, vorlesen, ruhige Vaterpräsenz.", "B: Sonnenuntergang, Gitarre, Stille."]},
        {"t1": "21:00", "label": "Ausklang", "items": ["Lesen oder Gitarre.", "Dankbar, nicht müde."]},
    ]

    plans[6] = [  # Sonntag — Reflexion & Vorbereitung
        {"t1": "08:30", "label": "Frühstück, langsam", "items": ["Ruhig ankommen, kein Programm."]},
        {"t1": "10:00", "label": "Spaziergang", "items": ["Natur, frische Luft, ohne Kopfhörer einmal."]},
        {"t1": "11:30", "label": "Wochenrückblick", "items": ["Würde: Wo war ich ich selbst?", "Wachstum: Wo bin ich gewachsen?", "Korrektur: Was ändere ich nächste Woche?"]},
        {"t1": "13:00", "label": "Mittag", "items": ["Clean, ruhig."]},
        {"t1": "14:00", "label": "Woche vorbereiten", "items": ["Wäsche fertig, Tasche, Mo-Vormittag klar.", "Termine & Deep-Work-Ziele festlegen."]},
        {"t1": "17:00", "label": "Natur oder Sauna", "items": ["Nervensystem auftanken."]},
        {"t1": "20:00", "label": "Piano / Lesen", "items": ["Ruhiger Abend, warmes Licht."]},
        {"t1": "22:00", "label": "Früh ins Bett", "items": ["Stark in die Woche starten."]},
    ]

    # Umgangs-Wochenende — Tochter (alle zwei Wochen)
    tochter_fri = [  # Freitag, Abholung 13:00
        {"t1": "07:00", "label": "Morgen-Anker", "items": ["Wasser, Fenster auf, Soul leise. Heute kommt sie.", "Mobility, Atmung, kurze Kraft.", "Espresso — und ein Lächeln im Spiegel."]},
        {"t1": "08:00", "label": "Deep Work — fokussiert", "items": ["Alles Wichtige VOR 12:00 erledigen.", "Phone weg, ein klares Ziel.", "Keinen Termin in den Nachmittag legen."]},
        {"t1": "11:30", "label": "Wohnung tochterklar machen", "items": ["Ihre Ecke, ihre Decke, ihre Bücher bereit.", "Snacks da, Kühlschrank gefüllt.", "Ordnung = Ruhe für sie."]},
        {"t1": "12:30", "label": "Frisch machen & losfahren", "items": ["Duschen, gutes Hemd, Duft.", "Weg klar, Playlist bereit.", "Kopf leeren — nur sie."]},
        {"t1": "13:00", "label": "Sie abholen · Übergabe", "items": ["Pünktlich, ruhig, warm. Sonnenbrille hoch, Herz offen.", "Freundlich an der Tür — kein altes Drama.", "Die erste Minute gehört nur ihr."]},
        {"t1": "14:00", "label": "Ankommen", "items": ["Zuhause ankommen, Snack, durchatmen.", "Kein Programmdruck — sie gibt das Tempo vor."]},
        {"t1": "15:30", "label": "Erstes kleines Abenteuer", "items": ["Spielplatz, See, Eis, kurze Runde.", "Draußen, Bewegung, Lachen."]},
        {"t1": "18:00", "label": "Zusammen kochen", "items": ["Sie hat eine kleine Aufgabe.", "Einfaches, gutes Essen.", "Küche, Musik, Wärme."]},
        {"t1": "19:30", "label": "Ruhiger Abend", "items": ["Vorlesen, leise Musik, kuscheln.", "Bildschirm aus.", "Sichere Vaterenergie."]},
        {"t1": "21:00", "label": "Sie schläft — du", "items": ["Gitarre ganz leise.", "Drei Zeilen Tagebuch über heute.", "Dankbar. Nicht müde."]},
    ]
    tochter_sat = [  # Samstag, Übergabe zurück 18:30
        {"t1": "08:00", "label": "Langsames Frühstück", "items": ["Kein Wecker, kein Handy am Tisch.", "Sie wählt die Musik."]},
        {"t1": "09:30", "label": "Natur", "items": ["See, Wald oder Berg.", "Steine werfen, Stöcke sammeln, frei sein."]},
        {"t1": "12:00", "label": "Mittag zusammen", "items": ["Wieder zusammen kochen oder gutes Lokal.", "Ruhig, ohne Eile."]},
        {"t1": "13:30", "label": "Das Hauptabenteuer", "items": ["Tegernsee, Boot, Museum, kleines Konzert.", "Ein Moment, an den sie sich erinnert."]},
        {"t1": "16:30", "label": "Musik & Ruhe", "items": ["Ein Lied zusammen — hören oder erfinden.", "Malen, bauen, einfach da sein."]},
        {"t1": "17:45", "label": "Sanft zusammenpacken", "items": ["Sachen einsammeln, kein Stress.", "Ihr sagen, dass es schön war.", "Übergang ruhig einleiten."]},
        {"t1": "18:30", "label": "Übergabe zurück", "items": ["Pünktlich, warm, klarer liebevoller Abschied.", "Kein Drama, keine Schwere.", "'Bis in zwei Wochen' — fest und freundlich."]},
        {"t1": "19:30", "label": "Übergang für dich", "items": ["Spaziergang oder Dusche.", "Die Stille zulassen, nicht füllen.", "Kurz fühlen, dann loslassen."]},
        {"t1": "20:30", "label": "Abend für dich", "items": ["Gitarre, Schreiben, oder Freund:innen.", "Das Wochenende würdigen.", "Du bist frei — aber du bleibst."]},
    ]

    return [
        {"wd": 0, "custody": "any", "blocks": plans[0]},
        {"wd": 1, "custody": "any", "blocks": plans[1]},
        {"wd": 2, "custody": "any", "blocks": plans[2]},
        {"wd": 3, "custody": "any", "blocks": plans[3]},
        {"wd": 4, "custody": "1",   "blocks": tochter_fri},
        {"wd": 4, "custody": "0",   "blocks": plans[4]},
        {"wd": 5, "custody": "1",   "blocks": tochter_sat},
        {"wd": 5, "custody": "0",   "blocks": plans[5]},
        {"wd": 6, "custody": "any", "blocks": plans[6]},
    ]


def _alex_heute_panel() -> str:
    all_plans_html = ""
    for plan in _alex_day_plans():
        rows = ""
        for i, b in enumerate(plan["blocks"]):
            h, _m = _hm(b["t1"])
            steps = "".join(
                f'<label class="step"><input type="checkbox"><span>{x}</span></label>'
                for x in b["items"]
            )
            rows += f"""
            <div class="tl" data-hour="{h}" data-idx="{i}">
              <button class="tl-head" type="button">
                <span class="tl-time">{b['t1']}</span>
                <span class="tl-label">{b['label']}</span>
                <span class="tl-state"></span>
              </button>
              <div class="tl-body"><div>{steps}</div></div>
            </div>"""
        all_plans_html += f'<div class="day-plan" data-weekday="{plan["wd"]}" data-custody="{plan["custody"]}">{rows}</div>'

    modes = [d["mode"] for d in ALEX["week"]]
    data_js = (
        "<script>window.ALEX_MODES="
        + json.dumps(modes, ensure_ascii=False)
        + ";window.ALEX_CUSTODY="
        + json.dumps(ALEX["custody"], ensure_ascii=False)
        + ";window.ALEX_ICS="
        + json.dumps(_alex_build_ics(), ensure_ascii=False)
        + ";</script>"
    )
    return f"""
    <section data-panel="heute" class="panel active">
      <div class="now-hero">
        <div class="now-eyebrow"><span id="today-date">Heute</span> · <span id="today-mode"></span></div>
        <div class="now-line">Jetzt dran: <b id="now-label">—</b></div>
        <div class="bar"><div id="day-bar"></div></div>
        <div class="now-progress"><span id="day-meter">0 / 0 Schritte</span><span id="now-clock"></span></div>
        <div class="streak" id="streak">Serie startet heute</div>
        <div class="weekend-status" id="weekend-status"></div>
      </div>
      <button id="ics-btn" class="ics-btn" type="button">⏰  Wecker &amp; Kalender laden (.ics)</button>
      <p class="ics-hint">Einmal importieren — dein Handy erinnert dich täglich an jeden Schritt, plus Wochen-Anker (Mittwoch Reset, Wien-Anreise am Vorabend, Tochter-Wochenende ab Freitag) und den Monatscheck.</p>
      <div id="day-timeline">{all_plans_html}</div>
      {data_js}
    </section>"""


def _alex_dashboard_panel() -> str:
    pillars = _alex_pillars_html()
    return f"""
    <section data-panel="dashboard" class="panel">
      <h2 class="section">Die acht Säulen</h2>
      <p class="section-sub">Acht Achsen, auf denen dieses Leben getragen wird. Nicht nebeneinander, sondern miteinander.</p>
      {pillars}
      <div class="quote">„{ALEX['leitsatz']}“</div>
      <div class="quote" style="opacity:.85;font-size:17px">„{ALEX['leitsatz_v1']}“</div>
      <div class="card" style="margin-top:18px">
        <div class="meta">Frame-Regel</div>
        <p>{ALEX['frame_rule']}</p>
      </div>
      <div class="schluss">„{ALEX['schlusssatz']}“</div>
    </section>"""


def _alex_nerve_panel() -> str:
    n = ALEX["nervensystem"]
    braucht = "".join(f"<li>{x}</li>" for x in n["braucht"])
    nicht = "".join(f"<li>{x}</li>" for x in n["nicht"])
    tools = " ".join(f'<span class="tag gold">{x}</span>' for x in n["tools"])
    return f"""
    <section data-panel="nervensystem" class="panel">
      <h2 class="section">Nervensystem · Ruhe als Fundament</h2>
      <p class="section-sub">{n['intro']}</p>
      <div class="twin">
        <div class="card"><div class="meta">Was es braucht</div><h3>Nahrung fürs System</h3><ul>{braucht}</ul></div>
        <div class="card dim"><div class="meta">Was es zerstört</div><h3>Worin es nicht leben darf</h3><ul>{nicht}</ul></div>
      </div>
      <div class="card"><div class="meta">Regulations-Tools</div><h3>Wenn es eng wird — greif hierzu</h3><div class="tag-row">{tools}</div></div>
    </section>"""


def _alex_week_panel() -> str:
    week = _alex_week_html()
    weekends = _alex_weekend_html()
    return f"""
    <section data-panel="wochenplan" class="panel">
      <h2 class="section">Wochenstruktur</h2>
      <p class="section-sub">Mo–So mit klarem Modus pro Tag. Der aktuelle Tag wird automatisch markiert.</p>
      {week}
      <h2 class="section" style="margin-top:30px">Die zwei Wochenenden</h2>
      <p class="section-sub">A für Tochter. B für Freiheit. Klar getrennt — keine Mischmodi.</p>
      {weekends}
    </section>"""


def _alex_day_panel() -> str:
    blocks = _alex_day_blocks_html()
    koerper = "".join(f"<li>{x}</li>" for x in ALEX["koerper"]["wochen"])
    ernaehrung = "".join(f"<li>{x}</li>" for x in ALEX["koerper"]["ernaehrung"])
    return f"""
    <section data-panel="tagesplan" class="panel">
      <h2 class="section">Tagesplan</h2>
      <p class="section-sub">Acht Blöcke vom Morgen-Anker bis zur Schlafvorbereitung. Der aktuelle Block ist hervorgehoben.</p>
      {blocks}
      <div class="twin" style="margin-top:22px">
        <div class="card">
          <div class="meta">Körper · Wochenrhythmus</div>
          <h3>{ALEX['koerper']['kern']}</h3>
          <ul>{koerper}</ul>
        </div>
        <div class="card">
          <div class="meta">Ernährung</div>
          <h3>Clean. Sinnlich. Einfach.</h3>
          <ul>{ernaehrung}</ul>
        </div>
      </div>
    </section>"""


def _alex_wien_panel() -> str:
    w = ALEX["wien"]
    anreise = "".join(f"<li>{x}</li>" for x in w["anreise"])
    unterkunft = "".join(f"<li>{x}</li>" for x in w["unterkunft"])
    rituale = "".join(f"<li>{x}</li>" for x in w["rituale"])
    insp = " ".join(f'<span class="tag gold">{x}</span>' for x in w["inspiration"])
    donts = "".join(f"<li>{x}</li>" for x in w["donts"])
    return f"""
    <section data-panel="wien" class="panel">
      <h2 class="section">Wien · Creative Gentleman Work Mode</h2>
      <p class="section-sub">{w['modus']}</p>
      <div class="twin">
        <div class="card"><div class="meta">Anreise</div><h3>Schwarzer Mantel, Notizbuch</h3><ul>{anreise}</ul></div>
        <div class="card"><div class="meta">Unterkunft</div><h3>Ein Ort, der dich kennt</h3><ul>{unterkunft}</ul></div>
      </div>
      <div class="card"><div class="meta">Rituale</div><h3>Espresso, Spaziergang, Notizen, Jazz</h3><ul>{rituale}</ul></div>
      <div class="card">
        <div class="meta">Inspirationsquellen</div>
        <h3>Wien wird Material — nicht Belastung</h3>
        <div class="tag-row">{insp}</div>
      </div>
      <div class="card dim"><div class="meta">Nicht</div><h3>Was Wien zerstören würde</h3><ul>{donts}</ul></div>
    </section>"""


def _alex_muc_panel() -> str:
    m = ALEX["muenchen"]
    atmo = "".join(f"<li>{x}</li>" for x in m["atmosphaere"])
    funktion = "".join(f"<li>{x}</li>" for x in m["funktion"])
    reset = "".join(f"<li>{x}</li>" for x in m["reset_wochen"])
    hilfe = "".join(f"<li>{x}</li>" for x in m["hilfe"])
    return f"""
    <section data-panel="muenchen" class="panel">
      <h2 class="section">München-Base · die Wohnung zurückerobern</h2>
      <p class="section-sub">{m['ist_zustand']}</p>
      <div class="twin">
        <div class="card"><div class="meta">Atmosphäre</div><h3>Holz, warmes Licht, Leder, Pflanzen</h3><ul>{atmo}</ul></div>
        <div class="card"><div class="meta">Funktion</div><h3>Vier Räume in einem</h3><ul>{funktion}</ul></div>
      </div>
      <div class="card"><div class="meta">Reset · 6 Wochen</div><h3>Pro Mittwoch ein Bereich</h3><ul>{reset}</ul></div>
      <div class="card"><div class="meta">Hilfe</div><h3>Stärke ist Verantwortung — nicht Alleingang</h3><ul>{hilfe}</ul></div>
    </section>"""


def _alex_van_panel() -> str:
    v = ALEX["van"]
    vibe = " ".join(f'<span class="tag">{x}</span>' for x in v["vibe"])
    nicht = " ".join(f'<span class="tag warn">{x}</span>' for x in v["nicht"])
    touren = "".join(f"<li>{x}</li>" for x in v["touren"])
    ritual = "".join(f"<li>{x}</li>" for x in v["ritual"])
    return f"""
    <section data-panel="van" class="panel">
      <h2 class="section">Van · ein mobiles Atelier</h2>
      <p class="section-sub">Kein Fluchtfahrzeug. Ein Boutique-Hotel für einen Mann, der wieder atmen lernt.</p>
      <div class="card"><div class="meta">Vehikel</div><h3>{v['vehikel']}</h3><p>Frequenz: {v['frequenz']}</p></div>
      <div class="twin">
        <div class="card"><div class="meta">Vibe</div><h3>Holz, Licht, Leder</h3><div class="tag-row">{vibe}</div></div>
        <div class="card dim"><div class="meta">Nicht</div><h3>Was den Van billig macht</h3><div class="tag-row">{nicht}</div></div>
      </div>
      <div class="twin">
        <div class="card"><div class="meta">Touren</div><h3>Sechs Routen, ein Rhythmus</h3><ul>{touren}</ul></div>
        <div class="card"><div class="meta">Ritual im Van</div><h3>Stille, Espresso, Gitarre</h3><ul>{ritual}</ul></div>
      </div>
    </section>"""


def _alex_para_panel() -> str:
    p = ALEX["paragliding"]
    kal = ""
    for monat, txt in p["kalender"]:
        kal += f'<div class="block" data-hour="-1"><div class="tm">{monat}</div><div><div class="it">{txt}</div></div></div>'
    routine = "".join(f"<li>{x}</li>" for x in p["routine"])
    gear = " ".join(f'<span class="tag">{x}</span>' for x in p["gear"])
    return f"""
    <section data-panel="paragliding" class="panel">
      <h2 class="section">Paragliding · Meditation im Himmel</h2>
      <p class="section-sub">{p['funktion']}</p>
      {kal}
      <div class="twin" style="margin-top:14px">
        <div class="card"><div class="meta">Routine</div><h3>Sicher fliegen heißt: ruhig vorbereiten</h3><ul>{routine}</ul></div>
        <div class="card"><div class="meta">Gear</div><h3>Was immer dabei ist</h3><div class="tag-row">{gear}</div></div>
      </div>
    </section>"""


def _alex_ptgo_panel() -> str:
    p = ALEX["ptgo"]
    elemente = " ".join(f'<span class="tag gold">{x}</span>' for x in p["elemente"])
    produkte = "".join(f"<li>{x}</li>" for x in p["produkte"])
    marke = " ".join(f'<span class="tag">{x}</span>' for x in p["marke"])
    rhythmus = "".join(f"<li>{x}</li>" for x in p["rhythmus"])
    return f"""
    <section data-panel="ptgo" class="panel">
      <h2 class="section">PTGO · Premium-Kunstform</h2>
      <p class="section-sub">{p['positionierung']}</p>
      <div class="card"><div class="meta">Elemente</div><h3>Was eine Session zu Kunst macht</h3><div class="tag-row">{elemente}</div></div>
      <div class="card"><div class="meta">Produkte</div><h3>Sieben Türen ins gleiche Haus</h3><ul>{produkte}</ul></div>
      <div class="twin">
        <div class="card"><div class="meta">Marke</div><h3>Wie sie klingt</h3><div class="tag-row">{marke}</div></div>
        <div class="card"><div class="meta">Rhythmus</div><h3>Wie sie atmet</h3><ul>{rhythmus}</ul></div>
      </div>
    </section>"""


def _alex_music_panel() -> str:
    m = ALEX["musik"]
    sound = " ".join(f'<span class="tag gold">{x}</span>' for x in m["sound"])
    formate = "".join(f"<li>{x}</li>" for x in m["formate"])
    setup = "".join(f"<li>{x}</li>" for x in m["setup"])
    return f"""
    <section data-panel="musik" class="panel">
      <h2 class="section">Musik · Markenkern, nicht Hobby</h2>
      <p class="section-sub">{m['ritual']}</p>
      <div class="card"><div class="meta">Sound-DNA</div><h3>Worauf alles ruht</h3><div class="tag-row">{sound}</div></div>
      <div class="twin">
        <div class="card"><div class="meta">Formate</div><h3>Wie Musik aus dir herauskommt</h3><ul>{formate}</ul></div>
        <div class="card"><div class="meta">Setup</div><h3>Was immer bereit steht</h3><ul>{setup}</ul></div>
      </div>
    </section>"""


def _alex_style_panel() -> str:
    s = ALEX["style"]
    kleidung = "".join(f"<li>{x}</li>" for x in s["kleidung"])
    farben = " ".join(f'<span class="tag">{x}</span>' for x in s["farben"])
    duefte = " ".join(f'<span class="tag gold">{x}</span>' for x in s["duefte"])
    nicht = "".join(f"<li>{x}</li>" for x in s["nicht"])
    return f"""
    <section data-panel="stil" class="panel">
      <h2 class="section">Stil · zeitlose Rock-Soul-Männlichkeit</h2>
      <p class="section-sub">Wiedererkennbar. Niemals Hype.</p>
      <div class="twin">
        <div class="card"><div class="meta">Kleidung</div><h3>Was getragen wird</h3><ul>{kleidung}</ul></div>
        <div class="card dim"><div class="meta">Nicht</div><h3>Was nie ins Bild passt</h3><ul>{nicht}</ul></div>
      </div>
      <div class="twin">
        <div class="card"><div class="meta">Farben</div><h3>Palette</h3><div class="tag-row">{farben}</div></div>
        <div class="card"><div class="meta">Düfte</div><h3>Stimmung am Körper</h3><div class="tag-row">{duefte}</div></div>
      </div>
      <div class="quote">„{s['tattoo_regel']}“</div>
    </section>"""


def _alex_frauen_panel() -> str:
    f = ALEX["frauen"]
    regeln = "".join(f"<li>{x}</li>" for x in f["regeln"])
    stattdessen = "".join(f"<li>{x}</li>" for x in f["stattdessen"])
    pia = "".join(f"<li>{x}</li>" for x in f["pia"])
    return f"""
    <section data-panel="frauen" class="panel">
      <h2 class="section">Frauen · Verbindung statt Zentrum</h2>
      <p class="section-sub">{f['grundsatz']}</p>
      <div class="twin">
        <div class="card dim"><div class="meta">Nicht mehr</div><h3>Alte Muster, die enden</h3><ul>{regeln}</ul></div>
        <div class="card"><div class="meta">Stattdessen</div><h3>Wie Liebe jetzt klingt</h3><ul>{stattdessen}</ul></div>
      </div>
      <div class="card"><div class="meta">Pia</div><h3>Ruhe, Grenzen, Klarheit</h3><ul>{pia}</ul></div>
      <div class="quote">„{f['leitsatz']}“</div>
    </section>"""


def _alex_tochter_panel() -> str:
    t = ALEX["tochter"]
    rituale = "".join(f"<li>{x}</li>" for x in t["rituale"])
    nicht = "".join(f"<li>{x}</li>" for x in t["nicht"])
    return f"""
    <section data-panel="tochter" class="panel">
      <h2 class="section">Tochter · der heilige Anker</h2>
      <p class="section-sub">{t['grundsatz']}</p>
      <div class="twin">
        <div class="card"><div class="meta">Rituale</div><h3>Was sie erlebt</h3><ul>{rituale}</ul></div>
        <div class="card dim"><div class="meta">Nicht</div><h3>Was sie nicht erleben darf</h3><ul>{nicht}</ul></div>
      </div>
      <div class="quote">„{t['ziel']}“</div>
    </section>"""


def _alex_daily_panel() -> str:
    items = "".join(
        f'<label class="check"><input type="checkbox"><span>{q}</span></label>'
        for q in ALEX["daily_checklist"]
    )
    return f"""
    <section data-panel="taeglich" class="panel">
      <h2 class="section">Tägliche Checkliste</h2>
      <p class="section-sub">Setzt sich um Mitternacht automatisch zurück. Kein Druck — eine Einladung.</p>
      <div class="card" id="daily-checklist">
        <div class="check-head"><h3 style="margin:0">{_now_local().strftime('%A, %d. %B')}</h3><div class="meter">0 / {len(ALEX['daily_checklist'])}</div></div>
        <div class="bar"><div></div></div>
        <div class="check-list">{items}</div>
      </div>
    </section>"""


def _alex_notion_panel() -> str:
    tree = "\n".join(ALEX["notion"]["tree"])
    dbs = ""
    for name, cols in ALEX["notion"]["datenbanken"]:
        dbs += f'<div class="db-row"><b>{name}</b><span>{cols}</span></div>'
    tags = " ".join(f'<span class="tag gold">{t}</span>' for t in ALEX["notion"]["tags"])
    return f"""
    <section data-panel="notion" class="panel">
      <h2 class="section">Notion-Struktur</h2>
      <p class="section-sub">Ein Workspace, der atmet wie das Leben darin.</p>
      <div class="card"><div class="meta">Workspace-Baum</div><h3>So liegt es in Notion</h3>
        <div class="tree">{tree}</div>
      </div>
      <div class="card"><div class="meta">Datenbanken</div><h3>Sechs Tabellen, die das System tragen</h3>
        {dbs}
      </div>
      <div class="card"><div class="meta">Tags</div><h3>Wie Einträge atmen</h3><div class="tag-row">{tags}</div></div>
    </section>"""


def _alex_roadmap_panel() -> str:
    sections = ""
    total = 0
    for ph in ALEX["roadmap"]:
        items_html = ""
        for it in ph["items"]:
            items_html += f'<label class="check"><input type="checkbox"><span>{it}</span></label>'
            total += 1
        sections += f"""
        <div class="card">
          <div class="meta">{ph['phase']}</div>
          <h3>{ph['lede']}</h3>
          <div class="check-list">{items_html}</div>
        </div>"""
    return f"""
    <section data-panel="roadmap" class="panel">
      <h2 class="section">90-Tage-Roadmap</h2>
      <p class="section-sub">Drei Phasen × 30 Tage. Foundation → Build → Expand. Häkchen bleiben gespeichert.</p>
      <div id="roadmap-checklist">
        <div class="check-head" style="padding:0 4px"><h3 style="margin:0">Fortschritt</h3><div class="meter">0 / {total}</div></div>
        <div class="bar"><div></div></div>
        {sections}
      </div>
    </section>"""


def _alex_chaos_panel() -> str:
    stops = "".join(f"<li>{x}</li>" for x in ALEX["no_chaos"])
    antwort = "".join(f"<li>{x}</li>" for x in ALEX["no_chaos_antwort"])
    return f"""
    <section data-panel="nochaos" class="panel">
      <h2 class="section">No-Chaos · Notausstieg</h2>
      <p class="section-sub">Wenn eines der folgenden Signale dauerhaft brennt, gilt das Notprotokoll.</p>
      <div class="twin">
        <div class="card dim"><div class="meta">Sofort stoppen, wenn…</div><h3>Frühwarnsystem</h3><ul>{stops}</ul></div>
        <div class="card"><div class="meta">Antwort</div><h3>Was dann passiert</h3><ul>{antwort}</ul></div>
      </div>
      <div class="quote">„Ein Mann mit offenen Straßen im Herzen, Musik in den Händen, Ruhe im Nervensystem und einer Tochter, die weiß: Mein Vater ist frei — aber er bleibt.“</div>
    </section>"""


@app.get("/alex", response_class=HTMLResponse)
def alex_dashboard(request: Request):
    tabs_def = [
        ("heute",       "Heute"),
        ("dashboard",   "Dashboard"),
        ("wochenplan",  "Wochenplan"),
        ("tagesplan",   "Tagesplan"),
        ("wien",        "Wien"),
        ("muenchen",    "München-Base"),
        ("van",         "Van"),
        ("paragliding", "Paragliding"),
        ("ptgo",        "PTGO"),
        ("musik",       "Musik"),
        ("stil",        "Stil"),
        ("frauen",      "Frauen"),
        ("tochter",     "Tochter"),
        ("nervensystem", "Nervensystem"),
        ("monat",       "Monatscheck"),
        ("jahr",        "Jahresvision"),
        ("taeglich",    "Täglich"),
        ("notion",      "Notion"),
        ("roadmap",     "90-Tage"),
        ("nochaos",     "No-Chaos"),
    ]
    tabs_html = ""
    for key, label in tabs_def:
        active = " active" if key == "heute" else ""
        tabs_html += f'<button class="tab{active}" data-tab="{key}">{label}</button>'

    monthly_items = "".join(
        f'<label class="check"><input type="checkbox"><span>{q}</span></label>'
        for q in ALEX["monthly"]
    )
    monat_panel = f"""
    <section data-panel="monat" class="panel">
      <h2 class="section">Monatscheck · zehn Fragen</h2>
      <p class="section-sub">Einmal pro Monat ehrlich beantworten. Wird automatisch zum Monatswechsel zurückgesetzt.</p>
      <div class="card" id="monthly-checklist">
        <div class="check-head"><h3 style="margin:0">{_now_local().strftime('%B %Y')}</h3><div class="meter">0 / {len(ALEX['monthly'])}</div></div>
        <div class="bar"><div></div></div>
        <div class="check-list">{monthly_items}</div>
      </div>
    </section>"""

    year_items_html = ""
    for ph in ALEX["year"]:
        items_y = "".join(f"<li>{x}</li>" for x in ph["items"])
        year_items_html += f'<div class="card"><div class="meta">{ph["phase"]}</div><h3>{ph["title"]}</h3><ul>{items_y}</ul></div>'
    jahr_panel = f"""
    <section data-panel="jahr" class="panel">
      <h2 class="section">Jahresvision · fünf Phasen</h2>
      <p class="section-sub">Das Jahr in Atemzügen — nicht in Quartalsberichten.</p>
      <div class="grid cols-2">{year_items_html}</div>
    </section>"""

    body = f"""
    <div class="hero">
      <div class="eyebrow">Alex · Lebensdesign-System</div>
      <h1 class="title">Blue Electric <em>Life</em></h1>
      <p class="sub">{ALEX['subtitle']}. Ein Leben mit Seele — zwischen Hotelzimmern in Wien, stillen Morgen am Tegernsee, Musik um Mitternacht, Espresso vor Sonnenaufgang, Tattoos im warmen Licht, offenen Straßen Richtung Italien, Gleitschirm über Bergen und der ruhigen Verantwortung eines Vaters.</p>
      <div class="leitsatz">„{ALEX['leitsatz']}“</div>
    </div>

    <nav class="tabs">{tabs_html}</nav>

    {_alex_heute_panel()}
    {_alex_dashboard_panel()}
    {_alex_week_panel()}
    {_alex_day_panel()}
    {_alex_wien_panel()}
    {_alex_muc_panel()}
    {_alex_van_panel()}
    {_alex_para_panel()}
    {_alex_ptgo_panel()}
    {_alex_music_panel()}
    {_alex_style_panel()}
    {_alex_frauen_panel()}
    {_alex_tochter_panel()}
    {_alex_nerve_panel()}
    {monat_panel}
    {jahr_panel}
    {_alex_daily_panel()}
    {_alex_notion_panel()}
    {_alex_roadmap_panel()}
    {_alex_chaos_panel()}

    <div class="footnote">Blue Electric Life · ein Mann baut sich langsam, ehrlich und stilvoll zurück ins Leben.</div>
    """
    return _alex_page(body)


def _alex_build_ics() -> str:
    from datetime import timedelta

    now = _now_local()
    today = now.strftime("%Y%m%d")
    stamp = now.strftime("%Y%m%dT%H%M%S")
    byday = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Alex//Blue Electric Life//DE",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:Alex · Blue Electric Life",
             "X-WR-TIMEZONE:Europe/Berlin"]

    def esc(s):
        return str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    def event(uid, date_yyyymmdd, h, m, dur, summary, desc, rrule, alarms):
        out = ["BEGIN:VEVENT", f"UID:{uid}@blueelectric", f"DTSTAMP:{stamp}",
               f"DTSTART:{date_yyyymmdd}T{h:02d}{m:02d}00", f"DURATION:PT{dur}M",
               f"RRULE:{rrule}", f"SUMMARY:{esc(summary)}", f"DESCRIPTION:{esc(desc)}"]
        for trig, label in alarms:
            out += ["BEGIN:VALARM", "ACTION:DISPLAY", f"TRIGGER:{trig}", f"DESCRIPTION:{esc(label)}", "END:VALARM"]
        out.append("END:VEVENT")
        return out

    def next_weekday(wd):  # wd: 0=Mon … 6=Sun
        d = now.date()
        return (d + timedelta(days=(wd - d.weekday()) % 7)).strftime("%Y%m%d")

    # — Tägliche Anker (stabiler Grundrhythmus) —
    daily = [
        (6, 0, 60, "☀ Morgen-Anker", "Wasser, Fenster, Licht, kein Handy. Atmung, Mobility, Kraft. 5 Min Vision."),
        (8, 0, 240, "✦ Deep Work", "PTGO / Masterclass / Musik / Texte / KI. Phone weg, ein Ziel pro Block."),
        (12, 0, 90, "❖ Clean Lunch + Walk", "Leicht essen, ohne Bildschirm. 20 Min Spaziergang, Sonne ins Gesicht."),
        (17, 30, 90, "△ Golden Hour", "Isar / Tegernsee / Spaziergang. Fahrt mit Musik. Nervensystempflege."),
        (20, 0, 90, "♪ Musik / Schreiben", "Piano oder Gitarre. Tagebuch: Würde / Wachstum / Korrektur."),
        (22, 0, 60, "≈ Schlafvorbereitung", "Dusche, Duft, Lesen. Kein Bildschirm im Bett. Vor 23:00 Schirme aus."),
    ]
    for i, (h, m, dur, summ, desc) in enumerate(daily):
        lines += event(f"alex-daily-{i}", today, h, m, dur, summ, desc,
                       "FREQ=DAILY", [("PT0S", summ)])

    # — Wochen-Anker mit Vorlauf-Erinnerung —
    weekly = [
        # (wd, h, m, dur, summary, desc, [(trigger, label), ...])
        (2, 12, 0, 180, "🧹 Reset Day — Wohnung", "Ein Bereich heute (Bad/Küche/Schlafzimmer/Musikecke/Schrank). Ein Karton raus, eine Fläche frei.",
         [("PT0S", "Reset Day: ein Bereich, nicht perfekt — atmungsfähig.")]),
        (2, 18, 30, 45, "🧳 Tasche packen für Wien", "3 Hemden, 2 Hosen, Mantel, Notizbuch, Buch, Ladegeräte. Alles an die Tür.",
         [("PT0S", "Tasche für Wien packen.")]),
        (3, 7, 30, 210, "🚆 Wien-Anreise", "Schwarzer Mantel, Notizbuch, Buch. Im Zug schreiben statt scrollen.",
         [("-PT13H30M", "Morgen früh nach Wien — heute Abend Tasche & Zug checken."), ("PT0S", "Wien-Anreise. Creative Gentleman Mode.")]),
        (3, 20, 30, 120, "♫ Wien-Abend", "Kaffeehaus / Jazzbar / Schreiben. Notizen: Stil, Architektur, Ideen.",
         [("PT0S", "Wien-Abend: Kaffeehaus, Jazz, Schreiben.")]),
        (6, 11, 30, 60, "🪞 Wochenrückblick", "Würde: wo war ich ich selbst? Wachstum: wo bin ich gewachsen? Korrektur: was ändere ich?",
         [("PT0S", "Wochenrückblick: Würde / Wachstum / Korrektur.")]),
    ]
    for i, (wd, h, m, dur, summ, desc, alarms) in enumerate(weekly):
        lines += event(f"alex-weekly-{i}", next_weekday(wd), h, m, dur, summ, desc,
                       f"FREQ=WEEKLY;BYDAY={byday[wd]}", alarms)

    # — Umgangs- & Freiheits-Wochenenden (alle zwei Wochen, ab Anker-Freitag) —
    from datetime import datetime as _dt
    cust = ALEX["custody"]
    anchor = _dt.strptime(cust["anchor"], "%Y-%m-%d").date()  # Umgangs-Freitag
    sat = anchor + timedelta(days=1)                          # Übergabe-Samstag
    wien_fri = anchor + timedelta(days=7)                     # Wien-Freitag (Off-Woche)
    free_sat = anchor + timedelta(days=8)                     # Freiheits-Samstag (Off-Woche)
    ymd = lambda d: d.strftime("%Y%m%d")

    lines += event("alex-custody", ymd(anchor), 13, 0, 1770,
                   "🜂 Umgangswochenende — Tochter (Fr 13:00 – Sa 18:30)",
                   "Volle, ruhige Vaterpräsenz. Sonnenbrille hoch, Musik leise, Herz offen. Kein Termin im Kopf — nur sie.",
                   "FREQ=WEEKLY;INTERVAL=2;BYDAY=FR",
                   [("-P1D", "Morgen 13:00 Tochter abholen — Wohnung tochterklar machen, Plan locker halten."),
                    ("-PT2H", "In 2 Std abholen. Frisch machen, Playlist bereit, Kopf leeren."),
                    ("PT0S", "Jetzt abholen — pünktlich, warm, präsent.")])
    lines += event("alex-handover", ymd(sat), 18, 30, 30,
                   "↩ Übergabe zurück — Tochter (18:30)",
                   "Pünktlich, warm, klarer liebevoller Abschied. Kein Drama, keine Schwere. 'Bis in zwei Wochen.'",
                   "FREQ=WEEKLY;INTERVAL=2;BYDAY=SA",
                   [("-PT45M", "In 45 Min Übergabe — langsam zusammenpacken, schön ausklingen."),
                    ("PT0S", "Übergabe: warm, ohne Hektik. Du bist frei — aber du bleibst.")])
    lines += event("alex-free-weekend", ymd(free_sat), 9, 0, 600,
                   "△ Freies Wochenende — Freiheit",
                   "Van, Berge, Paragliding, Gardasee, Musik. Bewegte Stille, kein Eskapismus.",
                   "FREQ=WEEKLY;INTERVAL=2;BYDAY=SA",
                   [("-P1DT13H", "Morgen freies Wochenende — Van / Berge / Fliegen planen."),
                    ("PT0S", "Freies Wochenende. Voll da sein für dich.")])
    lines += event("alex-wien-sauna", ymd(wien_fri), 17, 0, 90,
                   "🔥 Sauna / Gym (Wien)", "Körper regulieren. Hitze, Kälte, Atem. Nur Off-Wochen — Umgangs-Freitag gehört der Tochter.",
                   "FREQ=WEEKLY;INTERVAL=2;BYDAY=FR",
                   [("PT0S", "Sauna oder Gym.")])

    # — Monatscheck (1. des Monats) —
    first = now.replace(day=1).strftime("%Y%m%d")
    lines += event("alex-monthly", first, 9, 0, 60, "📋 Monatscheck — 10 Fragen",
                   "Tochter? PTGO? Musik? Körper? Natur? Frauen nicht zentral? Nervensystem? Geld? Schönheit? Freiheit & Verantwortung?",
                   "FREQ=MONTHLY;BYMONTHDAY=1",
                   [("-P1D", "Morgen Monatscheck — die 10 ehrlichen Fragen."), ("PT0S", "Monatscheck: die 10 Fragen.")])

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


@app.get("/alex/tagesplan.ics")
def alex_ics():
    from starlette.responses import Response
    return Response(
        content=_alex_build_ics(),
        media_type="text/calendar",
        headers={"Content-Disposition": "attachment; filename=alex-tagesplan.ics"},
    )
