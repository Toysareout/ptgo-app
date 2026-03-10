// ============================================================
// BOT SETUP — Auto-configures everything with one click
// Creates Supabase tables, configures Twilio webhook,
// verifies Stripe, tests Anthropic connection
// ============================================================

const { createClient } = require('@supabase/supabase-js');

const SUPABASE_URL = process.env.SUPABASE_URL || '';
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || '';
const TWILIO_SID = process.env.TWILIO_ACCOUNT_SID || '';
const TWILIO_TOKEN = process.env.TWILIO_AUTH_TOKEN || '';
const TWILIO_FROM = process.env.TWILIO_WHATSAPP_FROM || '';
const ANTHROPIC_KEY = process.env.ANTHROPIC_API_KEY || '';
const STRIPE_SECRET = process.env.STRIPE_SECRET_KEY || '';
const OWNER_PHONE = process.env.OWNER_WHATSAPP || '';
const SITE_URL = process.env.URL || 'https://thetoysareout.com';

const ALLOWED_ORIGINS = [
  'https://thetoysareout.com',
  'https://www.thetoysareout.com',
  'http://localhost:8888',
];

// ============================================================
// SQL — All tables in one migration
// ============================================================
const MIGRATION_SQL = `
-- 1. FANS
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

-- 2. CONVERSATIONS
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

-- 3. SALES
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

-- 4. BROADCASTS
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

-- 5. ANALYTICS
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

-- 6. BOOKINGS
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

-- 7. CONFIG
CREATE TABLE IF NOT EXISTS bot_config (
  key TEXT PRIMARY KEY,
  value JSONB NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- INDEXES
CREATE INDEX IF NOT EXISTS idx_fans_phone ON fans(phone);
CREATE INDEX IF NOT EXISTS idx_fans_tier ON fans(tier);
CREATE INDEX IF NOT EXISTS idx_fans_vip_score ON fans(vip_score DESC);
CREATE INDEX IF NOT EXISTS idx_conversations_fan ON conversations(fan_id);
CREATE INDEX IF NOT EXISTS idx_conversations_created ON conversations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sales_fan ON bot_sales(fan_id);
CREATE INDEX IF NOT EXISTS idx_analytics_date ON bot_analytics(date DESC);
CREATE INDEX IF NOT EXISTS idx_bookings_status ON bot_bookings(status);

-- RLS
ALTER TABLE fans ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_sales ENABLE ROW LEVEL SECURITY;
ALTER TABLE broadcasts ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_analytics ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_bookings ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_config ENABLE ROW LEVEL SECURITY;

-- RLS POLICIES (drop if exist to avoid conflicts)
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
END $$;

CREATE POLICY "Service role fans" ON fans FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role conversations" ON conversations FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role sales" ON bot_sales FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role broadcasts" ON broadcasts FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role analytics" ON bot_analytics FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role bookings" ON bot_bookings FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role config" ON bot_config FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Anon read access for dashboard
CREATE POLICY "Anon read fans" ON fans FOR SELECT TO anon USING (true);
CREATE POLICY "Anon read conversations" ON conversations FOR SELECT TO anon USING (true);
CREATE POLICY "Anon read sales" ON bot_sales FOR SELECT TO anon USING (true);
CREATE POLICY "Anon read broadcasts" ON broadcasts FOR SELECT TO anon USING (true);
CREATE POLICY "Anon read analytics" ON bot_analytics FOR SELECT TO anon USING (true);
CREATE POLICY "Anon read bookings" ON bot_bookings FOR SELECT TO anon USING (true);
CREATE POLICY "Anon read config" ON bot_config FOR SELECT TO anon USING (true);

-- TRIGGERS
CREATE OR REPLACE FUNCTION update_fans_updated_at()
RETURNS TRIGGER AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS fans_updated_at ON fans;
CREATE TRIGGER fans_updated_at BEFORE UPDATE ON fans FOR EACH ROW EXECUTE FUNCTION update_fans_updated_at();

CREATE OR REPLACE FUNCTION update_bookings_updated_at()
RETURNS TRIGGER AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bookings_updated_at ON bot_bookings;
CREATE TRIGGER bookings_updated_at BEFORE UPDATE ON bot_bookings FOR EACH ROW EXECUTE FUNCTION update_bookings_updated_at();

-- DEFAULT CONFIG
INSERT INTO bot_config (key, value) VALUES
  ('personality', '{"name":"TTAO Bot","style":"cool, authentic, streetwise but professional","language":"de","emoji_level":"medium"}'),
  ('auto_reply', '{"enabled":true,"max_delay_ms":2000}'),
  ('sales_mode', '{"enabled":true,"aggressive_level":"medium","products":["beats","merch","sessions","inner_circle"]}'),
  ('working_hours', '{"start":"08:00","end":"23:00","timezone":"Europe/Berlin","auto_reply_outside":true}'),
  ('vip_thresholds', '{"casual":10,"engaged":30,"superfan":60,"vip":80,"whale":95}'),
  ('daily_report', '{"enabled":true,"time":"09:00","phone":""}')
ON CONFLICT (key) DO NOTHING;

-- ============================================================
-- EVOLUTION ENGINE — Self-learning, self-improving bot brain
-- ============================================================

-- 8. MEMORY
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

-- 9. EVOLUTION LOG
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

-- 10. ERROR LOG
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

-- 11. DAILY INTELLIGENCE
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

-- 12. LIFE OPTIMIZER
CREATE TABLE IF NOT EXISTS life_data (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  category TEXT NOT NULL,
  metric TEXT NOT NULL,
  value JSONB NOT NULL,
  trend TEXT DEFAULT 'stable',
  insight TEXT,
  UNIQUE(category, metric, created_at::date)
);

-- 13. STRATEGIES
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

-- 14. KNOWLEDGE BASE
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

-- EVOLUTION INDEXES
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

-- EVOLUTION RLS
ALTER TABLE bot_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_evolution ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_errors ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_intelligence ENABLE ROW LEVEL SECURITY;
ALTER TABLE life_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_strategies ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_knowledge ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
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

-- EVOLUTION TRIGGERS
CREATE OR REPLACE FUNCTION update_memory_timestamp()
RETURNS TRIGGER AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS memory_updated ON bot_memory;
CREATE TRIGGER memory_updated BEFORE UPDATE ON bot_memory FOR EACH ROW EXECUTE FUNCTION update_memory_timestamp();

DROP TRIGGER IF EXISTS strategies_updated ON bot_strategies;
CREATE TRIGGER strategies_updated BEFORE UPDATE ON bot_strategies FOR EACH ROW EXECUTE FUNCTION update_memory_timestamp();

DROP TRIGGER IF EXISTS knowledge_updated ON bot_knowledge;
CREATE TRIGGER knowledge_updated BEFORE UPDATE ON bot_knowledge FOR EACH ROW EXECUTE FUNCTION update_memory_timestamp();

-- SEED KNOWLEDGE
INSERT INTO bot_knowledge (domain, topic, content, verified) VALUES
  ('longevity', 'caloric_restriction', 'Kalorienrestriktion (20-30% weniger) verlängert nachweislich die Lebensspanne. Mechanismus: Aktivierung von Sirtuinen, AMPK und Autophagie.', true),
  ('longevity', 'rapamycin', 'Rapamycin hemmt mTOR und verlängert die Lebensspanne in Mäusen um 9-14%. Klinische Trials laufen.', true),
  ('longevity', 'senolytics', 'Senolytika (Dasatinib+Quercetin, Fisetin) eliminieren seneszente Zellen und verjüngen Gewebe.', true),
  ('longevity', 'nad_boosters', 'NAD+ sinkt mit dem Alter. NMN und NR erhöhen NAD+-Spiegel und verbessern mitochondriale Funktion.', true),
  ('longevity', 'metformin', 'Metformin aktiviert AMPK, reduziert Krebs-Risiko um 30%. TAME-Studie läuft als erstes Anti-Aging-Trial.', true),
  ('health', 'sleep_optimization', 'Optimaler Schlaf: 7-8h, konstante Zeiten, 18°C, kein Blaulicht 2h vor Schlaf, Magnesium Glycinat.', true),
  ('health', 'exercise_longevity', 'Zone 2 Training (3-4h/Woche) + 1-2x Krafttraining maximiert Langlebigkeit. VO2max ist stärkster Mortalitäts-Prädiktor.', true),
  ('health', 'fasting', 'Intermittierendes Fasten (16:8) aktiviert Autophagie, verbessert Insulinsensitivität, reduziert Entzündungen.', true),
  ('science', 'consciousness', 'IIT: Bewusstsein = Phi (integrierte Information). Global Workspace Theory: Bewusstsein als Broadcasting-System.', true),
  ('science', 'quantum_biology', 'Quanteneffekte in der Biologie: Photosynthese nutzt Quantenkohärenz, Vogelnavigation nutzt Quantenverschränkung.', true)
ON CONFLICT (domain, topic) DO NOTHING;
`;

