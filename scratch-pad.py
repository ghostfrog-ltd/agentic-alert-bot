#from agent.actions.maintenance.rebuild_model_keys import rebuild_model_keys as model_keys
#model_keys()

#from agent.reminders.reem import send_reem_test_email
#send_reem_test_email()

'''

from dotenv import load_dotenv

from agent.actions.telegram.roi_summary import build_roi_message

def main():
    msg = build_roi_message(limit=3)
    print(msg)

if __name__ == "__main__":
    main()

    from dotenv import load_dotenv
load_dotenv()

import os
from infrastructure.adapters.telegram import TelegramAdapter

adapter = TelegramAdapter.from_env()

chan_id = int(os.getenv("TELEGRAM_FIREHOSE_CHANNEL_ID"))
adapter.send_message("🔥 GhostFrog eBay Firehose test alert!", chat_id=chan_id)

#from agent.actions.maintenance.attributes import run

#run(limit=1)

from agent.actions.maintenance.rebuild_model_keys import rebuild_model_keys as model_keys
model_keys()

'''

from dotenv import load_dotenv
from infrastructure.db.schema import get_connection


def count_listings():
    conn = get_connection()  # raw psycopg connection
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM auction_listings")
    result = cur.fetchone()[0]
    cur.close()
    return result

if __name__ == "__main__":
    load_dotenv()  # make sure DATABASE_URL is loaded

    try:
        total = count_listings()
        print(f"🟢 OK: {total} auction listings found.")
    except Exception as e:
        print(f"🔴 DB ERROR: {e}")

