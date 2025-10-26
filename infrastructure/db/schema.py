import psycopg2
from infrastructure.utils import db_connection
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, List

connection = db_connection.connection


# --- SELECTS

def get_open_auctions(now: datetime) -> list[dict]:
    sql = """
    SELECT id, external_id, detail_url, end_time, status
    FROM auction_listings
    WHERE status IN ('OPEN','ENDING_SOON')
    """
    with connection.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def get_open_auctions_ending_before(now: datetime) -> list[dict]:
    sql = """
    SELECT id, external_id, detail_url, end_time
    FROM auction_listings
    WHERE status IN ('OPEN','ENDING_SOON')
      AND end_time IS NOT NULL
      AND end_time <= %s
    """
    with connection.cursor() as cur:
        cur.execute(sql, (now,))
        rows = cur.fetchall()
        cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def get_recent_max_price(auction_id: int, window_minutes: int = 10) -> Optional[float]:
    sql = """
    SELECT MAX(price) FROM price_history
    WHERE auction_id = %s AND seen_at >= (NOW() AT TIME ZONE 'utc' - INTERVAL '%s minutes')
    """
    with connection.cursor() as cur:
        cur.execute(sql, (auction_id, window_minutes))
        (max_price,) = cur.fetchone()
    return max_price


# --- UPDATES

def mark_status(auction_id: int, status: str):
    with connection.cursor() as cur:
        cur.execute("""
            UPDATE auction_listings
               SET status = %s
             WHERE id = %s
        """, (status, auction_id))
    connection.commit()


def finalize_auction(
        auction_id: int,
        final_price: Optional[float],
        final_price_confidence: Optional[str] = None,
        status: str = 'ENDED_CONFIRMED',
        sale_type: Optional[str] = None
):
    with connection.cursor() as cur:
        cur.execute("""
            UPDATE auction_listings
               SET status = %s,
                   final_price = %s,
                   final_price_confidence = %s,
                   sale_type = COALESCE(%s, sale_type)
             WHERE id = %s
        """, (status, final_price, final_price_confidence, sale_type, auction_id))
    connection.commit()


def touch_last_seen(auction_id: int):
    with connection.cursor() as cur:
        cur.execute("""
            UPDATE auction_listings
               SET last_seen_at = NOW() AT TIME ZONE 'utc'
             WHERE id = %s
        """, (auction_id,))
    connection.commit()


def create_sources():
    cursor = connection.cursor()
    q = ("CREATE TABLE IF NOT EXISTS sources ("
         "id SERIAL PRIMARY KEY,"
         "name TEXT UNIQUE NOT NULL,"
         "type TEXT NOT NULL DEFAULT 'website',"
         "base_url TEXT NOT NULL,"
         "enabled BOOLEAN DEFAULT TRUE,"
         "created_at_utc TIMESTAMP NOT NULL,"
         "updated_at_utc TIMESTAMP NOT NULL)"
         )
    cursor.execute(q)
    connection.commit()


def add_source(name, type_, base_url, enabled=True):
    cursor = connection.cursor()
    q = """
    INSERT INTO sources (name, type, base_url, enabled, created_at_utc, updated_at_utc)
    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    ON CONFLICT (name) DO NOTHING
    """
    cursor.execute(q, (name, type_, base_url, enabled))
    connection.commit()


def get_source_fields(source_key: str) -> dict:
    """
    Return a row dict for sources.* given a key you already use with resolve_source_field/resolve_source_id.
    """
    with connection.cursor() as cur:
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
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE sources SET last_scraped_at = %s WHERE id = %s",
            (when or datetime.utcnow(), source_id),
        )
    connection.commit()


def add_sources():
    cursor = connection.cursor()
    q = ("INSERT INTO sources (name, type, base_url, enabled, created_at_utc, updated_at_utc) VALUES"
         "('coindesk', 'news', 'https://www.coindesk.com', TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),"
         "('cointelegraph', 'news', 'https://cointelegraph.com', TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),"
         "('decrypt', 'news', 'https://decrypt.co', TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),"
         "('bitcoinmagazine', 'news', 'https://bitcoinmagazine.com', TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),"
         "('cryptopanic', 'news', 'https://cryptopanic.com', TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),"
         "('coinmarketcap', 'news', 'https://coinmarketcap.com/headlines/news', TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),"
         "('coingecko', 'market', 'https://api.coingecko.com', TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),"
         "('binance', 'market', 'https://api.binance.com', TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);")
    cursor.execute(q)
    connection.commit()


def drop_sources():
    cursor = connection.cursor()
    cursor.execute("DROP TABLE IF EXISTS  sources")
    connection.commit()