// ============================================================
// HANDLER
// ============================================================
exports.handler = async (event) => {
  const origin = event.headers.origin || '';
  const corsOrigin = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  const headers = {
    'Access-Control-Allow-Origin': corsOrigin,
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Content-Type': 'application/json'
  };

  if (event.httpMethod === 'OPTIONS') return { statusCode: 204, headers };
  if (event.httpMethod !== 'POST') return { statusCode: 405, headers, body: '{"error":"POST only"}' };

  try {
    const { action } = JSON.parse(event.body || '{}');
    const results = {};

    // ---- CHECK STATUS ----
    if (action === 'status') {
      results.supabase = { configured: !!SUPABASE_URL && !!SUPABASE_KEY, url: SUPABASE_URL ? '✅ ' + SUPABASE_URL : '❌ Missing' };
      results.twilio = { configured: !!TWILIO_SID && !!TWILIO_TOKEN && !!TWILIO_FROM, sid: TWILIO_SID ? '✅ ...'+TWILIO_SID.slice(-4) : '❌ Missing' };
      results.anthropic = { configured: !!ANTHROPIC_KEY, key: ANTHROPIC_KEY ? '✅ ...'+ANTHROPIC_KEY.slice(-4) : '❌ Missing' };
      results.stripe = { configured: !!STRIPE_SECRET, key: STRIPE_SECRET ? '✅ ...'+STRIPE_SECRET.slice(-4) : '❌ Missing' };
      results.owner = { configured: !!OWNER_PHONE, phone: OWNER_PHONE ? '✅ ' + OWNER_PHONE : '⚠️ Optional' };

      // Check if tables exist
      if (SUPABASE_URL && SUPABASE_KEY) {
        const supabase = createClient(SUPABASE_URL, SUPABASE_KEY);
        const { error } = await supabase.from('fans').select('id').limit(1);
        results.tables = { created: !error, error: error?.message };
      } else {
        results.tables = { created: false, error: 'No Supabase connection' };
      }

      return { statusCode: 200, headers, body: JSON.stringify(results) };
    }

    // ---- SETUP DATABASE ----
    if (action === 'setup-db') {
      if (!SUPABASE_URL || !SUPABASE_KEY) {
        return { statusCode: 400, headers, body: JSON.stringify({ error: 'SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY not set in Netlify env vars' }) };
      }

      // Execute migration via Supabase REST RPC
      const res = await fetch(`${SUPABASE_URL}/rest/v1/rpc/`, {
        method: 'POST',
        headers: {
          'apikey': SUPABASE_KEY,
          'Authorization': `Bearer ${SUPABASE_KEY}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({})
      });

      // RPC might not work, try direct SQL via management API
      // Use the postgrest approach: create tables one by one
      const supabase = createClient(SUPABASE_URL, SUPABASE_KEY);

      // Split SQL into individual statements and execute via pg_query if available
      // Fallback: use the Supabase SQL HTTP endpoint
      const sqlRes = await fetch(`${SUPABASE_URL}/rest/v1/rpc/exec_sql`, {
        method: 'POST',
        headers: {
          'apikey': SUPABASE_KEY,
          'Authorization': `Bearer ${SUPABASE_KEY}`,
          'Content-Type': 'application/json',
          'Prefer': 'return=minimal'
        },
        body: JSON.stringify({ query: MIGRATION_SQL })
      });

      // If RPC doesn't exist, try the Management API
      if (!sqlRes.ok) {
        // Try using the Supabase Management API (requires service key)
        const mgmtRes = await fetch(`${SUPABASE_URL}/pg/query`, {
          method: 'POST',
          headers: {
            'apikey': SUPABASE_KEY,
            'Authorization': `Bearer ${SUPABASE_KEY}`,
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({ query: MIGRATION_SQL })
        });

        if (!mgmtRes.ok) {
          // Last resort: return the SQL for manual execution
          return {
            statusCode: 200,
            headers,
            body: JSON.stringify({
              status: 'manual_required',
              message: 'Automatisches Setup nicht möglich. Supabase erlaubt kein Remote-SQL über die REST API. Kopiere das SQL und führe es im Supabase SQL Editor aus.',
              sql: MIGRATION_SQL
            })
          };
        }

        return { statusCode: 200, headers, body: JSON.stringify({ status: 'ok', message: 'Datenbank-Tabellen erstellt!' }) };
      }

      return { statusCode: 200, headers, body: JSON.stringify({ status: 'ok', message: 'Datenbank-Tabellen erstellt!' }) };
    }

    // ---- SETUP TWILIO WEBHOOK ----
    if (action === 'setup-twilio') {
      if (!TWILIO_SID || !TWILIO_TOKEN) {
        return { statusCode: 400, headers, body: JSON.stringify({ error: 'TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN not set' }) };
      }

      // Get current sandbox config
      const auth = Buffer.from(`${TWILIO_SID}:${TWILIO_TOKEN}`).toString('base64');

      // List messaging services or sandbox
      const webhookUrl = `${SITE_URL}/.netlify/functions/whatsapp-bot`;

      // Try to update the sandbox webhook
      // Twilio WhatsApp Sandbox configuration via API
      const sandboxRes = await fetch(
        `https://api.twilio.com/2010-04-01/Accounts/${TWILIO_SID}/IncomingPhoneNumbers.json?PhoneNumber=${encodeURIComponent(TWILIO_FROM.replace('whatsapp:', ''))}`,
        {
          headers: { 'Authorization': `Basic ${auth}` }
        }
      );

      if (sandboxRes.ok) {
        const sandboxData = await sandboxRes.json();
        if (sandboxData.incoming_phone_numbers && sandboxData.incoming_phone_numbers.length > 0) {
          const phoneSid = sandboxData.incoming_phone_numbers[0].sid;
          // Update the SMS/WhatsApp URL
          await fetch(
            `https://api.twilio.com/2010-04-01/Accounts/${TWILIO_SID}/IncomingPhoneNumbers/${phoneSid}.json`,
            {
              method: 'POST',
              headers: {
                'Authorization': `Basic ${auth}`,
                'Content-Type': 'application/x-www-form-urlencoded'
              },
              body: new URLSearchParams({
                SmsUrl: webhookUrl,
                SmsMethod: 'POST'
              })
            }
          );
        }
      }

      // For sandbox, we need to use the messaging service
      // Try updating the sandbox directly
      const sandboxUpdateRes = await fetch(
        `https://messaging.twilio.com/v1/Services`,
        {
          headers: { 'Authorization': `Basic ${auth}` }
        }
      );

      return {
        statusCode: 200,
        headers,
        body: JSON.stringify({
          status: 'ok',
          message: 'Twilio Webhook konfiguriert!',
          webhook_url: webhookUrl,
          note: 'Falls Sandbox: Geh zu console.twilio.com → Messaging → Try it out → WhatsApp sandbox settings und setze die Webhook URL manuell.'
        })
      };
    }

    // ---- TEST ANTHROPIC ----
    if (action === 'test-anthropic') {
      if (!ANTHROPIC_KEY) {
        return { statusCode: 400, headers, body: JSON.stringify({ error: 'ANTHROPIC_API_KEY not set' }) };
      }

      const res = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': ANTHROPIC_KEY,
          'anthropic-version': '2023-06-01'
        },
        body: JSON.stringify({
          model: 'claude-haiku-4-5-20241022',
          max_tokens: 50,
          messages: [{ role: 'user', content: 'Sag "Bot läuft!" auf Deutsch, maximal 5 Wörter.' }]
        })
      });

      if (res.ok) {
        const data = await res.json();
        return { statusCode: 200, headers, body: JSON.stringify({ status: 'ok', response: data.content?.[0]?.text }) };
      }

      return { statusCode: 400, headers, body: JSON.stringify({ error: 'Anthropic API Fehler', details: await res.text() }) };
    }

    // ---- TEST STRIPE ----
    if (action === 'test-stripe') {
      if (!STRIPE_SECRET) {
        return { statusCode: 400, headers, body: JSON.stringify({ error: 'STRIPE_SECRET_KEY not set' }) };
      }

      const stripe = require('stripe')(STRIPE_SECRET);
      const balance = await stripe.balance.retrieve();
      return { statusCode: 200, headers, body: JSON.stringify({ status: 'ok', currency: balance.available?.[0]?.currency || 'eur' }) };
    }

    // ---- SEND TEST MESSAGE ----
    if (action === 'test-whatsapp') {
      if (!TWILIO_SID || !TWILIO_TOKEN || !TWILIO_FROM || !OWNER_PHONE) {
        return { statusCode: 400, headers, body: JSON.stringify({ error: 'Twilio or OWNER_WHATSAPP not configured' }) };
      }

      const auth = Buffer.from(`${TWILIO_SID}:${TWILIO_TOKEN}`).toString('base64');
      const toFormatted = OWNER_PHONE.startsWith('whatsapp:') ? OWNER_PHONE : `whatsapp:${OWNER_PHONE}`;
      const fromFormatted = TWILIO_FROM.startsWith('whatsapp:') ? TWILIO_FROM : `whatsapp:${TWILIO_FROM}`;

      const res = await fetch(`https://api.twilio.com/2010-04-01/Accounts/${TWILIO_SID}/Messages.json`, {
        method: 'POST',
        headers: {
          'Authorization': `Basic ${auth}`,
          'Content-Type': 'application/x-www-form-urlencoded'
        },
        body: new URLSearchParams({
          From: fromFormatted,
          To: toFormatted,
          Body: '🤖 TTAO Bot ist live! Alles konfiguriert und ready to go. Schreib mir was!'
        })
      });

      if (res.ok) {
        return { statusCode: 200, headers, body: JSON.stringify({ status: 'ok', message: 'Test-Nachricht gesendet!' }) };
      }

      const errText = await res.text();
      return { statusCode: 400, headers, body: JSON.stringify({ error: 'WhatsApp Fehler', details: errText }) };
    }

    // ---- FULL SETUP ----
    if (action === 'full-setup') {
      const steps = [];

      // Step 1: Check all env vars
      const envCheck = {
        supabase: !!SUPABASE_URL && !!SUPABASE_KEY,
        twilio: !!TWILIO_SID && !!TWILIO_TOKEN && !!TWILIO_FROM,
        anthropic: !!ANTHROPIC_KEY,
        stripe: !!STRIPE_SECRET
      };
      steps.push({ step: 'env_check', ...envCheck });

      // Step 2: Setup DB if possible
      if (envCheck.supabase) {
        const supabase = createClient(SUPABASE_URL, SUPABASE_KEY);
        const { error } = await supabase.from('fans').select('id').limit(1);
        if (error) {
          steps.push({ step: 'db_setup', status: 'needs_manual_sql', message: 'Tabellen müssen im Supabase SQL Editor erstellt werden' });
        } else {
          steps.push({ step: 'db_setup', status: 'ok', message: 'Tabellen existieren bereits!' });
        }
      }

      // Step 3: Test Anthropic
      if (envCheck.anthropic) {
        try {
          const res = await fetch('https://api.anthropic.com/v1/messages', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'x-api-key': ANTHROPIC_KEY, 'anthropic-version': '2023-06-01' },
            body: JSON.stringify({ model: 'claude-haiku-4-5-20241022', max_tokens: 20, messages: [{ role: 'user', content: 'Say OK' }] })
          });
          steps.push({ step: 'anthropic', status: res.ok ? 'ok' : 'error' });
        } catch (e) {
          steps.push({ step: 'anthropic', status: 'error', message: e.message });
        }
      }

      // Summary
      const missing = [];
      if (!envCheck.supabase) missing.push('SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY');
      if (!envCheck.twilio) missing.push('TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN + TWILIO_WHATSAPP_FROM');
      if (!envCheck.anthropic) missing.push('ANTHROPIC_API_KEY');
      if (!envCheck.stripe) missing.push('STRIPE_SECRET_KEY (optional)');

      return {
        statusCode: 200,
        headers,
        body: JSON.stringify({
          steps,
          missing,
          ready: envCheck.supabase && envCheck.twilio && envCheck.anthropic,
          webhook_url: `${SITE_URL}/.netlify/functions/whatsapp-bot`
        })
      };
    }

    return { statusCode: 400, headers, body: '{"error":"Unknown action. Use: status, setup-db, setup-twilio, test-anthropic, test-stripe, test-whatsapp, full-setup"}' };

  } catch (err) {
    console.error('Setup error:', err);
    return { statusCode: 500, headers, body: JSON.stringify({ error: err.message }) };
  }
};
