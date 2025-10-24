from datetime import datetime, timedelta
from infrastructure.db.schema import connection
from datetime import datetime, timezone, timedelta

now = datetime.now(timezone.utc)

SCRAPE_INTERVAL_MINUTES = 5

def should_scrape_now():
    with connection.cursor() as cur:
        cur.execute("SELECT last_scrape FROM scrape_state WHERE id = 1")
        last_scrape = cur.fetchone()[0]
        if last_scrape is None or now - last_scrape >= timedelta(minutes=SCRAPE_INTERVAL_MINUTES):
            cur.execute("UPDATE scrape_state SET last_scrape = %s WHERE id = 1", (datetime.utcnow(),))
            connection.commit()
            return True
    return False
