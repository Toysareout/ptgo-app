-- ============================================================
-- TTAO COMPLETE DATABASE SETUP
-- Run this ONCE in Supabase SQL Editor:
-- https://supabase.com/dashboard/project/pwdhxarvemcgkhhnvbng/sql/new
--
-- Creates ALL tables: heal_applications + 14 bot tables
-- Safe to re-run (uses IF NOT EXISTS everywhere)
-- ============================================================

-- ============================================================
-- 1. HEAL APPLICATIONS (session applications from heal.html)
-- ============================================================
CREATE TABLE IF NOT EXISTS heal_applications (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  phone TEXT,
  reason TEXT NOT NULL,
  duration TEXT,
  tried TEXT,
  portal_entry TEXT DEFAULT 'direct',
  source TEXT DEFAULT 'direct',
  status TEXT DEFAULT 'new' NOT NULL,
  notes TEXT,
  call_date TIMESTAMPTZ,
  session_date TIMESTAMPTZ,
  revenue_cents INTEGER DEFAULT 0,
  CONSTRAINT valid_status CHECK (status IN ('new','contacted','call_scheduled','booked','completed','follow_up','declined'))
);

CREATE INDEX IF NOT EXISTS idx_heal_apps_status ON heal_applications(status);
CREATE INDEX IF NOT EXISTS idx_heal_apps_email ON heal_applications(email);
CREATE INDEX IF NOT EXISTS idx_heal_apps_created ON heal_applications(created_at DESC);

ALTER TABLE heal_applications ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  DROP POLICY IF EXISTS "Allow anonymous insert" ON heal_applications;
  DROP POLICY IF EXISTS "Service role full access" ON heal_applications;
END $$;

CREATE POLICY "Allow anonymous insert" ON heal_applications FOR INSERT TO anon WITH CHECK (true);
CREATE POLICY "Service role full access" ON heal_applications FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE OR REPLACE FUNCTION update_heal_updated_at()
RETURNS TRIGGER AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS heal_applications_updated_at ON heal_applications;
CREATE TRIGGER heal_applications_updated_at BEFORE UPDATE ON heal_applications FOR EACH ROW EXECUTE FUNCTION update_heal_updated_at();

-- ============================================================
-- 2. FANS — Every person who contacts the bot
-- ============================================================
CREATE TABLE IF NOT EXISTS fans (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  phone TEXT UNIQUE NOT NULL,
  name TEXT,
  instagram TEXT,
  email TEXT,
  engagement_score INTEGER DEFAULT 0,
  loyalty_score INTEGER DEFAULT 0,
  purchase_score INTEGER DEFAULT 0,
  vip_score INTEGER DEFAULT 0,
  tier TEXT DEFAULT 'new' NOT NULL,
  mood TEXT DEFAULT 'neutral',
  language TEXT DEFAULT 'de',
  total_messages INTEGER DEFAULT 0,
  total_purchases INTEGER DEFAULT 0,
  total_spent_cents INTEGER DEFAULT 0,
  last_message_at TIMESTAMPTZ,
  last_purchase_at TIMESTAMPTZ,
  first_contact_at TIMESTAMPTZ DEFAULT now(),
  conversation_state TEXT DEFAULT 'idle',
  context_data JSONB DEFAULT '{}',
  interests TEXT[] DEFAULT '{}',
  preferred_time TEXT,
  opt_out BOOLEAN DEFAULT false
);

-- ============================================================
-- 3. CONVERSATIONS — Every message exchange
-- ============================================================
CREATE TABLE IF NOT EXISTS conversations (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  fan_id UUID REFERENCES fans(id) ON DELETE CASCADE,
  phone TEXT NOT NULL,
  direction TEXT NOT NULL,
  message_type TEXT DEFAULT 'text',
  body TEXT,
  media_url TEXT,
  intent TEXT,
  sentiment REAL,
  topics TEXT[],
  entities JSONB DEFAULT '{}',
  response_strategy TEXT,
  response_time_ms INTEGER,
  ai_model TEXT,
  ai_tokens_used INTEGER DEFAULT 0
);

-- ============================================================
-- 4. BOT SALES — Every transaction
-- ============================================================
CREATE TABLE IF NOT EXISTS bot_sales (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  fan_id UUID REFERENCES fans(id) ON DELETE SET NULL,
  phone TEXT NOT NULL,
  item_type TEXT NOT NULL,
  item_name TEXT NOT NULL,
  price_cents INTEGER NOT NULL,
  currency TEXT DEFAULT 'eur',
  stripe_session_id TEXT,
  stripe_payment_status TEXT DEFAULT 'pending',
  checkout_url TEXT,
  source TEXT DEFAULT 'bot',
  conversation_id UUID REFERENCES conversations(id)
);

