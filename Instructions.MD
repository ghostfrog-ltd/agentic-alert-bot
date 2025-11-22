# Run as service

### Create file

From File local filesystem make: run_gf_heartbeat-prod.sh
```
#!/bin/bash
set -euo pipefail

LOGFILE="$HOME/ghostfrog_heartbeat_wrapper.log"

echo "[$(date)] Wrapper starting…" >> "$LOGFILE"

TARGET="/Volumes/Bob/www/ghostfrog-agentic-alert-bot-prod/bin/python"

# Wait until Bob & python exist (max ~2 minutes)
for i in {1..60}; do
  if [ -x "$TARGET" ]; then
    echo "[$(date)] Found $TARGET, starting heartbeat…" >> "$LOGFILE"
    break
  fi
  echo "[$(date)] $TARGET not found yet, retry $i…" >> "$LOGFILE"
  sleep 2
done

if [ ! -x "$TARGET" ]; then
  echo "[$(date)] ERROR: $TARGET still not found after retries, exiting 78" >> "$LOGFILE"
  exit 78
fi

# Go to project dir on Bob
cd /Volumes/Bob/www/ghostfrog-agentic-alert-bot-prod

# Load .env if present
if [ -f ".env" ]; then
  echo "[$(date)] Loading .env…" >> "$LOGFILE"
  export $(grep -v '^[[:space:]]*#' .env | grep -v '^[[:space:]]*$' | xargs) || true
fi

# Run the working command exactly as you do manually
echo "[$(date)] Exec heartbeat now…" >> "$LOGFILE"
exec /Volumes/Bob/www/ghostfrog-agentic-alert-bot-prod/bin/python app.py heartbeat

```

also make:

```
#!/bin/bash

PLIST="$HOME/Library/LaunchAgents/com.ghostfrog.prod.plist"
LABEL="com.ghostfrog.agentic-alert-bot-prod"

case "$1" in
  start)
    echo "🚀 Starting PROD GhostFrog…"
    launchctl bootstrap gui/$UID "$PLIST"
    launchctl kickstart -k gui/$UID/$LABEL
    ;;

  stop)
    echo "🛑 Stopping PROD GhostFrog…"
    launchctl bootout gui/$UID "$PLIST" 2>/dev/null
    ;;

  restart)
    echo "🔄 Restarting PROD GhostFrog…"
    launchctl bootout gui/$UID "$PLIST" 2>/dev/null
    launchctl bootstrap gui/$UID "$PLIST"
    launchctl kickstart -k gui/$UID/$LABEL
    ;;

  status)
    echo "📡 Status:"
    launchctl list | grep "$LABEL" && tail -n 5 "$HOME/ghostfrog_heartbeat_wrapper.log"
    ;;

  *)
    echo "Usage: gf-prod {start|stop|restart|status}"
    exit 1
    ;;
esac
```

add to path (the location of the files)

```
echo 'export PATH="$HOME/sites:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### LOAD AND START

then we can run

```
gf-prod status
gf-dev restart
gf-bob stop
```

### Check its listed

launchctl list | grep ghostfrog

### Tail Logs

cat /Users/garyconstable/sites/ghostfrog-agentic-alert-bot/heartbeat.log
cat /Users/garyconstable/sites/ghostfrog-agentic-alert-bot/heartbeat.err

# PIP

### install a package

```
source bin/activate       
pip install lxml         
pip freeze > requirements.txt
```

### Install from txt file
```
pip install -r requirements.txt
```
