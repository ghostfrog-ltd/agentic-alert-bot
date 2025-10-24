from agent.state import should_scrape_now
from agent.actions.scrape_sources import run as run_scrape
import time

def tick():
    if should_scrape_now():
        print("[Heartbeat] Scraping...")
        run_scrape()
    else:
        print("[Heartbeat] Not time yet")
    time.sleep(5)