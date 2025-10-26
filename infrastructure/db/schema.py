from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import median
from typing import Optional, Tuple, List, Dict, Any

from psycopg2.extras import RealDictCursor

from infrastructure.utils import db_connection

# Global shared connection (do NOT close this in helpers)
connection = db_connection.connection


# ---------------------------
# Time helpers (aware UTC)
# ---------------------------
def to_aware_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def db_now_utc(cur) -> datetime:
    """Stable 'now' from DB in UTC."""
    cur.execute("SELECT (now() AT TIME ZONE 'utc')")
    return to_aware_utc(cur.fetchone()[0])

def ensure_utc_session(cur):
    try:
        cur.execute("SET TIME ZONE 'UTC'")
    except Exception:
        pass


# ---------------------------
# SELECTS
# ---------------------------
def get_open_auctions(now: datetime) -> list[dict]:
    sql = """
    SELECT id, external_id, detail_url, end_time, status
    FROM auction_listings
    WHERE status IN ('OPEN','ENDING_SOON','live','active')
    """
    with connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def get_open_auctions_ending_before(now: datetime) -> list[dict]:
    sql = """
    SELECT id, external_id, detail_url, end_time
    FROM auction_listings
    WHERE status IN ('OPEN','ENDING_SOON','live','active')
      AND end_time IS NOT NULL
      AND end_time <= %s
    """
    with connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute(sql, (to_aware_utc(now),))
        rows = cur.fetchall()
        cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def get_recent_max_price_by_external_id(external_id: str, window_minutes: int = 10) -> Optional[float]:
    """
    New: use auction_price_history (external_id, recorded_at timestamptz).
    """
    sql = """
    SELECT MAX(price) FROM auction_price_history
    WHERE external_id = %s
      AND recorded_at >= (now() AT TIME ZONE 'utc' - (%s || ' minutes')::interval)
    """
    with connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute(sql, (external_id, str(window_minutes)))
        (max_price,) = cur.fetchone()
    return max_price


def get_recent_max_price(auction_id: int, window_minutes: int = 10) -> Optional[float]:
    """
    Legacy (kept for callers that have auction_id). Uses auction_price_history(auction_id).
    """
    sql = """
    SELECT MAX(price) FROM auction_price_history
    WHERE auction_id = %s
      AND recorded_at >= (now() AT TIME ZONE 'utc' - (%s || ' minutes')::interval)
    """
    with connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute(sql, (auction_id, str(window_minutes)))
        (max_price,) = cur.fetchone()
    return max_price


# ---------------------------
# UPDATES
# ---------------------------
def mark_status(auction_id: int, status: str):
    conn = connection
    with conn, conn.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            UPDATE auction_listings
               SET status = %s
             WHERE id = %s
        """, (status, auction_id))


def finalize_auction(
    auction_id: int,
    final_price: Optional[float],
    final_price_confidence: Optional[str] = None,
    status: str = 'ENDED_CONFIRMED',
    sale_type: Optional[str] = None
):
    conn = connection
    with conn, conn.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            UPDATE auction_listings
               SET status = %s,
                   final_price = %s,
                   final_price_confidence = %s,
                   sale_type = COALESCE(%s, sale_type)
             WHERE id = %s
        """, (status, final_price, final_price_confidence, sale_type, auction_id))


def touch_last_seen(auction_id: int):
    conn = connection
    with conn, conn.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            UPDATE auction_listings
               SET last_seen = (now() AT TIME ZONE 'utc')
             WHERE id = %s
        """, (auction_id,))


# ---------------------------
# SOURCES
# ---------------------------
def create_sources():
    with connection, connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                type TEXT NOT NULL DEFAULT 'website',
                base_url TEXT NOT NULL,
                domain TEXT,
                niche TEXT,
                enabled BOOLEAN DEFAULT TRUE,
                scrape_interval_seconds INTEGER,
                last_scraped_at TIMESTAMPTZ,
                created_at_utc TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
                updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
            )
        """)


