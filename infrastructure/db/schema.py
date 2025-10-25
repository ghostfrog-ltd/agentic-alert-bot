import psycopg2
from infrastructure.utils import db_connection
from dataclasses import asdict
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
