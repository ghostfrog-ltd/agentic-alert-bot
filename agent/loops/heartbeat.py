from agent.state import should_scrape_now
from agent.actions.scrape_sources import run as run_scrape

def tick():
    if should_scrape_now():
        run_scrape()