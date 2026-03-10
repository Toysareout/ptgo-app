-- ============================================================
-- MEHRDIMENSIONALER BOT — Database Schema
-- 7 Dimensions: Fan-Manager, Sales, Content, Analytics,
--               Community, Booking, Mood-Reader
-- ============================================================

-- 1. FANS — Every person who ever contacts the bot
CREATE TABLE IF NOT EXISTS fans (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,

  -- Identity
  phone TEXT UNIQUE NOT NULL,              -- WhatsApp number (E.164)
  name TEXT,                                -- Extracted or provided name
  instagram TEXT,                           -- IG handle if shared
  email TEXT,                               -- Email if shared

  -- Intelligence scores (0-100, auto-calculated)
  engagement_score INTEGER DEFAULT 0,       -- How active they are
  loyalty_score INTEGER DEFAULT 0,          -- How long they've been around
  purchase_score INTEGER DEFAULT 0,         -- How much they spend
  vip_score INTEGER DEFAULT 0,              -- Combined score

  -- Classification
  tier TEXT DEFAULT 'new' NOT NULL,         -- new, casual, engaged, superfan, vip, whale
  mood TEXT DEFAULT 'neutral',              -- positive, neutral, negative, excited, angry
  language TEXT DEFAULT 'de',               -- de, en, tr, ar (auto-detected)

  -- Stats
  total_messages INTEGER DEFAULT 0,
  total_purchases INTEGER DEFAULT 0,
  total_spent_cents INTEGER DEFAULT 0,
  last_message_at TIMESTAMPTZ,
  last_purchase_at TIMESTAMPTZ,
  first_contact_at TIMESTAMPTZ DEFAULT now(),

  -- Bot state
  conversation_state TEXT DEFAULT 'idle',   -- idle, onboarding, shopping, booking, support, feedback
  context_data JSONB DEFAULT '{}',          -- Current conversation context

  -- Preferences (learned over time)
  interests TEXT[] DEFAULT '{}',            -- beats, merch, sessions, shows, collabs
  preferred_time TEXT,                      -- When they usually write
  opt_out BOOLEAN DEFAULT false,            -- Unsubscribed from broadcasts

  CONSTRAINT valid_tier CHECK (tier IN ('new', 'casual', 'engaged', 'superfan', 'vip', 'whale')),
  CONSTRAINT valid_mood CHECK (mood IN ('positive', 'neutral', 'negative', 'excited', 'angry'))
);

-- 2. CONVERSATIONS — Every message exchange
CREATE TABLE IF NOT EXISTS conversations (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,

  fan_id UUID REFERENCES fans(id) ON DELETE CASCADE,
  phone TEXT NOT NULL,

  -- Message data
  direction TEXT NOT NULL,                  -- inbound, outbound
  message_type TEXT DEFAULT 'text',         -- text, image, audio, video, document, location, button
  body TEXT,                                -- Message content
  media_url TEXT,                           -- Media attachment URL

  -- AI analysis (filled by bot brain)
  intent TEXT,                              -- greeting, question, purchase, complaint, booking, feedback, other
  sentiment REAL,                           -- -1.0 to 1.0
  topics TEXT[],                            -- detected topics
  entities JSONB DEFAULT '{}',              -- extracted entities (product names, dates, etc.)

  -- Bot response metadata
  response_strategy TEXT,                   -- how the bot decided to respond
  response_time_ms INTEGER,                 -- how fast the bot responded
  ai_model TEXT,                            -- which AI model was used
  ai_tokens_used INTEGER DEFAULT 0,

  CONSTRAINT valid_direction CHECK (direction IN ('inbound', 'outbound'))
);

-- 3. SALES — Every transaction
CREATE TABLE IF NOT EXISTS bot_sales (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,

  fan_id UUID REFERENCES fans(id) ON DELETE SET NULL,
  phone TEXT NOT NULL,

  -- Product
  item_type TEXT NOT NULL,                  -- drop, merch, ticket, session, beat, inner_circle
  item_name TEXT NOT NULL,
  price_cents INTEGER NOT NULL,
  currency TEXT DEFAULT 'eur',

  -- Stripe
  stripe_session_id TEXT,
  stripe_payment_status TEXT DEFAULT 'pending', -- pending, paid, failed, refunded
  checkout_url TEXT,

  -- Tracking
  source TEXT DEFAULT 'bot',                -- bot, website, direct
  conversation_id UUID REFERENCES conversations(id),

  CONSTRAINT valid_payment CHECK (stripe_payment_status IN ('pending', 'paid', 'failed', 'refunded'))
);

-- 4. BROADCASTS — Scheduled mass messages
CREATE TABLE IF NOT EXISTS broadcasts (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,

  -- Content
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  media_url TEXT,

  -- Targeting
  target_tier TEXT[] DEFAULT '{new,casual,engaged,superfan,vip,whale}',
  target_interests TEXT[],
  target_mood TEXT[],

  -- Schedule
  scheduled_at TIMESTAMPTZ,
  sent_at TIMESTAMPTZ,
  status TEXT DEFAULT 'draft',              -- draft, scheduled, sending, sent, cancelled

  -- Stats
  total_recipients INTEGER DEFAULT 0,
  total_delivered INTEGER DEFAULT 0,
  total_read INTEGER DEFAULT 0,
  total_replied INTEGER DEFAULT 0,
  total_conversions INTEGER DEFAULT 0,

  CONSTRAINT valid_broadcast_status CHECK (status IN ('draft', 'scheduled', 'sending', 'sent', 'cancelled'))
);