-- ============================================================
-- 5. BROADCASTS — Scheduled mass messages
-- ============================================================
CREATE TABLE IF NOT EXISTS broadcasts (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  media_url TEXT,
  target_tier TEXT[] DEFAULT '{new,casual,engaged,superfan,vip,whale}',
  target_interests TEXT[],
  target_mood TEXT[],
  scheduled_at TIMESTAMPTZ,
  sent_at TIMESTAMPTZ,
  status TEXT DEFAULT 'draft',
  total_recipients INTEGER DEFAULT 0,
  total_delivered INTEGER DEFAULT 0,
  total_read INTEGER DEFAULT 0,
  total_replied INTEGER DEFAULT 0,
  total_conversions INTEGER DEFAULT 0
);

-- ============================================================
-- 6. BOT ANALYTICS — Daily aggregated stats
-- ============================================================
CREATE TABLE IF NOT EXISTS bot_analytics (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  date DATE UNIQUE NOT NULL DEFAULT CURRENT_DATE,
  total_messages_in INTEGER DEFAULT 0,
  total_messages_out INTEGER DEFAULT 0,
  unique_fans INTEGER DEFAULT 0,
  new_fans INTEGER DEFAULT 0,
  avg_sentiment REAL DEFAULT 0,
  top_intents JSONB DEFAULT '{}',
  top_topics JSONB DEFAULT '{}',
  total_sales INTEGER DEFAULT 0,
  total_revenue_cents INTEGER DEFAULT 0,
  checkout_links_sent INTEGER DEFAULT 0,
  conversion_rate REAL DEFAULT 0,
  avg_response_time_ms INTEGER DEFAULT 0,
  total_ai_tokens INTEGER DEFAULT 0,
  total_ai_cost_cents INTEGER DEFAULT 0,
  booking_requests INTEGER DEFAULT 0,
  bookings_confirmed INTEGER DEFAULT 0
);

-- ============================================================
-- 7. BOT BOOKINGS — Show/session/feature requests
-- ============================================================
CREATE TABLE IF NOT EXISTS bot_bookings (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  fan_id UUID REFERENCES fans(id) ON DELETE SET NULL,
  phone TEXT NOT NULL,
  booking_type TEXT NOT NULL,
  description TEXT,
  preferred_date TIMESTAMPTZ,
  budget_cents INTEGER,
  location TEXT,
  status TEXT DEFAULT 'new',
  notes TEXT
);

