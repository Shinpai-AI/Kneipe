#!/usr/bin/env python3
"""Kneipen-Schlägerei System-Tray Icon.
Startet Server, zeigt Status, Rechtsklick-Menü.
Universell: Linux (AppIndicator3) + Windows (pystray) + macOS (pystray)."""

import os, sys, signal, subprocess, threading, time, webbrowser, tempfile, platform
from pathlib import Path
from PIL import Image, ImageDraw

SCRIPT_DIR = Path(__file__).resolve().parent
PORT = 4567
URL = f"http://localhost:{PORT}"
IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"
server_proc = None
status = "starting"


def _prepare_logo_icon():
    """Logo als Tray-Icon vorbereiten (skaliert auf 64x64)."""
    icon_dir = Path(tempfile.gettempdir()) / "kneipe-icons"
    icon_dir.mkdir(exist_ok=True)
    icon_name = "kneipe-logo"
    path = icon_dir / f"{icon_name}.png"
    if not path.exists():
        logo_path = SCRIPT_DIR / "kneipe.png"
        if logo_path.exists():
            img = Image.open(str(logo_path)).resize((64, 64), Image.LANCZOS)
            img.save(str(path))
        else:
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.ellipse([4, 4, 60, 60], fill=(212, 168, 80, 255))
            img.save(str(path))
    return str(icon_dir), icon_name, str(path)

ICON_DIR, ICON_NAME, ICON_PATH = _prepare_logo_icon()


def kill_old_servers():
    """Alte Server-Prozesse auf Port killen."""
    try:
        if IS_WINDOWS:
            # Alle Prozesse auf dem Port finden und killen
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=5)
            killed = set()
            for line in result.stdout.split("\n"):
                if f":{PORT}" in line and "LISTENING" in line:
                    pid = line.strip().split()[-1]
                    if pid not in killed and pid != "0":
                        try:
                            subprocess.run(["taskkill", "/F", "/PID", pid],
                                           capture_output=True, timeout=5)
                            killed.add(pid)
                        except Exception:
                            pass
            if killed:
                time.sleep(2)
        else:
            result = subprocess.run(["lsof", "-t", "-i", f":{PORT}"],
                                    capture_output=True, text=True, timeout=5)
            if result.stdout.strip():
                for pid in result.stdout.strip().split("\n"):
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                    except (ProcessLookupError, ValueError):
                        pass
                time.sleep(2)
    except Exception:
        pass


def find_python():
    """Python finden: venv > embedded python > system python."""
    candidates = []
    if IS_WINDOWS:
        candidates = [
            SCRIPT_DIR / "venv" / "Scripts" / "python.exe",
            SCRIPT_DIR / "venv" / "Scripts" / "python3.exe",
            SCRIPT_DIR / "python" / "python.exe",
            SCRIPT_DIR / "python" / "pythonw.exe",
        ]
    else:
        candidates = [
            SCRIPT_DIR / "venv" / "bin" / "python3",
            SCRIPT_DIR / "env" / "bin" / "python3",
        ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def start_server_process():
    """Server starten und warten bis er antwortet."""
    global server_proc, status

    kill_old_servers()

    python = find_python()
    server_py = SCRIPT_DIR / "server.py"

    if not python:
        status = "error"
        return

    # Server-Log in Datei schreiben statt DEVNULL (zum Debuggen)
    log_path = SCRIPT_DIR / "logs"
    log_path.mkdir(exist_ok=True)
    log_file = open(str(log_path / "server-stdout.log"), "a")
    kwargs = {"cwd": str(SCRIPT_DIR), "stdout": log_file, "stderr": log_file}
    if not IS_WINDOWS:
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    server_proc = subprocess.Popen([python, str(server_py)], **kwargs)

    import urllib.request
    for _ in range(30):
        time.sleep(1)
        if server_proc.poll() is not None:
            status = "error"
            return
        try:
            urllib.request.urlopen(URL, timeout=2)
            status = "running"
            webbrowser.open(URL)
            return
        except Exception:
            pass
    status = "error"


def monitor_server():
    """Server-Prozess ueberwachen."""
    global status
    while True:
        time.sleep(5)
        if server_proc and server_proc.poll() is not None:
            if status != "error":
                status = "error"


def quit_app():
    """Server beenden und App schliessen."""
    global server_proc
    if server_proc:
        if IS_WINDOWS:
            # Windows: taskkill /F /T killt den ganzen Prozessbaum
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(server_proc.pid)],
                               capture_output=True, timeout=5)
            except Exception:
                server_proc.kill()
        else:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()
    # Sicherheitshalber: Port nochmal freigeben
    kill_old_servers()


