"""Patcht oqs.py damit die mitgelieferte DLL gefunden wird.
Wird im GitHub Actions Workflow nach dem Kopieren der oqs-Dateien ausgeführt."""
import sys, os

oqs_dir = sys.argv[1]  # z.B. installer-build/python/Lib/site-packages/oqs
oqs_py = os.path.join(oqs_dir, "oqs.py")

if not os.path.exists(oqs_py):
    print(f"oqs.py nicht gefunden: {oqs_py}")
    sys.exit(1)

with open(oqs_py, "r", encoding="utf-8") as f:
    content = f.read()

# Patch: Am Anfang der _load_liboqs() Funktion zuerst neben oqs.py suchen
patch = '''
# === SHINPAI-AI PATCH: DLL neben oqs.py suchen ===
def _load_liboqs_patched() -> ct.CDLL:
    """Sucht DLL zuerst im eigenen Ordner, dann original Fallback."""
    import sys
    _oqs_dir = str(Path(__file__).parent)
    _python_dir = str(Path(sys.executable).parent)
    # DLL-Ordner zum PATH hinzufuegen
    for d in [_oqs_dir, _python_dir]:
        if d not in os.environ.get("PATH", ""):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    # Direkt versuchen die DLL zu laden
    for search_dir in [_oqs_dir, _python_dir]:
        for name in ["oqs.dll", "liboqs.dll", "oqs.so", "liboqs.so"]:
            dll_path = os.path.join(search_dir, name)
            if os.path.exists(dll_path):
                try:
                    return ct.CDLL(dll_path)
                except OSError:
                    continue
    # Original-Funktion als Fallback
    return _load_liboqs_original()

_load_liboqs_original = _load_liboqs
_load_liboqs = _load_liboqs_patched
# === END PATCH ===
'''

# Patch nach der Funktionsdefinition einfügen (vor _liboqs = _load_liboqs())
content = content.replace(
    "_liboqs = _load_liboqs()",
    patch + "\n_liboqs = _load_liboqs()"
)

with open(oqs_py, "w", encoding="utf-8") as f:
    f.write(content)

print(f"oqs.py gepatcht: {oqs_py}")
