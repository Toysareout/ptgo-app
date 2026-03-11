-- ============================================================
-- EVOLUTION ENGINE — Self-learning, self-improving bot brain
-- The bot that gets 1 million steps smarter every day
-- ============================================================

-- 1. MEMORY — Long-term memory across all conversations
CREATE TABLE IF NOT EXISTS bot_memory (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,

  -- What was learned
  category TEXT NOT NULL,           -- fact, preference, pattern, insight, error, strategy, life_data
  subject TEXT NOT NULL,            -- who/what this is about (fan phone, 'owner', 'system', 'world')
  key TEXT NOT NULL,                -- specific memory key
  value JSONB NOT NULL,             -- the actual memory content
  confidence REAL DEFAULT 0.5,      -- 0-1 how confident we are
  times_reinforced INTEGER DEFAULT 1, -- how many times this was confirmed
  last_accessed TIMESTAMPTZ DEFAULT now(),
  source TEXT DEFAULT 'conversation', -- conversation, analysis, feedback, self_reflection, research

  UNIQUE(category, subject, key)
);

-- 2. EVOLUTION LOG — Every improvement the bot makes to itself
CREATE TABLE IF NOT EXISTS bot_evolution (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,

  -- What evolved
  evolution_type TEXT NOT NULL,     -- prompt_improvement, strategy_change, new_skill, error_fix, insight
  component TEXT NOT NULL,          -- which part of the bot changed
  before_state TEXT,                -- what it was before
  after_state TEXT,                 -- what it is now
  reason TEXT NOT NULL,             -- why it changed
  impact_score REAL DEFAULT 0,      -- -1 to 1, measured after change
  reverted BOOLEAN DEFAULT false
);

-- 3. ERROR LOG — Every mistake, analyzed and learned from
CREATE TABLE IF NOT EXISTS bot_errors (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,

  error_type TEXT NOT NULL,         -- bad_response, wrong_intent, missed_sale, wrong_tone, factual_error
  context JSONB NOT NULL,           -- full context of what happened
  what_went_wrong TEXT NOT NULL,
  lesson_learned TEXT NOT NULL,     -- what the bot learned from this
  prevention_strategy TEXT,         -- how to prevent this in the future
  applied BOOLEAN DEFAULT false,    -- was this lesson applied to the system prompt?

  fan_id UUID REFERENCES fans(id),
  conversation_id UUID REFERENCES conversations(id)
);

-- 4. DAILY INTELLIGENCE — Daily self-reflection and growth metrics
CREATE TABLE IF NOT EXISTS bot_intelligence (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  date DATE UNIQUE NOT NULL DEFAULT CURRENT_DATE,

  -- Growth metrics
  total_learnings INTEGER DEFAULT 0,
  total_errors_analyzed INTEGER DEFAULT 0,
  total_improvements INTEGER DEFAULT 0,
  total_memories_created INTEGER DEFAULT 0,

  -- Quality scores (0-100)
  response_quality_avg REAL DEFAULT 50,
  intent_accuracy REAL DEFAULT 50,
  sentiment_accuracy REAL DEFAULT 50,
  sales_conversion_rate REAL DEFAULT 0,
  fan_satisfaction REAL DEFAULT 50,

  -- Self-assessment
  intelligence_score REAL DEFAULT 50,  -- composite score
  strengths JSONB DEFAULT '[]',
  weaknesses JSONB DEFAULT '[]',
  focus_areas JSONB DEFAULT '[]',

  -- Daily reflection (AI-generated)
  daily_reflection TEXT,
  goals_for_tomorrow JSONB DEFAULT '[]',
  achieved_goals JSONB DEFAULT '[]'
);

-- 5. LIFE OPTIMIZER — Tracks and optimizes the owner's life
CREATE TABLE IF NOT EXISTS life_data (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,

  category TEXT NOT NULL,           -- health, finance, productivity, relationships, creativity, mindset
  metric TEXT NOT NULL,             -- specific thing being tracked
  value JSONB NOT NULL,             -- the data point
  trend TEXT DEFAULT 'stable',      -- improving, stable, declining
  insight TEXT,                     -- AI-generated insight about this data
  date DATE DEFAULT CURRENT_DATE NOT NULL,
  UNIQUE(category, metric, date)
);