# ═══════════════════════════════════════════
#  LINUX: AppIndicator3 + GTK3
# ═══════════════════════════════════════════

def run_linux():
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('AppIndicator3', '0.1')
    from gi.repository import Gtk, AppIndicator3, GLib

    indicator = AppIndicator3.Indicator.new(
        "kneipe-tray", ICON_NAME,
        AppIndicator3.IndicatorCategory.APPLICATION_STATUS)
    indicator.set_icon_theme_path(ICON_DIR)
    indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

    menu = Gtk.Menu()
    item_open = Gtk.MenuItem(label="Kneipe öffnen")
    item_open.connect("activate", lambda _: webbrowser.open(URL))
    menu.append(item_open)

    item_status = Gtk.MenuItem(label="Status")
    def show_status(_):
        msgs = {"running": f"Server läuft auf Port {PORT}",
                "starting": "Server startet...",
                "error": "Server-Fehler! Logs prüfen."}
        d = Gtk.MessageDialog(message_type=Gtk.MessageType.INFO,
                              buttons=Gtk.ButtonsType.OK,
                              text="Kneipen-Schlägerei",
                              secondary_text=msgs.get(status, "Unbekannt"))
        d.run(); d.destroy()
    item_status.connect("activate", show_status)
    menu.append(item_status)

    menu.append(Gtk.SeparatorMenuItem())

    item_quit = Gtk.MenuItem(label="Beenden")
    item_quit.connect("activate", lambda _: (quit_app(), Gtk.main_quit()))
    menu.append(item_quit)

    menu.show_all()
    indicator.set_menu(menu)

    threading.Thread(target=start_server_process, daemon=True).start()
    threading.Thread(target=monitor_server, daemon=True).start()
    Gtk.main()


# ═══════════════════════════════════════════
#  WINDOWS / macOS: pystray
# ═══════════════════════════════════════════

def run_pystray():
    import pystray

    icon_image = Image.open(ICON_PATH)

    def on_open(icon, item):
        webbrowser.open(URL)

    def on_status_click(icon, item):
        msgs = {"running": f"Server läuft auf Port {PORT}",
                "starting": "Server startet...",
                "error": "Server-Fehler! Logs prüfen."}
        # Einfache Benachrichtigung
        try:
            icon.notify(msgs.get(status, "Unbekannt"), "Kneipen-Schlägerei")
        except Exception:
            pass

    def on_quit_click(icon, item):
        quit_app()
        icon.stop()

    icon = pystray.Icon(
        "kneipe",
        icon_image,
        "Kneipen-Schlägerei",
        menu=pystray.Menu(
            pystray.MenuItem("Kneipe öffnen", on_open, default=True),
            pystray.MenuItem("Status", on_status_click),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Beenden", on_quit_click),
        )
    )

    def startup():
        start_server_process()

    threading.Thread(target=startup, daemon=True).start()
    threading.Thread(target=monitor_server, daemon=True).start()
    icon.run()


# ═══════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════

if __name__ == "__main__":
    if IS_LINUX:
        try:
            run_linux()
        except Exception:
            # Fallback auf pystray wenn AppIndicator nicht verfügbar
            run_pystray()
    else:
        run_pystray()
