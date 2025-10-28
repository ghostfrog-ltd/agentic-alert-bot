'''
from infrastructure.utils.emailer import send_email
send_email("Test Alert", "This is a test from GhostFrog Bot")

from infrastructure.db.migrations import apply_migrations
apply_migrations()

'''



from infrastructure.db.schema import connection
from infrastructure.utils.model_key import normalise_model

with connection, connection.cursor() as cur:
    cur.execute("SELECT id, title FROM auction_listings WHERE model_key IS NULL")
    rows = cur.fetchall()

for row_id, title in rows:
    key = normalise_model(title or "")
    if key:
        with connection, connection.cursor() as cur:
            cur.execute(
                "UPDATE auction_listings SET model_key = %s WHERE id = %s",
                (key, row_id)
            )