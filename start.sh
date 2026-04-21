#!/bin/bash
# Kneipen-Schlägerei — Server
# Shinpai Games | Ist einfach passiert. 🐉
# 1:1 nach ShinNexus-Pattern: autarkes venv, setsid-Start, PID-File.
# systemd-Unit (Type=forking) ruft dieses Skript auf — NICHT umgekehrt.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_PATH="$SCRIPT_DIR/$(basename "$0")"
PID_FILE="$SCRIPT_DIR/.kneipe.pid"
PORT=4567
PYTHON="$SCRIPT_DIR/venv/bin/python3"
MAIN="server.py"

show_intro() {
  echo ""
  echo "  ╔══════════════════════════════════════╗"
  echo "  ║                                      ║"
  echo "  ║  🍺  KNEIPEN-SCHLÄGEREI  🍺          ║"
  echo "  ║                                      ║"
  echo "  ║  Shinpai Games — Port $PORT           ║"
  echo "  ║  Seelenfick für die Kneipe.          ║"
  echo "  ║                                      ║"
  echo "  ║  🐉 Ist einfach passiert.            ║"
  echo "  ║                                      ║"
  echo "  ╚══════════════════════════════════════╝"
  echo ""
}

# Idempotenter venv-Setup: neu anlegen wenn fehlt, requirements bei Hash-Änderung syncen.
setup_env() {
  local fresh=0
  if [ ! -d "$SCRIPT_DIR/venv" ]; then
    echo "  📦 Erstelle venv..."
    if ! python3 -m venv "$SCRIPT_DIR/venv" 2>&1; then
      echo "  ❌ 'python3 -m venv' fehlgeschlagen — evtl. fehlt das Paket 'python3-venv'."
      exit 1
    fi
    "$PYTHON" -m pip install --upgrade pip -q
    fresh=1
  fi
  if [ ! -x "$PYTHON" ]; then
    echo "  ❌ venv defekt — $PYTHON fehlt. Fix: rm -rf '$SCRIPT_DIR/venv' && '$SCRIPT_PATH' start"
    exit 1
  fi
  if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    REQ_HASH_FILE="$SCRIPT_DIR/venv/.requirements.sha1"
    NEW_HASH=$(sha1sum "$SCRIPT_DIR/requirements.txt" | awk '{print $1}')
    OLD_HASH=$(cat "$REQ_HASH_FILE" 2>/dev/null || echo "")
    if [ "$fresh" = "1" ] || [ "$NEW_HASH" != "$OLD_HASH" ]; then
      echo "  📦 Synce requirements.txt..."
      if ! "$PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt" -q; then
        echo "  ❌ pip install fehlgeschlagen."
        exit 1
      fi
      echo "$NEW_HASH" > "$REQ_HASH_FILE"
    fi
  fi
  [ "$fresh" = "1" ] && echo "  ✅ venv erstellt"
}

wait_port_free() {
  for i in $(seq 1 10); do
    PIDS=$(lsof -t -i :$PORT 2>/dev/null || ss -tlnp 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+')
    [ -z "$PIDS" ] && return 0
    [ "$i" -eq 5 ] && kill -9 $PIDS 2>/dev/null
    sleep 1
  done
}

kill_pgid() {
  local pid="$1"
  local sig="${2:-TERM}"
  [ -z "$pid" ] && return 1
  kill -0 "$pid" 2>/dev/null || return 0
  local pgid
  pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
  if [ -n "$pgid" ] && [ "$pgid" != "0" ]; then
    kill "-$sig" -- "-$pgid" 2>/dev/null
  else
    kill "-$sig" "$pid" 2>/dev/null
  fi
}

graceful_stop_pid() {
  local pid="$1"
  [ -z "$pid" ] && return 0
  kill -0 "$pid" 2>/dev/null || return 0
  kill_pgid "$pid" TERM
  for i in 1 2 3 4 5 6; do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.5
  done
  kill_pgid "$pid" KILL
}

run_converter() {
  [ -f "$SCRIPT_DIR/converter.py" ] && "$PYTHON" "$SCRIPT_DIR/converter.py" 2>/dev/null
}

case "${1:-start}" in
  start)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "🍺 Kneipen-Schlägerei läuft bereits (PID $(cat "$PID_FILE"))"
      exit 0
    fi
    FOREIGN_PIDS=$(lsof -t -i :$PORT 2>/dev/null)
    if [ -n "$FOREIGN_PIDS" ]; then
      echo "  ⚠️  Port $PORT belegt von fremdem Prozess (PID $FOREIGN_PIDS) — räume auf..."
      wait_port_free
    fi
    setup_env
    show_intro
    mkdir -p "$SCRIPT_DIR/logs"
    cd "$SCRIPT_DIR"
    run_converter
    echo "  🍺 Starte Server..."
    setsid "$PYTHON" "$MAIN" > /dev/null 2>&1 &
    echo $! > "$PID_FILE"
    for i in $(seq 1 8); do
      sleep 1
      lsof -i :$PORT &>/dev/null && break
    done
    if kill -0 "$(cat "$PID_FILE")" 2>/dev/null && lsof -i :$PORT &>/dev/null; then
      echo ""
      echo "  ╔══════════════════════════════════════╗"
      echo "  ║  ✅ Kneipen-Schlägerei etabliert     ║"
      echo "  ║  🌐 http://127.0.0.1:$PORT           ║"
      echo "  ║  📋 Logs: logs/server.log            ║"
      echo "  ║  Terminal kann geschlossen werden.   ║"
      echo "  ╚══════════════════════════════════════╝"
      echo ""
    else
      echo "  ❌ Start fehlgeschlagen! Check logs/server.log"
      rm -f "$PID_FILE"
      exit 1
    fi
    exit 0
    ;;
  stop)
    if [ -f "$PID_FILE" ]; then
      graceful_stop_pid "$(cat "$PID_FILE")"
      rm -f "$PID_FILE"
    fi
    PIDS=$(lsof -t -i :$PORT 2>/dev/null)
    for p in $PIDS; do graceful_stop_pid "$p"; done
    wait_port_free
    echo "🛑 Kneipen-Schlägerei gestoppt"
    ;;
  restart)
    "$SCRIPT_PATH" stop
    wait_port_free
    "$SCRIPT_PATH" start
    ;;
  status)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "🍺 Läuft (PID $(cat "$PID_FILE"))"
    else
      echo "💤 Nicht aktiv"
      rm -f "$PID_FILE" 2>/dev/null
    fi
    ;;
  logs) tail -f "$SCRIPT_DIR/logs/server.log" ;;
  *) echo "Usage: $0 {start|stop|restart|status|logs}"; exit 1 ;;
esac
