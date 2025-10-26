import psycopg2
from infrastructure.utils import db_connection
from dataclasses import asdict
from typing import Optional
from datetime import datetime
from typing import Optional

connection = db_connection.connection


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


def upsert_auction_listing(
        source: str,  # <-- add this
        external_id: str,
        title: str,
        price_current: int,
        bids_count: int,
        end_time,
        url: str,
        roi_estimate: float = None,
        max_bid: int = None,
        notes: str = None,
        source_id: int | None = None  # <-- optional, since you also have source_id column
):
    conn = connection
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO auction_listings (
                source, external_id, title, price_current, bids_count, end_time,
                url, fetched_at, roi_estimate, max_bid, notes, last_seen, source_id
            )
            VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, CURRENT_TIMESTAMP, %s, %s, %s, CURRENT_TIMESTAMP, %s
            )
            ON CONFLICT (external_id) DO UPDATE
            SET
                source        = EXCLUDED.source,
                title         = EXCLUDED.title,
                price_current = EXCLUDED.price_current,
                bids_count    = EXCLUDED.bids_count,
                end_time      = EXCLUDED.end_time,
                url           = EXCLUDED.url,
                fetched_at    = CURRENT_TIMESTAMP,
                roi_estimate  = EXCLUDED.roi_estimate,
                max_bid       = EXCLUDED.max_bid,
                notes         = EXCLUDED.notes,
                last_seen     = CURRENT_TIMESTAMP,
                source_id     = EXCLUDED.source_id;
        """, (
            source, external_id, title, price_current, bids_count, end_time,
            url, roi_estimate, max_bid, notes, source_id
        ))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()  # keep global connection open; don't conn.close()


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
