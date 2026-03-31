-- ============================================================
-- 006 — KI-CHEFAGENT: Patient & Check-in Tabellen fuer Supabase
-- Spiegelt die PTGO app.py Datenbank-Modelle fuer den Chefagenten
-- ============================================================

-- Therapeuten
CREATE TABLE IF NOT EXISTS therapists (
  id BIGSERIAL PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  phone TEXT,
  password_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Patienten
CREATE TABLE IF NOT EXISTS patients (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  phone TEXT UNIQUE NOT NULL,
  email TEXT UNIQUE NOT NULL,
  email_verified BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  subscription_active BOOLEAN DEFAULT FALSE,
  reminder_enabled BOOLEAN DEFAULT TRUE,
  reminder_time_local TEXT DEFAULT '08:00',
  last_reminder_sent_on TEXT,
  therapist_id BIGINT REFERENCES therapists(id)
);

CREATE INDEX IF NOT EXISTS idx_patients_therapist ON patients(therapist_id);

-- Check-ins
CREATE TABLE IF NOT EXISTS checkins (
  id BIGSERIAL PRIMARY KEY,
  patient_id BIGINT NOT NULL REFERENCES patients(id),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  local_day TEXT,

  -- Modul 1 — Conversational
  daily_state INTEGER,
  overall_text TEXT,
  stress INTEGER,
  sleep INTEGER,
  context_text TEXT,
  body INTEGER,
  body_text TEXT,
  pain_map_json TEXT,
  pain_region TEXT,
  pain_type TEXT,
  craving INTEGER,
  avoidance INTEGER,
  mental_text TEXT,
  goal_text TEXT,

  -- Modul 2 — Signal Extraction
  signals_json TEXT,

  -- Modul 5 — Pattern Engine
  pattern_code TEXT,
  pattern_label TEXT,

  -- Modul 7 — Action Engine
  action_code TEXT,
  action_label TEXT,
  action_text TEXT,

  -- Score
  score INTEGER DEFAULT 0,
  risk_level TEXT DEFAULT 'low'
);

CREATE INDEX IF NOT EXISTS idx_checkins_patient ON checkins(patient_id);
CREATE INDEX IF NOT EXISTS idx_checkins_day ON checkins(local_day);
CREATE INDEX IF NOT EXISTS idx_checkins_created ON checkins(created_at);

-- Outcomes
CREATE TABLE IF NOT EXISTS outcomes (
  id BIGSERIAL PRIMARY KEY,
  checkin_id BIGINT NOT NULL REFERENCES checkins(id),
  patient_id BIGINT NOT NULL REFERENCES patients(id),
  rating TEXT NOT NULL,  -- better | same | worse
  outcome_note TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outcomes_checkin ON outcomes(checkin_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_patient ON outcomes(patient_id);

-- RLS Policies (Service-Key only — kein oeffentlicher Zugriff)
ALTER TABLE therapists ENABLE ROW LEVEL SECURITY;
ALTER TABLE patients ENABLE ROW LEVEL SECURITY;
ALTER TABLE checkins ENABLE ROW LEVEL SECURITY;
ALTER TABLE outcomes ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access" ON therapists FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON patients FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON checkins FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON outcomes FOR ALL USING (true) WITH CHECK (true);
