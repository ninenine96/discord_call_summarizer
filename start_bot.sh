#!/bin/bash

cd "$(dirname "$0")"

EXISTING=$(pgrep -f "discord_bot.py")
if [ -n "$EXISTING" ]; then
    echo "Stopping existing bot (PID $EXISTING)..."
    pkill -TERM -f "discord_bot.py"
    sleep 4
    pkill -9 -f "discord_bot.py" 2>/dev/null
    sleep 1
fi

source venv/bin/activate
nohup python -u discord_bot.py > bot.log 2>&1 &
echo "Bot started with PID $!"
