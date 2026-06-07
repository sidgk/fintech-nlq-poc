#!/bin/bash
# Keepalive wrapper: runs the Slack bot and restarts it within seconds if it ever
# exits (crash, or the watchdog exiting on a dead Slack socket). This is what
# makes the bot survive network blips without you babysitting it.
#
#   bash bot/run_bot.sh            # run in foreground
#   nohup bash bot/run_bot.sh > /tmp/slackbot.log 2>&1 &   # background

set -uo pipefail
cd "$(dirname "$0")/.."                 # project root

set -a; source .env; set +a
source venv/bin/activate

while true; do
  echo "[keepalive] starting bot at $(date)"
  python bot/app.py
  code=$?
  echo "[keepalive] bot exited (code $code) at $(date) — restarting in 3s…"
  sleep 3
done
