#!/usr/bin/env python3
"""Kneipen-Schlägerei System-Tray Icon.
Startet Server, zeigt Status, Rechtsklick-Menü.
Verwendet AppIndicator3 (funktioniert auf KDE Wayland + GNOME + X11)."""

import os, sys, signal, subprocess, threading, time, webbrowser, tempfile
from pathlib import Path

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AppIndicator3', '0.1')
from gi.repository import Gtk, AppIndicator3, GLib

SCRIPT_DIR = Path(__file__).resolve().parent
PORT = 4567
URL = f"http://localhost:{PORT}"
server_proc = None
status = "starting"  # starting, running, error
indicator = None


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
    return str(icon_dir), icon_name

ICON_DIR, ICON_NAME = _prepare_logo_icon()


def kill_old_servers():
    """Alte Server-Prozesse auf Port killen."""
    try:
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


def update_icon(new_status):
    """Icon-Status aendern (thread-safe via GLib.idle_add)."""
    global status
    status = new_status
    GLib.idle_add(indicator.set_icon_full, ICON_NAME, f"Kneipe - {new_status}")


def start_server():
    """Server starten und Status ueberwachen."""
    global server_proc

    kill_old_servers()

    venv_python = SCRIPT_DIR / "venv" / "bin" / "python3"
    server_py = SCRIPT_DIR / "server.py"

    if not venv_python.exists():
        update_icon("error")
        return

    server_proc = subprocess.Popen(
        [str(venv_python), str(server_py)],
        cwd=str(SCRIPT_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    import urllib.request
    for _ in range(30):
        time.sleep(1)
        if server_proc.poll() is not None:
            update_icon("error")
            return
        try:
            urllib.request.urlopen(URL, timeout=2)
            update_icon("running")
            webbrowser.open(URL)
            return
        except Exception:
            pass

    update_icon("error")


def monitor_server():
    """Server-Prozess ueberwachen."""
    while True:
        time.sleep(5)
        if server_proc and server_proc.poll() is not None:
            if status != "error":
                update_icon("error")


def on_open(_):
    webbrowser.open(URL)


def on_status(_):
    if status == "running":
        msg = f"Server laeuft auf Port {PORT}"
    elif status == "starting":
        msg = "Server startet..."
    else:
        msg = "Server-Fehler! Logs pruefen."
    dialog = Gtk.MessageDialog(
        message_type=Gtk.MessageType.INFO,
        buttons=Gtk.ButtonsType.OK,
        text="Kneipen-Schlägerei",
        secondary_text=msg
    )
    dialog.run()
    dialog.destroy()


def on_quit(_):
    global server_proc
    if server_proc:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()
    Gtk.main_quit()


def main():
    global indicator

    indicator = AppIndicator3.Indicator.new(
        "kneipe-tray",
        ICON_NAME,
        AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
    )
    indicator.set_icon_theme_path(ICON_DIR)
    indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

    # Rechtsklick-Menü
    menu = Gtk.Menu()

    item_open = Gtk.MenuItem(label="Kneipe öffnen")
    item_open.connect("activate", on_open)
    menu.append(item_open)

    item_status = Gtk.MenuItem(label="Status")
    item_status.connect("activate", on_status)
    menu.append(item_status)

    menu.append(Gtk.SeparatorMenuItem())

    item_quit = Gtk.MenuItem(label="Beenden")
    item_quit.connect("activate", on_quit)
    menu.append(item_quit)

    menu.show_all()
    indicator.set_menu(menu)

    # Server im Hintergrund starten
    threading.Thread(target=start_server, daemon=True).start()
    threading.Thread(target=monitor_server, daemon=True).start()

    Gtk.main()


if __name__ == "__main__":
    main()