def truncate_sources():
    cursor = connection.cursor()
    cursor.execute("TRUNCATE TABLE IF EXISTS sources")
    connection.commit()


def create_articles():
    cursor = connection.cursor()
    q = ("CREATE TABLE IF NOT EXISTS articles ("
         "id SERIAL PRIMARY KEY,"
         "source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,"
         "title TEXT NOT NULL,"
         "url TEXT UNIQUE NOT NULL,"
         "summary TEXT,"
         "published_at TIMESTAMP,"
         "fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
         "content TEXT,"
         "sentiment TEXT,"
         "created_at_utc TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
         "updated_at_utc TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
         ");"
         )
    cursor.execute(q)
    connection.commit()


def resolve_source_id(domain_or_name: str) -> int:
    """
    Accepts either 'coindesk.com', 'https://www.coindesk.com', or 'coindesk'.
    Tries name first, then base_url (domain match). Inserts if missing.
    """
    with connection.cursor() as cur:
        # Try exact name
        cur.execute("SELECT id FROM sources WHERE name = %s", (domain_or_name,))
        row = cur.fetchone()
        if row:
            return row[0]

        # Try matching base_url by domain (works for https://www.coindesk.com etc.)
        cur.execute("""
            SELECT id FROM sources
            WHERE replace(replace(replace(base_url,'https://',''),'http://',''),'www.','') ILIKE
                  replace(replace(replace(%s,'https://',''),'http://',''),'www.','')
            LIMIT 1
        """, (domain_or_name,))
        row = cur.fetchone()
        if row:
            return row[0]

        # Insert minimal record (name + base_url both set to input)
        cur.execute("""
            INSERT INTO sources (name, type, base_url, enabled, created_at_utc, updated_at_utc)
            VALUES (%s, 'news', %s, TRUE, NOW(), NOW())
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
    with connection.cursor() as cur:
        cur.execute("""
            UPDATE articles
            SET
              title = COALESCE(%s, title),
              summary = COALESCE(%s, summary),
              published_at = COALESCE(%s, published_at),
              content = %s,
              updated_at_utc = NOW()
            WHERE url = %s
        """, (title, summary, published_at, content, url))
    connection.commit()


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
    with connection.cursor() as cur:
        if ts is None:
            cur.execute("UPDATE articles SET updated_at_utc = NOW() WHERE url = %s", (url,))
        else:
            cur.execute("UPDATE articles SET updated_at_utc = %s WHERE url = %s", (ts, url))
    connection.commit()


def upsert_article(article):
    UPSERT_ARTICLE_SQL = """
    INSERT INTO articles (
        source_id, title, url, summary, published_at,
        fetched_at,
        content, sentiment,
        created_at_utc, updated_at_utc
    )
    VALUES (
        %(source_id)s, %(title)s, %(url)s, %(summary)s, %(published_at)s,
        NOW(),
        %(content)s, %(sentiment)s,
        NOW(), NOW()
    )
    ON CONFLICT (url) DO UPDATE SET
        source_id      = EXCLUDED.source_id,
        title          = COALESCE(EXCLUDED.title,        articles.title),
        summary        = COALESCE(EXCLUDED.summary,      articles.summary),
        published_at   = COALESCE(EXCLUDED.published_at, articles.published_at),
        fetched_at     = NOW(),
        content        = COALESCE(EXCLUDED.content,      articles.content),
        sentiment      = COALESCE(EXCLUDED.sentiment,    articles.sentiment),
        updated_at_utc = NOW()
    RETURNING id;
    """
    with connection.cursor() as cur:
        cur.execute(UPSERT_ARTICLE_SQL, asdict(article))
        (article_id,) = cur.fetchone()

    connection.commit()
    return article_id


def add_article(source_id, title, url, summary, published_at, content, sentiment):
    cursor = connection.cursor()
    q = """
    INSERT INTO articles (source_id, title, url, summary, published_at, content, sentiment)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (url) DO NOTHING
    """
    cursor.execute(q, (source_id, title, url, summary, published_at, content, sentiment))
    connection.commit()


def drop_articles():
    cursor = connection.cursor()
    cursor.execute("DROP TABLE IF EXISTS articles")
    connection.commit()


def truncate_articles():
    cursor = connection.cursor()
    cursor.execute("TRUNCATE TABLE IF EXISTS articles")
    connection.commit()


def create_prices():
    cursor = connection.cursor()
    q = ("CREATE TABLE IF NOT EXISTS prices ("
         "id SERIAL PRIMARY KEY,"
         "source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,"
         "symbol TEXT NOT NULL,"
         "price NUMERIC(18,8) NOT NULL,"
         "currency TEXT NOT NULL DEFAULT 'USD',"
         "fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
         "created_at_utc TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
         "updated_at_utc TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
         ");"
         )
    cursor.execute(q)
    connection.commit()


def add_price(source_id, symbol, price, currency='USD'):
    cursor = connection.cursor()
    q = """
    INSERT INTO prices (source_id, symbol, price, currency, fetched_at)
    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
    """
    cursor.execute(q, (source_id, symbol, price, currency))
    connection.commit()


def drop_prices():
    cursor = connection.cursor()
    cursor.execute("DROP TABLE IF EXISTS prices")
    connection.commit()


def truncate_prices():
    cursor = connection.cursor()
    cursor.execute("TRUNCATE TABLE IF EXISTS prices")
    connection.commit()


def create_scrape_state():
    cursor = connection.cursor()
    q = ("CREATE TABLE scrape_state ("
         "id SERIAL PRIMARY KEY,"
         "last_scrape TIMESTAMP WITH TIME ZONE,"
         "created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),"
         "updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW());"
         )
    cursor.execute(q)
    connection.commit()


def create_auction_tables():
    with connection.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS auction_listings (
                id SERIAL PRIMARY KEY,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                external_id TEXT UNIQUE NOT NULL,
                title TEXT,
                price_current BIGINT,
                price_final BIGINT,
                bids_count INTEGER,
                end_time TIMESTAMP,
                status TEXT DEFAULT 'active',
                url TEXT,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                fetched_at TIMESTAMP,
                roi_estimate DOUBLE PRECISION,
                max_bid BIGINT,
                notes TEXT
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS auction_price_history (
                id SERIAL PRIMARY KEY,
                auction_id INTEGER REFERENCES auction_listings(id) ON DELETE CASCADE,
                price BIGINT,
                bids_count INTEGER,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
    connection.commit()
    print("Auction tables created or already exist.")


from typing import Optional, Dict, Any
from statistics import median
from decimal import Decimal
from psycopg2.extras import RealDictCursor


# --- existing: connection, etc. ---

def append_price_history(external_id: str, price: float | int | Decimal, bids_count: Optional[int]):
    conn = connection
    with conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO price_history (listing_external_id, price, bids_count) VALUES (%s,%s,%s)",
            (external_id, price, bids_count)
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
        cur.execute("""
            UPDATE auction_listings
               SET price_current = COALESCE(%s, price_current),
                   bids_count     = COALESCE(%s, bids_count),
                   end_time       = COALESCE(%s, end_time),
                   time_left_s    = COALESCE(%s, time_left_s),
                   model_key      = COALESCE(%s, model_key),
                   status         = COALESCE(%s, status),
                   final_price    = COALESCE(%s, final_price),
                   last_seen      = CURRENT_TIMESTAMP
             WHERE external_id = %s
        """, (price_current, bids_count, end_time, time_left_s, model_key, status, final_price, external_id))


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
        cur.execute("""
            INSERT INTO auction_listings (source, external_id, title, url, price_current, bids_count, end_time, model_key, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'live')
            ON CONFLICT (external_id) DO NOTHING
        """, (source, external_id, title, url, price_current, bids_count, end_time, model_key))


def latest_comps_map() -> Dict[str, Dict[str, Any]]:
    """
    Returns { model_key: {median_final_price, mean_final_price, samples} } for latest computed_at per model.
    """
    conn = connection
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
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
    """
    Simple roll-up using auction_listings where status='sold' and final_price is not null.
    Run nightly.
    """
    conn = connection
    with conn, conn.cursor() as cur:
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
    """
    Upsert an alert; return (created_now, alert_id)
    created_now=True means we should send an email.
    """
    cur = connection.cursor()
    cur.execute("""
        INSERT INTO alerts (external_id, score, max_bid, created_at)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT (external_id) DO UPDATE
            SET score = EXCLUDED.score,
                max_bid = EXCLUDED.max_bid,
                updated_at = NOW()
        RETURNING id, (xmax = 0) AS inserted;
    """, (external_id, score, max_bid))
    row = cur.fetchone()
    connection.commit()
    # On Postgres, xmax=0 implies freshly inserted in this simple pattern
    created_now = bool(row[1])
    return created_now, row[0]


def mark_alert_emailed(alert_id: int):
    cur = connection.cursor()
    cur.execute("UPDATE alerts SET sent_at = NOW() WHERE id = %s", (alert_id,))
    connection.commit()


def upsert_auction_listing(
        *,
        source: str,
        external_id: str,
        title: str,
        price_current: Optional[float | int | Decimal],
        bids_count: Optional[int],
        end_time,  # datetime | None
        url: str,
        # NEW optional fields
        detail_url: Optional[str] = None,
        sale_type: Optional[str] = None,  # 'bin' | 'auction' | etc.
        roi_estimate: Optional[float | int | Decimal] = None,
        max_bid: Optional[float | int | Decimal] = None,
        notes: Optional[str] = None,
        source_id: Optional[int] = None,
        model_key: Optional[str] = None,
        time_left_s: Optional[int] = None,
        status: Optional[str] = "live",
):
    """
    Idempotent upsert for auction_listings.
    - Inserts on first sight, stamping first_seen/last_seen.
    - On conflict(external_id) updates ONLY with non-null values and refreshes last_seen.
    - Safe to call repeatedly from adapters.
    """
    conn = connection
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO auction_listings (
                source, external_id, title, price_current, bids_count, end_time,
                url, detail_url, sale_type, roi_estimate, max_bid, notes,
                source_id, model_key, time_left_s, status, first_seen, last_seen
            )
            VALUES (
                %(source)s, %(external_id)s, %(title)s, %(price_current)s, %(bids_count)s, %(end_time)s,
                %(url)s, %(detail_url)s, %(sale_type)s, %(roi_estimate)s, %(max_bid)s, %(notes)s,
                %(source_id)s, %(model_key)s, %(time_left_s)s, %(status)s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT (external_id) DO UPDATE
            SET
                -- prefer new non-null values; otherwise keep existing
                source        = COALESCE(EXCLUDED.source,        auction_listings.source),
                title         = COALESCE(EXCLUDED.title,         auction_listings.title),
                price_current = COALESCE(EXCLUDED.price_current, auction_listings.price_current),
                bids_count    = COALESCE(EXCLUDED.bids_count,    auction_listings.bids_count),
                end_time      = COALESCE(EXCLUDED.end_time,      auction_listings.end_time),
                url           = COALESCE(EXCLUDED.url,           auction_listings.url),
                detail_url    = COALESCE(EXCLUDED.detail_url,    auction_listings.detail_url),
                sale_type     = COALESCE(EXCLUDED.sale_type,     auction_listings.sale_type),
                roi_estimate  = COALESCE(EXCLUDED.roi_estimate,  auction_listings.roi_estimate),
                max_bid       = COALESCE(EXCLUDED.max_bid,       auction_listings.max_bid),
                notes         = COALESCE(EXCLUDED.notes,         auction_listings.notes),
                source_id     = COALESCE(EXCLUDED.source_id,     auction_listings.source_id),
                model_key     = COALESCE(EXCLUDED.model_key,     auction_listings.model_key),
                time_left_s   = COALESCE(EXCLUDED.time_left_s,   auction_listings.time_left_s),
                status        = COALESCE(EXCLUDED.status,        auction_listings.status),
                last_seen     = CURRENT_TIMESTAMP
            """
            ,
            {
                "source": source,
                "external_id": external_id,
                "title": title,
                "price_current": price_current,
                "bids_count": bids_count,
                "end_time": end_time,
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
    """Insert a new row into auction_price_history to track changes over time."""
    conn = connection
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO auction_price_history (auction_id, price, bids_count)
        VALUES (%s, %s, %s);
    """, (auction_id, price, bids_count))

    conn.commit()
    cur.close()
    conn.close()


def mark_auction_sold(external_id: str, final_price: int):
    """Mark auction as sold and set final hammer price."""
    with connection.cursor() as cur:
        cur.execute("""
            UPDATE auction_listings
            SET status = 'sold',
                price_final = %s,
                last_seen = CURRENT_TIMESTAMP
            WHERE external_id = %s;
        """, (final_price, external_id))
    connection.commit()


def mark_auction_expired(external_id: str):
    """Mark auction as unsold/expired if end time has passed and no sale."""
    with connection.cursor() as cur:
        cur.execute("""
            UPDATE auction_listings
            SET status = 'unsold',
                last_seen = CURRENT_TIMESTAMP
            WHERE external_id = %s;
        """, (external_id,))
    connection.commit()


def get_active_auctions():
    """Return all active auctions (useful for heartbeat polling)."""
    with connection.cursor() as cur:
        cur.execute("""
            SELECT id, source_id, external_id, title, price_current, bids_count, end_time, url
            FROM auction_listings
            WHERE status = 'active'
            ORDER BY end_time ASC;
        """)
        return cur.fetchall()
