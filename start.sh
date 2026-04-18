#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=4567
SERVICE="kneipe"
VENV_DIR="$SCRIPT_DIR/venv"
PYTHON="$VENV_DIR/bin/python3"
PIP="$VENV_DIR/bin/pip"

# ── VENV: Lokale Python-Umgebung (autark, keine systemweiten Deps!) ──
ensure_venv() {
  if [ ! -f "$PYTHON" ]; then
    echo "  🐍 Erstelle lokale Python-Umgebung..."
    python3 -m venv "$VENV_DIR"
    echo "  📦 Installiere Abhängigkeiten..."
    "$PIP" install --upgrade pip -q
    "$PIP" install -r "$SCRIPT_DIR/requirements.txt" -q
    echo "  ✅ Umgebung bereit!"
  fi
}

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

# Prüfe ob systemd-Service existiert und aktiv ist
has_service() {
  systemctl is-enabled "$SERVICE" &>/dev/null
}

case "${1:-start}" in
  start)
    if has_service; then
      if systemctl is-active "$SERVICE" &>/dev/null; then
        echo "🍺 Kneipen-Schlägerei läuft bereits (systemd)"
        systemctl status "$SERVICE" --no-pager -l 2>/dev/null | head -5
        exit 0
      fi
      show_intro
      ensure_venv
      echo "  📖 Themen konvertieren..."
      cd "$SCRIPT_DIR" && "$PYTHON" converter.py 2>/dev/null
      echo ""
      echo "  🍺 Starte Server (systemd)..."
      sudo systemctl start "$SERVICE"
      sleep 2
      if systemctl is-active "$SERVICE" &>/dev/null; then
        PID=$(systemctl show "$SERVICE" -p MainPID --value)
        echo "  ✅ Server läuft (PID $PID)"
        echo "  🌐 http://127.0.0.1:$PORT"
        echo "  📋 Logs: journalctl -u $SERVICE -f"
        echo "  📋 App-Log: $SCRIPT_DIR/logs/server.log"
      else
        echo "  ❌ Start fehlgeschlagen!"
        systemctl status "$SERVICE" --no-pager -l 2>/dev/null | tail -10
      fi
      echo ""
    else
      # Fallback: kein systemd-Service → nohup wie bisher
      ensure_venv
      echo "  ⚠️ Kein systemd-Service gefunden, starte manuell..."
      PIDS=$(lsof -t -i :$PORT 2>/dev/null)
      if [ -n "$PIDS" ]; then
        echo "  ⚠️ Port $PORT belegt (PID: $PIDS) — wird beendet..."
        kill $PIDS 2>/dev/null; sleep 3
        PIDS=$(lsof -t -i :$PORT 2>/dev/null)
        [ -n "$PIDS" ] && kill -9 $PIDS 2>/dev/null && sleep 1
        echo "  ✅ Port $PORT frei!"
      fi
      show_intro
      echo "  📖 Themen konvertieren..."
      cd "$SCRIPT_DIR" && "$PYTHON" converter.py 2>/dev/null
      echo ""
      # Port 4567 belegt? Erst aufräumen.
      OLD_PIDS=$(lsof -t -i :$PORT 2>/dev/null)
      if [ -n "$OLD_PIDS" ]; then
        echo "  ⚠️ Port $PORT noch belegt (PID: $OLD_PIDS) — wird beendet..."
        kill $OLD_PIDS 2>/dev/null
        sleep 1
        STILL=$(lsof -t -i :$PORT 2>/dev/null)
        [ -n "$STILL" ] && kill -9 $STILL 2>/dev/null && sleep 1
        echo "  ✅ Port $PORT frei!"
      fi
      echo "  🍺 Starte Server (daemonisiert)..."
      mkdir -p "$SCRIPT_DIR/logs"
      # setsid -f = forkt SOFORT in neue Session → Script kehrt sauber zurück
      setsid -f "$PYTHON" "$SCRIPT_DIR/server.py" </dev/null >>"$SCRIPT_DIR/logs/stdout.log" 2>&1
      # Retry-Loop: bis 8 Sekunden warten bis Port gebunden ist
      SERVER_PID=""
      for i in 1 2 3 4 5 6 7 8; do
        sleep 1
        SERVER_PID=$(lsof -t -i :$PORT 2>/dev/null | head -1)
        [ -n "$SERVER_PID" ] && break
      done
      if [ -n "$SERVER_PID" ]; then
        echo "$SERVER_PID" > "$SCRIPT_DIR/.server.pid"
        echo "  ✅ Server läuft (PID $SERVER_PID)"
        echo "  🌐 http://127.0.0.1:$PORT"
        echo "  📋 App-Log:  $SCRIPT_DIR/logs/server.log"
        echo "  📋 Stdout:   $SCRIPT_DIR/logs/stdout.log"
        echo ""
        echo "  🍺 Fertig — Terminal kann geschlossen werden."
      else
        echo "  ❌ Start fehlgeschlagen! Letzte 20 Zeilen aus stdout.log:"
        tail -20 "$SCRIPT_DIR/logs/stdout.log" 2>/dev/null
      fi
      echo ""
    fi
    ;;
  stop)
    if has_service; then
      sudo systemctl stop "$SERVICE"
      echo "🛑 Kneipen-Schlägerei gestoppt (systemd)"
    else
      PID_FILE="$SCRIPT_DIR/.server.pid"
      [ -f "$PID_FILE" ] && kill "$(cat "$PID_FILE")" 2>/dev/null && rm -f "$PID_FILE"
      PIDS=$(lsof -t -i :$PORT 2>/dev/null)
      [ -n "$PIDS" ] && kill $PIDS 2>/dev/null
      echo "🛑 Kneipen-Schlägerei gestoppt"
    fi
    ;;
  restart)
    if has_service; then
      echo "🔄 Kneipen-Schlägerei neustarten (systemd)..."
      cd "$SCRIPT_DIR" && "$PYTHON" converter.py 2>/dev/null
      sudo systemctl restart "$SERVICE"
      sleep 2
      if systemctl is-active "$SERVICE" &>/dev/null; then
        PID=$(systemctl show "$SERVICE" -p MainPID --value)
        echo "✅ Neugestartet (PID $PID)"
      else
        echo "❌ Restart fehlgeschlagen!"
        systemctl status "$SERVICE" --no-pager -l 2>/dev/null | tail -10
      fi
    else
      bash "$SCRIPT_DIR/start.sh" stop
      # Warten bis Port WIRKLICH frei ist (max 10s)
      for i in $(seq 1 10); do
        PIDS=$(lsof -t -i :$PORT 2>/dev/null)
        [ -z "$PIDS" ] && break
        [ "$i" -eq 5 ] && kill -9 $PIDS 2>/dev/null
        sleep 1
      done
      bash "$SCRIPT_DIR/start.sh" start
    fi
    ;;
  status)
    if has_service; then
      systemctl status "$SERVICE" --no-pager -l 2>/dev/null | head -10
    else
      PID_FILE="$SCRIPT_DIR/.server.pid"
      if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "🍺 Läuft (PID $(cat "$PID_FILE"))"
      else
        echo "💤 Nicht aktiv"
        rm -f "$PID_FILE" 2>/dev/null
      fi
    fi
    ;;
  logs)
    tail -f "$SCRIPT_DIR/logs/server.log"
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|logs}"
    exit 1
    ;;
esac
