import psycopg2
from infrastructure.utils import db_connection
from dataclasses import asdict

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


def resolve_source_id(domain: str) -> int:
    """
    Returns the ID of a source by its domain.
    If it doesn't exist, insert it and return the new ID.
    """
    with connection.cursor() as cur:
        # 1. Check if source already exists
        cur.execute("SELECT id FROM sources WHERE base_url = %s", (domain,))
        row = cur.fetchone()
        if row:
            return row[0]

        # 2. Insert a new one if not found
        cur.execute("""
            INSERT INTO sources (name, base_url, enabled, created_at_utc, updated_at_utc)
            VALUES (%s, %s, true, NOW(), NOW())
            RETURNING id
        """, (domain, domain))
        new_id = cur.fetchone()[0]
    connection.commit()
    return new_id


def upsert_article(article):
    UPSERT_ARTICLE_SQL = """
    INSERT INTO articles (
        source_id, title, url, summary, published_at,
        fetched_at,            -- keep column, but use DEFAULT
        content, sentiment,    -- keep columns, but use DEFAULT
        created_at_utc, updated_at_utc
    )
    VALUES (
        %(source_id)s, %(title)s, %(url)s, %(summary)s, %(published_at)s,
        DEFAULT,               -- fetched_at -> table default CURRENT_TIMESTAMP
        DEFAULT,               -- content    -> NULL by default
        DEFAULT,               -- sentiment  -> NULL by default
        NOW(), NOW()
    )
    ON CONFLICT (url) DO UPDATE SET
        source_id       = EXCLUDED.source_id,
        title           = COALESCE(EXCLUDED.title,        articles.title),
        summary         = COALESCE(EXCLUDED.summary,      articles.summary),
        published_at    = COALESCE(EXCLUDED.published_at, articles.published_at),
        fetched_at      = NOW(),
        content         = COALESCE(EXCLUDED.content,      articles.content),
        sentiment       = COALESCE(EXCLUDED.sentiment,    articles.sentiment),
        updated_at_utc  = NOW()
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
