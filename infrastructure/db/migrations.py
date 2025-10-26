# infrastructure/db/migrations.py

from __future__ import annotations
from infrastructure.utils import db_connection

connection = db_connection.connection

SQL = r"""
-- Always operate in UTC
SET TIME ZONE 'UTC';

----------------------------
-- 1) sources
----------------------------
CREATE TABLE IF NOT EXISTS sources (
  id SERIAL PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  type TEXT NOT NULL DEFAULT 'website',
  base_url TEXT NOT NULL,
  enabled BOOLEAN DEFAULT TRUE
);

ALTER TABLE sources
  ADD COLUMN IF NOT EXISTS domain TEXT,
  ADD COLUMN IF NOT EXISTS niche TEXT,
  ADD COLUMN IF NOT EXISTS scrape_interval_seconds INTEGER,
  ADD COLUMN IF NOT EXISTS last_scraped_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS created_at_utc TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
  ADD COLUMN IF NOT EXISTS updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc');

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'sources' AND column_name='last_scraped_at'
      AND udt_name IN ('timestamp', 'timestampwithouttimezone')
  ) THEN
    ALTER TABLE sources
      ALTER COLUMN last_scraped_at TYPE TIMESTAMPTZ
      USING (last_scraped_at AT TIME ZONE 'utc');
  END IF;
END$$;

----------------------------
-- 2) auction_listings
----------------------------
CREATE TABLE IF NOT EXISTS auction_listings (
  id SERIAL PRIMARY KEY,
  external_id TEXT UNIQUE NOT NULL
);

ALTER TABLE auction_listings
  ADD COLUMN IF NOT EXISTS source TEXT,
  ADD COLUMN IF NOT EXISTS source_id INTEGER,
  ADD COLUMN IF NOT EXISTS title TEXT,
  ADD COLUMN IF NOT EXISTS price_current NUMERIC,
  ADD COLUMN IF NOT EXISTS final_price NUMERIC,
  ADD COLUMN IF NOT EXISTS bids_count INTEGER,
  ADD COLUMN IF NOT EXISTS end_time TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'live',
  ADD COLUMN IF NOT EXISTS url TEXT,
  ADD COLUMN IF NOT EXISTS detail_url TEXT,
  ADD COLUMN IF NOT EXISTS sale_type TEXT,
  ADD COLUMN IF NOT EXISTS first_seen TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
  ADD COLUMN IF NOT EXISTS last_seen  TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
  ADD COLUMN IF NOT EXISTS fetched_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS roi_estimate DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS max_bid NUMERIC,
  ADD COLUMN IF NOT EXISTS notes TEXT,
  ADD COLUMN IF NOT EXISTS model_key TEXT,
  ADD COLUMN IF NOT EXISTS time_left_s INTEGER;

DO $$
DECLARE
  col TEXT;
BEGIN
  FOR col IN SELECT unnest(ARRAY['end_time','first_seen','last_seen','fetched_at'])
  LOOP
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name='auction_listings' AND column_name=col
        AND udt_name IN ('timestamp','timestampwithouttimezone')
    ) THEN
      EXECUTE format(
        'ALTER TABLE auction_listings ALTER COLUMN %I TYPE TIMESTAMPTZ USING (%I AT TIME ZONE ''utc'')',
        col, col
      );
    END IF;
  END LOOP;
END$$;

ALTER TABLE auction_listings
  ALTER COLUMN status SET DEFAULT 'live';

----------------------------
-- 3) auction_price_history
----------------------------
CREATE TABLE IF NOT EXISTS auction_price_history (
  id SERIAL PRIMARY KEY
);

ALTER TABLE auction_price_history
  ADD COLUMN IF NOT EXISTS auction_id INTEGER,
  ADD COLUMN IF NOT EXISTS external_id TEXT,
  ADD COLUMN IF NOT EXISTS price NUMERIC,
  ADD COLUMN IF NOT EXISTS bids_count INTEGER,
  ADD COLUMN IF NOT EXISTS recorded_at TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc');

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='auction_price_history' AND column_name='timestamp'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='auction_price_history' AND column_name='recorded_at'
  ) THEN
    ALTER TABLE auction_price_history RENAME COLUMN "timestamp" TO recorded_at;
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='auction_price_history' AND column_name='seen_at'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='auction_price_history' AND column_name='recorded_at'
  ) THEN
    ALTER TABLE auction_price_history RENAME COLUMN seen_at TO recorded_at;
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='auction_price_history' AND column_name='recorded_at'
      AND udt_name IN ('timestamp','timestampwithouttimezone')
  ) THEN
    ALTER TABLE auction_price_history
      ALTER COLUMN recorded_at TYPE TIMESTAMPTZ
      USING (recorded_at AT TIME ZONE 'utc');
  END IF;
END$$;

CREATE INDEX IF NOT EXISTS aph_ext_time_idx ON auction_price_history (external_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS aph_auc_time_idx ON auction_price_history (auction_id, recorded_at DESC);

-- ✅ FIXED: only create view if *no table or view* named price_history exists
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relname = 'price_history'
  ) THEN
    CREATE VIEW price_history AS
      SELECT auction_id, price, bids_count, recorded_at AS seen_at
      FROM auction_price_history;
  END IF;
END$$;

----------------------------
-- 4) alerts
----------------------------
CREATE TABLE IF NOT EXISTS alerts (
  id SERIAL PRIMARY KEY,
  external_id TEXT UNIQUE,
  score NUMERIC,
  max_bid NUMERIC,
  created_at TIMESTAMPTZ DEFAULT (now() AT TIME ZONE 'utc'),
  updated_at TIMESTAMPTZ,
  sent_at TIMESTAMPTZ
);

----------------------------
-- 5) comps
----------------------------
CREATE TABLE IF NOT EXISTS comps (
  id SERIAL PRIMARY KEY,
  model_key TEXT NOT NULL,
  median_final_price NUMERIC,
  mean_final_price NUMERIC,
  samples INTEGER,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS comps_model_time_idx ON comps (model_key, computed_at DESC);
"""

def apply_migrations():
    conn = connection
    with conn, conn.cursor() as cur:
        cur.execute(SQL)
    return True
