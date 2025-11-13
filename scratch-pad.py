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
'''


from dotenv import load_dotenv
load_dotenv()

import os
from infrastructure.adapters.telegram import TelegramAdapter

adapter = TelegramAdapter.from_env()

chan_id = int(os.getenv("TELEGRAM_FIREHOSE_CHANNEL_ID"))
adapter.send_message("🔥 GhostFrog eBay Firehose test alert!", chat_id=chan_id)