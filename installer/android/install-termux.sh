#!/data/data/com.termux/files/usr/bin/bash
# ============================================
#  Kneipen-Schlaegerei - Termux Installer
#  Fuer Android (Termux App von GitHub/F-Droid)
# ============================================

set -e

APP_NAME="Kneipen-Schlaegerei"
REPO_URL="https://github.com/Shinpai-AI/Kneipe.git"
INSTALL_DIR="$HOME/kneipe"
PORT=4567

echo ""
echo "=========================================="
echo "  $APP_NAME - Termux Installer"
echo "=========================================="
echo ""

# 1. System-Pakete
echo "[1/5] System-Pakete installieren..."
pkg update -y
pkg install -y python git curl build-essential libjpeg-turbo libpng zlib freetype rust openssl

# 2. App klonen oder updaten
echo ""
echo "[2/5] App herunterladen..."
if [ -d "$INSTALL_DIR" ]; then
    echo "  Update: Aktualisiere bestehende Installation..."
    cd "$INSTALL_DIR"
    git pull --ff-only || {
        echo "  Git pull fehlgeschlagen, loesche und klone neu..."
        cd "$HOME"
        rm -rf "$INSTALL_DIR"
        git clone "$REPO_URL" "$INSTALL_DIR"
        cd "$INSTALL_DIR"
    }
else
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# 3. Python venv erstellen
echo ""
echo "[3/5] Python-Umgebung einrichten..."
python -m venv env
source env/bin/activate

# 4. Dependencies installieren (ohne liboqs-python, kompiliert nicht auf ARM)
echo ""
echo "[4/5] Python-Pakete installieren..."
pip install --upgrade pip
pip install pyotp qrcode Pillow edge-tts cryptography hdwallet bitcoin-utils

# liboqs-python separat versuchen (optional, kann auf ARM fehlschlagen)
echo ""
echo "  PQ-Crypto installieren (optional)..."
pip install liboqs-python 2>/dev/null || {
    echo "  HINWEIS: liboqs-python nicht verfuegbar auf diesem Geraet."
    echo "  Post-Quantum Features sind deaktiviert, alles andere funktioniert!"
}

# 5. Start-Script erstellen
echo ""
echo "[5/5] Start-Script erstellen..."
cat > "$HOME/kneipe-start.sh" << 'STARTEOF'
#!/data/data/com.termux/files/usr/bin/bash
cd "$HOME/kneipe"
source env/bin/activate
echo "Kneipen-Schlaegerei startet auf Port 4567..."
echo "Oeffne im Browser: http://localhost:4567"
python server.py
STARTEOF
chmod +x "$HOME/kneipe-start.sh"

# Fertig!
echo ""
echo "=========================================="
echo "  INSTALLATION ERFOLGREICH!"
echo "=========================================="
echo ""
echo "  Starten:  bash ~/kneipe-start.sh"
echo "  Browser:  http://localhost:$PORT"
echo ""
echo "  Tipp: Termux offen lassen waehrend"
echo "  du die Kneipe im Browser nutzt!"
echo ""
echo "=========================================="