-- 5. BOT_ANALYTICS — Daily aggregated stats
CREATE TABLE IF NOT EXISTS bot_analytics (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  date DATE UNIQUE NOT NULL DEFAULT CURRENT_DATE,

  -- Volume
  total_messages_in INTEGER DEFAULT 0,
  total_messages_out INTEGER DEFAULT 0,
  unique_fans INTEGER DEFAULT 0,
  new_fans INTEGER DEFAULT 0,

  -- Engagement
  avg_sentiment REAL DEFAULT 0,
  top_intents JSONB DEFAULT '{}',
  top_topics JSONB DEFAULT '{}',

  -- Revenue
  total_sales INTEGER DEFAULT 0,
  total_revenue_cents INTEGER DEFAULT 0,
  checkout_links_sent INTEGER DEFAULT 0,
  conversion_rate REAL DEFAULT 0,

  -- Performance
  avg_response_time_ms INTEGER DEFAULT 0,
  total_ai_tokens INTEGER DEFAULT 0,
  total_ai_cost_cents INTEGER DEFAULT 0,

  -- Bookings
  booking_requests INTEGER DEFAULT 0,
  bookings_confirmed INTEGER DEFAULT 0
);

-- 6. BOOKINGS — Show/session/feature requests
CREATE TABLE IF NOT EXISTS bot_bookings (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,

  fan_id UUID REFERENCES fans(id) ON DELETE SET NULL,
  phone TEXT NOT NULL,

  -- Details
  booking_type TEXT NOT NULL,               -- show, feature, session, interview, collab
  description TEXT,
  preferred_date TIMESTAMPTZ,
  budget_cents INTEGER,
  location TEXT,

  -- Status
  status TEXT DEFAULT 'new',                -- new, reviewing, negotiating, confirmed, declined, completed
  notes TEXT,

  CONSTRAINT valid_booking_type CHECK (booking_type IN ('show', 'feature', 'session', 'interview', 'collab')),
  CONSTRAINT valid_booking_status CHECK (status IN ('new', 'reviewing', 'negotiating', 'confirmed', 'declined', 'completed'))
);

-- 7. BOT_CONFIG — Runtime configuration
CREATE TABLE IF NOT EXISTS bot_config (
  key TEXT PRIMARY KEY,
  value JSONB NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- Insert default config
INSERT INTO bot_config (key, value) VALUES
  ('personality', '{"name": "TTAO Bot", "style": "cool, authentic, streetwise but professional", "language": "de", "emoji_level": "medium"}'),
  ('auto_reply', '{"enabled": true, "max_delay_ms": 2000}'),
  ('sales_mode', '{"enabled": true, "aggressive_level": "medium", "products": ["beats", "merch", "sessions", "inner_circle"]}'),
  ('working_hours', '{"start": "08:00", "end": "23:00", "timezone": "Europe/Berlin", "auto_reply_outside": true}'),
  ('vip_thresholds', '{"casual": 10, "engaged": 30, "superfan": 60, "vip": 80, "whale": 95}'),
  ('daily_report', '{"enabled": true, "time": "09:00", "phone": ""}')
ON CONFLICT (key) DO NOTHING;

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

-- ============================================================
-- RLS POLICIES
-- ============================================================
ALTER TABLE fans ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_sales ENABLE ROW LEVEL SECURITY;
ALTER TABLE broadcasts ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_analytics ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_bookings ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_config ENABLE ROW LEVEL SECURITY;

-- Service role full access on all tables
CREATE POLICY "Service role fans" ON fans FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role conversations" ON conversations FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role sales" ON bot_sales FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role broadcasts" ON broadcasts FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role analytics" ON bot_analytics FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role bookings" ON bot_bookings FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role config" ON bot_config FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ============================================================
-- TRIGGERS
-- ============================================================
CREATE OR REPLACE FUNCTION update_fans_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER fans_updated_at BEFORE UPDATE ON fans
  FOR EACH ROW EXECUTE FUNCTION update_fans_updated_at();

CREATE OR REPLACE FUNCTION update_bookings_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER bookings_updated_at BEFORE UPDATE ON bot_bookings
  FOR EACH ROW EXECUTE FUNCTION update_bookings_updated_at();

-- ============================================================
COMMENT ON TABLE fans IS 'Fan profiles with intelligence scoring';
COMMENT ON TABLE conversations IS 'All WhatsApp message exchanges';
COMMENT ON TABLE bot_sales IS 'Sales triggered through the bot';
COMMENT ON TABLE broadcasts IS 'Scheduled mass messages to fans';
COMMENT ON TABLE bot_analytics IS 'Daily aggregated bot statistics';
COMMENT ON TABLE bot_bookings IS 'Show/feature/session booking requests';
COMMENT ON TABLE bot_config IS 'Runtime bot configuration';
