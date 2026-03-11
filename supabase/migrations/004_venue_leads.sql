-- ============================================================
-- VENUE LEADS TABLE — Live Piano Outreach System
-- Run in Supabase SQL Editor:
-- https://supabase.com/dashboard/project/pwdhxarvemcgkhhnvbng/sql/new
-- ============================================================

CREATE TABLE IF NOT EXISTS venue_leads (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  name TEXT NOT NULL,
  venue_type TEXT DEFAULT 'other',
  region TEXT DEFAULT 'munich',
  email TEXT,
  phone TEXT,
  website TEXT,
  notes TEXT,
  status TEXT DEFAULT 'new',
  outreach_status TEXT DEFAULT 'pending',
  outreach_message TEXT,
  outreach_sent_at TIMESTAMPTZ,
  approval_requested_at TIMESTAMPTZ,
  response TEXT,
  response_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_venue_region ON venue_leads(region);
CREATE INDEX IF NOT EXISTS idx_venue_status ON venue_leads(status);
CREATE INDEX IF NOT EXISTS idx_venue_outreach ON venue_leads(outreach_status);

ALTER TABLE venue_leads ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  DROP POLICY IF EXISTS "srv_venues" ON venue_leads;
  DROP POLICY IF EXISTS "anon_venues" ON venue_leads;
END $$;

CREATE POLICY "srv_venues" ON venue_leads FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "anon_venues" ON venue_leads FOR ALL TO anon USING (true) WITH CHECK (true);

-- Auto-update timestamp
CREATE OR REPLACE FUNCTION update_venue_updated_at()
RETURNS TRIGGER AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS venue_updated_at ON venue_leads;
CREATE TRIGGER venue_updated_at BEFORE UPDATE ON venue_leads FOR EACH ROW EXECUTE FUNCTION update_venue_updated_at();
