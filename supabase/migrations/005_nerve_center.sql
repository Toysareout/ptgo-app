-- ============================================================
-- NERVE CENTER — Personal AI Brain Tables
-- Knowledge Vault, Daily Questions, Situation Analysis
-- ============================================================

-- 1. KNOWLEDGE VAULT — permanent storage for all know-how
CREATE TABLE IF NOT EXISTS nerve_knowledge (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  category TEXT NOT NULL DEFAULT 'general',
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  tags TEXT[] DEFAULT '{}',
  source TEXT DEFAULT 'manual',
  meta JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_nerve_knowledge_cat ON nerve_knowledge(category);
CREATE INDEX IF NOT EXISTS idx_nerve_knowledge_tags ON nerve_knowledge USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_nerve_knowledge_created ON nerve_knowledge(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_nerve_knowledge_search ON nerve_knowledge USING GIN(to_tsvector('german', title || ' ' || content));

-- 2. DAILY QUESTIONS — 10 questions per day
CREATE TABLE IF NOT EXISTS nerve_daily_questions (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  question_date DATE NOT NULL DEFAULT CURRENT_DATE,
  question_number INTEGER NOT NULL CHECK (question_number BETWEEN 1 AND 10),
  question TEXT NOT NULL,
  category TEXT NOT NULL,
  answer TEXT,
  answered_at TIMESTAMPTZ,
  impact_rating INTEGER CHECK (impact_rating BETWEEN 1 AND 5),
  ai_insight TEXT,
  UNIQUE(question_date, question_number)
);
CREATE INDEX IF NOT EXISTS idx_nerve_dq_date ON nerve_daily_questions(question_date DESC);

-- 3. SITUATION ANALYSIS — paste text, get analysis
CREATE TABLE IF NOT EXISTS nerve_situations (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  title TEXT NOT NULL DEFAULT 'Neue Analyse',
  input_text TEXT NOT NULL,
  category TEXT DEFAULT 'general',
  research JSONB DEFAULT '{}'::jsonb,
  plan JSONB DEFAULT '[]'::jsonb,
  analysis TEXT,
  recommendation TEXT,
  status TEXT DEFAULT 'pending' CHECK (status IN ('pending','researching','planning','complete','archived')),
  related_knowledge UUID[]
);
CREATE INDEX IF NOT EXISTS idx_nerve_sit_status ON nerve_situations(status);
CREATE INDEX IF NOT EXISTS idx_nerve_sit_created ON nerve_situations(created_at DESC);

-- 4. RESEARCH LOG — auto-discoveries
CREATE TABLE IF NOT EXISTS nerve_research (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  topic TEXT NOT NULL,
  category TEXT NOT NULL,
  findings TEXT NOT NULL,
  source_urls TEXT[],
  relevance_score REAL DEFAULT 0.5,
  applied BOOLEAN DEFAULT false,
  meta JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_nerve_research_cat ON nerve_research(category);
CREATE INDEX IF NOT EXISTS idx_nerve_research_created ON nerve_research(created_at DESC);

-- 5. PATIENT INTELLIGENCE — ethical pattern analysis
CREATE TABLE IF NOT EXISTS nerve_patient_insights (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  patient_ref TEXT NOT NULL,
  insight_type TEXT NOT NULL,
  pattern_data JSONB NOT NULL DEFAULT '{}'::jsonb,
  recommendation TEXT,
  confidence REAL DEFAULT 0.5,
  reviewed BOOLEAN DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_nerve_pi_ref ON nerve_patient_insights(patient_ref);
CREATE INDEX IF NOT EXISTS idx_nerve_pi_type ON nerve_patient_insights(insight_type);

-- 6. ERROR LOG — self-correction tracking
CREATE TABLE IF NOT EXISTS nerve_corrections (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  source_module TEXT NOT NULL,
  error_description TEXT NOT NULL,
  correction TEXT NOT NULL,
  auto_fixed BOOLEAN DEFAULT false
);

-- RLS policies
ALTER TABLE nerve_knowledge ENABLE ROW LEVEL SECURITY;
ALTER TABLE nerve_daily_questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE nerve_situations ENABLE ROW LEVEL SECURITY;
ALTER TABLE nerve_research ENABLE ROW LEVEL SECURITY;
ALTER TABLE nerve_patient_insights ENABLE ROW LEVEL SECURITY;
ALTER TABLE nerve_corrections ENABLE ROW LEVEL SECURITY;

-- Service role full access for all nerve tables
DO $$
DECLARE
  tbl TEXT;
BEGIN
  FOR tbl IN SELECT unnest(ARRAY[
    'nerve_knowledge','nerve_daily_questions','nerve_situations',
    'nerve_research','nerve_patient_insights','nerve_corrections'
  ]) LOOP
    EXECUTE format('DROP POLICY IF EXISTS "Service role full access" ON %I', tbl);
    EXECUTE format('CREATE POLICY "Service role full access" ON %I FOR ALL TO service_role USING (true) WITH CHECK (true)', tbl);
  END LOOP;
END $$;

-- Updated_at trigger for relevant tables
CREATE OR REPLACE FUNCTION nerve_update_timestamp()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE tbl TEXT;
BEGIN
  FOR tbl IN SELECT unnest(ARRAY['nerve_knowledge','nerve_situations']) LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS set_updated_at ON %I', tbl);
    EXECUTE format('CREATE TRIGGER set_updated_at BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION nerve_update_timestamp()', tbl);
  END LOOP;
END $$;