-- ============================================================
-- 8. BOT CONFIG — Runtime configuration
-- ============================================================
CREATE TABLE IF NOT EXISTS bot_config (
  key TEXT PRIMARY KEY,
  value JSONB NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- 9. BOT MEMORY — Long-term memory across conversations
-- ============================================================
CREATE TABLE IF NOT EXISTS bot_memory (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  category TEXT NOT NULL,
  subject TEXT NOT NULL,
  key TEXT NOT NULL,
  value JSONB NOT NULL,
  confidence REAL DEFAULT 0.5,
  times_reinforced INTEGER DEFAULT 1,
  last_accessed TIMESTAMPTZ DEFAULT now(),
  source TEXT DEFAULT 'conversation',
  UNIQUE(category, subject, key)
);

-- ============================================================
-- 10. BOT EVOLUTION — Self-improvement log
-- ============================================================
CREATE TABLE IF NOT EXISTS bot_evolution (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  evolution_type TEXT NOT NULL,
  component TEXT NOT NULL,
  before_state TEXT,
  after_state TEXT,
  reason TEXT NOT NULL,
  impact_score REAL DEFAULT 0,
  reverted BOOLEAN DEFAULT false
);

-- ============================================================
-- 11. BOT ERRORS — Mistakes analyzed and learned from
-- ============================================================
CREATE TABLE IF NOT EXISTS bot_errors (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  error_type TEXT NOT NULL,
  context JSONB NOT NULL,
  what_went_wrong TEXT NOT NULL,
  lesson_learned TEXT NOT NULL,
  prevention_strategy TEXT,
  applied BOOLEAN DEFAULT false,
  fan_id UUID REFERENCES fans(id),
  conversation_id UUID REFERENCES conversations(id)
);

-- ============================================================
-- 12. BOT INTELLIGENCE — Daily self-reflection & growth
-- ============================================================
CREATE TABLE IF NOT EXISTS bot_intelligence (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  date DATE UNIQUE NOT NULL DEFAULT CURRENT_DATE,
  total_learnings INTEGER DEFAULT 0,
  total_errors_analyzed INTEGER DEFAULT 0,
  total_improvements INTEGER DEFAULT 0,
  total_memories_created INTEGER DEFAULT 0,
  response_quality_avg REAL DEFAULT 50,
  intent_accuracy REAL DEFAULT 50,
  sentiment_accuracy REAL DEFAULT 50,
  sales_conversion_rate REAL DEFAULT 0,
  fan_satisfaction REAL DEFAULT 50,
  intelligence_score REAL DEFAULT 50,
  strengths JSONB DEFAULT '[]',
  weaknesses JSONB DEFAULT '[]',
  focus_areas JSONB DEFAULT '[]',
  daily_reflection TEXT,
  goals_for_tomorrow JSONB DEFAULT '[]',
  achieved_goals JSONB DEFAULT '[]'
);

-- ============================================================
-- 13. LIFE DATA — Owner life optimization tracking
-- ============================================================
CREATE TABLE IF NOT EXISTS life_data (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  category TEXT NOT NULL,
  metric TEXT NOT NULL,
  value JSONB NOT NULL,
  trend TEXT DEFAULT 'stable',
  insight TEXT,
  date DATE DEFAULT CURRENT_DATE NOT NULL,
  UNIQUE(category, metric, date)
);

-- ============================================================
-- 14. BOT STRATEGIES — Learned response strategies
-- ============================================================
CREATE TABLE IF NOT EXISTS bot_strategies (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  name TEXT UNIQUE NOT NULL,
  trigger_conditions JSONB NOT NULL,
  response_template TEXT NOT NULL,
  success_rate REAL DEFAULT 0.5,
  times_used INTEGER DEFAULT 0,
  times_succeeded INTEGER DEFAULT 0,
  category TEXT NOT NULL,
  fan_tiers TEXT[] DEFAULT '{}'
);

-- ============================================================
-- 15. BOT KNOWLEDGE — Facts the bot has learned
-- ============================================================
CREATE TABLE IF NOT EXISTS bot_knowledge (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  domain TEXT NOT NULL,
  topic TEXT NOT NULL,
  content TEXT NOT NULL,
  source TEXT,
  verified BOOLEAN DEFAULT false,
  relevance_score REAL DEFAULT 0.5,
  UNIQUE(domain, topic)
);

-- ============================================================
-- INDEXES
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_fans_phone ON fans(phone);
CREATE INDEX IF NOT EXISTS idx_fans_tier ON fans(tier);
CREATE INDEX IF NOT EXISTS idx_fans_vip_score ON fans(vip_score DESC);
CREATE INDEX IF NOT EXISTS idx_fans_last_message ON fans(last_message_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversations_fan ON conversations(fan_id);
CREATE INDEX IF NOT EXISTS idx_conversations_phone ON conversations(phone);
CREATE INDEX IF NOT EXISTS idx_conversations_created ON conversations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversations_intent ON conversations(intent);
CREATE INDEX IF NOT EXISTS idx_sales_fan ON bot_sales(fan_id);
CREATE INDEX IF NOT EXISTS idx_sales_status ON bot_sales(stripe_payment_status);
CREATE INDEX IF NOT EXISTS idx_analytics_date ON bot_analytics(date DESC);
CREATE INDEX IF NOT EXISTS idx_bookings_status ON bot_bookings(status);
CREATE INDEX IF NOT EXISTS idx_memory_category ON bot_memory(category);
CREATE INDEX IF NOT EXISTS idx_memory_subject ON bot_memory(subject);
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

-- ============================================================
-- ROW LEVEL SECURITY
-- ============================================================
ALTER TABLE fans ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_sales ENABLE ROW LEVEL SECURITY;
ALTER TABLE broadcasts ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_analytics ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_bookings ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_evolution ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_errors ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_intelligence ENABLE ROW LEVEL SECURITY;
ALTER TABLE life_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_strategies ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_knowledge ENABLE ROW LEVEL SECURITY;

-- Drop existing policies to avoid conflicts
DO $$ BEGIN
  DROP POLICY IF EXISTS "Service role fans" ON fans;
  DROP POLICY IF EXISTS "Service role conversations" ON conversations;
  DROP POLICY IF EXISTS "Service role sales" ON bot_sales;
  DROP POLICY IF EXISTS "Service role broadcasts" ON broadcasts;
  DROP POLICY IF EXISTS "Service role analytics" ON bot_analytics;
  DROP POLICY IF EXISTS "Service role bookings" ON bot_bookings;
  DROP POLICY IF EXISTS "Service role config" ON bot_config;
  DROP POLICY IF EXISTS "Anon read fans" ON fans;
  DROP POLICY IF EXISTS "Anon read conversations" ON conversations;
  DROP POLICY IF EXISTS "Anon read sales" ON bot_sales;
  DROP POLICY IF EXISTS "Anon read broadcasts" ON broadcasts;
  DROP POLICY IF EXISTS "Anon read analytics" ON bot_analytics;
  DROP POLICY IF EXISTS "Anon read bookings" ON bot_bookings;
  DROP POLICY IF EXISTS "Anon read config" ON bot_config;
  DROP POLICY IF EXISTS "srv_memory" ON bot_memory;
  DROP POLICY IF EXISTS "srv_evolution" ON bot_evolution;
  DROP POLICY IF EXISTS "srv_errors" ON bot_errors;
  DROP POLICY IF EXISTS "srv_intelligence" ON bot_intelligence;
  DROP POLICY IF EXISTS "srv_life" ON life_data;
  DROP POLICY IF EXISTS "srv_strategies" ON bot_strategies;
  DROP POLICY IF EXISTS "srv_knowledge" ON bot_knowledge;
  DROP POLICY IF EXISTS "anon_memory" ON bot_memory;
  DROP POLICY IF EXISTS "anon_evolution" ON bot_evolution;
  DROP POLICY IF EXISTS "anon_errors" ON bot_errors;
  DROP POLICY IF EXISTS "anon_intelligence" ON bot_intelligence;
  DROP POLICY IF EXISTS "anon_life" ON life_data;
  DROP POLICY IF EXISTS "anon_strategies" ON bot_strategies;
  DROP POLICY IF EXISTS "anon_knowledge" ON bot_knowledge;
END $$;

-- Service role: full access on ALL tables
CREATE POLICY "Service role fans" ON fans FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role conversations" ON conversations FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role sales" ON bot_sales FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role broadcasts" ON broadcasts FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role analytics" ON bot_analytics FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role bookings" ON bot_bookings FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role config" ON bot_config FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "srv_memory" ON bot_memory FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "srv_evolution" ON bot_evolution FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "srv_errors" ON bot_errors FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "srv_intelligence" ON bot_intelligence FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "srv_life" ON life_data FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "srv_strategies" ON bot_strategies FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "srv_knowledge" ON bot_knowledge FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Anon: read access for dashboard
CREATE POLICY "Anon read fans" ON fans FOR SELECT TO anon USING (true);
CREATE POLICY "Anon read conversations" ON conversations FOR SELECT TO anon USING (true);
CREATE POLICY "Anon read sales" ON bot_sales FOR SELECT TO anon USING (true);
CREATE POLICY "Anon read broadcasts" ON broadcasts FOR SELECT TO anon USING (true);
CREATE POLICY "Anon read analytics" ON bot_analytics FOR SELECT TO anon USING (true);
CREATE POLICY "Anon read bookings" ON bot_bookings FOR SELECT TO anon USING (true);
CREATE POLICY "Anon read config" ON bot_config FOR SELECT TO anon USING (true);
CREATE POLICY "anon_memory" ON bot_memory FOR SELECT TO anon USING (true);
CREATE POLICY "anon_evolution" ON bot_evolution FOR SELECT TO anon USING (true);
CREATE POLICY "anon_errors" ON bot_errors FOR SELECT TO anon USING (true);
CREATE POLICY "anon_intelligence" ON bot_intelligence FOR SELECT TO anon USING (true);
CREATE POLICY "anon_life" ON life_data FOR SELECT TO anon USING (true);
CREATE POLICY "anon_strategies" ON bot_strategies FOR SELECT TO anon USING (true);
CREATE POLICY "anon_knowledge" ON bot_knowledge FOR SELECT TO anon USING (true);

-- ============================================================
-- TRIGGERS
-- ============================================================
CREATE OR REPLACE FUNCTION update_fans_updated_at()
RETURNS TRIGGER AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS fans_updated_at ON fans;
CREATE TRIGGER fans_updated_at BEFORE UPDATE ON fans FOR EACH ROW EXECUTE FUNCTION update_fans_updated_at();

CREATE OR REPLACE FUNCTION update_bookings_updated_at()
RETURNS TRIGGER AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bookings_updated_at ON bot_bookings;
CREATE TRIGGER bookings_updated_at BEFORE UPDATE ON bot_bookings FOR EACH ROW EXECUTE FUNCTION update_bookings_updated_at();

CREATE OR REPLACE FUNCTION update_memory_timestamp()
RETURNS TRIGGER AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS memory_updated ON bot_memory;
CREATE TRIGGER memory_updated BEFORE UPDATE ON bot_memory FOR EACH ROW EXECUTE FUNCTION update_memory_timestamp();

DROP TRIGGER IF EXISTS strategies_updated ON bot_strategies;
CREATE TRIGGER strategies_updated BEFORE UPDATE ON bot_strategies FOR EACH ROW EXECUTE FUNCTION update_memory_timestamp();

DROP TRIGGER IF EXISTS knowledge_updated ON bot_knowledge;
CREATE TRIGGER knowledge_updated BEFORE UPDATE ON bot_knowledge FOR EACH ROW EXECUTE FUNCTION update_memory_timestamp();

-- ============================================================
-- DEFAULT CONFIG
-- ============================================================
INSERT INTO bot_config (key, value) VALUES
  ('personality', '{"name":"TTAO Bot","style":"cool, authentic, streetwise but professional","language":"de","emoji_level":"medium"}'),
  ('auto_reply', '{"enabled":true,"max_delay_ms":2000}'),
  ('sales_mode', '{"enabled":true,"aggressive_level":"medium","products":["beats","merch","sessions","inner_circle"]}'),
  ('working_hours', '{"start":"08:00","end":"23:00","timezone":"Europe/Berlin","auto_reply_outside":true}'),
  ('vip_thresholds', '{"casual":10,"engaged":30,"superfan":60,"vip":80,"whale":95}'),
  ('daily_report', '{"enabled":true,"time":"09:00","phone":""}')
ON CONFLICT (key) DO NOTHING;

-- ============================================================
-- SEED KNOWLEDGE
-- ============================================================
INSERT INTO bot_knowledge (domain, topic, content, verified) VALUES
  ('longevity', 'caloric_restriction', 'Kalorienrestriktion (20-30% weniger) verlängert nachweislich die Lebensspanne. Mechanismus: Aktivierung von Sirtuinen, AMPK und Autophagie.', true),
  ('longevity', 'rapamycin', 'Rapamycin hemmt mTOR und verlängert die Lebensspanne in Mäusen um 9-14%. Klinische Trials laufen.', true),
  ('longevity', 'senolytics', 'Senolytika (Dasatinib+Quercetin, Fisetin) eliminieren seneszente Zellen und verjüngen Gewebe.', true),
  ('longevity', 'nad_boosters', 'NAD+ sinkt mit dem Alter. NMN und NR erhöhen NAD+-Spiegel und verbessern mitochondriale Funktion.', true),
  ('longevity', 'metformin', 'Metformin aktiviert AMPK, reduziert Krebs-Risiko um 30%. TAME-Studie läuft als erstes Anti-Aging-Trial.', true),
  ('health', 'sleep_optimization', 'Optimaler Schlaf: 7-8h, konstante Zeiten, 18C, kein Blaulicht 2h vor Schlaf, Magnesium Glycinat.', true),
  ('health', 'exercise_longevity', 'Zone 2 Training (3-4h/Woche) + 1-2x Krafttraining maximiert Langlebigkeit. VO2max ist stärkster Mortalitäts-Prädiktor.', true),
  ('health', 'fasting', 'Intermittierendes Fasten (16:8) aktiviert Autophagie, verbessert Insulinsensitivität, reduziert Entzündungen.', true),
  ('science', 'consciousness', 'IIT: Bewusstsein = Phi (integrierte Information). Global Workspace Theory: Bewusstsein als Broadcasting-System.', true),
  ('science', 'quantum_biology', 'Quanteneffekte in der Biologie: Photosynthese nutzt Quantenkohärenz, Vogelnavigation nutzt Quantenverschränkung.', true)
ON CONFLICT (domain, topic) DO NOTHING;

-- ============================================================
-- DONE! All 15 tables created with RLS, indexes, triggers, and seed data.
-- ============================================================
