#!/bin/bash
DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
cd "$DIR"

# venv erstellen wenn nötig
if [ ! -f "$DIR/venv/bin/python3" ]; then
  python3 -m venv "$DIR/venv"
  "$DIR/venv/bin/pip" install --upgrade pip -q
  "$DIR/venv/bin/pip" install -r "$DIR/requirements.txt" -q
fi

# Server starten
"$DIR/venv/bin/python3" "$DIR/server.py" &
SERVER_PID=$!

# Warten bis Server antwortet
for i in $(seq 1 30); do
  sleep 1
  curl -s -o /dev/null "http://localhost:4567" 2>/dev/null && break
done

# Browser öffnen
open "http://localhost:4567"

# Warten
wait $SERVER_PID
