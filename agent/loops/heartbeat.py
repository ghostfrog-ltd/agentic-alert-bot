# agent/loops/heartbeat.py
from agent.actions.scrape_sources import run
from agent.state import should_scrape_now
from infrastructure.utils.logger import get_logger
import time

logger = get_logger(__name__)

def tick():
    if should_scrape_now():
        try:
            run()
        except Exception as e:
            logger.error(f"[Heartbeat] scrape run failed: {e}")
    time.sleep(5)
