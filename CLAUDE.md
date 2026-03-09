# CLAUDE.md — PTGO v2

## Project Overview

PTGO (Pain/Trauma/Growth/Outcome) v2 is a therapeutic daily check-in application for patients with pain, trauma, or substance use disorders. It provides AI-driven conversational assessments, pattern detection, evidence-based action recommendations, and a therapist dashboard.

**Production URL:** https://app.ptgo.de

## Tech Stack

- **Language:** Python 3.9+
- **Framework:** FastAPI (async ASGI)
- **Database:** SQLAlchemy ORM (SQLite for dev, PostgreSQL for production)
- **Frontend:** Server-side rendered HTML/CSS/JS (no frontend build step)
- **AI:** Anthropic Claude API (`claude-haiku-4-5`) for signal extraction
- **External Services:** Twilio (WhatsApp), Stripe (payments), SMTP (email)

## Project Structure

This is a **monolithic single-file application**. All code lives in `app.py` (~2,500 lines).

```
ptgo-app/
├── .gitignore
├── app.py          # Entire application
└── CLAUDE.md       # This file
```

### Logical Sections in app.py

| Section | ~Lines | Purpose |
|---------|--------|---------|
| CONFIG | 40–67 | Environment variable loading |
| DB MODELS | 70–189 | SQLAlchemy models (5 tables) |
| APP INIT | 204–205 | FastAPI instance + session middleware |
| UTILS | 209–266 | Auth helpers, hashing, login events |
| WHATSAPP | 269–298 | Twilio WhatsApp integration |
| MODUL 2 – SIGNAL EXTRACTION | 357–396 | Claude AI integration |
| MODUL 5 – PATTERN ENGINE | 400–437 | Pattern detection (8 patterns) |
| MODUL 6 – ACTION LIBRARY | 441–508 | 8 therapeutic actions with scripts |
| MODUL 7 – ACTION ENGINE | 512–529 | Pattern-to-action mapping |
| SCORE CALCULATION | 532–558 | Recovery score computation |
| UI HELPERS | 561–637 | HTML/CSS template generation |
| REMINDERS | 640–696 | Background reminder loop (threading) |
| AUTH – MAGIC LINK | 708–820 | Passwordless patient login |
| MODUL 1 – CHECK-IN FLOW | 823–1587 | 5-screen patient assessment |
| RESULT SCREEN | 1587–1656 | Pattern + action display |
| OUTCOME FEEDBACK | 1656–1719 | Patient satisfaction rating |
| PROGRESS | 1681–1835 | Patient statistics dashboard |
| SETTINGS | 1719–1918 | Reminder configuration |
| TIMELINE | 1918–2025 | Historical check-in timeline |
| SUBSCRIPTION | 2025–2204 | Stripe payment flows |
| THERAPIST | 2382–2530 | Therapist dashboard & patient review |

## Commands

### Run locally

