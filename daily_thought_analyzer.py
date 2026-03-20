#!/usr/bin/env python3
"""
Daily Thought Analyzer — Elon Musk Style
Analysiert alle Branches, erkennt Denkmuster, gibt brutales Feedback.
Sendet um 12:00 und 18:00 via WhatsApp.

Usage:
    python daily_thought_analyzer.py              # Einmal jetzt analysieren + senden
    python daily_thought_analyzer.py --daemon      # Läuft dauerhaft, sendet um 12:00 & 18:00
    python daily_thought_analyzer.py --analyze     # Nur Analyse anzeigen, nicht senden
"""

import os
import sys
import time
import subprocess
import json
import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

# ── Config ──────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "").strip()
MY_WHATSAPP_TO = os.getenv("THERAPIST_WHATSAPP_TO", os.getenv("MY_WHATSAPP_TO", "")).strip()
APP_TZ = os.getenv("APP_TZ", "Europe/Berlin")
REPO_PATH = os.getenv("REPO_PATH", os.path.dirname(os.path.abspath(__file__)))

SEND_TIMES = [12, 18]  # Uhrzeiten für WhatsApp Push
LAST_SENT_FILE = os.path.join(REPO_PATH, ".thought_analyzer_sent")

# ── Git Analysis ────────────────────────────────────────────

def _run(cmd, cwd=None):
    """Run shell command, return stdout."""
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd or REPO_PATH)
    return r.stdout.strip()


def get_all_branches():
    """Alle Branches (lokal + remote)."""
    raw = _run("git branch -a --format='%(refname:short)'")
    branches = []
    seen = set()
    for b in raw.splitlines():
        name = b.strip().replace("origin/", "")
        if name and name not in seen and "HEAD" not in name:
            seen.add(name)
            branches.append(name)
    return branches


def get_today_commits(branch, days=1):
    """Commits der letzten N Tage auf einem Branch."""
    since = (datetime.now(ZoneInfo(APP_TZ)) - timedelta(days=days)).strftime("%Y-%m-%d")
    raw = _run(f'git log origin/{branch} --since="{since}" --oneline --no-merges 2>/dev/null || '
               f'git log {branch} --since="{since}" --oneline --no-merges 2>/dev/null')
    return [line.strip() for line in raw.splitlines() if line.strip()]


def get_recent_diff_summary(branch, max_lines=200):
    """Zusammenfassung der letzten Änderungen auf einem Branch."""
    diff = _run(f'git log origin/{branch} --since="2 days ago" --no-merges -p --stat 2>/dev/null | head -n {max_lines} || '
                f'git log {branch} --since="2 days ago" --no-merges -p --stat 2>/dev/null | head -n {max_lines}')
    return diff


def get_file_list():
    """Alle tracked files."""
    return _run("git ls-files").splitlines()


def build_daily_snapshot():
    """Kompaktes Snapshot aller Branch-Aktivitäten."""
    branches = get_all_branches()
    snapshot = {
        "date": datetime.now(ZoneInfo(APP_TZ)).strftime("%Y-%m-%d %H:%M"),
        "branches": {},
        "total_files": len(get_file_list()),
    }

    for branch in branches:
        commits = get_today_commits(branch, days=2)
        if not commits:
            continue
        diff_summary = get_recent_diff_summary(branch, max_lines=150)
        snapshot["branches"][branch] = {
            "commits": commits[:15],  # Max 15 pro Branch
            "diff_preview": diff_summary[:3000],  # Max 3000 chars
        }

    return snapshot


# ── Claude Analysis ─────────────────────────────────────────

SYSTEM_PROMPT = """Du bist ein brutaler, ehrlicher Thought Analyzer im Stil von Elon Musk.
Deine Aufgabe: Analysiere die Git-Aktivitäten eines Entwicklers und gib ihm ein TÄGLICHES FAZIT.

DEIN STIL:
- First Principles Thinking. Schneide den Bullshit weg.
- Was FUNKTIONIERT wirklich? Was ist nur Beschäftigung?
- Wo verschwendet er Zeit? Wo ist echtes Momentum?
- Gib 1-2 konkrete "Nächster Schritt" Empfehlungen
- Sei direkt wie Elon in einem Sprint Meeting

FORMAT (WhatsApp-tauglich, kurz):
🧠 THOUGHT DIRECTION
[1-2 Sätze: Wohin gehen die Gedanken heute?]

✅ FUNKTIONIERT
[Max 2-3 Punkte]

❌ ACHTUNG
[Max 2-3 Punkte — was nicht klappt oder Zeitverschwendung ist]

🚀 NEXT MOVE
[1-2 konkrete Aktionen für maximalen Skill-Boost]

📊 SKILL SCORE: X/10
[1 Satz warum]

Halte die gesamte Nachricht unter 800 Zeichen. Minimalistisch. Brutal ehrlich. Deutsch."""


