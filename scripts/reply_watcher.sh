#!/bin/sh
# Run check_replies.py --watch in the background on macOS, so candidates'
# replies are answered automatically within a minute: it starts at login and
# restarts if it ever stops. Output goes to logs/replies.log.
#
#   scripts/reply_watcher.sh install     # start now and at every login
#   scripts/reply_watcher.sh status      # is it running? recent log lines
#   scripts/reply_watcher.sh uninstall   # stop it and remove it
set -e

LABEL="com.resume-screener.replies"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$PROJECT/logs/replies.log"
UV="$(command -v uv || true)"

case "$1" in
  install)
    [ -n "$UV" ] || { echo "uv not found on PATH"; exit 1; }
    mkdir -p "$PROJECT/logs" "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$UV</string><string>run</string><string>check_replies.py</string><string>--watch</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$(dirname "$UV"):/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "Reply watcher installed and running. Log: $LOG"
    ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Reply watcher stopped and removed."
    ;;
  status)
    if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
      launchctl print "gui/$(id -u)/$LABEL" | grep -E "^\s+(state|pid) ="
    else
      echo "Not installed."
    fi
    [ -f "$LOG" ] && tail -n 15 "$LOG"
    ;;
  *)
    echo "Usage: $0 install|status|uninstall"; exit 1 ;;
esac
