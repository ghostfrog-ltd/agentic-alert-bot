import time
from agent.state import should_scrape_now
from agent.actions.scrape_sources import run as run_scrape
from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)

def tick():
    if should_scrape_now():
        logger.info("[Heartbeat] Scraping...")
        try:
            run_scrape()
        except Exception as e:
            logger.error(f"[Heartbeat] scrape run failed: {e}")
    else:
        logger.info("[Heartbeat] Not time yet")

    time.sleep(5)
