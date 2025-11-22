# Run as service

### Create file

```

~/Library/LaunchAgents/com.ghostfrog.prod.plist

<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.ghostfrog.prod</string>

    <key>ProgramArguments</key>
    <array>
      <string>/usr/bin/python3</string>
      <string>/Volumes/Bob/www/ghostfrog-agentic-alert-bot-prod/app.py</string>
      <string>heartbeat</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Volumes/Bob/www/ghostfrog-agentic-alert-bot-prod</string>

    <key>StandardOutPath</key>
    <string>/Volumes/Bob/www/ghostfrog-agentic-alert-bot-prod/heartbeat.log</string>

    <key>StandardErrorPath</key>
    <string>/Volumes/Bob/www/ghostfrog-agentic-alert-bot-prod/heartbeat.err</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>
  </dict>
</plist>

~/Library/LaunchAgents/com.ghostfrog.dev.plist

<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.ghostfrog.dev</string>

    <key>ProgramArguments</key>
    <array>
      <string>/usr/bin/python3</string>
      <string>/Volumes/Bob/www/ghostfrog-agentic-alert-bot-dev/app.py</string>
      <string>heartbeat</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Volumes/Bob/www/ghostfrog-agentic-alert-bot-dev</string>

    <key>StandardOutPath</key>
    <string>/Volumes/Bob/www/ghostfrog-agentic-alert-bot-dev/heartbeat.log</string>

    <key>StandardErrorPath</key>
    <string>/Volumes/Bob/www/ghostfrog-agentic-alert-bot-dev/heartbeat.err</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>
  </dict>
</plist>

~/Library/LaunchAgents/com.ghostfrog.bob.plist

<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.ghostfrog.bob</string>

    <key>ProgramArguments</key>
    <array>
      <string>/usr/bin/python3</string>
      <string>/Volumes/Bob/www/ghostfrog-agentic-alert-bot-bob/app.py</string>
      <string>heartbeat</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Volumes/Bob/www/ghostfrog-agentic-alert-bot-bob</string>

    <key>StandardOutPath</key>
    <string>/Volumes/Bob/www/ghostfrog-agentic-alert-bot-bob/heartbeat.log</string>

    <key>StandardErrorPath</key>
    <string>/Volumes/Bob/www/ghostfrog-agentic-alert-bot-bob/heartbeat.err</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>
  </dict>
</plist>
```

### LOAD AND START

# unload old ones if they exist
launchctl unload ~/Library/LaunchAgents/com.ghostfrog.prod.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.ghostfrog.dev.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.ghostfrog.bob.plist 2>/dev/null || true

# load new configs
launchctl load  ~/Library/LaunchAgents/com.ghostfrog.prod.plist
launchctl load  ~/Library/LaunchAgents/com.ghostfrog.dev.plist
launchctl load  ~/Library/LaunchAgents/com.ghostfrog.bob.plist

# start them
launchctl start com.ghostfrog.prod
launchctl start com.ghostfrog.dev
launchctl start com.ghostfrog.bob



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
