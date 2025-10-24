
import argparse
from agent.loops.heartbeat import tick
from agent.actions.scrape_sources import run as run_scrape

parser = argparse.ArgumentParser()
parser.add_argument("cmd", choices=["heartbeat", "scrape"])
args = parser.parse_args()

if args.cmd == "heartbeat":
    tick()
elif args.cmd == "scrape":
    run_scrape()