```bash
pip install fastapi sqlalchemy pydantic requests starlette python-multipart uvicorn
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

### Production deployment

```bash
# Behind Nginx reverse proxy with HTTPS, managed by systemd
uvicorn app:app --host 0.0.0.0 --port 8000
```

### No test suite or linter configured

There are currently no tests, linting, or CI/CD pipelines. When adding these in the future, prefer `pytest` for tests and `ruff` for linting.

## Environment Variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `APP_SECRET` | Yes (prod) | `"dev-secret-change-me"` | Session encryption & token hashing |
| `BASE_URL` | Yes (prod) | `"http://127.0.0.1:8000"` | Application base URL |
| `DB_URL` | Yes (prod) | `"sqlite:////opt/ptgo/ptgo.db"` | Database connection string |
| `APP_TZ` | No | `"Europe/Berlin"` | Timezone for reminders |
| `ANTHROPIC_API_KEY` | No | `""` | Claude AI for signal extraction |
| `STRIPE_SECRET_KEY` | No | `""` | Stripe payments |
| `STRIPE_PUBLISHABLE_KEY` | No | `""` | Stripe frontend key |
| `STRIPE_PRICE_ID` | No | auto-created | Stripe product price |
| `TWILIO_ACCOUNT_SID` | No | `""` | Twilio WhatsApp |
| `TWILIO_AUTH_TOKEN` | No | `""` | Twilio auth |
| `TWILIO_WHATSAPP_FROM` | No | `""` | WhatsApp sender number |
| `THERAPIST_WHATSAPP_TO` | No | `""` | Therapist WhatsApp target |
| `SMTP_HOST` | No | `""` | Email server |
| `SMTP_PORT` | No | `587` | Email port |
| `SMTP_USER` | No | `""` | Email auth user |
| `SMTP_PASS` | No | `""` | Email auth password |
| `SMTP_FROM` | No | `""` | Email from address |
| `REMINDER_LOOP_SECONDS` | No | `30` | Reminder check interval |

Database tables are auto-created on startup.

## Database Models

5 SQLAlchemy tables:

- **Therapist** — therapist accounts (email, name, phone, password_hash)
- **Patient** — patient records with auth tokens, subscription status, reminder config
- **CheckIn** — daily check-in data (mood, stress, sleep, body, craving, avoidance, patterns, actions, scores)
- **Outcome** — patient feedback on actions (better/same/worse)
- **LoginEvent** — audit log for login/logout/magic-link events

Key relationships: Therapist 1:N Patient, Patient 1:N CheckIn, CheckIn 1:N Outcome.

## Architecture & Patterns

### Authentication

- **Patients:** Passwordless magic-link via email. Tokens are SHA256-hashed with `APP_SECRET` salt and stored with expiration.
- **Therapists:** Email + password login. Passwords hashed with SHA256 + `APP_SECRET` salt.
- **Sessions:** FastAPI `SessionMiddleware` stores `patient_id` or `therapist_id`.

### Check-In Flow (5 Screens)

1. Overall state (0–10), stress, sleep, context text
2. Body scan with interactive pain map (SVG)
3. Craving (0–10), avoidance (0–10)
4. Mental state text, goal text
5. Summary & confirmation (with optional voice input)

### Pattern Detection (8 Patterns)

Rule-based engine matching check-in data to patterns:
- `stress_overload` — stress > 7 and sleep < 5
- `recovery_deficit` — sleep < 4
- `upper_body_tension` — shoulder/upper back pain
- `neck_guarding` — neck pain
- `impulse_pattern` — craving > 6
- `avoidance_pattern` — avoidance > 6
- `low_mood` — daily state < 4
- `balanced` — default good state

### Recovery Score

```
raw = 0.28*mood + 0.22*sleep + 0.18*body + 0.12*(10-stress) + 0.10*(10-craving) + 0.10*(10-avoidance)
score = clamp(round(raw/10*100), 0, 100)
risk_level = "high" if risk_points >= 6 else "medium" if risk_points >= 3 else "low"
```

### Action Library (8 Evidence-Based Interventions)

Each pattern maps to a specific therapeutic action with timed guided scripts:
`physiological_sigh`, `extended_exhale`, `shoulder_release`, `neck_reset`, `walk_reset`, `five_minute_start`, `urge_interrupt`, `sleep_downshift`

## Code Conventions

- **Single-file architecture** — all code in `app.py`; do not split into modules without explicit request
- **snake_case** for all Python identifiers
- **UPPER_CASE** for module-level constants (`PATTERNS`, `ACTION_LIBRARY`, `DB_URL`)
- **Private functions** prefixed with `_` (e.g., `_hash_code`, `_stripe_headers`)
- **Route handlers** named by feature (e.g., `checkin_1`, `therapist_dashboard`)
- **HTML is generated inline** as Python f-strings; use the `_page()` helper for consistent page wrappers
- **German language UI** — all patient-facing text is in German
- **Dependency injection** via FastAPI's `Depends(get_db)` for database sessions
- **No ORMs for queries** — uses SQLAlchemy Core-style queries mixed with ORM session methods

## Important Notes for AI Assistants

1. **Do not split app.py** into multiple files unless explicitly asked. The monolithic design is intentional.
2. **All UI text is in German.** Maintain German for any patient-facing or therapist-facing strings.
3. **No tests exist.** When modifying logic, be extra careful with manual verification.
4. **External services are optional.** Code gracefully handles missing API keys (Twilio, Stripe, Anthropic, SMTP).
5. **Security-sensitive code:** Magic tokens, password hashing, and session management all use `APP_SECRET`. Never log or expose tokens.
6. **The reminder system runs in a background thread** started at module load. Be careful with database session handling in threaded code.
7. **Two Stripe subscription implementations exist** (old and new). The newer one uses `/subscribe/*` routes.
8. **Pain map uses an inline SVG** with JavaScript click handlers for body region selection.
