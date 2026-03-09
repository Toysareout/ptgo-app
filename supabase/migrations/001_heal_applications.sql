-- PTGO Heal Applications Table
-- Stores session applications from heal.html
-- Status workflow: new → contacted → call_scheduled → booked → completed → follow_up

CREATE TABLE IF NOT EXISTS heal_applications (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,

  -- Applicant info
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  phone TEXT,

  -- Application details
  reason TEXT NOT NULL,
  duration TEXT,
  tried TEXT,

  -- Tracking & funnel
  portal_entry TEXT DEFAULT 'direct',   -- 'music', 'heal', or 'direct'
  source TEXT DEFAULT 'direct',          -- referrer URL
  status TEXT DEFAULT 'new' NOT NULL,    -- workflow status

  -- Internal notes (therapist fills in)
  notes TEXT,
  call_date TIMESTAMPTZ,
  session_date TIMESTAMPTZ,
  revenue_cents INTEGER DEFAULT 0,

  -- Constraints
  CONSTRAINT valid_status CHECK (status IN ('new', 'contacted', 'call_scheduled', 'booked', 'completed', 'follow_up', 'declined'))
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_heal_apps_status ON heal_applications(status);
CREATE INDEX IF NOT EXISTS idx_heal_apps_email ON heal_applications(email);
CREATE INDEX IF NOT EXISTS idx_heal_apps_created ON heal_applications(created_at DESC);

-- RLS: Enable row-level security
ALTER TABLE heal_applications ENABLE ROW LEVEL SECURITY;

-- Policy: Allow anonymous inserts (from the website form)
CREATE POLICY "Allow anonymous insert" ON heal_applications
  FOR INSERT TO anon
  WITH CHECK (true);

-- Policy: Only service_role can read/update/delete (admin dashboard)
CREATE POLICY "Service role full access" ON heal_applications
  FOR ALL TO service_role
  USING (true)
  WITH CHECK (true);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_heal_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER heal_applications_updated_at
  BEFORE UPDATE ON heal_applications
  FOR EACH ROW
  EXECUTE FUNCTION update_heal_updated_at();

-- Comment
COMMENT ON TABLE heal_applications IS 'PTGO Method session applications from heal.html';