def add_source(name, type_, base_url, enabled=True):
    with connection, connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            INSERT INTO sources (name, type, base_url, enabled, created_at_utc, updated_at_utc)
            VALUES (%s, %s, %s, %s, (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc'))
            ON CONFLICT (name) DO NOTHING
        """, (name, type_, base_url, enabled))


def get_source_fields(source_key: str) -> dict:
    """
    Return sources.* row given a key you already use (name or domain).
    """
    with connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            SELECT id, name, scrape_interval_seconds, last_scraped_at
            FROM sources
            WHERE name = %s OR domain = %s
            ORDER BY name = %s DESC, domain = %s DESC
            LIMIT 1
        """, (source_key, source_key, source_key, source_key))
        row = cur.fetchone()
    if not row:
        return {}
    return {
        "id": row[0],
        "name": row[1],
        "scrape_interval_seconds": row[2],
        "last_scraped_at": row[3],
    }


def update_source_last_scraped(source_id: int, when: Optional[datetime] = None) -> None:
    conn = connection
    with conn, conn.cursor() as cur:
        ensure_utc_session(cur)
        if when is None:
            cur.execute("UPDATE sources SET last_scraped_at = (now() AT TIME ZONE 'utc') WHERE id = %s", (source_id,))
        else:
            cur.execute("UPDATE sources SET last_scraped_at = %s WHERE id = %s", (to_aware_utc(when), source_id))


# seed/maintenance helpers left as-is but wrapped
def add_sources():
    with connection, connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            INSERT INTO sources (name, type, base_url, enabled, created_at_utc, updated_at_utc)
            VALUES
            ('coindesk', 'news', 'https://www.coindesk.com', TRUE, (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')),
            ('cointelegraph', 'news', 'https://cointelegraph.com', TRUE, (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')),
            ('decrypt', 'news', 'https://decrypt.co', TRUE, (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')),
            ('bitcoinmagazine', 'news', 'https://bitcoinmagazine.com', TRUE, (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')),
            ('cryptopanic', 'news', 'https://cryptopanic.com', TRUE, (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')),
            ('coinmarketcap', 'news', 'https://coinmarketcap.com/headlines/news', TRUE, (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')),
            ('coingecko', 'market', 'https://api.coingecko.com', TRUE, (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')),
            ('binance', 'market', 'https://api.binance.com', TRUE, (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc'))
            ON CONFLICT (name) DO NOTHING
        """)

def drop_sources():
    with connection, connection.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS sources")

def truncate_sources():
    with connection, connection.cursor() as cur:
        cur.execute("TRUNCATE TABLE IF EXISTS sources")


# ---------------------------
# ARTICLES (kept, tidied)
# ---------------------------
def create_articles():
    with connection, connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id SERIAL PRIMARY KEY,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                url TEXT UNIQUE NOT NULL,
                summary TEXT,
                published_at TIMESTAMPTZ,
                fetched_at TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
                content TEXT,
                sentiment TEXT,
                created_at_utc TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
                updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
            )
        """)


def resolve_source_id(domain_or_name: str) -> int:
    with connection.cursor() as cur:
        ensure_utc_session(cur)
        # Try exact name
        cur.execute("SELECT id FROM sources WHERE name = %s", (domain_or_name,))
        row = cur.fetchone()
        if row:
            return row[0]

        # Try matching base_url by domain
        cur.execute("""
            SELECT id FROM sources
            WHERE replace(replace(replace(base_url,'https://',''),'http://',''),'www.','') ILIKE
                  replace(replace(replace(%s,'https://',''),'http://',''),'www.','')
            LIMIT 1
        """, (domain_or_name,))
        row = cur.fetchone()
        if row:
            return row[0]

        # Insert minimal record
        cur.execute("""
            INSERT INTO sources (name, type, base_url, enabled, created_at_utc, updated_at_utc)
            VALUES (%s, 'news', %s, TRUE, (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc'))
            RETURNING id
        """, (domain_or_name, domain_or_name))
        new_id = cur.fetchone()[0]
    connection.commit()
    return new_id


def find_by_url(url: str):
    with connection.cursor() as cur:
        cur.execute("""
            SELECT id, source_id, title, url, summary, published_at,
                   fetched_at, content, sentiment, created_at_utc, updated_at_utc
            FROM articles
            WHERE url = %s
            LIMIT 1
        """, (url,))
        row = cur.fetchone()
    if not row:
        return None
    keys = ["id", "source_id", "title", "url", "summary", "published_at",
            "fetched_at", "content", "sentiment", "created_at_utc", "updated_at_utc"]
    return dict(zip(keys, row))


def resolve_source_field(domain: str, field: str) -> Optional[str]:
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT {field} FROM sources WHERE base_url LIKE %s OR name = %s LIMIT 1",
            (f'%{domain}%', domain),
        )
        row = cur.fetchone()
        return row[0] if row else None


def update_content(url: str, content: str, title=None, summary=None, published_at=None):
    with connection, connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            UPDATE articles
            SET
              title = COALESCE(%s, title),
              summary = COALESCE(%s, summary),
              published_at = COALESCE(%s, published_at),
              content = %s,
              updated_at_utc = (now() AT TIME ZONE 'utc')
            WHERE url = %s
        """, (title, summary, published_at, content, url))


def resolve_source_niche(domain: str) -> Optional[str]:
    with connection.cursor() as cur:
        cur.execute("SELECT niche FROM sources WHERE base_url LIKE %s OR name = %s LIMIT 1", (f'%{domain}%', domain))
        row = cur.fetchone()
        return row[0] if row else None


def article_exists_by_url(url: str) -> bool:
    with connection.cursor() as cur:
        cur.execute("SELECT 1 FROM articles WHERE url = %s LIMIT 1", (url,))
        return cur.fetchone() is not None


def touch_updated_at(url: str, ts=None):
    with connection, connection.cursor() as cur:
        if ts is None:
            cur.execute("UPDATE articles SET updated_at_utc = (now() AT TIME ZONE 'utc') WHERE url = %s", (url,))
        else:
            cur.execute("UPDATE articles SET updated_at_utc = %s WHERE url = %s", (to_aware_utc(ts), url))


UPSERT_ARTICLE_SQL = """
INSERT INTO articles (
    source_id, title, url, summary, published_at,
    fetched_at,
    content, sentiment,
    created_at_utc, updated_at_utc
)
VALUES (
    %(source_id)s, %(title)s, %(url)s, %(summary)s, %(published_at)s,
    (now() AT TIME ZONE 'utc'),
    %(content)s, %(sentiment)s,
    (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')
)
ON CONFLICT (url) DO UPDATE SET
    source_id      = EXCLUDED.source_id,
    title          = COALESCE(EXCLUDED.title,        articles.title),
    summary        = COALESCE(EXCLUDED.summary,      articles.summary),
    published_at   = COALESCE(EXCLUDED.published_at, articles.published_at),
    fetched_at     = (now() AT TIME ZONE 'utc'),
    content        = COALESCE(EXCLUDED.content,      articles.content),
    sentiment      = COALESCE(EXCLUDED.sentiment,    articles.sentiment),
    updated_at_utc = (now() AT TIME ZONE 'utc')
RETURNING id;
"""

def upsert_article(article):
    with connection, connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute(UPSERT_ARTICLE_SQL, asdict(article))
        (article_id,) = cur.fetchone()
        return article_id


def add_article(source_id, title, url, summary, published_at, content, sentiment):
    with connection, connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            INSERT INTO articles (source_id, title, url, summary, published_at, content, sentiment)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (url) DO NOTHING
        """, (source_id, title, url, summary, published_at, content, sentiment))


def drop_articles():
    with connection, connection.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS articles")

def truncate_articles():
    with connection, connection.cursor() as cur:
        cur.execute("TRUNCATE TABLE IF EXISTS articles")


# ---------------------------
# PRICES (kept, tidied)
# ---------------------------
def create_prices():
    with connection, connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS prices (
                id SERIAL PRIMARY KEY,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                symbol TEXT NOT NULL,
                price NUMERIC(18,8) NOT NULL,
                currency TEXT NOT NULL DEFAULT 'USD',
                fetched_at TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
                created_at_utc TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
                updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
            )
        """)

def add_price(source_id, symbol, price, currency='USD'):
    with connection, connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            INSERT INTO prices (source_id, symbol, price, currency, fetched_at)
            VALUES (%s, %s, %s, %s, (now() AT TIME ZONE 'utc'))
        """, (source_id, symbol, price, currency))

def drop_prices():
    with connection, connection.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS prices")

def truncate_prices():
    with connection, connection.cursor() as cur:
        cur.execute("TRUNCATE TABLE IF EXISTS prices")


# ---------------------------
# SCRAPE STATE
# ---------------------------
def create_scrape_state():
    with connection, connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scrape_state (
                id SERIAL PRIMARY KEY,
                last_scrape TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)


# ---------------------------
# AUCTIONS
# ---------------------------
def create_auction_tables():
    """
    Modernised DDL to match functions elsewhere. If tables exist, this is a no-op.
    (Types use TIMESTAMPTZ; names match mark_listing_seen/upsert_auction_listing.)
    """
    with connection, connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS auction_listings (
                id SERIAL PRIMARY KEY,
                source TEXT,
                source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE,
                external_id TEXT UNIQUE NOT NULL,
                title TEXT,
                price_current NUMERIC,
                final_price NUMERIC,
                bids_count INTEGER,
                end_time TIMESTAMPTZ,
                status TEXT DEFAULT 'live',
                url TEXT,
                detail_url TEXT,
                sale_type TEXT,
                first_seen TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
                last_seen  TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
                fetched_at TIMESTAMPTZ,
                roi_estimate DOUBLE PRECISION,
                max_bid NUMERIC,
                notes TEXT,
                model_key TEXT,
                time_left_s INTEGER
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS auction_price_history (
                id SERIAL PRIMARY KEY,
                auction_id INTEGER REFERENCES auction_listings(id) ON DELETE CASCADE,
                external_id TEXT,
                price NUMERIC,
                bids_count INTEGER,
                recorded_at TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
            )
        """)
    print("Auction tables created or already exist.")


def append_price_history(*, external_id: str, price: int | float | None, bids_count: int | None):
    conn = connection
    with conn, conn.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute(
            """
            INSERT INTO auction_price_history (external_id, price, bids_count, recorded_at)
            VALUES (%s, %s, %s, (now() AT TIME ZONE 'utc'))
            """,
            (external_id, price, bids_count),
        )


def mark_listing_seen(
    external_id: str,
    price_current: float | int | Decimal,
    bids_count: Optional[int],
    end_time,  # datetime or None
    time_left_s: Optional[int],
    model_key: Optional[str] = None,
    status: Optional[str] = None,
    final_price: Optional[float | int | Decimal] = None
):
    """
    Update live listing snapshot + first_seen/last_seen.
    """
    conn = connection
    with conn, conn.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            UPDATE auction_listings
               SET price_current = COALESCE(%s, price_current),
                   bids_count     = COALESCE(%s, bids_count),
                   end_time       = COALESCE(%s, end_time),
                   time_left_s    = COALESCE(%s, time_left_s),
                   model_key      = COALESCE(%s, model_key),
                   status         = COALESCE(%s, status),
                   final_price    = COALESCE(%s, final_price),
                   last_seen      = (now() AT TIME ZONE 'utc')
             WHERE external_id = %s
        """, (price_current, bids_count, to_aware_utc(end_time) if end_time else None,
              time_left_s, model_key, status, final_price, external_id))


def insert_or_ignore_listing(
    source: str,
    external_id: str,
    title: str,
    url: str,
    price_current: Optional[float],
    bids_count: Optional[int],
    end_time,
    model_key: Optional[str],
):
    """
    Idempotent insert for new live listings (first_seen).
    """
    conn = connection
    with conn, conn.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            INSERT INTO auction_listings (source, external_id, title, url, price_current, bids_count, end_time, model_key, status, first_seen, last_seen, fetched_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'live', (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc'))
            ON CONFLICT (external_id) DO NOTHING
        """, (source, external_id, title, url, price_current, bids_count, to_aware_utc(end_time) if end_time else None, model_key))


def latest_comps_map() -> Dict[str, Dict[str, Any]]:
    conn = connection
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        ensure_utc_session(cur)
        cur.execute("""
            WITH lc AS (
              SELECT DISTINCT ON (model_key) model_key, median_final_price, mean_final_price, samples, computed_at
              FROM comps
              ORDER BY model_key, computed_at DESC
            )
            SELECT * FROM lc
        """)
        rows = cur.fetchall()
        return {r["model_key"]: r for r in rows}


def compute_daily_comps():
    conn = connection
    with conn, conn.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            INSERT INTO comps (model_key, median_final_price, mean_final_price, samples)
            SELECT model_key,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY final_price)::numeric AS median_final_price,
                   AVG(final_price)::numeric AS mean_final_price,
                   COUNT(*)::int AS samples
            FROM auction_listings
            WHERE status = 'sold' AND final_price IS NOT NULL AND model_key IS NOT NULL
            GROUP BY model_key
        """)


def record_alert(external_id: str, score: float, max_bid: float) -> tuple[bool, int | None]:
    with connection, connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            INSERT INTO alerts (external_id, score, max_bid, created_at)
            VALUES (%s, %s, %s, (now() AT TIME ZONE 'utc'))
            ON CONFLICT (external_id) DO UPDATE
                SET score = EXCLUDED.score,
                    max_bid = EXCLUDED.max_bid,
                    updated_at = (now() AT TIME ZONE 'utc')
            RETURNING id, (xmax = 0) AS inserted;
        """, (external_id, score, max_bid))
        row = cur.fetchone()
        created_now = bool(row[1])
        return created_now, row[0]


def mark_alert_emailed(alert_id: int):
    with connection, connection.cursor() as cur:
        cur.execute("UPDATE alerts SET sent_at = (now() AT TIME ZONE 'utc') WHERE id = %s", (alert_id,))


def upsert_auction_listing(
    *,
    source: str,
    external_id: str,
    title: str,
    price_current: int | float | None,
    bids_count: int | None,
    end_time,                # datetime | None (naive or aware)
    url: str,
    detail_url: str | None = None,
    sale_type: str | None = None,
    roi_estimate: float | None = None,
    max_bid: int | float | None = None,
    notes: str | None = None,
    source_id: int | None = None,
    model_key: str | None = None,
    time_left_s: int | None = None,
    status: str | None = "live",
):
    conn = connection
    with conn, conn.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute(
            """
            INSERT INTO auction_listings (
                source, external_id, title, price_current, bids_count, end_time,
                url, detail_url, sale_type,
                fetched_at, first_seen, last_seen,
                roi_estimate, max_bid, notes, source_id, model_key, time_left_s, status
            )
            VALUES (
                %(source)s, %(external_id)s, %(title)s, %(price_current)s, %(bids_count)s, %(end_time)s,
                %(url)s, %(detail_url)s, %(sale_type)s,
                (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc'),
                %(roi_estimate)s, %(max_bid)s, %(notes)s, %(source_id)s, %(model_key)s, %(time_left_s)s, %(status)s
            )
            ON CONFLICT (external_id) DO UPDATE
            SET
                title         = EXCLUDED.title,
                price_current = COALESCE(EXCLUDED.price_current, auction_listings.price_current),
                bids_count    = COALESCE(EXCLUDED.bids_count, auction_listings.bids_count),
                end_time      = COALESCE(EXCLUDED.end_time, auction_listings.end_time),
                url           = COALESCE(EXCLUDED.url, auction_listings.url),
                detail_url    = COALESCE(EXCLUDED.detail_url, auction_listings.detail_url),
                sale_type     = COALESCE(EXCLUDED.sale_type, auction_listings.sale_type),
                roi_estimate  = COALESCE(EXCLUDED.roi_estimate, auction_listings.roi_estimate),
                max_bid       = COALESCE(EXCLUDED.max_bid, auction_listings.max_bid),
                notes         = COALESCE(EXCLUDED.notes, auction_listings.notes),
                source_id     = COALESCE(EXCLUDED.source_id, auction_listings.source_id),
                model_key     = COALESCE(EXCLUDED.model_key, auction_listings.model_key),
                time_left_s   = COALESCE(EXCLUDED.time_left_s, auction_listings.time_left_s),
                status        = COALESCE(EXCLUDED.status, auction_listings.status),
                last_seen     = (now() AT TIME ZONE 'utc')
            """,
            {
                "source": source,
                "external_id": external_id,
                "title": title,
                "price_current": price_current,
                "bids_count": bids_count,
                "end_time": to_aware_utc(end_time) if end_time else None,
                "url": url,
                "detail_url": detail_url,
                "sale_type": sale_type,
                "roi_estimate": roi_estimate,
                "max_bid": max_bid,
                "notes": notes,
                "source_id": source_id,
                "model_key": model_key,
                "time_left_s": time_left_s,
                "status": status,
            },
        )


def insert_price_history(auction_id: int, price: int, bids_count: int):
    """
    Legacy variant (auction_id). Kept for compatibility.
    """
    conn = connection
    with conn, conn.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            INSERT INTO auction_price_history (auction_id, price, bids_count, recorded_at)
            VALUES (%s, %s, %s, (now() AT TIME ZONE 'utc'))
        """, (auction_id, price, bids_count))


def mark_auction_sold(external_id: str, final_price: int):
    with connection, connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            UPDATE auction_listings
            SET status = 'sold',
                final_price = %s,
                last_seen = (now() AT TIME ZONE 'utc')
            WHERE external_id = %s
        """, (final_price, external_id))


def mark_auction_expired(external_id: str):
    with connection, connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            UPDATE auction_listings
            SET status = 'unsold',
                last_seen = (now() AT TIME ZONE 'utc')
            WHERE external_id = %s
        """, (external_id,))


def get_active_auctions():
    with connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("""
            SELECT id, source_id, external_id, title, price_current, bids_count, end_time, url
            FROM auction_listings
            WHERE status IN ('active','live','OPEN','ENDING_SOON')
            ORDER BY end_time ASC NULLS LAST
        """)
        return cur.fetchall()
