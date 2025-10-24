# Run as service

### Create file

```
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.ghostfrog.agentic-alert-bot</string>

    <key>ProgramArguments</key>
    <array>
      <string>/Users/garyconstable/sites/ghostfrog-agentic-alert-bot/bin/python</string>
      <string>/Users/garyconstable/sites/ghostfrog-agentic-alert-bot/app.py</string>
      <string>heartbeat</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/garyconstable/sites/ghostfrog-agentic-alert-bot</string>

    <key>StandardOutPath</key>
    <string>/Users/garyconstable/sites/ghostfrog-agentic-alert-bot/heartbeat.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/garyconstable/sites/ghostfrog-agentic-alert-bot/heartbeat.err</string>

    <!-- Start when loaded / on login -->
    <key>RunAtLoad</key>
    <true/>

    <!-- Relaunch if it exits/crashes -->
    <key>KeepAlive</key>
    <true/>
  </dict>
</plist>
```

### LOAD AND START

launchctl unload ~/Library/LaunchAgents/com.ghostfrog.agentic-alert-bot.plist 2>/dev/null || true
launchctl load  ~/Library/LaunchAgents/com.ghostfrog.agentic-alert-bot.plist
launchctl start com.ghostfrog.agentic-alert-bot

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