def analyze_with_claude(snapshot):
    """Sende Snapshot an Claude, bekomme Elon-Style Analyse zurück."""
    if not ANTHROPIC_API_KEY:
        return _fallback_analysis(snapshot)

    user_msg = f"""Hier ist das Git-Aktivitäts-Snapshot von heute ({snapshot['date']}):

Aktive Branches: {len(snapshot['branches'])}
Total Files: {snapshot['total_files']}

Branch-Details:
"""
    for branch, data in snapshot['branches'].items():
        user_msg += f"\n--- {branch} ---\n"
        user_msg += f"Commits: {json.dumps(data['commits'], ensure_ascii=False)}\n"
        if data['diff_preview']:
            user_msg += f"Diff-Preview:\n{data['diff_preview'][:1500]}\n"

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 600,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_msg}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]
    except Exception as e:
        print(f"[!] Claude API error: {e}")
        return _fallback_analysis(snapshot)


def _fallback_analysis(snapshot):
    """Wenn kein API Key: einfache regelbasierte Analyse."""
    branches = snapshot["branches"]
    active = len(branches)
    total_commits = sum(len(d["commits"]) for d in branches.values())

    msg = f"🧠 THOUGHT DIRECTION\n"
    msg += f"{active} aktive Branches, {total_commits} Commits heute.\n\n"

    if total_commits > 10:
        msg += "✅ FUNKTIONIERT\nHohes Tempo. Viel Output.\n\n"
        msg += "❌ ACHTUNG\nZu viele parallele Branches? Fokus prüfen.\n\n"
    elif total_commits > 3:
        msg += "✅ FUNKTIONIERT\nSolider Output. Konstant.\n\n"
        msg += "❌ ACHTUNG\nPrüfe ob du an dem RICHTIGEN arbeitest.\n\n"
    else:
        msg += "❌ ACHTUNG\nWenig Aktivität. Blockiert oder planend?\n\n"

    msg += f"🚀 NEXT MOVE\nFokus auf den Branch mit dem meisten Impact.\n\n"
    msg += f"📊 SKILL SCORE: {'7' if total_commits > 5 else '5'}/10"
    return msg


# ── WhatsApp Delivery ───────────────────────────────────────

def send_whatsapp(message):
    """Sende via Twilio WhatsApp."""
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM, MY_WHATSAPP_TO]):
        print("[!] WhatsApp nicht konfiguriert. Nur Console-Output.")
        print("─" * 50)
        print(message)
        print("─" * 50)
        return False

    try:
        resp = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json",
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            data={
                "From": f"whatsapp:{TWILIO_WHATSAPP_FROM}",
                "To": f"whatsapp:{MY_WHATSAPP_TO}",
                "Body": message,
            },
            timeout=15,
        )
        resp.raise_for_status()
        print(f"[✓] WhatsApp gesendet an {MY_WHATSAPP_TO}")
        return True
    except Exception as e:
        print(f"[!] WhatsApp Fehler: {e}")
        print(message)
        return False


# ── Scheduler ───────────────────────────────────────────────

def _sent_key(hour):
    """Unique key für heute + Stunde."""
    today = datetime.now(ZoneInfo(APP_TZ)).strftime("%Y-%m-%d")
    return f"{today}-{hour}"


def _was_sent(hour):
    """Check ob heute zu dieser Stunde schon gesendet."""
    key = _sent_key(hour)
    try:
        with open(LAST_SENT_FILE, "r") as f:
            return key in f.read()
    except FileNotFoundError:
        return False


def _mark_sent(hour):
    """Markiere als gesendet."""
    key = _sent_key(hour)
    with open(LAST_SENT_FILE, "a") as f:
        f.write(key + "\n")


def run_analysis():
    """Führe eine Analyse durch und gib das Ergebnis zurück."""
    print(f"[*] Starte Analyse... {datetime.now(ZoneInfo(APP_TZ)).strftime('%H:%M')}")
    snapshot = build_daily_snapshot()

    if not snapshot["branches"]:
        msg = "🧠 Heute keine Git-Aktivität erkannt. Planungstag oder Pause?"
    else:
        msg = analyze_with_claude(snapshot)

    return msg


def run_once():
    """Einmal analysieren + senden."""
    msg = run_analysis()
    send_whatsapp(msg)


def run_daemon():
    """Daemon-Modus: Prüft alle 60s, sendet um 12:00 und 18:00."""
    print(f"[*] Thought Analyzer Daemon gestartet")
    print(f"[*] Sendezeiten: {SEND_TIMES} Uhr ({APP_TZ})")
    print(f"[*] Repo: {REPO_PATH}")

    while True:
        now = datetime.now(ZoneInfo(APP_TZ))
        current_hour = now.hour

        for send_hour in SEND_TIMES:
            if current_hour == send_hour and not _was_sent(send_hour):
                print(f"\n[*] === {send_hour}:00 Analyse ===")
                msg = run_analysis()
                send_whatsapp(msg)
                _mark_sent(send_hour)

        time.sleep(60)


# ── Main ────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--daemon" in sys.argv:
        run_daemon()
    elif "--analyze" in sys.argv:
        msg = run_analysis()
        print("\n" + msg + "\n")
    else:
        run_once()