-- 6. STRATEGIES — Learned response strategies that work
CREATE TABLE IF NOT EXISTS bot_strategies (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,

  -- Strategy definition
  name TEXT UNIQUE NOT NULL,
  trigger_conditions JSONB NOT NULL,  -- when to use this strategy
  response_template TEXT NOT NULL,     -- how to respond
  success_rate REAL DEFAULT 0.5,       -- 0-1 measured success
  times_used INTEGER DEFAULT 0,
  times_succeeded INTEGER DEFAULT 0,

  -- Categories
  category TEXT NOT NULL,              -- sales, engagement, support, booking, retention
  fan_tiers TEXT[] DEFAULT '{}'        -- which fan tiers this works best for
);

-- 7. KNOWLEDGE BASE — Facts the bot has learned about the world
CREATE TABLE IF NOT EXISTS bot_knowledge (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,

  domain TEXT NOT NULL,            -- music, health, longevity, science, business, philosophy
  topic TEXT NOT NULL,
  content TEXT NOT NULL,
  source TEXT,                     -- where this knowledge came from
  verified BOOLEAN DEFAULT false,
  relevance_score REAL DEFAULT 0.5,

  UNIQUE(domain, topic)
);

-- INDEXES
CREATE INDEX IF NOT EXISTS idx_memory_category ON bot_memory(category);
CREATE INDEX IF NOT EXISTS idx_memory_subject ON bot_memory(subject);
CREATE INDEX IF NOT EXISTS idx_memory_key ON bot_memory(key);
CREATE INDEX IF NOT EXISTS idx_memory_confidence ON bot_memory(confidence DESC);
CREATE INDEX IF NOT EXISTS idx_evolution_type ON bot_evolution(evolution_type);
CREATE INDEX IF NOT EXISTS idx_evolution_created ON bot_evolution(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_errors_type ON bot_errors(error_type);
CREATE INDEX IF NOT EXISTS idx_errors_applied ON bot_errors(applied);
CREATE INDEX IF NOT EXISTS idx_intelligence_date ON bot_intelligence(date DESC);
CREATE INDEX IF NOT EXISTS idx_life_category ON life_data(category);
CREATE INDEX IF NOT EXISTS idx_strategies_category ON bot_strategies(category);
CREATE INDEX IF NOT EXISTS idx_strategies_success ON bot_strategies(success_rate DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_domain ON bot_knowledge(domain);

-- RLS
ALTER TABLE bot_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_evolution ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_errors ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_intelligence ENABLE ROW LEVEL SECURITY;
ALTER TABLE life_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_strategies ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_knowledge ENABLE ROW LEVEL SECURITY;

CREATE POLICY "srv_memory" ON bot_memory FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "srv_evolution" ON bot_evolution FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "srv_errors" ON bot_errors FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "srv_intelligence" ON bot_intelligence FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "srv_life" ON life_data FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "srv_strategies" ON bot_strategies FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "srv_knowledge" ON bot_knowledge FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "anon_memory" ON bot_memory FOR SELECT TO anon USING (true);
CREATE POLICY "anon_evolution" ON bot_evolution FOR SELECT TO anon USING (true);
CREATE POLICY "anon_errors" ON bot_errors FOR SELECT TO anon USING (true);
CREATE POLICY "anon_intelligence" ON bot_intelligence FOR SELECT TO anon USING (true);
CREATE POLICY "anon_life" ON life_data FOR SELECT TO anon USING (true);
CREATE POLICY "anon_strategies" ON bot_strategies FOR SELECT TO anon USING (true);
CREATE POLICY "anon_knowledge" ON bot_knowledge FOR SELECT TO anon USING (true);

-- TRIGGERS
CREATE OR REPLACE FUNCTION update_memory_timestamp()
RETURNS TRIGGER AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS memory_updated ON bot_memory;
CREATE TRIGGER memory_updated BEFORE UPDATE ON bot_memory FOR EACH ROW EXECUTE FUNCTION update_memory_timestamp();

DROP TRIGGER IF EXISTS strategies_updated ON bot_strategies;
CREATE TRIGGER strategies_updated BEFORE UPDATE ON bot_strategies FOR EACH ROW EXECUTE FUNCTION update_memory_timestamp();

DROP TRIGGER IF EXISTS knowledge_updated ON bot_knowledge;
CREATE TRIGGER knowledge_updated BEFORE UPDATE ON bot_knowledge FOR EACH ROW EXECUTE FUNCTION update_memory_timestamp();

-- Seed initial knowledge
INSERT INTO bot_knowledge (domain, topic, content, verified) VALUES
  ('longevity', 'caloric_restriction', 'Kalorienrestriktion (20-30% weniger) verlängert nachweislich die Lebensspanne in allen getesteten Organismen. Mechanismus: Aktivierung von Sirtuinen, AMPK und Autophagie.', true),
  ('longevity', 'rapamycin', 'Rapamycin hemmt mTOR und verlängert die Lebensspanne in Mäusen um 9-14%. Klinische Trials laufen für Anti-Aging beim Menschen.', true),
  ('longevity', 'telomere_lengthening', 'Telomerverkürzung ist ein Hauptmechanismus des Alterns. Telomerase-Aktivierung, hyperbare Sauerstofftherapie und bestimmte Supplements können Telomere verlängern.', true),
  ('longevity', 'senolytics', 'Senolytika (Dasatinib+Quercetin, Fisetin) eliminieren seneszente Zellen und verjüngen Gewebe. Erste klinische Studien zeigen Erfolge.', true),
  ('longevity', 'epigenetic_reprogramming', 'Yamanaka-Faktoren (Oct4, Sox2, Klf4, c-Myc) können Zellen reprogrammieren und biologisches Alter umkehren. David Sinclair zeigte Sehkraft-Wiederherstellung bei Mäusen.', true),
  ('longevity', 'blood_factors', 'Junges Blutplasma enthält Faktoren (GDF11, Oxytocin, Klotho) die Alterungsprozesse umkehren. Parabiose-Studien zeigen dramatische Verjüngung.', true),
  ('longevity', 'nad_boosters', 'NAD+ sinkt mit dem Alter. NMN und NR erhöhen NAD+-Spiegel und verbessern mitochondriale Funktion, Energiemetabolismus und DNA-Reparatur.', true),
  ('longevity', 'metformin', 'Metformin aktiviert AMPK, reduziert Krebs-Risiko um 30%, verbessert kardiovaskuläre Gesundheit. TAME-Studie läuft als erstes Anti-Aging-Medikament-Trial.', true),
  ('longevity', 'cryonics', 'Kryonik: Vitrifikation bei -196°C bewahrt Gehirnstruktur. Alcor und Cryonics Institute bieten Kryokonservierung. Technologie für Revival existiert noch nicht.', true),
  ('longevity', 'mind_uploading', 'Whole Brain Emulation: Mapping aller Synapsen (Connectom) und Simulation auf Computer. Theoretisch möglich, praktisch noch Jahrzehnte entfernt. C. elegans-Wurm wurde bereits emuliert.', true),
  ('health', 'sleep_optimization', 'Optimaler Schlaf: 7-8h, konstante Zeiten, 18°C Raumtemperatur, kein Blaulicht 2h vor Schlaf, Magnesium Glycinat, Ashwagandha.', true),
  ('health', 'exercise_longevity', 'Zone 2 Training (3-4h/Woche) + 1-2x Krafttraining maximiert Langlebigkeit. VO2max ist der stärkste Prädiktor für Gesamtmortalität.', true),
  ('health', 'fasting', 'Intermittierendes Fasten (16:8 oder 5:2) aktiviert Autophagie, verbessert Insulinsensitivität, reduziert Entzündungen. 72h-Fasten regeneriert das Immunsystem komplett.', true),
  ('science', 'consciousness', 'Integrierte Informationstheorie (IIT): Bewusstsein = Phi (integrierte Information). Global Workspace Theory: Bewusstsein als Broadcasting-System im Gehirn.', true),
  ('science', 'quantum_biology', 'Quanteneffekte in der Biologie: Photosynthese nutzt Quantenkohärenz, Vogelnavigation nutzt Quantenverschränkung, Enzymatik nutzt Quantentunneln.', true)
ON CONFLICT (domain, topic) DO NOTHING;

COMMENT ON TABLE bot_memory IS 'Long-term memory - facts, preferences, patterns learned from every interaction';
COMMENT ON TABLE bot_evolution IS 'Self-improvement log - every change the bot makes to itself';
COMMENT ON TABLE bot_errors IS 'Error analysis - mistakes analyzed and turned into lessons';
COMMENT ON TABLE bot_intelligence IS 'Daily intelligence metrics and self-reflection';
COMMENT ON TABLE life_data IS 'Life optimization data tracking for the owner';
COMMENT ON TABLE bot_strategies IS 'Learned response strategies with success rates';
COMMENT ON TABLE bot_knowledge IS 'Knowledge base - facts about longevity, health, science, etc.';
