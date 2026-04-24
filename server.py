#!/usr/bin/env python3
"""Kneipen-Schlägerei V1.5 — Game Server
Account-System, Theme-Engine, Titel-Berechnung, Teilnehmer-Liste.
Orpheus TTS, Voice Cloning, Barkeeper Ralf 32.
Vision 1: Durchsage + Tresen + PQ-Crypto (ML-DSA-65 + ML-KEM-768)
  + Nexus-Trust-Whitelist + Session-Limit + Sovereign-Pocket-Deployment.
Shinpai Games | Ist einfach passiert. 🐉"""

VERSION = "1.5.0"

import asyncio, base64, hashlib, hmac, io, json, logging, os, re, secrets, sqlite3, time, threading, uuid
from http.server import HTTPServer, ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime
import pyotp
import qrcode
from PIL import Image, ImageDraw, ImageFont
import edge_tts

BASE = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE, 'db')
THEMEN_DIR = os.path.join(BASE, 'themen_json')
VOICE_DIR = os.path.join(BASE, 'voices')
PORT = 4567
NAME_RE = re.compile(r'^[A-Za-z0-9]{1,12}$')
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(VOICE_DIR, exist_ok=True)

# --- LOGGING ---
LOG_DIR = os.path.join(BASE, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
from logging.handlers import RotatingFileHandler
log = logging.getLogger('KneipenSchlaegerei')
log.setLevel(logging.INFO)
handler = RotatingFileHandler(
    os.path.join(LOG_DIR, 'server.log'), maxBytes=5*1024*1024, backupCount=10, encoding='utf-8'
)
handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', '%Y-%m-%d %H:%M:%S'))
log.addHandler(handler)

# ═════════════════════════════════════════════════════════════════════════
#  VAULT — AES-256-GCM + PBKDF2 + machine-bound (PQ-Architektur Schicht 1)
#  Portiert von ShinNexus. Owner-Passwort + Machine-ID → Master-Key.
#  Format: KVAULT2(7) + Salt(32) + Nonce(12) + AES-256-GCM(data)
# ═════════════════════════════════════════════════════════════════════════

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes as _chashes
from argon2.low_level import hash_secret_raw, Type as _Argon2Type
from pathlib import Path as _Path

VAULT_DIR = os.path.join(BASE, 'vault')
CREDENTIALS_DIR = os.path.join(BASE, 'credentials')
IDENTITY_VAULT = os.path.join(VAULT_DIR, 'identity.vault')
RECOVERY_HASH_FILE = os.path.join(CREDENTIALS_DIR, 'recovery.hash')
RECOVERY_KEY_FILE = os.path.join(CREDENTIALS_DIR, 'recovery.enc')
# Ignition Key — 1:1 ShinNexus-Pattern
_IGNITION_DIR: _Path | None = None
_VAULT_BOOTSTRAP: _Path | None = None
_IGNI_SALT = b"Kneipe-Igni-2026"
# System Vault (maschinengebunden, 1:1 von ShinNexus)
SYSTEM_VAULT_FILE = _Path(VAULT_DIR) / "system.vault"
SYSTEM_SALT_FILE = _Path(VAULT_DIR) / "system.salt"
SYSTEM_OWNER_SIG = _Path(VAULT_DIR) / "system.owner.sig"
SIGNING_KEY_FILE = _Path(CREDENTIALS_DIR) / "signing_key.vault"

# PQ-Vault-Wrap (KEK/DEK/ML-KEM — 1:1 von ShinNexus)
SALT_FILE = _Path(VAULT_DIR) / ".salt"                                     # 16 Byte zufällig + 8 Byte Unix-Timestamp, unverschlüsselt, gehört ins Backup
VAULT_KEM_PRIV_FILE = _Path(CREDENTIALS_DIR) / "vault_kem_priv.vault"
VAULT_KEM_PRIV_SEED_FILE = _Path(CREDENTIALS_DIR) / "vault_kem_priv.seed.vault"
VAULT_KEM_PUB_FILE = _Path(CREDENTIALS_DIR) / "vault_kem_pub.key"
DEK_WRAP_FILE = _Path(VAULT_DIR) / "dek.wrap"

# Argon2id-Parameter (hard-coded nach PQ-Architektur.md „KDF-Härtung"):
# 128 MB RAM, 3 Iterationen, 4 Threads, 32 Byte Output. Keine Config, kein „schneller Modus".
_ARGON2_MEMORY_COST = 131_072   # KiB = 128 MB
_ARGON2_TIME_COST = 3
_ARGON2_PARALLELISM = 4
_ARGON2_HASH_LEN = 32

# Salzstreuer: max 1x pro 24h
_SALT_COOLDOWN_SECONDS = 24 * 3600

# Users (Vault-basiert, 1:1 von ShinNexus)
USERS_VAULT = _Path(VAULT_DIR) / "users.vault"

# Blockliste (Zeitsperre)
BLOCKLIST_VAULT = _Path(VAULT_DIR) / "blocklist.vault"

# Bitcoin
BTC_WALLET_VAULT = _Path(VAULT_DIR) / 'btc_wallet.vault'
ANCHOR_JSON = _Path(BASE) / 'anchor-kneipe.json'

os.makedirs(VAULT_DIR, exist_ok=True)
os.makedirs(CREDENTIALS_DIR, exist_ok=True)
try:
    os.chmod(CREDENTIALS_DIR, 0o700)
except OSError:
    pass

_VAULT_MAGIC = b'KVAULT2'
_vault_unlock_time = 0.0
_VAULT_MAX_AGE = 86400     # 24h Auto-Lock
_dek: bytes | None = None  # Data Encryption Key — RAM-only (PQ-Vault-Wrap, einziger Schlüssel!)


def _get_machine_id():
    """Machine-ID lesen (Linux: /etc/machine-id, Fallback: hostname)."""
    for p in ('/etc/machine-id', '/var/lib/dbus/machine-id'):
        try:
            mid = _Path(p).read_text().strip()
            if mid:
                return mid
        except (OSError, PermissionError):
            continue
    import platform
    return hashlib.sha256(f'{platform.node()}-{platform.machine()}'.encode()).hexdigest()


def _derive_vault_key(password: str, salt: bytes) -> bytes:
    """256-bit AES Key aus Passwort + Salt (PBKDF2, 600k Iterationen)."""
    kdf = PBKDF2HMAC(algorithm=_chashes.SHA256(), length=32, salt=salt, iterations=600_000)
    return kdf.derive(password.encode('utf-8'))


def _derive_file_key(salt: bytes) -> bytes:
    """Per-File AES-256 Key: SHA256(DEK + machine_id + file_salt).
    DEK kommt aus _pq_init_fresh / _pq_unlock_dek_via_password / _pq_unlock_dek_via_seed."""
    if _dek is None:
        raise RuntimeError('Vault gesperrt — unlock() zuerst!')
    mid = _get_machine_id().encode()
    return hashlib.sha256(_dek + mid + salt).digest()


def _vault_encrypt_bytes(plaintext: bytes, password: str = None) -> bytes:
    """AES-256-GCM Encrypt. Format: KVAULT2(7) + Salt(32) + Nonce(12) + Ciphertext."""
    salt = secrets.token_bytes(32)
    nonce = secrets.token_bytes(12)
    key = _derive_vault_key(password, salt) if password else _derive_file_key(salt)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, _VAULT_MAGIC)
    return _VAULT_MAGIC + salt + nonce + ciphertext


def _vault_decrypt_bytes(ciphertext: bytes, password: str = None) -> bytes:
    """AES-256-GCM Decrypt. Erwartet: KVAULT2(7) + Salt(32) + Nonce(12) + Ciphertext."""
    if not ciphertext.startswith(_VAULT_MAGIC):
        raise ValueError('Keine Vault-Datei (Magic mismatch — erwartet KVAULT2)')
    salt = ciphertext[7:39]
    nonce = ciphertext[39:51]
    encrypted = ciphertext[51:]
    key = _derive_vault_key(password, salt) if password else _derive_file_key(salt)
    return AESGCM(key).decrypt(nonce, encrypted, _VAULT_MAGIC)


def vault_is_unlocked() -> bool:
    """Vault-Status: entsperrt und nicht abgelaufen?
    Sliding Window: jeder erfolgreiche Check refresht den Timer (Owner-Activity = Keep-Alive).
    Bei Timeout wird Igni-Auto-Refresh versucht bevor hart gelockt wird."""
    global _vault_unlock_time
    if _dek is None:
        return False
    if time.time() - _vault_unlock_time > _VAULT_MAX_AGE:
        # Auto-Lock naht — erst Igni-Refresh versuchen bevor hart gelockt wird
        try:
            igni_pw = igni_load()
            if igni_pw and vault_unlock(igni_pw):
                log.info('🔑 Igni-Auto-Refresh erfolgreich — Vault-Timer auf 24h zurückgesetzt')
                return True
        except Exception as e:
            log.warning(f'Igni-Auto-Refresh-Fehler: {e}')
        vault_lock()
        log.info('🔒 Vault: 24h Timeout — automatisch gesperrt (kein Igni verfügbar)')
        return False
    _vault_unlock_time = time.time()  # Sliding Window — active use extends lease
    return True


def vault_lock():
    """Vault sperren — DEK aus RAM löschen."""
    global _vault_unlock_time, _dek
    _dek = None
    _vault_unlock_time = 0
    log.info('🔒 Vault gesperrt')


def _vault_keeper_loop():
    """Proaktiver Vault-Keeper: alle 60s prüfen ob Lock naht (<5min),
    dann präventiv Igni-Auto-Refresh. Läuft als Daemon-Thread.
    Vermeidet das Szenario wo Vault lockt zwischen zwei API-Calls und Server
    in Ersteinrichtungszustand fällt (Bug 2026-04-24)."""
    REFRESH_THRESHOLD = 300  # 5 Minuten vor Lock
    while True:
        try:
            time.sleep(60)
            if _dek is None:
                continue  # Vault eh schon zu, Keeper inaktiv bis manueller Unlock
            remaining = _VAULT_MAX_AGE - (time.time() - _vault_unlock_time)
            if remaining > REFRESH_THRESHOLD:
                continue
            # Lock droht in <5min — Igni-Refresh versuchen
            igni_pw = igni_load()
            if igni_pw and vault_unlock(igni_pw):
                log.info(f'🔑 Vault-Keeper: Igni-Refresh erfolgreich (Rest war {int(remaining)}s)')
            else:
                log.warning(f'🔑 Vault-Keeper: kein Igni oder Unlock failed — Vault lockt in {int(remaining)}s')
        except Exception as e:
            log.error(f'Vault-Keeper-Exception: {e}')


# ═════════════════════════════════════════════════════════════════════════
#  PQ-VAULT-WRAP — KEK/DEK mit ML-KEM-768 (1:1 von ShinNexus)
#  Schicht 1 konkret: Vault-Daten mit DEK, DEK mit ML-KEM gewrappt,
#  ML-KEM-Private mit KEK(PW+machine-id) verschlüsselt. PQ-nativer Key-Wrap.
# ═════════════════════════════════════════════════════════════════════════

def _pq_get_machine_id_bytes() -> bytes:
    try:
        return _Path("/etc/machine-id").read_text().strip().encode()
    except Exception:
        import platform
        return platform.node().encode()


# ── .salt — 16 Byte Zufall + Unix-Timestamp (last_rotated), unverschlüsselt ──
# Format: zwei Zeilen Text
#   Zeile 1: 32 hex chars (16 Byte zufälliger Salt)
#   Zeile 2: Unix-Timestamp (Sekunden) — letzter Salzstreuer-Zeitpunkt
# Salt ist NICHT geheim. Schutz vor Rainbow-Tables per Einzigartigkeit.
# Nicht in Igni packen — muss mit ins Vault-Backup!

def _salt_read() -> tuple[bytes, int] | None:
    """Liest .salt. Returns (salt_bytes_16, last_rotated_unix) oder None wenn Datei fehlt/defekt."""
    if not SALT_FILE.exists():
        return None
    try:
        text = SALT_FILE.read_text("utf-8").strip().splitlines()
        salt_bytes = bytes.fromhex(text[0].strip())
        last_rotated = int(text[1].strip()) if len(text) > 1 else 0
        if len(salt_bytes) != 16:
            return None
        return (salt_bytes, last_rotated)
    except Exception:
        return None


def _salt_write(salt_bytes: bytes, rotated_at: int | None = None) -> None:
    """Schreibt .salt (16 Byte Salt + Unix-Timestamp). chmod 600 — nicht geheim, aber hygienisch."""
    if len(salt_bytes) != 16:
        raise ValueError("Salt muss 16 Byte sein")
    os.makedirs(VAULT_DIR, exist_ok=True)
    ts = rotated_at if rotated_at is not None else int(time.time())
    SALT_FILE.write_text(f"{salt_bytes.hex()}\n{ts}\n", encoding="utf-8")
    try:
        os.chmod(str(SALT_FILE), 0o600)
    except OSError:
        pass


def _salt_ensure() -> bytes:
    """Liefert Salt. Erzeugt ihn einmalig wenn er noch nicht existiert."""
    existing = _salt_read()
    if existing is not None:
        return existing[0]
    new_salt = secrets.token_bytes(16)
    _salt_write(new_salt, rotated_at=int(time.time()))
    return new_salt


def _salt_metadata() -> dict:
    """Für UI/API: aktueller Salt (gekürzt hex), letzter Wurf, nächster Wurf möglich."""
    existing = _salt_read()
    if existing is None:
        return {"present": False, "salt_short": "", "last_rotated": 0, "cooldown_until": 0}
    salt_bytes, last_rotated = existing
    salt_hex = salt_bytes.hex()
    short = f"{salt_hex[:8]}…{salt_hex[-4:]}"
    return {
        "present": True,
        "salt_short": short,
        "last_rotated": last_rotated,
        "cooldown_until": last_rotated + _SALT_COOLDOWN_SECONDS,
    }


# ── Argon2id KDFs (PFLICHT, kein SHA256-Fallback!) ────────────────
# Parameter hard-coded (siehe PQ-Architektur.md „KDF-Härtung"):
#   128 MB Speicher, 3 Iterationen, 4 Threads, 32 Byte Output.
# Kein „schneller Modus", keine Config-Variante. Ende.

def _pq_derive_kek(password: str, salt_bytes: bytes) -> bytes:
    """32-Byte KEK aus Passwort + Salt + machine-id via Argon2id.
    Salt MUSS übergeben werden (aus _salt_ensure()/_salt_read()). Keine Default!"""
    if not salt_bytes or len(salt_bytes) != 16:
        raise ValueError("Salt (16 Byte) ist Pflicht für KEK-Ableitung")
    mid = _pq_get_machine_id_bytes()
    return hash_secret_raw(
        secret=b"shinpai-vault-kek-v4-" + password.encode("utf-8") + mid,
        salt=salt_bytes,
        time_cost=_ARGON2_TIME_COST,
        memory_cost=_ARGON2_MEMORY_COST,
        parallelism=_ARGON2_PARALLELISM,
        hash_len=_ARGON2_HASH_LEN,
        type=_Argon2Type.ID,
    )


def _pq_derive_seed_key(seed_phrase: str, salt_bytes: bytes) -> bytes:
    """32-Byte Seed-Key aus Seed-Phrase + Salt + machine-id via Argon2id."""
    if not salt_bytes or len(salt_bytes) != 16:
        raise ValueError("Salt (16 Byte) ist Pflicht für Seed-Key-Ableitung")
    mid = _pq_get_machine_id_bytes()
    normalized = " ".join(seed_phrase.strip().lower().split())
    return hash_secret_raw(
        secret=b"shinpai-vault-seed-v4-" + normalized.encode("utf-8") + mid,
        salt=salt_bytes,
        time_cost=_ARGON2_TIME_COST,
        memory_cost=_ARGON2_MEMORY_COST,
        parallelism=_ARGON2_PARALLELISM,
        hash_len=_ARGON2_HASH_LEN,
        type=_Argon2Type.ID,
    )


def _pq_encrypt_priv(kem_sk: bytes, key: bytes, aad: bytes) -> bytes:
    """ML-KEM-Private mit AES-256-GCM verschlüsseln. Format: salt(32) + nonce(12) + ct."""
    salt = secrets.token_bytes(32)
    nonce = secrets.token_bytes(12)
    derived = hashlib.sha256(key + salt).digest()
    ct = AESGCM(derived).encrypt(nonce, kem_sk, aad)
    return salt + nonce + ct


def _pq_decrypt_priv(blob: bytes, key: bytes, aad: bytes) -> bytes:
    """ML-KEM-Private entschlüsseln."""
    salt = blob[:32]
    nonce = blob[32:44]
    ct = blob[44:]
    derived = hashlib.sha256(key + salt).digest()
    return AESGCM(derived).decrypt(nonce, ct, aad)


def _pq_wrap_dek(dek: bytes, kem_pk: bytes) -> bytes:
    """DEK mit ML-KEM-768 encapsulieren. Format: len(ct)(4) + encap_ct + nonce(12) + aes_ct."""
    kem = oqs.KeyEncapsulation("ML-KEM-768")
    encap_ct, shared = kem.encap_secret(kem_pk)
    kem.free()
    nonce = secrets.token_bytes(12)
    aes_key = hashlib.sha256(shared).digest()[:32]
    aes_ct = AESGCM(aes_key).encrypt(nonce, dek, b"vault-dek-wrap-v3")
    return len(encap_ct).to_bytes(4, "big") + encap_ct + nonce + aes_ct


def _pq_unwrap_dek(wrap_blob: bytes, kem_sk: bytes) -> bytes:
    """DEK über ML-KEM-Private entkapseln."""
    ct_len = int.from_bytes(wrap_blob[:4], "big")
    encap_ct = wrap_blob[4:4 + ct_len]
    nonce = wrap_blob[4 + ct_len:4 + ct_len + 12]
    aes_ct = wrap_blob[4 + ct_len + 12:]
    kem = oqs.KeyEncapsulation("ML-KEM-768", secret_key=kem_sk)
    shared = kem.decap_secret(encap_ct)
    kem.free()
    aes_key = hashlib.sha256(shared).digest()[:32]
    return AESGCM(aes_key).decrypt(nonce, aes_ct, b"vault-dek-wrap-v3")


_pq_pending_kem_sk: bytes | None = None

def _pq_init_fresh(password: str, seed_phrase: str | None = None) -> bytes:
    """Erstinitialisierung: DEK + ML-KEM-Pair generieren, alle Wrap-Files schreiben.
    Gibt DEK zurück (im RAM behalten für weitere Operationen)."""
    global _pq_pending_kem_sk
    dek = secrets.token_bytes(32)
    kem = oqs.KeyEncapsulation("ML-KEM-768")
    kem_pk = kem.generate_keypair()
    kem_sk = kem.export_secret_key()
    kem.free()

    # DEK wrappen mit Public-Key
    wrap_blob = _pq_wrap_dek(dek, kem_pk)
    DEK_WRAP_FILE.write_bytes(wrap_blob)
    try: os.chmod(str(DEK_WRAP_FILE), 0o600)
    except OSError: pass

    # ML-KEM-Public speichern (Klartext)
    VAULT_KEM_PUB_FILE.write_bytes(kem_pk)
    try: os.chmod(str(VAULT_KEM_PUB_FILE), 0o644)
    except OSError: pass

    # Salt erzeugen (einmalig bei Erst-Init) + ML-KEM-Private mit Argon2id-KEK verschlüsseln
    salt_bytes = _salt_ensure()
    kek = _pq_derive_kek(password, salt_bytes)
    priv_blob = _pq_encrypt_priv(kem_sk, kek, b"vault-kem-priv-pw-v3")
    VAULT_KEM_PRIV_FILE.write_bytes(priv_blob)
    try: os.chmod(str(VAULT_KEM_PRIV_FILE), 0o600)
    except OSError: pass

    # Seed-Backup falls Seed übergeben — gleicher Salt wie KEK
    if seed_phrase:
        seed_key = _pq_derive_seed_key(seed_phrase, salt_bytes)
        seed_blob = _pq_encrypt_priv(kem_sk, seed_key, b"vault-kem-priv-seed-v3")
        VAULT_KEM_PRIV_SEED_FILE.write_bytes(seed_blob)
        try: os.chmod(str(VAULT_KEM_PRIV_SEED_FILE), 0o600)
        except OSError: pass
        _pq_pending_kem_sk = None
    else:
        # KEM-SK im RAM halten bis create_account den Seed generiert hat
        _pq_pending_kem_sk = kem_sk

    log.info("🌿 PQ-Vault initialisiert (Argon2id + ML-KEM-768 Wrap, KEK/DEK)")
    return dek


def _pq_create_seed_backup(seed_phrase: str) -> bool:
    """Nachträgliches Seed-Backup für ML-KEM-Private erstellen.
    Wird nach _pq_init_fresh aufgerufen wenn Seed erst nach vault_unlock generiert wird."""
    global _pq_pending_kem_sk
    if _pq_pending_kem_sk is None:
        return False
    try:
        salt_bytes = _salt_ensure()
        seed_key = _pq_derive_seed_key(seed_phrase, salt_bytes)
        seed_blob = _pq_encrypt_priv(_pq_pending_kem_sk, seed_key, b"vault-kem-priv-seed-v3")
        VAULT_KEM_PRIV_SEED_FILE.write_bytes(seed_blob)
        try: os.chmod(str(VAULT_KEM_PRIV_SEED_FILE), 0o600)
        except OSError: pass
        log.info("🌿 PQ-Seed-Backup nachträglich erstellt")
        return True
    finally:
        _pq_pending_kem_sk = None


def _pq_unlock_dek_via_password(password: str) -> bytes | None:
    """PW → Argon2id-KEK (mit .salt) → ML-KEM-Private entschlüsseln → DEK entwrappen."""
    if not VAULT_KEM_PRIV_FILE.exists() or not DEK_WRAP_FILE.exists():
        return None
    salt_info = _salt_read()
    if salt_info is None:
        log.warning("⚠️ .salt fehlt — Vault nicht entsperrbar. Mit ins Backup nehmen!")
        return None
    try:
        kek = _pq_derive_kek(password, salt_info[0])
        kem_sk = _pq_decrypt_priv(VAULT_KEM_PRIV_FILE.read_bytes(), kek, b"vault-kem-priv-pw-v3")
        return _pq_unwrap_dek(DEK_WRAP_FILE.read_bytes(), kem_sk)
    except Exception:
        return None


def _pq_unlock_dek_via_seed(seed_phrase: str) -> bytes | None:
    """Seed → Argon2id-Seed-Key (mit .salt) → ML-KEM-Private entschlüsseln → DEK entwrappen."""
    if not VAULT_KEM_PRIV_SEED_FILE.exists() or not DEK_WRAP_FILE.exists():
        return None
    salt_info = _salt_read()
    if salt_info is None:
        log.warning("⚠️ .salt fehlt — Seed-Recovery nicht möglich. Mit ins Backup nehmen!")
        return None
    try:
        seed_key = _pq_derive_seed_key(seed_phrase, salt_info[0])
        kem_sk = _pq_decrypt_priv(VAULT_KEM_PRIV_SEED_FILE.read_bytes(), seed_key, b"vault-kem-priv-seed-v3")
        return _pq_unwrap_dek(DEK_WRAP_FILE.read_bytes(), kem_sk)
    except Exception:
        return None


def _pq_rotate_salt(password: str) -> dict:
    """Salzstreuer — atomische Salt-Rotation.
    Altes Salz lesen → KEK-alt → ML-KEM-Priv im RAM → neues Salz würfeln →
    KEK-neu → ML-KEM-Priv mit KEK-neu re-wrap → .salt überschreiben.
    DEK und alle anderen Vault-Dateien bleiben unberührt.
    Returns {ok, new_salt_short, last_rotated, seed_backup_stale} oder {error}."""
    if not VAULT_KEM_PRIV_FILE.exists():
        return {"error": "Vault nicht initialisiert"}
    salt_info = _salt_read()
    if salt_info is None:
        return {"error": ".salt fehlt — Vault-Backup unvollständig"}
    old_salt, _ = salt_info
    try:
        old_kek = _pq_derive_kek(password, old_salt)
        kem_sk = _pq_decrypt_priv(VAULT_KEM_PRIV_FILE.read_bytes(), old_kek, b"vault-kem-priv-pw-v3")
    except Exception:
        return {"error": "Passwort falsch"}

    # Neues Salz würfeln
    new_salt = secrets.token_bytes(16)
    new_kek = _pq_derive_kek(password, new_salt)
    try:
        new_priv_blob = _pq_encrypt_priv(kem_sk, new_kek, b"vault-kem-priv-pw-v3")
        VAULT_KEM_PRIV_FILE.write_bytes(new_priv_blob)
        try: os.chmod(str(VAULT_KEM_PRIV_FILE), 0o600)
        except OSError: pass

        # Seed-Backup mit neuem Salt re-wrappen geht hier nicht (wir kennen seed_phrase nicht).
        # Konsequenz: kem_priv.seed.vault ist jetzt mit OLD salt verschlüsselt.
        # UI zeigt „Seed-Backup stale" — User erneuert es bei nächstem 2FA-Refresh.
        seed_backup_stale = VAULT_KEM_PRIV_SEED_FILE.exists()

        now_ts = int(time.time())
        _salt_write(new_salt, rotated_at=now_ts)
        log.info(f"🧂 Salt rotiert (Salzstreuer) — neuer Salt {new_salt.hex()[:8]}…{new_salt.hex()[-4:]}")
        return {
            "ok": True,
            "new_salt_short": f"{new_salt.hex()[:8]}…{new_salt.hex()[-4:]}",
            "last_rotated": now_ts,
            "seed_backup_stale": seed_backup_stale,
        }
    except Exception as e:
        log.error(f"⚠️ Salt-Rotation fehlgeschlagen: {e}")
        return {"error": f"Salt-Rotation fehlgeschlagen: {e}"}


def _pq_rewrap_kem_priv(old_password: str, new_password: str) -> bool:
    """Atomischer PW-Change: ML-KEM-Private mit neuer KEK neu verschlüsseln.
    DEK und Vault-Files bleiben unberührt — nur eine kleine Datei wird ersetzt.
    Salt bleibt gleich (nur der Salzstreuer rotiert den Salt!). Returns True bei Erfolg."""
    if not VAULT_KEM_PRIV_FILE.exists():
        return False
    salt_info = _salt_read()
    if salt_info is None:
        log.warning("⚠️ .salt fehlt — PW-Change unmöglich")
        return False
    try:
        salt_bytes = salt_info[0]
        old_kek = _pq_derive_kek(old_password, salt_bytes)
        kem_sk = _pq_decrypt_priv(VAULT_KEM_PRIV_FILE.read_bytes(), old_kek, b"vault-kem-priv-pw-v3")
        new_kek = _pq_derive_kek(new_password, salt_bytes)
        new_blob = _pq_encrypt_priv(kem_sk, new_kek, b"vault-kem-priv-pw-v3")
        VAULT_KEM_PRIV_FILE.write_bytes(new_blob)
        try: os.chmod(str(VAULT_KEM_PRIV_FILE), 0o600)
        except OSError: pass
        return True
    except Exception as e:
        log.error(f"⚠️ PQ-Rewrap fehlgeschlagen: {e}")
        return False


def _pq_get_kem_sk_via_password(password: str) -> bytes | None:
    """Holt ML-KEM-Private per PW (intern genutzt)."""
    if not VAULT_KEM_PRIV_FILE.exists():
        return None
    salt_info = _salt_read()
    if salt_info is None:
        return None
    try:
        kek = _pq_derive_kek(password, salt_info[0])
        return _pq_decrypt_priv(VAULT_KEM_PRIV_FILE.read_bytes(), kek, b"vault-kem-priv-pw-v3")
    except Exception:
        return None


def _pq_write_seed_backup_with_sk(seed_phrase: str, kem_sk: bytes) -> bool:
    """Schreibt kem_priv.seed.vault wenn kem_sk bekannt."""
    try:
        salt_info = _salt_read()
        if salt_info is None:
            return False
        seed_key = _pq_derive_seed_key(seed_phrase, salt_info[0])
        blob = _pq_encrypt_priv(kem_sk, seed_key, b"vault-kem-priv-seed-v3")
        VAULT_KEM_PRIV_SEED_FILE.write_bytes(blob)
        try: os.chmod(str(VAULT_KEM_PRIV_SEED_FILE), 0o600)
        except OSError: pass
        return True
    except Exception as e:
        log.error(f"⚠️ Seed-Backup-Write fehlgeschlagen: {e}")
        return False


def vault_unlock(password: str) -> bool:
    """Vault entsperren — PQ-only (Argon2id → KEK → ML-KEM-Priv → DEK).
    KEIN SHA256-Legacy-Pfad, KEIN Fallback. Wer einen alten v2-Vault hat: muss neu anlegen.
    Siehe PQ-Architektur.md „Eiserne Regeln: KEIN Fallback"."""
    global _vault_unlock_time, _dek

    # Fall 1: PQ-Vault existiert → Argon2id-Entsperrung
    if VAULT_KEM_PRIV_FILE.exists() and DEK_WRAP_FILE.exists():
        if not SALT_FILE.exists():
            log.error("❌ Vault gefunden aber .salt fehlt — Backup unvollständig!")
            _dek = None
            _vault_unlock_time = 0
            return False
        dek = _pq_unlock_dek_via_password(password)
        if dek is None:
            _dek = None
            _vault_unlock_time = 0
            log.warning("🔒 Vault-Passwort falsch!")
            return False
        _dek = dek
        _vault_unlock_time = time.time()
        log.info("🔒 Vault entsperrt (Argon2id + ML-KEM-768 + DEK)")
        return True

    # Fall 2: Altes v2-Format erkannt → KEINE Migration, klarer Fehler
    if os.path.exists(IDENTITY_VAULT):
        log.error("❌ Alter v2-Vault erkannt — Migration nicht unterstützt. "
                  "Bitte vault/ + credentials/ leeren und neu anlegen.")
        _dek = None
        _vault_unlock_time = 0
        return False

    # Fall 3: Kein Vault → First-Start, PQ-Wrap direkt aufbauen
    try:
        _dek = _pq_init_fresh(password)
        _vault_unlock_time = time.time()
        log.info("🔒 Neuer Vault — PQ-Wrap (Argon2id + ML-KEM-768 + DEK) initialisiert")
        return True
    except Exception as e:
        log.error(f"⚠️ PQ-Init fehlgeschlagen: {e}")
        _dek = None
        _vault_unlock_time = 0
        return False


def vault_setup(password: str, owner_username: str, owner_email: str) -> str:
    """Vault erstmalig einrichten + Recovery-Seed zurückgeben.
    PQ-only: _pq_init_fresh baut DEK/ML-KEM/Wrap-Files auf. Kein SHA256-Master-Key mehr."""
    global _vault_unlock_time, _dek
    # Recovery-Seed jetzt generieren — damit _pq_init_fresh den Seed-Backup direkt miterzeugt
    seed = _generate_recovery_seed()
    _dek = _pq_init_fresh(password, seed_phrase=seed)
    _vault_unlock_time = time.time()

    # Identity-Vault schreiben (Owner-Info, verschlüsselt mit DEK-abgeleitetem File-Key)
    identity = {
        'owner_username': owner_username,
        'owner_email': owner_email,
        'created_at': int(time.time()),
        'magic': 'kneipe-owner-v1',
    }
    with open(IDENTITY_VAULT, 'wb') as f:
        f.write(_vault_encrypt_bytes(json.dumps(identity).encode('utf-8')))
    os.chmod(IDENTITY_VAULT, 0o600)

    # Recovery-Hash/Key-File (PW ↔ Seed-Kopplung für PW-Reset-Flow)
    _save_recovery_data(password, seed)
    log.info(f'🔒 Vault eingerichtet für Owner "{owner_username}" (Argon2id + ML-KEM-768)')
    return seed


def vault_read_identity() -> dict | None:
    """Identity-Vault entschlüsseln (nur bei unlocked Vault)."""
    if not vault_is_unlocked() or not os.path.exists(IDENTITY_VAULT):
        return None
    try:
        with open(IDENTITY_VAULT, 'rb') as f:
            return json.loads(_vault_decrypt_bytes(f.read()).decode('utf-8'))
    except Exception:
        return None


def vault_exists() -> bool:
    """True wenn Identity-Vault auf Disk (also Owner existiert)."""
    return os.path.exists(IDENTITY_VAULT)


# ═════════════════════════════════════════════════════════════════════════
#  IGNITION KEY — 1:1 von ShinNexus (Fernet Auto-Unlock)
# ═════════════════════════════════════════════════════════════════════════

def _igni_dir_name(kuerzel: str = "") -> str:
    return f"Kneipe-Igni-{kuerzel}" if kuerzel else "Kneipe-Igni"


def _igni_auto_detect():
    """Sucht Kneipe-Igni-* im Arbeitsverzeichnis."""
    for p in _Path(BASE).glob("Kneipe-Igni-*"):
        if p.is_dir() and (p / "vault_bootstrap.enc").exists():
            return p
    return None


def _igni_init(cfg: dict = None):
    """Setzt _IGNITION_DIR und _VAULT_BOOTSTRAP. Mit Shinpai-ID wenn verfügbar."""
    global _IGNITION_DIR, _VAULT_BOOTSTRAP
    # Erst Auto-Detect (findet existierende Kneipe-Igni-* Ordner)
    resolved = _igni_auto_detect()
    if resolved:
        _IGNITION_DIR = resolved
        _VAULT_BOOTSTRAP = resolved / "vault_bootstrap.enc"
        return
    # Shinpai-ID aus Identity versuchen (nach Vault-Unlock)
    kuerzel = ""
    try:
        identity = vault_read_identity()
        if identity:
            owner_name = identity.get("owner_username", "")
            if owner_name:
                kuerzel = _b62_hash(f"shinpai-name-{owner_name}")[:6]
    except Exception:
        pass
    _IGNITION_DIR = _Path(BASE) / _igni_dir_name(kuerzel)
    _VAULT_BOOTSTRAP = _IGNITION_DIR / "vault_bootstrap.enc"


def _igni_bootstrap_key() -> bytes:
    """Bootstrap-Key: SHA256(Salt + machine-id) → Fernet-Key."""
    try:
        mid = _Path("/etc/machine-id").read_text().strip().encode()
    except Exception:
        import platform
        mid = platform.node().encode()
    return base64.urlsafe_b64encode(hashlib.sha256(_IGNI_SALT + mid).digest())


def igni_save(password: str):
    """Vault-Passwort verschlüsselt in Igni-Ordner speichern."""
    from cryptography.fernet import Fernet
    _IGNITION_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(str(_IGNITION_DIR), 0o700)
    bootstrap = {
        "vault_password": password,
        "mode": "auto-unlock",
        "created_at": int(time.time()),
    }
    f = Fernet(_igni_bootstrap_key())
    _VAULT_BOOTSTRAP.write_bytes(f.encrypt(json.dumps(bootstrap).encode("utf-8")))
    os.chmod(str(_VAULT_BOOTSTRAP), 0o600)
    log.info("🔑 Igni-Key gespeichert (Auto-Unlock aktiv)")


def igni_load() -> str | None:
    """Vault-Passwort aus Igni-Bootstrap entschlüsseln."""
    if not _VAULT_BOOTSTRAP or not _VAULT_BOOTSTRAP.exists():
        return None
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_igni_bootstrap_key())
        bootstrap = json.loads(f.decrypt(_VAULT_BOOTSTRAP.read_bytes()).decode("utf-8"))
        if bootstrap.get("mode") != "auto-unlock":
            return None
        return bootstrap.get("vault_password")
    except Exception:
        return None


def igni_delete():
    """Igni-Key löschen."""
    if _VAULT_BOOTSTRAP and _VAULT_BOOTSTRAP.exists():
        _VAULT_BOOTSTRAP.unlink()
        log.info("🔑 Igni-Key gelöscht")
    if _IGNITION_DIR and _IGNITION_DIR.exists():
        try:
            _IGNITION_DIR.rmdir()
        except OSError:
            pass


# ═════════════════════════════════════════════════════════════════════════
#  SYSTEM SIGNING — ML-DSA-65 + ML-KEM-768 (1:1 von ShinNexus)
# ═════════════════════════════════════════════════════════════════════════

_pq_keys: dict | None = None


def _ensure_keypair():
    """Erzeugt ML-DSA-65 + ML-KEM-768 Keypair. Braucht offenen Vault!"""
    global _pq_keys
    if not vault_is_unlocked():
        return
    if not PQ_AVAILABLE:
        return
    if SIGNING_KEY_FILE.exists():
        try:
            raw = _vault_decrypt_bytes(SIGNING_KEY_FILE.read_bytes())
            keys = json.loads(raw.decode())
            if keys.get("algo") == "ML-DSA-65":
                _pq_keys = keys
                log.info("PQ-Keypair geladen (ML-DSA-65 + ML-KEM-768)")
                return
        except Exception:
            log.warning("Alter Signing-Key defekt — erzeuge neuen PQ-Keypair!")
    sig = oqs.Signature("ML-DSA-65")
    sig_pk = sig.generate_keypair()
    sig_sk = sig.export_secret_key()
    kem = oqs.KeyEncapsulation("ML-KEM-768")
    kem_pk = kem.generate_keypair()
    kem_sk = kem.export_secret_key()
    keys = {
        "algo": "ML-DSA-65",
        "sig_sk": sig_sk.hex(), "sig_pk": sig_pk.hex(),
        "kem_sk": kem_sk.hex(), "kem_pk": kem_pk.hex(),
        "created": int(time.time()),
    }
    key_bytes = json.dumps(keys).encode()
    SIGNING_KEY_FILE.write_bytes(_vault_encrypt_bytes(key_bytes))
    try: os.chmod(str(SIGNING_KEY_FILE), 0o600)
    except OSError: pass
    _pq_keys = keys
    log.info("ML-DSA-65 + ML-KEM-768 Keypair erzeugt")


def _sign_data(data: bytes) -> str:
    """Signiert Daten mit ML-DSA-65. Gibt Signatur als Hex zurück."""
    if not _pq_keys:
        raise RuntimeError("PQ-Keys nicht geladen")
    sig_obj = oqs.Signature("ML-DSA-65", secret_key=bytes.fromhex(_pq_keys["sig_sk"]))
    signature = sig_obj.sign(data)
    return signature.hex()


def _verify_signature(data: bytes, signature_hex: str, public_key_hex: str) -> bool:
    """Prüft ML-DSA-65 Signatur gegen Public Key."""
    try:
        sig_obj = oqs.Signature("ML-DSA-65")
        return sig_obj.verify(data, bytes.fromhex(signature_hex), bytes.fromhex(public_key_hex))
    except Exception:
        return False


# --- Recovery-Seed (24-Wörter BIP39-style, eigene Wortliste) ---
_SEED_WORDS = (
    'anker bier bock chef dorf engel feder gold hund igel jagd kino lampe '
    'mond nacht oper pilz quarz rose sturm tiger uhr vogel wolke zebra '
    'axt baum eis fuchs gras herz iglu kerze leiter magier nebel ofen '
    'pforte quelle regen segel tal urwald vase wald zauber adler burg '
    'clown donner elch falter garten heim insel kelch lowe muster nest '
    'oase pfad qualle rabe schnee tor ulme veilchen welle zwerg'
).split()


def _generate_recovery_seed() -> str:
    """24-Wort Seed aus lokaler Wortliste."""
    import random
    rng = random.SystemRandom()
    return ' '.join(rng.choice(_SEED_WORDS) for _ in range(24))


def _save_recovery_data(password: str, seed: str):
    """Recovery-Daten AUSSERHALB des Vaults speichern.
    recovery.hash = SHA-256 des Seeds (Verify)
    recovery.enc  = Vault-Passwort verschlüsselt mit Seed-Key (Recover)
    """
    seed_hash = hashlib.sha256(seed.encode()).hexdigest()
    with open(RECOVERY_HASH_FILE, 'w') as f:
        f.write(seed_hash)
    try:
        os.chmod(RECOVERY_HASH_FILE, 0o600)
    except OSError:
        pass
    seed_salt = hashlib.sha256(b'kneipe-recovery-salt-' + seed.encode()).digest()
    seed_key = _derive_vault_key(seed, seed_salt)
    nonce = secrets.token_bytes(12)
    encrypted_pw = AESGCM(seed_key).encrypt(nonce, password.encode('utf-8'), b'kneipe-recovery')
    with open(RECOVERY_KEY_FILE, 'wb') as f:
        f.write(seed_salt + nonce + encrypted_pw)
    try:
        os.chmod(RECOVERY_KEY_FILE, 0o600)
    except OSError:
        pass


def recover_vault_password(seed: str) -> str | None:
    """Recovery: Seed eingeben → altes Vault-Passwort entschlüsseln."""
    if not os.path.exists(RECOVERY_HASH_FILE):
        return None
    with open(RECOVERY_HASH_FILE, 'r') as f:
        stored_hash = f.read().strip()
    if hashlib.sha256(seed.encode()).hexdigest() != stored_hash:
        return None
    if not os.path.exists(RECOVERY_KEY_FILE):
        return None
    try:
        with open(RECOVERY_KEY_FILE, 'rb') as f:
            raw = f.read()
        seed_salt = raw[:32]
        nonce = raw[32:44]
        encrypted_pw = raw[44:]
        seed_key = _derive_vault_key(seed, seed_salt)
        return AESGCM(seed_key).decrypt(nonce, encrypted_pw, b'kneipe-recovery').decode('utf-8')
    except Exception:
        return None


# (Alte Igni-Funktionen entfernt — ersetzt durch neue oben, 1:1 von ShinNexus)


# Whitelist der Endpoints die bei gesperrtem Vault erlaubt sind
_VAULT_GATE_ALLOWED_GET = {'/api/status', '/api/verify', '/api/public-url/status', '/api/chain/info', '/api/whitelist'}
_VAULT_GATE_ALLOWED_POST = {
    '/api/status',
    '/api/owner-setup',
    '/api/verify-code',
    '/api/login',
    '/api/nexus/login',
    '/api/nexus/owner',      # First-Start: Nexus-User wird Kneipe-Owner
    '/api/vault/unlock',
    '/api/vault/recover',
    '/api/public-url/check',
    '/api/public-url/save',
    '/api/solo-mode',
}


def _vault_gate_allowed(method: str, path: str) -> bool:
    """True wenn Zugriff erlaubt: Vault offen ODER Whitelist oder statische Ressource."""
    if vault_is_unlocked():
        return True
    # Statische Pfade (kein /api/) immer erlaubt — UI/Assets müssen laden
    if not path.startswith('/api/'):
        return True
    if method == 'GET' and path in _VAULT_GATE_ALLOWED_GET:
        return True
    if method == 'POST' and path in _VAULT_GATE_ALLOWED_POST:
        return True
    return False


# --- Kompat-Wrapper für vorhandenen Code (string-basiert) ---
def vault_encrypt(plaintext):
    """String-Wrapper: gibt base64-str zurück (für DB-Kompat)."""
    if not plaintext:
        return ''
    if not vault_is_unlocked():
        raise RuntimeError('Vault nicht entsperrt — kein Encrypt möglich!')
    raw = _vault_encrypt_bytes(plaintext.encode('utf-8'))
    return base64.urlsafe_b64encode(raw).decode('ascii')


def vault_decrypt(ciphertext):
    """String-Wrapper: nimmt base64-str, gibt Klartext-str zurück."""
    if not ciphertext:
        return ciphertext
    if not vault_is_unlocked():
        raise RuntimeError('Vault nicht entsperrt — kein Decrypt möglich!')
    try:
        raw = base64.urlsafe_b64decode(ciphertext.encode('ascii'))
        return _vault_decrypt_bytes(raw).decode('utf-8')
    except Exception as e:
        log.error(f'🔓 Vault-Decrypt FEHLGESCHLAGEN: {e}')
        return ''


# ═════════════════════════════════════════════════════════════════════════
#  POST-QUANTUM KEYS — ML-DSA-65 (Signatur) + ML-KEM-768 (Key-Exchange)
#  Pro User ein Keypair. Private-Blob mit User-PW+machine-id verschlüsselt.
#  Public-Teile in DB im Klartext. Server kann Private nicht lesen ohne PW.
# ═════════════════════════════════════════════════════════════════════════

try:
    import oqs
    _oqs = oqs  # Backward-compat für User-PQ Code
    PQ_AVAILABLE = True
except ImportError:
    oqs = None
    _oqs = None
    PQ_AVAILABLE = False
    log.warning('🔐 oqs-Library nicht verfügbar — PQ-Features deaktiviert!')


def pq_generate_user_keypair():
    """Generiert (dsa_pub_b64, kem_pub_b64, private_blob_json).
    private_blob_json enthält beide Private-Keys als base64 + Metadata.
    """
    if not PQ_AVAILABLE:
        return None, None, None
    with _oqs.Signature('ML-DSA-65') as sig:
        dsa_pub = sig.generate_keypair()
        dsa_priv = sig.export_secret_key()
    with _oqs.KeyEncapsulation('ML-KEM-768') as kem:
        kem_pub = kem.generate_keypair()
        kem_priv = kem.export_secret_key()
    private_blob = json.dumps({
        'dsa_priv': base64.b64encode(dsa_priv).decode('ascii'),
        'kem_priv': base64.b64encode(kem_priv).decode('ascii'),
        'alg_dsa': 'ML-DSA-65',
        'alg_kem': 'ML-KEM-768',
        'created_at': int(time.time()),
    })
    return (
        base64.b64encode(dsa_pub).decode('ascii'),
        base64.b64encode(kem_pub).decode('ascii'),
        private_blob,
    )


def _pq_user_key_salt(user_id: str) -> bytes:
    """Stabiler Salt pro User: SHA256('pq-user' + user_id)."""
    return hashlib.sha256(b'pq-user-salt-v1-' + user_id.encode('utf-8')).digest()


def pq_encrypt_private_blob(private_blob: str, password: str, user_id: str) -> str:
    """Private-Blob mit User-PW + machine-id verschlüsseln (so wie Vault-Schicht 1)."""
    mid = _get_machine_id().encode()
    salt = _pq_user_key_salt(user_id)
    combined = password.encode('utf-8') + mid + salt
    key = hashlib.sha256(b'pq-priv-enc-v1' + combined).digest()
    nonce = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, private_blob.encode('utf-8'), b'pq-user-priv')
    return base64.urlsafe_b64encode(nonce + ct).decode('ascii')


def pq_decrypt_private_blob(ciphertext_b64: str, password: str, user_id: str) -> dict | None:
    """Private-Blob wieder entschlüsseln (nur wenn User-PW bekannt)."""
    try:
        raw = base64.urlsafe_b64decode(ciphertext_b64.encode('ascii'))
        nonce = raw[:12]
        ct = raw[12:]
        mid = _get_machine_id().encode()
        salt = _pq_user_key_salt(user_id)
        combined = password.encode('utf-8') + mid + salt
        key = hashlib.sha256(b'pq-priv-enc-v1' + combined).digest()
        plain = AESGCM(key).decrypt(nonce, ct, b'pq-user-priv')
        return json.loads(plain.decode('utf-8'))
    except Exception as e:
        log.error(f'🔓 PQ-Private-Decrypt FEHLGESCHLAGEN: {e}')
        return None


def pq_kem_encapsulate(recipient_kem_pub_b64: str) -> tuple[bytes, bytes]:
    """Gruppen-Schlüssel-Verteilung: encapsulate shared secret mit User-KEM-Public.
    Returns (ciphertext, shared_secret). Ciphertext geht an User, shared_secret bleibt hier.
    """
    if not PQ_AVAILABLE:
        return b'', b''
    pub = base64.b64decode(recipient_kem_pub_b64.encode('ascii'))
    with _oqs.KeyEncapsulation('ML-KEM-768') as kem:
        ct, ss = kem.encap_secret(pub)
    return ct, ss


def pq_kem_decapsulate(ciphertext: bytes, kem_priv: bytes) -> bytes:
    """Empfänger-Seite: mit Private-Key shared secret wieder herstellen."""
    if not PQ_AVAILABLE:
        return b''
    with _oqs.KeyEncapsulation('ML-KEM-768', secret_key=kem_priv) as kem:
        return kem.decap_secret(ciphertext)


def pq_sign(message: bytes, dsa_priv: bytes) -> bytes:
    """Nachricht mit ML-DSA-65 signieren."""
    if not PQ_AVAILABLE:
        return b''
    with _oqs.Signature('ML-DSA-65', secret_key=dsa_priv) as sig:
        return sig.sign(message)


def pq_verify(message: bytes, signature: bytes, dsa_pub_b64: str) -> bool:
    """Signatur mit ML-DSA-65 prüfen."""
    if not PQ_AVAILABLE:
        return False
    try:
        pub = base64.b64decode(dsa_pub_b64.encode('ascii'))
        with _oqs.Signature('ML-DSA-65') as sig:
            return sig.verify(message, signature, pub)
    except Exception:
        return False

# --- RATE LIMITER ---
RATE_LIMIT = 300
RATE_WINDOW = 60
rate_store = {}
rate_lock = threading.Lock()

def check_rate_limit(ip):
    now = time.time()
    with rate_lock:
        if ip not in rate_store:
            rate_store[ip] = []
        rate_store[ip] = [t for t in rate_store[ip] if now - t < RATE_WINDOW]
        if len(rate_store[ip]) >= RATE_LIMIT:
            return False
        rate_store[ip].append(now)
        return True

# --- SESSION MANAGEMENT ---
sessions = {}  # token → {user_id, created, last_active}
sessions_lock = threading.Lock()

MAX_SESSIONS_HUMAN = 3
MAX_SESSIONS_BOT   = 1


def _user_is_bot(user_id):
    """is_bot-Flag lesen. True = Bot-Account."""
    conn = get_db('accounts.db')
    row = conn.execute('SELECT is_bot FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return bool(row and row['is_bot'])


def create_session(user_id):
    """Vision 1: mit Session-Limit (LRU-Kill ältester bei Limit-Überschreitung).
    is_bot=1 → max 1 Session · normal → max 3 Sessions
    """
    limit = MAX_SESSIONS_BOT if _user_is_bot(user_id) else MAX_SESSIONS_HUMAN
    token = secrets.token_hex(32)
    with sessions_lock:
        # Ältere Sessions dieses Users finden, nach last_active sortieren
        user_toks = [(tok, d) for tok, d in sessions.items() if d.get('user_id') == user_id]
        user_toks.sort(key=lambda x: x[1].get('last_active', 0))
        # Solange Limit erreicht → älteste killen
        while len(user_toks) >= limit:
            old_tok, _ = user_toks.pop(0)
            if old_tok in sessions:
                del sessions[old_tok]
        sessions[token] = {'user_id': user_id, 'created': time.time(), 'last_active': time.time()}
    return token

def get_session(token):
    with sessions_lock:
        sess = sessions.get(token)
        if not sess:
            return None
        if time.time() - sess['last_active'] > 86400:  # 24h timeout
            del sessions[token]
            return None
        sess['last_active'] = time.time()
        return sess

def delete_session(token):
    with sessions_lock:
        sessions.pop(token, None)

# --- GÄSTE-SYSTEM V4.4.8 ---
def _guest_config():
    """Gäste-Config aus DB laden (Owner-konfigurierbar)"""
    conn = get_db('accounts.db')
    cfg = {}
    for key in ('guest_enabled', 'guest_max', 'guest_message_full', 'guest_message_banned', 'guest_bark_enabled', 'guest_voice_enabled', 'guest_play_enabled', 'register_code', 'register_code_required', 'kneipe_title', 'kneipe_subtitle'):
        row = conn.execute('SELECT value FROM config WHERE key = ?', (key,)).fetchone()
        cfg[key] = row['value'] if row else None
    conn.close()
    return {
        'enabled': cfg['guest_enabled'] != '0',
        'max': int(cfg['guest_max'] or '10'),
        'msg_full': cfg['guest_message_full'] or 'Heute ist echt was los! Sry Bro! 🍺',
        'msg_banned': cfg['guest_message_banned'] or 'Du bist gesperrt! Benimm dich nächstes Mal!',
        'bark_enabled': cfg['guest_bark_enabled'] == '1',
        'voice_enabled': cfg['guest_voice_enabled'] == '1',
        'play_enabled': cfg['guest_play_enabled'] == '1',
        'register_code': cfg['register_code'] or '',
        'register_code_required': cfg['register_code_required'] == '1',
        'kneipe_title': cfg['kneipe_title'] or 'Kneipen-Schlägerei',
        'kneipe_subtitle': cfg['kneipe_subtitle'] or 'Seelenfick für die Kneipe.',
    }
GUEST_POOL = 50                 # 50 Accounts in DB
GUEST_MAX = 10                  # 10 aktive Slots
GUEST_SESSION_TIMEOUT = 86400   # 24h
GUEST_HEARTBEAT_TIMEOUT = 30    # 30sec
GUEST_MISS_LIMIT = 3            # 3x verpasst = raus (90sec)
GUEST_BAN_ESCALATION = [3*86400, 7*86400, 30*86400, 90*86400, 365*86400]  # 3d, 7d, 30d, 90d, 365d
guest_slots = {}                # slot_nr (1-10) → {user_id, session_token, ip, created, last_heartbeat}
guest_ip_kicks = {}             # ip → [kick_timestamps] (für Ban-Eskalation)
guest_ip_bans = {}              # ip → {until, strike}
guest_lock = threading.Lock()

def _find_free_guest_slot():
    with guest_lock:
        for i in range(1, GUEST_MAX + 1):
            if i not in guest_slots:
                return i
    return None

def _leave_all_tische(user_id):
    """User aus allen Tischen entfernen — Cleanup vor Session-Delete.
    Verhindert Geist-Member die nach Session-Tod in t['members'] hängen
    und später Crashes auslösen (z.B. DB-Lookup auf toten user_id in
    TTS-Thread oder Vault-Op)."""
    if 'raeume' not in globals():
        return
    try:
        snapshot = list(raeume.items())
    except Exception:
        return
    for _, r in snapshot:
        try:
            tische_items = list(r['tische'].items())
        except Exception:
            continue
        for tid, tisch in tische_items:
            try:
                if user_id in tisch.get('members', set()):
                    handle_tisch_leave(user_id, {'tisch_id': tid})
            except Exception as e:
                log.error(f'❌ Cleanup tisch-leave {user_id} ← {tid}: {e}')

def _release_guest_slot(slot_nr, kicked=False):
    with guest_lock:
        slot = guest_slots.pop(slot_nr, None)
    kick_msg = None
    if slot:
        user_id = slot.get('user_id')
        # Geist-Member verhindern: Tisch-Leave BEVOR Session gelöscht wird
        if user_id:
            _leave_all_tische(user_id)
        # Account bleibt in DB (Pool!), nur Session löschen
        delete_session(slot['session_token'])
        if kicked:
            kick_msg = _guest_kick(slot['ip'])
            log.info(f'🚪 GAST GEKICKT — Gast{slot_nr} (IP: {slot["ip"]})')
        else:
            log.info(f'🚪 GAST RAUS — Gast{slot_nr} (Slot frei)')
    return kick_msg

def is_guest_user(user_id):
    conn = get_db('accounts.db')
    user = conn.execute('SELECT is_guest FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return bool(user and user['is_guest'])

GUEST_KICK_MSGS = [
    'Freundliche Erinnerung: Benimm dich. 😊',
    '⚠️ Achtung! Sie werden bereits das 2. Mal ermahnt! Machen Sie so weiter und wir begrüßen Ihre Abwesenheit!',
    '🚫 Das war\'s. Tschüss! Wir haben Ihre IP notiert. Kommen Sie in ein paar Tagen wieder — wenn Sie sich benehmen können.',
]

def _guest_kick(ip):
    """Gast-Kick tracken → 3 Kicks in 24h = IP-Ban mit Eskalation"""
    now = time.time()
    kicks = guest_ip_kicks.get(ip, [])
    kicks = [t for t in kicks if now - t < 86400]  # Nur letzte 24h
    kicks.append(now)
    guest_ip_kicks[ip] = kicks
    kick_nr = len(kicks)
    if kick_nr >= 3:
        # Strike aus DB laden (überlebt Neustart!)
        conn = get_db('accounts.db')
        row = conn.execute('SELECT strike FROM guest_bans WHERE ip = ?', (ip,)).fetchone()
        old_strike = row['strike'] if row else 0
        strike = min(old_strike, len(GUEST_BAN_ESCALATION) - 1)
        duration = GUEST_BAN_ESCALATION[strike]
        until = now + duration
        conn.execute('INSERT OR REPLACE INTO guest_bans (ip, until, strike) VALUES (?, ?, ?)',
                     (ip, until, old_strike + 1))
        conn.commit()
        conn.close()
        guest_ip_bans[ip] = {'until': until, 'strike': old_strike + 1}
        guest_ip_kicks[ip] = []
        days = duration // 86400
        log.info(f'🚫 GAST IP-BAN — {ip} Strike {old_strike+1}: {days} Tage')
    return GUEST_KICK_MSGS[min(kick_nr - 1, len(GUEST_KICK_MSGS) - 1)]

def handle_guest_join(ip):
    # SMTP Pflicht — ohne SMTP keine Gäste!
    if not smtp_configured():
        return {'error': 'Kneipe nicht vollständig eingerichtet! SMTP fehlt — keine Gäste erlaubt.'}
    # Gäste erlaubt?
    gcfg = _guest_config()
    if not gcfg['enabled']:
        return {'error': 'Gäste sind derzeit nicht erwünscht. Registriere dich!'}
    # IP-Ban Check (DB-persistent!)
    conn_ban = get_db('accounts.db')
    ban_row = conn_ban.execute('SELECT until, strike FROM guest_bans WHERE ip = ? AND until > ?', (ip, time.time())).fetchone()
    conn_ban.close()
    if ban_row:
        days_left = int((ban_row['until'] - time.time()) / 86400) + 1
        return {'error': f'{gcfg["msg_banned"]} Noch {days_left} Tag(e).'}
    # Max 5 Gäste pro IP
    with guest_lock:
        ip_count = sum(1 for s in guest_slots.values() if s['ip'] == ip)
    if ip_count >= 5:
        return {'error': 'Maximale Gäste pro Verbindung erreicht (5). Registriere dich!'}
    slot_nr = _find_free_guest_slot()
    if slot_nr is None:
        return {'error': gcfg['msg_full']}
    guest_name = f'Gast{slot_nr}'
    now = time.time()
    conn = get_db('accounts.db')
    # Pool: Account recyceln wenn vorhanden, sonst neu erstellen
    existing = conn.execute('SELECT id FROM users WHERE name = ? AND is_guest = 1', (guest_name,)).fetchone()
    if existing:
        user_id = existing['id']
        conn.execute('UPDATE users SET updated_at = ? WHERE id = ?', (now, user_id))
    else:
        user_id = str(uuid.uuid4())
        conn.execute('''INSERT INTO users (id, name, email, pw_hash, verified, is_guest, tts_voice, created_at, updated_at)
                        VALUES (?, ?, ?, ?, 1, 1, 'de-DE-ConradNeural', ?, ?)''',
                     (user_id, guest_name, f'gast{slot_nr}@local', '', now, now))
    conn.commit()
    conn.close()
    token = create_session(user_id)
    with guest_lock:
        guest_slots[slot_nr] = {
            'user_id': user_id, 'session_token': token, 'ip': ip,
            'created': now, 'last_heartbeat': now,
        }
    log.info(f'🎫 GAST REIN — {guest_name} (Slot {slot_nr}, IP: {ip})')
    return {
        'ok': True, 'token': token,
        'user': {'id': user_id, 'name': guest_name, 'profile_pic': '', 'age': 'undefined',
                 'is_owner': False, 'is_guest': True, 'verified': True},
        'slot': slot_nr, 'expires_in': GUEST_SESSION_TIMEOUT,
    }

def handle_guest_heartbeat(user_id):
    with guest_lock:
        for slot_nr, slot in guest_slots.items():
            if slot['user_id'] == user_id:
                slot['last_heartbeat'] = time.time()
                slot['hb_misses'] = 0  # Reset AFK-Eskalation!
                remaining = GUEST_SESSION_TIMEOUT - (time.time() - slot['created'])
                return {'ok': True, 'slot': slot_nr, 'remaining': max(0, int(remaining))}
    return {'error': 'Kein aktiver Gast-Slot'}

def handle_guest_leave(user_id):
    with guest_lock:
        for slot_nr, slot in list(guest_slots.items()):
            if slot['user_id'] == user_id:
                break
        else:
            return {'error': 'Kein aktiver Gast-Slot'}
    _release_guest_slot(slot_nr)
    return {'ok': True}

def handle_guest_config_get(user_id):
    """Owner: Gäste-Config lesen"""
    conn = get_db('accounts.db')
    owner = conn.execute('SELECT is_owner FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    if not owner or not owner['is_owner']:
        return {'error': 'Nur Owner!'}
    gcfg = _guest_config()
    gcfg['active_slots'] = len(guest_slots)
    gcfg['slots'] = {nr: {'name': f'Gast{nr}', 'ip': s['ip'][:8]+'...'} for nr, s in guest_slots.items()}
    return {'ok': True, **gcfg}

def handle_guest_config_set(user_id, data):
    """Owner: Gäste-Config ändern"""
    conn = get_db('accounts.db')
    owner = conn.execute('SELECT is_owner FROM users WHERE id = ?', (user_id,)).fetchone()
    if not owner or not owner['is_owner']:
        conn.close()
        return {'error': 'Nur Owner!'}
    for key in ('guest_enabled', 'guest_max', 'guest_message_full', 'guest_message_banned', 'guest_bark_enabled', 'guest_voice_enabled', 'guest_play_enabled', 'register_code', 'register_code_required', 'kneipe_title', 'kneipe_subtitle'):
        if key in data:
            conn.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', (key, str(data[key])))
    conn.commit()
    conn.close()
    # Wenn Gäste deaktiviert → alle rauswerfen!
    if data.get('guest_enabled') == '0' or data.get('guest_enabled') == False:
        with guest_lock:
            all_slots = list(guest_slots.keys())
        for slot_nr in all_slots:
            _release_guest_slot(slot_nr)
        log.info(f'🚫 GÄSTE DEAKTIVIERT — {len(all_slots)} Gäste rausgeworfen')
    return {'ok': True}

def handle_guest_cleanup(user_id):
    """Owner: Alle Gäste rauswerfen + Bans löschen"""
    conn = get_db('accounts.db')
    owner = conn.execute('SELECT is_owner FROM users WHERE id = ?', (user_id,)).fetchone()
    if not owner or not owner['is_owner']:
        conn.close()
        return {'error': 'Nur Owner!'}
    with guest_lock:
        all_slots = list(guest_slots.keys())
    for slot_nr in all_slots:
        _release_guest_slot(slot_nr)
    conn.execute('DELETE FROM guest_bans')
    conn.commit()
    conn.close()
    log.info(f'🧹 GÄSTE CLEANUP (Owner) — {len(all_slots)} Slots + alle Bans gelöscht')
    return {'ok': True, 'cleared_slots': len(all_slots)}

def handle_guest_kick(owner_user_id, data):
    """Owner kickt einen Gast → Kick-Counter + ggf. IP-Ban"""
    conn = get_db('accounts.db')
    owner = conn.execute('SELECT is_owner FROM users WHERE id = ?', (owner_user_id,)).fetchone()
    conn.close()
    if not owner or not owner['is_owner']:
        return {'error': 'Nur der Owner kann Gäste kicken!'}
    target_name = data.get('name', '')
    with guest_lock:
        for slot_nr, slot in list(guest_slots.items()):
            if f'Gast{slot_nr}' == target_name:
                break
        else:
            return {'error': 'Gast nicht gefunden'}
    kick_msg = _release_guest_slot(slot_nr, kicked=True)
    return {'ok': True, 'kicked': target_name, 'message': kick_msg}

def _guest_cleanup_thread():
    while True:
        try:
            now = time.time()
            expired = []
            with guest_lock:
                for slot_nr, slot in guest_slots.items():
                    if now - slot['created'] >= GUEST_SESSION_TIMEOUT:
                        expired.append((slot_nr, '24h abgelaufen'))
                        continue
                    # 3x30sec kein Heartbeat = raus!
                    if now - slot['last_heartbeat'] >= GUEST_HEARTBEAT_TIMEOUT:
                        slot['hb_misses'] = slot.get('hb_misses', 0) + 1
                        if slot['hb_misses'] >= GUEST_MISS_LIMIT:
                            expired.append((slot_nr, f'3x Heartbeat verpasst'))
            for slot_nr, reason in expired:
                _release_guest_slot(slot_nr)
                log.info(f'🧹 GAST CLEANUP — Slot {slot_nr}: {reason}')
        except Exception as e:
            log.error(f'❌ Guest Cleanup Fehler: {e}')
        time.sleep(30)

TISCH_INACTIVITY_TIMEOUT = 3600  # 1 Stunde ohne Aktivität = raus!
TISCH_EMPTY_RENEW_TIMEOUT = 3600  # 1 Stunde komplett leer → Tisch stirbt, neuer Platz

def _tisch_inactivity_thread():
    """Zwei Aufgaben:
    1. Kickt User die 1h am Tisch sitzen ohne Aktivität.
    2. Ersetzt Tische die 1h durchgehend leer standen — nur dann! Timer
       setzt zurück sobald jemand beitritt (empty_since=None) und
       startet erst wenn der letzte Member aufsteht.
    """
    while True:
        try:
            now = time.time()
            to_kick = []  # (tisch_id, user_id, name)
            to_renew = []  # (raum_id, tisch_id)
            if 'raeume' not in globals() or not raeume:
                time.sleep(300)
                continue
            for r in raeume.values():
                for tid, t in r['tische'].items():
                    # 1) Member-AFK-Kick pro User
                    for uid in list(t['members']):
                        last = t['member_last_active'].get(uid, t['last_active'])
                        if now - last >= TISCH_INACTIVITY_TIMEOUT:
                            name = t['member_names'].get(uid, '???')
                            to_kick.append((tid, uid, name))
                    # 2) Tisch-Leer-Renew — NUR wenn tatsächlich noch leer
                    empty_since = t.get('empty_since')
                    if (not t['members'] and empty_since is not None and
                            now - empty_since >= TISCH_EMPTY_RENEW_TIMEOUT):
                        to_renew.append((r['id'], tid))

            for tid, uid, name in to_kick:
                handle_tisch_leave(uid, {'tisch_id': tid})
                with chat_lock:
                    chat_rooms.setdefault(tid, []).append({
                        'system': True,
                        'text': f'💤 {name} wurde nach 1h Stille vom Tisch geschickt.',
                        'time': time.time()
                    })
                log.info(f'💤 INAKTIV-KICK — {name} ← {tid} (1h ohne Aktivität)')

            for raum_id, tid in to_renew:
                with raum_lock:
                    r = raeume.get(raum_id)
                    if not r or tid not in r['tische']:
                        continue
                    # Doppelcheck innerhalb des Locks — nicht doch jemand reingekommen?
                    if r['tische'][tid]['members']:
                        r['tische'][tid]['empty_since'] = None
                        continue
                    del r['tische'][tid]
                    new_t = spawn_tisch(raum_id)
                    r['tische'][new_t['id']] = new_t
                # Chat-History des alten Tischs löschen (Tisch ist tot)
                with chat_lock:
                    chat_rooms.pop(tid, None)
                log.info(f'🔄 TISCH-RENEW — {tid} war 1h leer → neuer Tisch {new_t["id"]}')
        except Exception as e:
            log.error(f'❌ Tisch-Inaktivitäts-Cleanup Fehler: {e}')
        time.sleep(300)  # Alle 5 Minuten prüfen

# Session cleanup thread
def cleanup_sessions():
    while True:
        time.sleep(3600)
        now = time.time()
        with sessions_lock:
            expired_uids = [(k, v['user_id']) for k, v in sessions.items()
                            if now - v['last_active'] > 86400]
            for k, _ in expired_uids:
                del sessions[k]
        # Tische aufräumen außerhalb des sessions_lock — verhindert Geist-Member
        for _, uid in expired_uids:
            try:
                _leave_all_tische(uid)
            except Exception as e:
                log.error(f'❌ Session-Cleanup tisch-leave {uid}: {e}')
threading.Thread(target=cleanup_sessions, daemon=True).start()

# Unverified account cleanup thread (7 Tage)
def cleanup_unverified():
    while True:
        time.sleep(3600 * 6)  # Alle 6 Stunden prüfen
        try:
            conn = get_db('accounts.db')
            cutoff = time.time() - (7 * 86400)  # 7 Tage
            deleted = conn.execute(
                'SELECT name, email FROM users WHERE verified = 0 AND is_owner = 0 AND created_at < ?', (cutoff,)
            ).fetchall()
            if deleted:
                conn.execute(
                    'DELETE FROM users WHERE verified = 0 AND is_owner = 0 AND created_at < ?', (cutoff,)
                )
                conn.commit()
                for d in deleted:
                    log.info(f'🗑️ UNVERIFIED CLEANUP — {d["name"]} ({d["email"]}) nach 7 Tagen gelöscht')
            conn.close()
        except Exception as e:
            log.error(f'⚠️ Cleanup-Fehler: {e}')
threading.Thread(target=cleanup_unverified, daemon=True).start()
threading.Thread(target=_guest_cleanup_thread, daemon=True).start()
threading.Thread(target=_tisch_inactivity_thread, daemon=True).start()

# --- DATABASE ---
# Wrapper für sqlite3.Connection: garantiertes close() per Context-Manager
# UND per __del__ (Auto-Cleanup falls Caller close vergisst → kein Lock-Stau).
# Backward-kompatibel: alle alten conn.execute() / conn.close() Calls funktionieren.
class _DBConn:
    __slots__ = ('_conn', '_closed')
    def __init__(self, path):
        # timeout=30s gibt SQLite Zeit auf Locks zu warten statt sofort zu sterben
        # (Defaultsfehler bei "database is locked" / "unable to open database file")
        self._conn = sqlite3.connect(path, timeout=30, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute('PRAGMA journal_mode=WAL')  # WAL = besseres Concurrency-Verhalten
        self._closed = False
    def __enter__(self):
        return self._conn
    def __exit__(self, *args):
        self.close()
    def close(self):
        if not self._closed:
            try:
                self._conn.close()
            except Exception:
                pass
            self._closed = True
    def __del__(self):
        # Auto-Close bei Garbage Collection — verhindert Connection-Leaks
        # falls jemand vergisst close() zu rufen.
        self.close()
    def __getattr__(self, name):
        # Delegiere alle anderen Methoden an die echte Connection
        return getattr(self._conn, name)


def get_db(name):
    path = os.path.join(DB_DIR, name)
    return _DBConn(path)

def init_db():
    # Accounts
    conn = get_db('accounts.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        email TEXT DEFAULT '',
        pw_hash TEXT NOT NULL,
        totp_secret TEXT,
        totp_enabled INTEGER DEFAULT 0,
        is_owner INTEGER DEFAULT 0,
        profile_pic TEXT DEFAULT '',
        age TEXT DEFAULT 'undefined',
        verified INTEGER DEFAULT 0,
        verify_token TEXT,
        api_key TEXT,
        is_bot INTEGER DEFAULT 0,
        is_guest INTEGER DEFAULT 0,
        created_at REAL,
        updated_at REAL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS guest_bans (
        ip TEXT PRIMARY KEY,
        until REAL,
        strike INTEGER DEFAULT 1
    )''')
    # Migration: themen_access + themen_plays_counter
    try:
        c.execute('ALTER TABLE users ADD COLUMN themen_access INTEGER DEFAULT 0')
    except:
        pass
    try:
        c.execute('ALTER TABLE users ADD COLUMN themen_plays_counter INTEGER DEFAULT 0')
    except:
        pass
    try:
        c.execute("ALTER TABLE users ADD COLUMN tts_voice TEXT DEFAULT 'de-DE-ConradNeural'")
    except:
        pass
    # V4.1 Cheater 3-Strikes
    try:
        c.execute('ALTER TABLE users ADD COLUMN cheater_strikes INTEGER DEFAULT 0')
    except:
        pass
    # V4.4.8 Gäste-System
    try:
        c.execute('ALTER TABLE users ADD COLUMN is_guest INTEGER DEFAULT 0')
    except:
        pass
    try:
        c.execute('ALTER TABLE users ADD COLUMN stammgast_banned_until REAL DEFAULT 0')
    except:
        pass
    # V4.8 ShinNexus-Integration
    for col, default in [('shinpai_id', "''"), ('nexus_url', "''"), ('nexus_verified', '0'), ('verification_level', '0')]:
        try:
            c.execute(f'ALTER TABLE users ADD COLUMN {col} TEXT DEFAULT {default}')
        except:
            pass
    # V5 PQ-Keys: ML-DSA-65 (Signatur) + ML-KEM-768 (Key-Exchange)
    for col in ('pq_dsa_pub', 'pq_kem_pub', 'pq_private_enc'):
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT DEFAULT ''")
        except:
            pass
    # V5.1 Verify-Code-Expiry (TTL für Email-Verify-Code)
    try:
        c.execute("ALTER TABLE users ADD COLUMN verify_expires REAL DEFAULT 0")
    except:
        pass
    conn.commit()
    conn.close()

    # Gameplay
    conn = get_db('gameplay.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS plays (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        theme_id TEXT NOT NULL,
        answers TEXT NOT NULL,
        element TEXT NOT NULL,
        flags_triggered TEXT DEFAULT '[]',
        is_stammgast INTEGER DEFAULT 0,
        is_mauerblümchen INTEGER DEFAULT 0,
        client_hour INTEGER DEFAULT 12,
        played_at REAL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS titles (
        user_id TEXT NOT NULL,
        title_id TEXT NOT NULL,
        earned_at REAL,
        PRIMARY KEY (user_id, title_id)
    )''')
    # Community Themen (eingereicht, pending)
    c.execute('''CREATE TABLE IF NOT EXISTS community_themes (
        id TEXT PRIMARY KEY,
        author_id TEXT NOT NULL,
        author_name TEXT NOT NULL,
        title TEXT NOT NULL,
        setting TEXT NOT NULL,
        content_json TEXT NOT NULL,
        content_md TEXT NOT NULL,
        stammgast INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending',
        likes INTEGER DEFAULT 0,
        dislikes INTEGER DEFAULT 0,
        submitted_at REAL,
        approved_at REAL
    )''')
    # Votes auf Community Themen
    c.execute('''CREATE TABLE IF NOT EXISTS theme_votes (
        user_id TEXT NOT NULL,
        theme_id TEXT NOT NULL,
        vote INTEGER NOT NULL,
        PRIMARY KEY (user_id, theme_id)
    )''')
    # Bierdeckel (Wand-Sprüche) mit Lebenszyklus
    c.execute('''CREATE TABLE IF NOT EXISTS bierdeckel (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        user_name TEXT NOT NULL,
        text TEXT NOT NULL,
        prost_count INTEGER DEFAULT 0,
        phase TEXT DEFAULT 'collecting',
        phase_ends_at REAL DEFAULT 0,
        last_prost_at REAL DEFAULT 0,
        survive_count INTEGER DEFAULT 0,
        tier INTEGER DEFAULT 1,
        created_at REAL
    )''')
    # Migration: neue Felder für bestehende DB
    for col, default in [('phase', "'collecting'"), ('phase_ends_at', '0'), ('last_prost_at', '0'),
                         ('survive_count', '0'), ('tier', '1'),
                         ('voice_file', "''"), ('voice_expires_at', '0'),
                         ('archived', '0'), ('archive_vote_ends_at', '0'),
                         ('rebirth_vote_ends_at', '0')]:
        try:
            c.execute(f'ALTER TABLE bierdeckel ADD COLUMN {col} DEFAULT {default}')
        except:
            pass
    # Bierdeckel Prosts (wer hat wem geprosts)
    c.execute('''CREATE TABLE IF NOT EXISTS bierdeckel_prosts (
        bierdeckel_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        prost_type TEXT NOT NULL,
        created_at REAL,
        PRIMARY KEY (bierdeckel_id, user_id)
    )''')
    # Bierdeckel Votes (Archiv + Wiedergeburt)
    c.execute('''CREATE TABLE IF NOT EXISTS bierdeckel_votes (
        bierdeckel_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        vote_type TEXT NOT NULL,
        vote INTEGER NOT NULL,
        created_at REAL,
        PRIMARY KEY (bierdeckel_id, user_id, vote_type)
    )''')
    # V4.1 Raum-Persistenz
    c.execute('''CREATE TABLE IF NOT EXISTS rooms (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        name_fixed INTEGER DEFAULT 0,
        name_changes INTEGER DEFAULT 0,
        eigenschaften TEXT DEFAULT '[]',
        phase TEXT DEFAULT 'open',
        phase_ends_at REAL DEFAULT 0,
        tier INTEGER DEFAULT 1,
        survive_count INTEGER DEFAULT 0,
        created_at REAL,
        last_active REAL DEFAULT 0,
        archived INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS tables_db (
        id TEXT PRIMARY KEY,
        raum_id TEXT NOT NULL,
        thema TEXT DEFAULT '🍺',
        energie_emoji TEXT DEFAULT '😌',
        energie_label TEXT DEFAULT '',
        energie_valenz TEXT DEFAULT 'positiv',
        energie_intensitaet TEXT DEFAULT 'sanft',
        user_name TEXT DEFAULT '',
        created_at REAL,
        last_active REAL DEFAULT 0
    )''')
    # V4.1 Tribunal
    c.execute('''CREATE TABLE IF NOT EXISTS tribunals (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        user_name TEXT NOT NULL,
        reflection TEXT NOT NULL,
        status TEXT DEFAULT 'open',
        votes_yes INTEGER DEFAULT 0,
        votes_no INTEGER DEFAULT 0,
        voters TEXT DEFAULT '[]',
        created_at REAL,
        resolved_at REAL
    )''')
    # V4.5 Cheater-Unflag-Voting
    c.execute('''CREATE TABLE IF NOT EXISTS cheater_votes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_name TEXT NOT NULL,
        voter_id TEXT NOT NULL,
        voted_at REAL,
        UNIQUE(target_name, voter_id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS cheater_vote_sessions (
        target_name TEXT PRIMARY KEY,
        status TEXT DEFAULT 'voting',
        escalation_level INTEGER DEFAULT 0,
        trigger_count INTEGER DEFAULT 0,
        timer_ends_at REAL DEFAULT 0,
        locked_until REAL DEFAULT 0,
        created_at REAL,
        resolved_at REAL
    )''')
    # V4.5 Archiv-Protokoll
    c.execute('''CREATE TABLE IF NOT EXISTS archiv_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        db_name TEXT NOT NULL,
        table_name TEXT NOT NULL,
        deleted_count INTEGER NOT NULL,
        max_age TEXT NOT NULL,
        cleaned_at REAL
    )''')
    conn.commit()
    conn.close()

# --- ARCHIV-FUNKTION (Universell) ---
# Erlaubte Aufbewahrungszeiten — mehr gibt's nicht!
ARCHIV_ZEITEN = {
    '1h':      3600,
    '6h':      21600,
    '12h':     43200,
    '24h':     86400,
    '3d':      259200,
    '7d':      604800,
    '30d':     2592000,
    '365d':    31536000,
}

def _get_archiv_interval():
    """Owner-konfiguriertes Archiv-Intervall in Sekunden (Default: 3600 = 1h)."""
    conn = get_db('accounts.db')
    row = conn.execute("SELECT value FROM config WHERE key = 'archiv_interval'").fetchone()
    conn.close()
    if row:
        return int(row['value'])
    return 3600


def archiv_cleanup(db_name, table, ts_column, max_age):
    """Universelle Aufräum-Funktion: Löscht Einträge älter als max_age.

    Args:
        db_name:   'gameplay.db' oder 'accounts.db'
        table:     Tabellenname
        ts_column: Spalte mit dem Timestamp (REAL, Unix-Epoch)
        max_age:   Key aus ARCHIV_ZEITEN ('1h','6h','12h','24h','3d','7d','30d','365d')
                   ODER direkt Sekunden (int/float)

    Returns:
        Anzahl gelöschter Einträge

    Beispiel:
        archiv_cleanup('gameplay.db', 'cheater_votes', 'voted_at', '30d')
        archiv_cleanup('accounts.db', 'guest_bans', 'until', 3600)
    """
    if isinstance(max_age, str):
        sek = ARCHIV_ZEITEN.get(max_age)
        if not sek:
            log.warning(f'⚠️ ARCHIV: Unbekannte Zeit "{max_age}"! Erlaubt: {list(ARCHIV_ZEITEN.keys())}')
            return 0
    else:
        sek = int(max_age)
    cutoff = time.time() - sek
    conn = get_db(db_name)
    cursor = conn.execute(f'DELETE FROM {table} WHERE {ts_column} < ?', (cutoff,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    if deleted > 0:
        log.info(f'🗑️ ARCHIV — {table}: {deleted} Einträge älter als {max_age} gelöscht')
        # Protokollieren!
        try:
            conn_gp = get_db('gameplay.db')
            conn_gp.execute('INSERT INTO archiv_log (db_name, table_name, deleted_count, max_age, cleaned_at) VALUES (?,?,?,?,?)',
                             (db_name, table, deleted, str(max_age), time.time()))
            conn_gp.commit()
            conn_gp.close()
        except Exception:
            pass
    return deleted


def _archiv_thread():
    """Background-Thread: Räumt regelmäßig alte Daten auf. Intervall = Owner-konfigurierbar."""
    while True:
        try:
            interval = _get_archiv_interval()
            time.sleep(interval)
            # Cheater-Votes: max 30 Tage
            archiv_cleanup('gameplay.db', 'cheater_votes', 'voted_at', '30d')
            # Abgeschlossene Cheater-Vote-Sessions: max 365 Tage
            archiv_cleanup('gameplay.db', 'cheater_vote_sessions', 'resolved_at', '365d')
            # Alte Tribunals: max 365 Tage
            archiv_cleanup('gameplay.db', 'tribunals', 'resolved_at', '365d')
            # Archiv-Log selbst aufräumen: max 30 Tage
            archiv_cleanup('gameplay.db', 'archiv_log', 'cleaned_at', '30d')
        except Exception as e:
            log.error(f'Archiv-Thread Fehler: {e}')


# --- OWNER CHECK ---
def has_owner():
    """Owner existiert wenn Identity-Vault UND DB-Eintrag da sind (beide nötig!)."""
    if not vault_exists():
        return False
    conn = get_db('accounts.db')
    owner = conn.execute('SELECT id FROM users WHERE is_owner = 1').fetchone()
    conn.close()
    return owner is not None

def get_smtp_config():
    conn = get_db('accounts.db')
    result = {}
    for key in ['smtp_host', 'smtp_port', 'smtp_user', 'smtp_pass', 'smtp_from']:
        row = conn.execute('SELECT value FROM config WHERE key = ?', (key,)).fetchone()
        val = row['value'] if row else ''
        # Sensitive Felder entschlüsseln
        if key == 'smtp_pass' and val:
            val = vault_decrypt(val)
        result[key] = val
    conn.close()
    return result

def save_smtp_config(data):
    conn = get_db('accounts.db')
    for key in ['smtp_host', 'smtp_port', 'smtp_user', 'smtp_pass', 'smtp_from']:
        if key in data:
            val = data[key]
            # Sensitive Felder verschlüsseln
            if key == 'smtp_pass' and val and not val.startswith('gA'):
                val = vault_encrypt(val)
            conn.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', (key, val))
    conn.commit()
    conn.close()

def smtp_configured():
    cfg = get_smtp_config()
    return bool(cfg.get('smtp_host') and cfg.get('smtp_user') and cfg.get('smtp_pass'))

# --- EMAIL SENDEN ---
def _get_public_url():
    """Öffentliche Basis-URL aus config-Tabelle.
    Wird im Setup-Step 'Public-URL-Check' explizit gesetzt.
    Fallback: lokale Instanz (nur für Solo-Test sinnvoll).
    """
    conn = get_db('accounts.db')
    row = conn.execute('SELECT value FROM config WHERE key = ?', ('public_url',)).fetchone()
    conn.close()
    if row and row['value']:
        return row['value'].rstrip('/')
    return f'http://127.0.0.1:{PORT}'


def _is_solo_mode():
    """True wenn Owner explizit Solo-Mode gewählt hat (kein SMTP, kein Public-URL, nur Gäste)."""
    conn = get_db('accounts.db')
    row = conn.execute('SELECT value FROM config WHERE key = ?', ('solo_mode',)).fetchone()
    conn.close()
    return bool(row and row['value'] == '1')


# ═════════════════════════════════════════════════════════════════════════
#  PUBLIC-URL WATCHDOG — Network-Change-Detection (adaptive, sparsam)
#  Full-Check = ipify(~2KB) + Self-Test(~3KB) = ~5 KB pro Check.
#  Default: 30 Min Intervall (~7 MB/Monat).
# ═════════════════════════════════════════════════════════════════════════

# Globaler State (nur RAM)
_network_state = {
    'external_ip': None,        # aktuelle externe IP (von ipify)
    'local_ips': [],            # lokale LAN-IPs (z.B. 192.168.x.y)
    'best_url': None,           # beste share-bare URL (DynDNS > externe IP > lokale IP > localhost)
    'reachable_external': False,
    'reachable_local': False,
    'last_check': 0,            # unix timestamp
    'last_error': '',
}


def _get_config_int(key, default):
    conn = get_db('accounts.db')
    row = conn.execute('SELECT value FROM config WHERE key = ?', (key,)).fetchone()
    conn.close()
    try:
        return int(row['value']) if row and row['value'] else default
    except (ValueError, TypeError):
        return default


def _detect_local_ips():
    """Alle lokalen IPv4-Adressen (ohne 127.x.x.x) — für Café-Netz-Szenario."""
    import socket as _s
    ips = []
    try:
        hostname = _s.gethostname()
        for info in _s.getaddrinfo(hostname, None, _s.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith('127.') and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    # Zusätzlich über UDP-Hack: Route zu 8.8.8.8 → Quelle ist aktive IP
    try:
        s = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith('127.') and ip not in ips:
            ips.append(ip)
    except Exception:
        pass
    return ips


def _fetch_external_ip():
    """ipify-Abfrage — ~2 KB pro Call. Hartes 3s-Timeout, kein Endlos-Block."""
    try:
        import urllib.request, socket as _sock
        req = urllib.request.Request('https://api.ipify.org?format=json', headers={'User-Agent': 'Kneipe/5.1'})
        # Globalen Default-Timeout temporär setzen, da urlopen-timeout bei DNS-Hängern
        # nicht immer greift
        old = _sock.getdefaulttimeout()
        _sock.setdefaulttimeout(3)
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                return data.get('ip') or None
        finally:
            _sock.setdefaulttimeout(old)
    except Exception as e:
        log.debug(f'ipify-Fehler: {e}')
        return None


def _selftest_url(test_url, timeout=6):
    """HTTP-GET auf {url}/api/status und Response-Title vergleichen.
    ECHTER Roundtrip — testet DNS + Firewall + Caddy + Server zusammen.
    Funktioniert nur mit ThreadingHTTPServer (damit wir parallel requesten + handlen können)!
    """
    try:
        import urllib.request, ssl
        if not test_url.startswith(('http://', 'https://')):
            test_url = 'http://' + test_url
        req = urllib.request.Request(f'{test_url.rstrip("/")}/api/status')
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read(4096).decode('utf-8', 'replace')
        remote = json.loads(body) if body else {}
        local_title = _guest_config().get('kneipe_title', 'Kneipen-Schlägerei')
        return remote.get('kneipe_title') == local_title
    except Exception:
        return False


def _externtest_url(test_url, timeout=10):
    """Externer Check via isitup.org — umgeht NAT-Loopback-Probleme.
    isitup.org prüft von außen ob die URL antwortet.
    Returns True wenn von außen erreichbar, False sonst.
    """
    try:
        import urllib.request, re
        # Host:Port aus URL extrahieren (ohne http://)
        m = re.match(r'^(?:https?://)?([^/]+)', test_url.strip().rstrip('/'))
        if not m:
            return False
        target = m.group(1)
        req = urllib.request.Request(
            f'https://isitup.org/{target}.json',
            headers={'User-Agent': 'Mozilla/5.0 Kneipe-NetCheck', 'Accept': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8', 'replace')
        if not body.strip():
            log.debug('isitup: leeres Response')
            return False
        data = json.loads(body)
        # status_code 1 = up, 2 = down, 3 = invalid domain
        return int(data.get('status_code', 0)) == 1
    except Exception as e:
        log.debug(f'isitup-Fehler: {e}')
        return False


def _check_external_reachable(test_url):
    """2-Stage-Check: erst Self-Test (billig, ECHTER Roundtrip),
    bei fail externer Check (robust, NAT-Loopback-Fallback).
    """
    if _selftest_url(test_url):
        return True, 'self'
    # Self-Test fail: externer Dienst fragen (NAT-Loopback-Fallback)
    if _externtest_url(test_url):
        return True, 'external'
    return False, None


def run_network_check(full=True):
    """Full-Check: externe IP holen + Self-Test + lokale IPs.
    Setzt _network_state. full=False = nur IP-Abfrage (sparsam).
    """
    global _network_state
    now = time.time()
    new_state = {
        'last_check': now,
        'last_error': '',
    }
    # Manuelle Override-URL?
    conn = get_db('accounts.db')
    manual = conn.execute('SELECT value FROM config WHERE key = ?', ('public_url',)).fetchone()
    conn.close()
    manual_url = (manual['value'].strip() if manual and manual['value'] else '')

    # Externe IP
    ext_ip = _fetch_external_ip()
    new_state['external_ip'] = ext_ip
    new_state['local_ips'] = _detect_local_ips()

    # Reachability — wenn manual_url gesetzt (z.B. via Caddy auf Port 443),
    # DIESE testen statt der rohen ext_ip:PORT (die ohne Portweiterleitung eh zu wäre).
    # Manual-URL hat Vorrang, weil Owner sie explizit gesetzt hat.
    if manual_url:
        test_url = manual_url
    elif ext_ip:
        test_url = f'http://{ext_ip}:{PORT}'
    else:
        test_url = None
    if full and test_url:
        reach, method = _check_external_reachable(test_url)
        new_state['reachable_external'] = reach
        new_state['reachable_via'] = method  # 'self' oder 'external' (isitup.org)
    else:
        new_state['reachable_external'] = False
        new_state['reachable_via'] = None
    # Lokaler Selbsttest (greift via LAN-IP, funktioniert auch ohne Internet)
    if full and new_state['local_ips']:
        loc_url = f'http://{new_state["local_ips"][0]}:{PORT}'
        new_state['reachable_local'] = _selftest_url(loc_url, timeout=3)
    else:
        new_state['reachable_local'] = False

    # Beste URL priorisieren: Manuell > Externe > Lokale > Localhost
    if manual_url:
        new_state['best_url'] = manual_url
    elif new_state['reachable_external']:
        new_state['best_url'] = test_url
    elif new_state['reachable_local']:
        new_state['best_url'] = f'http://{new_state["local_ips"][0]}:{PORT}'
    else:
        new_state['best_url'] = f'http://127.0.0.1:{PORT}'

    _network_state.update(new_state)
    log.info(f'🌐 Net-Check: ext={ext_ip or "?"} ({"✅" if new_state["reachable_external"] else "❌"}) '
             f'local={new_state["local_ips"][0] if new_state["local_ips"] else "?"} '
             f'({"✅" if new_state["reachable_local"] else "❌"}) → {new_state["best_url"]}')
    return _network_state


def _network_watchdog():
    """Background-Thread: periodischer Check gemäß Config. Mit Backoff bei Fail."""
    consecutive_fails = 0
    while True:
        try:
            conn = get_db('accounts.db')
            try:
                enabled_row = conn.execute('SELECT value FROM config WHERE key = ?', ('autocheck_enabled',)).fetchone()
            finally:
                conn.close()
            enabled = bool(enabled_row and enabled_row['value'] == '1')
            interval = _get_config_int('autocheck_interval_sec', 1800)
            if interval < 60:
                interval = 60
            if enabled:
                state = run_network_check(full=True)
                # Reset Backoff bei Erfolg, Backoff bei Fail
                if state.get('external_ip') or state.get('reachable_local'):
                    consecutive_fails = 0
                else:
                    consecutive_fails = min(consecutive_fails + 1, 6)
            # Bei wiederholten Fails: exponential Backoff (max 1h)
            backoff = interval * (2 ** consecutive_fails) if consecutive_fails > 0 else interval
            time.sleep(min(backoff, 3600))
        except Exception as e:
            log.error(f'Watchdog-Fehler: {e}')
            consecutive_fails = min(consecutive_fails + 1, 6)
            time.sleep(min(300 * (2 ** consecutive_fails), 3600))


def get_public_url():
    """Aktuell beste URL — aus Netzwerk-State. Fallback: localhost."""
    return _network_state.get('best_url') or f'http://127.0.0.1:{PORT}'


def send_verify_email(to_email, verify_code, username):
    """Verifizierungs-Mail mit 6-stelligem Code (kein Link mehr — DAU-tauglich, überall)."""
    cfg = get_smtp_config()
    if not cfg.get('smtp_host'):
        log.warning(f'📧 SMTP nicht konfiguriert — kann keine Mail senden!')
        return False

    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    html = f"""
    <div style="background:#0a0a0a;color:#e0d8c8;font-family:Georgia,serif;padding:30px;max-width:500px;margin:0 auto;">
      <div style="text-align:center;">
        <h1 style="color:#d4a850;margin:15px 0;">🍺 Kneipen-Schlägerei</h1>
        <p style="font-size:16px;">Willkommen, {username}!</p>
        <p style="font-size:14px;color:#887755;">Dein Verifikations-Code:</p>
        <div style="margin:25px auto;padding:20px 30px;background:#d4a850;color:#0a0a0a;border-radius:12px;font-size:42px;font-weight:bold;letter-spacing:12px;font-family:monospace;display:inline-block;">{verify_code}</div>
        <p style="font-size:13px;color:#887755;">In der Kneipe-App eingeben. <b>10 Minuten gültig.</b></p>
        <p style="font-size:12px;color:#665540;margin-top:18px;font-style:italic;">Falls du das nicht warst: Mail einfach ignorieren.</p>
        <hr style="border:none;border-top:1px solid #2a2015;margin:20px 0;">
        <p style="font-size:11px;color:#665540;">Shinpai-AI — Shinpai Games</p>
      </div>
    </div>
    """

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f'🍺 Kneipe-Code: {verify_code}'
        msg['From'] = cfg.get('smtp_from', cfg['smtp_user'])
        msg['To'] = to_email
        msg.attach(MIMEText(f'Dein Kneipe-Verifikations-Code: {verify_code}\n\nIn der App eingeben. 10 Minuten gültig.', 'plain'))
        msg.attach(MIMEText(html, 'html'))

        port = int(cfg.get('smtp_port', 587))
        if port == 465:
            server = smtplib.SMTP_SSL(cfg['smtp_host'], port, timeout=30)
        else:
            server = smtplib.SMTP(cfg['smtp_host'], port, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
        server.login(cfg['smtp_user'], cfg['smtp_pass'])
        server.sendmail(msg['From'], to_email, msg.as_string())
        server.quit()
        log.info(f'📧 Verifizierungs-Mail gesendet an {to_email}')
        return True
    except Exception as e:
        log.error(f'📧 Mail-Versand fehlgeschlagen: {e}')
        return False

# --- SHINPAI-ID (identisch mit ShinNexus!) ---
_B62 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

def _b62_hash(seed, length=6):
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    result = ""
    for _ in range(length):
        result = _B62[h % 62] + result
        h //= 62
    return result

def _generate_shinpai_id(name, email):
    return f"{_b62_hash(f'shinpai-name-{name}')}-{_b62_hash(f'shinpai-email-{email}')}"


# --- NEXUS REQUEST HELPER ---
# ═════════════════════════════════════════════════════════════════════════
#  V5.2 — SHINNEXUS TRUST-WHITELIST
#  Owner-gepflegte Liste erlaubter Nexus-Code-Hashes. Schützt vor gefälschten
#  Nexus-Forks die 18+ / Identität fälschen wollen.
#  Leer = Permissive (alle akzeptieren, Default). Gefüllt = Strict.
# ═════════════════════════════════════════════════════════════════════════

def nexus_whitelist_get():
    """Liste erlaubter Nexus-Code-Hashes. Leer = Permissive."""
    conn = get_db('accounts.db')
    row = conn.execute('SELECT value FROM config WHERE key = ?', ('nexus_whitelist',)).fetchone()
    conn.close()
    if not row or not row['value']:
        return []
    try:
        return json.loads(row['value'])
    except Exception:
        return []


def nexus_whitelist_add(code_hash, label=''):
    hashes = nexus_whitelist_get()
    entry = {'hash': code_hash.strip(), 'label': label.strip()[:80], 'added_at': time.time()}
    # Duplikat prüfen
    if any(e.get('hash') == entry['hash'] for e in hashes):
        return False
    hashes.append(entry)
    conn = get_db('accounts.db')
    conn.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)',
                 ('nexus_whitelist', json.dumps(hashes)))
    conn.commit()
    conn.close()
    return True


def nexus_whitelist_remove(code_hash):
    hashes = nexus_whitelist_get()
    new_list = [e for e in hashes if e.get('hash') != code_hash]
    conn = get_db('accounts.db')
    conn.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)',
                 ('nexus_whitelist', json.dumps(new_list)))
    conn.commit()
    conn.close()


def nexus_code_hash_is_trusted(code_hash):
    """True wenn Hash in Whitelist. Leere Whitelist = kein Anchor = kein Zugriff."""
    hashes = nexus_whitelist_get()
    if not hashes:
        return False
    return any(e.get('hash') == code_hash for e in hashes)


def verify_nexus_trust(nx_url):
    """Holt code_hash vom Nexus via /api/chain/info und prüft gegen Whitelist.
    Returns (trusted: bool, code_hash: str, version: str).
    """
    status, data = nexus_request(nx_url, '/api/chain/info')
    if status != 200 or not isinstance(data, dict):
        return (False, '', '')
    code_hash = data.get('code_hash', '')
    version = data.get('version', '')
    return (nexus_code_hash_is_trusted(code_hash), code_hash, version)


def nexus_request(nexus_url, path, data=None):
    """HTTP(S)-Request an ShinNexus. Gibt (status_code, response_dict) zurück."""
    import urllib.request, urllib.error, ssl
    full_url = f"{nexus_url.rstrip('/')}{path}"
    headers = {"Accept": "application/json"}
    body = None
    if data:
        headers["Content-Type"] = "application/json; charset=utf-8"
        body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(full_url, data=body, headers=headers)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", errors="replace"))
        except Exception:
            return e.code, {"error": f"ShinNexus HTTP {e.code}"}
    except Exception as e:
        return 502, {"error": f"ShinNexus nicht erreichbar: {e}"}


# ═════════════════════════════════════════════════════════════════════════
#  BITCOIN WALLET — Chain of Trust (kopiert von ShinNexus 1:1)
# ═════════════════════════════════════════════════════════════════════════

def _btc_wallet_load() -> dict:
    """Wallet aus dem Vault laden. Gibt {wif, address, entries[]} zurück oder {}."""
    if not vault_is_unlocked() or not BTC_WALLET_VAULT.exists():
        return {}
    try:
        raw = _vault_decrypt_bytes(BTC_WALLET_VAULT.read_bytes())
        return json.loads(raw)
    except Exception:
        return {}


def _btc_wallet_save(data: dict) -> bool:
    """Wallet-Daten in den Vault speichern."""
    if not vault_is_unlocked():
        return False
    try:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        BTC_WALLET_VAULT.write_bytes(_vault_encrypt_bytes(raw))
        return True
    except Exception:
        return False


def _btc_wallet_create() -> dict:
    """Neues Bitcoin-Wallet erzeugen mit BIP39 Seed."""
    try:
        import secrets as _secrets
        try:
            from hdwallet import HDWallet
            from hdwallet.cryptocurrencies import Bitcoin
            from hdwallet.hds import BIP84HD
            from hdwallet.mnemonics.bip39 import BIP39Mnemonic
            from hdwallet.entropies.bip39 import BIP39Entropy
            from hdwallet.derivations import BIP84Derivation
        except ImportError as ie:
            log.error(f"⚠️ BTC: hdwallet nicht installiert! pip install hdwallet — {ie}")
            return {}
        entropy = BIP39Entropy(entropy=_secrets.token_hex(16))
        mnemonic_words = BIP39Mnemonic.from_entropy(entropy, language="english")
        mn = BIP39Mnemonic(mnemonic=mnemonic_words)
        hd = HDWallet(cryptocurrency=Bitcoin, hd=BIP84HD, network="mainnet")
        hd.from_mnemonic(mn)
        hd.from_derivation(BIP84Derivation(coin_type=0, account=0, change="external-chain", address=0))
        wif = hd.wif()
        addr = hd.address()
        return {"wif": wif, "address": addr, "mnemonic": mnemonic_words, "entries": [], "created_at": int(time.time())}
    except Exception as e:
        log.error(f"⚠️ BTC Wallet-Erzeugung fehlgeschlagen: {e}")
        return {}


def _btc_get_fee() -> int:
    """Dynamische Fee von mempool.space holen. Gibt sat/vB zurück."""
    try:
        import urllib.request
        with urllib.request.urlopen("https://mempool.space/api/v1/fees/recommended", timeout=10) as r:
            fees = json.loads(r.read())
        return max(int(fees.get("hourFee", 3)), 1)
    except Exception:
        return 3


def _btc_get_price_eur() -> float:
    """Aktuellen BTC-Preis in EUR holen."""
    try:
        import urllib.request
        with urllib.request.urlopen("https://mempool.space/api/v1/prices", timeout=10) as r:
            prices = json.loads(r.read())
        return float(prices.get("EUR", 0))
    except Exception:
        return 0.0


def _btc_estimate_fee_sats() -> tuple:
    """Fee berechnen für OP_RETURN TX. Gibt (fee_sats, sat_per_vb) zurück."""
    sat_per_vb = _btc_get_fee()
    tx_size_vb = 150
    return sat_per_vb * tx_size_vb, sat_per_vb


def _btc_check_tx_confirmed(txid: str) -> dict:
    """TX-Status von mempool.space prüfen."""
    try:
        import urllib.request
        url = f"https://mempool.space/api/tx/{txid}"
        with urllib.request.urlopen(url, timeout=10) as r:
            tx = json.loads(r.read())
        status = tx.get("status", {})
        return {
            "confirmed": bool(status.get("confirmed")),
            "block_height": status.get("block_height", 0),
            "block_time": status.get("block_time", 0),
        }
    except Exception:
        return {"confirmed": False, "block_height": 0, "block_time": 0}


def _btc_write_anchor_json(entry: dict):
    """anchor.json neben server.py schreiben — das öffentliche Zertifikat."""
    try:
        existing = []
        if ANCHOR_JSON.exists():
            old = json.loads(ANCHOR_JSON.read_text("utf-8"))
            existing = old.get("history", [])
        existing.append(entry)
        anchor = {
            "version": entry["version"],
            "code_hash": entry["code_hash"],
            "txid": entry["txid"],
            "btc_address": entry["address"],
            "timestamp": entry["timestamp"],
            "op_return": entry.get("op_return", ""),
            "company": "Shinpai-AI",
            "revoked": False,
            "history": existing,
        }
        ANCHOR_JSON.write_text(json.dumps(anchor, indent=2, ensure_ascii=False), "utf-8")
        log.info("📄 anchor-kneipe.json geschrieben")
    except Exception as e:
        log.error(f"⚠️ anchor.json schreiben fehlgeschlagen: {e}")


def _btc_write_anchor_json_raw(anchor: dict) -> None:
    """anchor.json direkt überschreiben."""
    try:
        ANCHOR_JSON.write_text(json.dumps(anchor, indent=2, ensure_ascii=False), "utf-8")
    except Exception as e:
        log.error(f"⚠️ anchor.json raw-write fehlgeschlagen: {e}")


def _btc_update_anchor_status(updates: dict) -> None:
    """Merge updates in anchor.json."""
    try:
        if not ANCHOR_JSON.exists():
            return
        data = json.loads(ANCHOR_JSON.read_text("utf-8"))
        data.update(updates)
        ANCHOR_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    except Exception as e:
        log.error(f"⚠️ anchor.json Update fehlgeschlagen: {e}")


def _parse_op_return_from_script(scriptpubkey_hex: str):
    """Extract push-data from an OP_RETURN scriptPubKey (hex encoded)."""
    if not scriptpubkey_hex or not scriptpubkey_hex.startswith("6a"):
        return None
    try:
        remaining = scriptpubkey_hex[2:]
        if not remaining:
            return None
        op = int(remaining[:2], 16)
        if 1 <= op <= 75:
            data_hex = remaining[2:2 + op * 2]
        elif op == 0x4c:
            length = int(remaining[2:4], 16)
            data_hex = remaining[4:4 + length * 2]
        elif op == 0x4d:
            length = int(remaining[4:6] + remaining[2:4], 16)
            data_hex = remaining[6:6 + length * 2]
        else:
            return None
        return bytes.fromhex(data_hex).decode("utf-8", errors="replace")
    except Exception:
        return None


def _btc_verify_anchor_live(txid: str, expected_hash: str, timeout: float = 10.0) -> dict:
    """Live-Verifikation: TX von mempool.space holen, OP_RETURN prüfen."""
    if not txid or not expected_hash:
        return {"status": "bad_format", "checked_at": int(time.time()), "error": "txid oder expected_hash fehlt"}
    try:
        import urllib.request
        req = urllib.request.Request(f"https://mempool.space/api/tx/{txid}",
                                     headers={"User-Agent": f"KneipenSchlaegerei/{VERSION}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            tx_data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"status": "network_error", "checked_at": int(time.time()), "error": f"mempool.space nicht erreichbar: {e}"}
    op_return_text = None
    for vout in tx_data.get("vout", []) or []:
        if vout.get("scriptpubkey_type") == "op_return":
            text = _parse_op_return_from_script(vout.get("scriptpubkey", ""))
            if text:
                op_return_text = text
                break
    if not op_return_text:
        return {"status": "bad_format", "checked_at": int(time.time()), "error": "Keine OP_RETURN in dieser TX"}
    parts = op_return_text.split(":")
    if len(parts) < 3 or parts[0] != "SHINPAI-AI":
        return {"status": "bad_format", "checked_at": int(time.time()), "error": f"Nicht SHINPAI-AI-Format: {op_return_text[:40]}"}
    on_chain_version = parts[1]
    on_chain_hash_prefix = parts[2]
    expected_prefix = expected_hash[:len(on_chain_hash_prefix)]
    match = (on_chain_hash_prefix.lower() == expected_prefix.lower())
    status_info = tx_data.get("status", {}) or {}
    confirmed = bool(status_info.get("confirmed", False))
    return {
        "status": "match" if match else "mismatch",
        "checked_at": int(time.time()),
        "on_chain_hash_prefix": on_chain_hash_prefix,
        "on_chain_version": on_chain_version,
        "block_height": int(status_info.get("block_height", 0)) if confirmed else 0,
        "confirmed": confirmed,
    }


def _whitelist_auto_default_from_anchor(entry: dict) -> bool:
    """Nach erfolgreicher BTC-Verankerung automatisch einen Whitelist-Default-Eintrag
    für die eigene Version einfügen. 1:1 von ShinNexus."""
    try:
        h = (entry.get("code_hash") or "").lower()
        if not h:
            return False
        conn = get_db('accounts.db')
        row = conn.execute('SELECT value FROM config WHERE key = ?', ('nexus_whitelist',)).fetchone()
        items = json.loads(row['value']) if row and row['value'] else []
        # Alte auto_default-Einträge aufräumen (nur EIN aktueller Default) — 1:1 Nexus
        items = [it for it in items if not it.get("auto_default")]
        # Wenn Hash schon manuell drin → nichts hinzufügen, nur alte auto_default entfernen
        if any((it.get("hash", "").lower() == h) for it in items):
            conn.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)',
                         ('nexus_whitelist', json.dumps(items)))
            conn.commit()
            conn.close()
            return False
        company = (entry.get("company") or "").strip() or "Shinpai-AI"
        items.append({
            "version": entry.get("version", ""),
            "hash": h,
            "txid": (entry.get("txid") or "").lower(),
            "label": f"{company} (Default)",
            "auto_default": True,
            "added_at": int(time.time()),
        })
        conn.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)',
                     ('nexus_whitelist', json.dumps(items)))
        conn.commit()
        conn.close()
        log.info(f"🦋 Whitelist Auto-Default gesetzt: v{entry.get('version', '?')} ({company})")
        return True
    except Exception as e:
        log.error(f"⚠️ Whitelist Auto-Default fehlgeschlagen: {e}")
        return False


def _btc_read_anchor_json() -> dict:
    """anchor.json lesen. Gibt {} zurück wenn nicht vorhanden."""
    try:
        if ANCHOR_JSON.exists():
            return json.loads(ANCHOR_JSON.read_text("utf-8"))
    except Exception:
        pass
    return {}


def _btc_startup_integrity_check():
    """Beim Start: Code-Hash mit anchor.json vergleichen + live verifizieren."""
    anchor = _btc_read_anchor_json()
    if not anchor or not anchor.get("code_hash"):
        log.info("ℹ️ Keine Verankerung vorhanden")
        return
    try:
        with open(__file__, "rb") as f:
            current_hash = hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return
    if anchor.get("revoked"):
        log.warning(f"🔴 ACHTUNG: Version {anchor.get('version', '?')} wurde WIDERRUFEN!")
        return
    if current_hash != anchor["code_hash"]:
        log.info("⚠️ Code seit letzter Verankerung geändert (nicht verankert)")
        return
    log.info(f"✅ Code-Hash stimmt mit Verankerung v{anchor.get('version', '?')} überein (lokal)")
    _btc_live_verify_and_persist(anchor, current_hash)


def _btc_live_verify_and_persist(anchor=None, current_hash=None) -> dict:
    """Live-Check + persistiere in anchor.json. Bei match: Auto-Whitelist."""
    if anchor is None:
        anchor = _btc_read_anchor_json()
    if not anchor or not anchor.get("txid") or not anchor.get("code_hash"):
        return {"status": "no_anchor"}
    if current_hash is None:
        try:
            with open(__file__, "rb") as f:
                current_hash = hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return {"status": "no_local_hash"}
    result = _btc_verify_anchor_live(anchor["txid"], current_hash)
    _btc_update_anchor_status({
        "live_verify_status": result.get("status"),
        "last_live_verify": result.get("checked_at"),
        "live_verify_detail": {
            "on_chain_hash_prefix": result.get("on_chain_hash_prefix"),
            "on_chain_version": result.get("on_chain_version"),
            "block_height": result.get("block_height"),
            "confirmed": result.get("confirmed"),
            "error": result.get("error"),
        },
    })
    if result.get("status") == "match":
        _whitelist_auto_default_from_anchor(anchor)
    if anchor.get("btc_address") and current_hash and not anchor.get("revoked"):
        _btc_check_revoke_broadcast(anchor, current_hash)
    return result


def _btc_check_revoke_broadcast(anchor: dict, current_hash: str) -> bool:
    """Scannt BTC-Adresse nach REVOKE-TXs."""
    addr = anchor.get("btc_address", "")
    if not addr:
        return False
    try:
        import urllib.request
        req = urllib.request.Request(f"https://mempool.space/api/address/{addr}/txs",
                                     headers={"User-Agent": f"KneipenSchlaegerei/{VERSION}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            txs = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.warning(f"⚠️ Revoke-Broadcast-Scan fehlgeschlagen: {e}")
        return False
    hash_short = current_hash[:32]
    for tx in txs:
        for vout in (tx.get("vout") or []):
            if vout.get("scriptpubkey_type") != "op_return":
                continue
            text = _parse_op_return_from_script(vout.get("scriptpubkey", ""))
            if not text:
                continue
            if text.startswith("SHINPAI-AI:REVOKE:") and text.split(":")[2] == hash_short:
                log.warning(f"🔴 REVOKE on-chain erkannt für Hash {hash_short[:16]}…!")
                anchor["revoked"] = True
                anchor["revoked_at"] = int(time.time())
                anchor["revoke_txid"] = tx.get("txid", "")
                for entry in (anchor.get("history") or []):
                    if (entry.get("code_hash") or "")[:32] == hash_short:
                        entry["revoked"] = True
                        entry["revoked_at"] = int(time.time())
                        entry["revoke_txid"] = tx.get("txid", "")
                _btc_write_anchor_json_raw(anchor)
                return True
    return False


def _btc_wallet_anchor_hash(code_hash: str, version: str):
    """Code-Hash als OP_RETURN in die Bitcoin-Blockchain schreiben."""
    wallet = _btc_wallet_load()
    if not wallet or not wallet.get("wif"):
        return None
    try:
        try:
            from bitcoinutils.setup import setup
            from bitcoinutils.keys import PrivateKey
            from bitcoinutils.transactions import Transaction, TxInput, TxOutput, TxWitnessInput
            from bitcoinutils.script import Script
        except ImportError as ie:
            log.error(f"⚠️ BTC: bitcoin-utils nicht installiert! pip install bitcoin-utils — {ie}")
            return None
        import urllib.request
        setup("mainnet")
        pk = PrivateKey.from_wif(wallet["wif"])
        pub = pk.get_public_key()
        addr = pub.get_segwit_address()
        addr_str = addr.to_string()
        utxo_url = f"https://mempool.space/api/address/{addr_str}/utxo"
        with urllib.request.urlopen(utxo_url, timeout=15) as resp:
            utxos = json.loads(resp.read())
        if not utxos:
            log.warning("⚠️ BTC: Keine UTXOs, Wallet leer")
            return None
        utxo = max(utxos, key=lambda u: u["value"])
        txin = TxInput(utxo["txid"], utxo["vout"])
        op_data = f"SHINPAI-AI:{version}:{code_hash[:32]}".encode("utf-8")
        if len(op_data) > 80:
            op_data = op_data[:80]
        fee_sats, sat_per_vb = _btc_estimate_fee_sats()
        change = utxo["value"] - fee_sats
        if change < 0:
            log.warning(f"⚠️ BTC: Nicht genug Sats ({utxo['value']} vorhanden, {fee_sats} nötig)")
            return None
        op_return_out = TxOutput(0, Script(["OP_RETURN", op_data.hex()]))
        change_out = TxOutput(change, addr.to_script_pub_key())
        tx = Transaction([txin], [op_return_out, change_out], has_segwit=True)
        script_code = Script(["OP_DUP", "OP_HASH160", pub.to_hash160(), "OP_EQUALVERIFY", "OP_CHECKSIG"])
        sig = pk.sign_segwit_input(tx, 0, script_code, utxo["value"])
        tx.witnesses.append(TxWitnessInput([sig, pub.to_hex()]))
        raw_tx = tx.serialize()
        broadcast_url = "https://mempool.space/api/tx"
        req = urllib.request.Request(broadcast_url, data=raw_tx.encode("utf-8"), headers={"Content-Type": "text/plain"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            txid = resp.read().decode("utf-8").strip()
        entry = {
            "txid": txid, "code_hash": code_hash, "version": version,
            "timestamp": int(time.time()), "address": addr_str,
            "op_return": op_data.decode("utf-8"), "fee_sats": fee_sats, "status": "pending",
        }
        wallet.setdefault("entries", []).append(entry)
        wallet["pending_anchor"] = entry
        _btc_wallet_save(wallet)
        log.info(f"₿ TX broadcast: {txid[:16]}... ({fee_sats} sats Fee)")
        return entry
    except Exception as e:
        log.error(f"⚠️ BTC Blockchain-Eintrag fehlgeschlagen: {e}")
        return None


def _btc_wallet_revoke(code_hash: str):
    """Version widerrufen via OP_RETURN REVOKE."""
    wallet = _btc_wallet_load()
    if not wallet or not wallet.get("wif"):
        return None
    try:
        try:
            from bitcoinutils.setup import setup
            from bitcoinutils.keys import PrivateKey
            from bitcoinutils.transactions import Transaction, TxInput, TxOutput, TxWitnessInput
            from bitcoinutils.script import Script
        except ImportError as ie:
            log.error(f"⚠️ BTC: bitcoin-utils nicht installiert! pip install bitcoin-utils — {ie}")
            return None
        import urllib.request
        setup("mainnet")
        pk = PrivateKey.from_wif(wallet["wif"])
        pub = pk.get_public_key()
        addr = pub.get_segwit_address()
        addr_str = addr.to_string()
        utxo_url = f"https://mempool.space/api/address/{addr_str}/utxo"
        with urllib.request.urlopen(utxo_url, timeout=15) as resp:
            utxos = json.loads(resp.read())
        if not utxos:
            return None
        utxo = max(utxos, key=lambda u: u["value"])
        txin = TxInput(utxo["txid"], utxo["vout"])
        op_data = f"SHINPAI-AI:REVOKE:{code_hash[:32]}".encode("utf-8")
        fee_sats, _ = _btc_estimate_fee_sats()
        change = utxo["value"] - fee_sats
        if change < 0:
            return None
        op_return_out = TxOutput(0, Script(["OP_RETURN", op_data.hex()]))
        change_out = TxOutput(change, addr.to_script_pub_key())
        tx = Transaction([txin], [op_return_out, change_out], has_segwit=True)
        script_code = Script(["OP_DUP", "OP_HASH160", pub.to_hash160(), "OP_EQUALVERIFY", "OP_CHECKSIG"])
        sig = pk.sign_segwit_input(tx, 0, script_code, utxo["value"])
        tx.witnesses.append(TxWitnessInput([sig, pub.to_hex()]))
        raw_tx = tx.serialize()
        req = urllib.request.Request("https://mempool.space/api/tx", data=raw_tx.encode("utf-8"),
                                     headers={"Content-Type": "text/plain"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            txid = resp.read().decode("utf-8").strip()
        revoke_entry = {
            "txid": txid, "code_hash": code_hash, "type": "revoke",
            "timestamp": int(time.time()), "address": addr_str, "status": "pending",
        }
        wallet.setdefault("revocations", []).append(revoke_entry)
        wallet["pending_revoke"] = revoke_entry
        _btc_wallet_save(wallet)
        log.info(f"₿ REVOKE broadcast: {txid[:16]}...")
        return revoke_entry
    except Exception as e:
        log.error(f"⚠️ BTC Revoke fehlgeschlagen: {e}")
        return None


def _btc_scan_external_anchors():
    """Beim Start: Alle anchor-*.json (außer eigene) lesen, Hash in Whitelist eintragen.
    Keine Blockchain-Verifikation — die passiert wenn das andere Programm sich meldet."""
    try:
        for anchor_file in _Path(BASE).glob("anchor-*.json"):
            if anchor_file == ANCHOR_JSON:
                continue
            try:
                ext_anchor = json.loads(anchor_file.read_text("utf-8"))
                ext_hash = (ext_anchor.get("code_hash") or "").lower()
                ext_version = ext_anchor.get("version", "?")
                ext_company = ext_anchor.get("company", anchor_file.stem)
                if not ext_hash:
                    continue
                if ext_anchor.get("revoked"):
                    log.warning(f"⚠️ Externer Anchor {anchor_file.name}: WIDERRUFEN — übersprungen")
                    continue
                existing = nexus_whitelist_get()
                if not any(e.get('hash') == ext_hash for e in existing):
                    nexus_whitelist_add(ext_hash, label=f"{ext_company} v{ext_version} ({anchor_file.name})")
                    log.info(f"🦋 Whitelist: {anchor_file.name} v{ext_version} eingetragen")
            except Exception as e:
                log.warning(f"⚠️ Externer Anchor {anchor_file.name} fehlerhaft: {e}")
    except Exception as e:
        log.warning(f"⚠️ Externe Anchor-Scan fehlgeschlagen: {e}")


def _btc_get_code_hash() -> str:
    """SHA-256 Hash des eigenen Quellcodes berechnen."""
    try:
        with open(__file__, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return ""


# ═════════════════════════════════════════════════════════════════════════
#  BLOCKLISTE — Zeitsperre (7/30/90/365 Tage)
# ═════════════════════════════════════════════════════════════════════════

def _blocklist_load() -> dict:
    """Blockliste laden. Gibt {username: {blocked_at, expires_at, reason}} zurück."""
    if not vault_is_unlocked() or not BLOCKLIST_VAULT.exists():
        return {}
    try:
        raw = _vault_decrypt_bytes(BLOCKLIST_VAULT.read_bytes())
        return json.loads(raw)
    except Exception:
        return {}


def _blocklist_save(data: dict) -> bool:
    """Blockliste speichern."""
    if not vault_is_unlocked():
        return False
    try:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        BLOCKLIST_VAULT.write_bytes(_vault_encrypt_bytes(raw))
        return True
    except Exception:
        return False


def _blocklist_cleanup(bl: dict) -> dict:
    """Abgelaufene Sperren entfernen."""
    now = time.time()
    return {u: v for u, v in bl.items() if v.get("expires_at", 0) > now}


def blocklist_is_blocked(username: str) -> dict | None:
    """Prüft ob User blockiert. Gibt Block-Info zurück oder None."""
    bl = _blocklist_load()
    bl = _blocklist_cleanup(bl)
    entry = bl.get(username.lower())
    if not entry:
        return None
    if entry.get("expires_at", 0) <= time.time():
        return None
    return entry


def blocklist_add(username: str, days: int, reason: str = "") -> bool:
    """User für X Tage blockieren."""
    bl = _blocklist_load()
    bl = _blocklist_cleanup(bl)
    bl[username.lower()] = {
        "username": username,
        "blocked_at": int(time.time()),
        "expires_at": int(time.time()) + days * 86400,
        "days": days,
        "reason": reason.strip()[:200],
    }
    return _blocklist_save(bl)


def blocklist_remove(username: str) -> bool:
    """User sofort entsperren."""
    bl = _blocklist_load()
    if username.lower() in bl:
        del bl[username.lower()]
        return _blocklist_save(bl)
    return False


# --- PASSWORD HASHING ---
def hash_pw(password):
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
    return f"{salt}${h.hex()}"

def verify_pw(password, stored):
    salt, h = stored.split('$')
    check = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
    return hmac.compare_digest(check.hex(), h)

# --- EMAIL VERIFICATION ---
def generate_verify_token():
    """Veralteter Link-Token — 32 Hex (falls irgendwo noch gebraucht)."""
    return secrets.token_hex(16)


def generate_verify_code():
    """6-stelliger Code für Email-Verifikation (DAU-tauglich, überall nutzbar)."""
    return ''.join(secrets.choice('0123456789') for _ in range(6))


CODE_TTL_SECONDS = 600  # 10 Min Gültigkeit


def set_verify_code(user_id, code):
    """Setzt Verify-Code + Expiry in users-Tabelle."""
    conn = get_db('accounts.db')
    conn.execute('UPDATE users SET verify_token = ?, verify_expires = ? WHERE id = ?',
                 (code, time.time() + CODE_TTL_SECONDS, user_id))
    conn.commit()
    conn.close()


def check_verify_code(email, code):
    """Prüft Code gegen Email + Expiry. Returns user_id bei Erfolg, None bei fail."""
    conn = get_db('accounts.db')
    now = time.time()
    user = conn.execute(
        'SELECT id, verify_expires FROM users WHERE LOWER(email) = LOWER(?) AND verify_token = ?',
        (email.strip(), code.strip())
    ).fetchone()
    conn.close()
    if not user:
        return None
    try:
        if user['verify_expires'] and float(user['verify_expires']) < now:
            return None  # abgelaufen
    except (TypeError, ValueError, KeyError):
        pass
    return user['id']

# --- TOTP (2FA) ---
def generate_totp_secret():
    return pyotp.random_base32()

def generate_totp_qr(secret, username):
    """Generiert QR-Code als Base64 PNG für Authenticator-App"""
    uri = f'otpauth://totp/Kneipe:{username}?secret={secret}&issuer=Kneipe'
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f'data:image/png;base64,{b64}', uri

def verify_totp(secret, code):
    """TOTP Code prüfen (±1 Zeitfenster Toleranz)"""
    if not secret or not code:
        return False
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)

# --- THEME ENGINE ---
def load_themes():
    index_path = os.path.join(THEMEN_DIR, '_index.json')
    if not os.path.exists(index_path):
        return []
    with open(index_path, 'r', encoding='utf-8') as f:
        return json.load(f).get('themes', [])

def load_theme(theme_id):
    path = os.path.join(THEMEN_DIR, f'{theme_id}.json')
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

# --- ELEMENT CALCULATION ---
def calculate_element(answers, theme):
    """Berechne Element basierend auf Antworten + Kontext-Check"""
    a_count = sum(1 for a in answers if a == 'A')
    b_count = sum(1 for a in answers if a == 'B')
    c_count = sum(1 for a in answers if a == 'C')
    total = len(answers)
    if total == 0:
        return 'wind'

    # Mauerblümchen-Check: C bei falschen Momenten?
    is_mauerblümchen = False
    for check in theme.get('context_checks', []):
        if check.get('is_mauerblümchen') and 'C' in str(answers):
            is_mauerblümchen = True
            break

    if c_count / total >= 0.6:
        return 'mauerblümchen' if is_mauerblümchen else 'stein'
    if a_count / total >= 0.6:
        return 'feuer'
    if b_count / total >= 0.6:
        return 'wasser'
    return 'wind'

# --- GESAMT-TITEL BERECHNUNG ---
def calculate_gesamt_titel(user_id):
    """Berechne Gesamt-Titel basierend auf allen Spielen"""
    conn = get_db('gameplay.db')
    c = conn.cursor()
    c.execute('SELECT element FROM plays WHERE user_id = ?', (user_id,))
    elements = [r['element'] for r in c.fetchall()]
    conn.close()

    if not elements:
        return None

    total = len(elements)
    counts = {}
    for e in elements:
        counts[e] = counts.get(e, 0) + 1

    feuer_pct = counts.get('feuer', 0) / total
    stein_pct = counts.get('stein', 0) / total
    mauer_pct = counts.get('mauerblümchen', 0) / total

    if feuer_pct >= 0.75:
        return 'kritiker'
    if stein_pct >= 0.75:
        return 'mystiker'
    if mauer_pct >= 0.75:
        # Mauerblümchen NUR wenn kein anderer Titel qualifiziert
        if feuer_pct < 0.3 and stein_pct < 0.3:
            return 'mauerblümchen'
    return 'denker'  # Default: keine klare Mehrheit

# --- KUMULATIVE TITEL CHECK ---
def check_kumulative_titel(user_id):
    """Prüfe und vergebe kumulative Titel"""
    conn = get_db('gameplay.db')
    c = conn.cursor()

    # Alle Spiele laden
    c.execute('SELECT element, flags_triggered, is_stammgast, client_hour, answers, theme_id FROM plays WHERE user_id = ? ORDER BY played_at', (user_id,))
    plays = c.fetchall()

    # Bestehende Titel
    c.execute('SELECT title_id FROM titles WHERE user_id = ?', (user_id,))
    existing = {r['title_id'] for r in c.fetchall()}

    new_titles = []
    elements = [p['element'] for p in plays]

    # Element-basierte Titel (5x)
    element_counts = {}
    for e in elements:
        element_counts[e] = element_counts.get(e, 0) + 1

    if element_counts.get('feuer', 0) >= 5 and 'brandstifter' not in existing:
        new_titles.append('brandstifter')
    if element_counts.get('wasser', 0) >= 5 and 'flussbett' not in existing:
        new_titles.append('flussbett')
    if element_counts.get('stein', 0) >= 5 and 'stammtischgast' not in existing:
        new_titles.append('stammtischgast')
    if element_counts.get('wind', 0) >= 5 and 'chaot' not in existing:
        new_titles.append('chaot')
    if element_counts.get('mauerblümchen', 0) >= 3 and 'mauerblümchen_titel' not in existing:
        new_titles.append('mauerblümchen_titel')

    # Maulwurf: 3 am Stück nur C
    all_c_streak = 0
    for p in plays:
        answers = json.loads(p['answers']) if isinstance(p['answers'], str) else p['answers']
        if all(a == 'C' for a in answers):
            all_c_streak += 1
            if all_c_streak >= 3 and 'maulwurf' not in existing:
                new_titles.append('maulwurf')
                break
        else:
            all_c_streak = 0

    # Nachtmensch: 75% nach Mitternacht
    if len(plays) >= 3:
        night_plays = sum(1 for p in plays if p['client_hour'] >= 0 and p['client_hour'] < 6)
        if night_plays / len(plays) >= 0.75 and 'nachtmensch' not in existing:
            new_titles.append('nachtmensch')

    # Ja-Sager: 5x Ja-Sager Flag
    ja_sager_count = 0
    for p in plays:
        flags = json.loads(p['flags_triggered']) if isinstance(p['flags_triggered'], str) else p['flags_triggered']
        if 'ja-sager' in flags:
            ja_sager_count += 1
    if ja_sager_count >= 5 and 'ja_sager' not in existing:
        new_titles.append('ja_sager')

    # Jukebox-Held: 5x Jukebox Flag
    jukebox_count = 0
    for p in plays:
        flags = json.loads(p['flags_triggered']) if isinstance(p['flags_triggered'], str) else p['flags_triggered']
        if 'jukebox' in flags:
            jukebox_count += 1
    if jukebox_count >= 5 and 'jukebox_held' not in existing:
        new_titles.append('jukebox_held')

    # Stammgast Zähler (nicht als Titel, als Counter)
    stammgast_count = sum(1 for p in plays if p['is_stammgast'])

    # V4.1 Cheater-Check 1: 3x Stammgast hintereinander = Farming!
    is_cheater = False
    if len(plays) >= 3:
        if all(p['is_stammgast'] for p in plays[-3:]):
            is_cheater = True
            log.info(f'💀 STAMMGAST-FARMING — {user_id} hat 3x hintereinander Stammgast-Thema gespielt!')

    # Cheater-Check 2: ein Thema 50%+ mehr + gleiche Antworten
    if not is_cheater and len(plays) >= 10:
        theme_counts = {}
        theme_answers = {}
        for p in plays:
            tid = p['theme_id']
            theme_counts[tid] = theme_counts.get(tid, 0) + 1
            ans = p['answers']
            if tid not in theme_answers:
                theme_answers[tid] = set()
            theme_answers[tid].add(ans)

        avg = len(plays) / max(len(theme_counts), 1)
        for tid, count in theme_counts.items():
            if count > avg * 1.5 and len(theme_answers.get(tid, set())) <= 2:
                is_cheater = True
                break

    # V4.8 Wind-Ausnahme: Wind = Mix/entspannter Spieler → KEIN Cheater!
    if is_cheater:
        elem_counts = {}
        for p in plays:
            e = p['element']
            elem_counts[e] = elem_counts.get(e, 0) + 1
        dominant = max(elem_counts, key=elem_counts.get) if elem_counts else ''
        if dominant == 'wind':
            is_cheater = False
            log.info(f'💨 WIND-AUSNAHME — {user_id}: Dominantes Element ist Wind, kein Cheater!')

    if is_cheater and 'cheater' not in existing:
        new_titles.append('cheater')
        # V4.1 Strike-System: 1→30T, 2→365T, 3→permanent
        conn_acc = get_db('accounts.db')
        user_row = conn_acc.execute('SELECT cheater_strikes FROM users WHERE id = ?', (user_id,)).fetchone()
        strikes = (user_row['cheater_strikes'] if user_row and user_row['cheater_strikes'] else 0) + 1
        ban_durations = {1: 30 * 86400, 2: 365 * 86400}
        ban_duration = ban_durations.get(min(strikes, 3), 0)
        banned_until = (time.time() + ban_duration) if strikes < 3 else 9999999999
        conn_acc.execute('UPDATE users SET cheater_strikes = ?, stammgast_banned_until = ? WHERE id = ?',
                         (strikes, banned_until, user_id))
        conn_acc.commit()
        conn_acc.close()
        ban_label = {1: '30 Tage', 2: '365 Tage', 3: 'PERMANENT'}
        log.info(f'💀 CHEATER STRIKE {strikes}/3 — {user_id} | Stammgast-Sperre: {ban_label.get(min(strikes, 3))}')

    # Stammgast-Sperre prüfen
    conn_acc2 = get_db('accounts.db')
    ban_row = conn_acc2.execute('SELECT stammgast_banned_until FROM users WHERE id = ?', (user_id,)).fetchone()
    conn_acc2.close()
    stammgast_banned = ban_row and ban_row['stammgast_banned_until'] and ban_row['stammgast_banned_until'] > time.time()

    # Titel speichern
    now = time.time()
    for title_id in new_titles:
        c.execute('INSERT OR IGNORE INTO titles (user_id, title_id, earned_at) VALUES (?, ?, ?)',
                  (user_id, title_id, now))

    conn.commit()
    conn.close()

    return {
        'new_titles': new_titles,
        'stammgast_count': stammgast_count if not stammgast_banned else 0,
        'is_cheater': is_cheater,
        'stammgast_banned': bool(stammgast_banned),
    }

# --- V4.1 CHEATER-TRIBUNAL ---

def handle_tribunal_submit(user_id, data):
    """Cheater stellt Antrag auf Rehabilitation."""
    reflection = (data.get('reflection') or '').strip()
    if not reflection or len(reflection) < 50:
        return {'error': 'Mindestens 50 Zeichen ehrliche Reflexion!'}
    if len(reflection) > 2000:
        return {'error': 'Max 2000 Zeichen!'}
    conn_acc = get_db('accounts.db')
    user = conn_acc.execute('SELECT name, cheater_strikes, stammgast_banned_until FROM users WHERE id = ?', (user_id,)).fetchone()
    conn_acc.close()
    if not user or not user['stammgast_banned_until'] or user['stammgast_banned_until'] <= time.time():
        return {'error': 'Du bist nicht gesperrt!'}
    conn_gp = get_db('gameplay.db')
    existing = conn_gp.execute('SELECT id FROM tribunals WHERE user_id = ? AND status = ?', (user_id, 'open')).fetchone()
    if existing:
        conn_gp.close()
        return {'error': 'Du hast bereits ein offenes Tribunal!'}
    tribunal_id = str(uuid.uuid4())[:8]
    conn_gp.execute('INSERT INTO tribunals (id, user_id, user_name, reflection, status, created_at) VALUES (?,?,?,?,?,?)',
        (tribunal_id, user_id, user['name'], reflection, 'open', time.time()))
    conn_gp.commit()
    conn_gp.close()
    log.info(f'⚖️ TRIBUNAL ERÖFFNET — {user["name"]} (Strike {user["cheater_strikes"]}/3)')
    return {'ok': True, 'tribunal_id': tribunal_id}


def handle_tribunal_vote(user_id, data):
    """Community voted über Rehabilitation. 90% bei min 3 Stimmen!"""
    tribunal_id = data.get('tribunal_id')
    vote = data.get('vote')
    if vote not in ('yes', 'no'):
        return {'error': 'Vote muss yes oder no sein!'}
    conn_gp = get_db('gameplay.db')
    tribunal = conn_gp.execute('SELECT * FROM tribunals WHERE id = ? AND status = ?', (tribunal_id, 'open')).fetchone()
    if not tribunal:
        conn_gp.close()
        return {'error': 'Tribunal nicht gefunden oder geschlossen!'}
    conn_acc = get_db('accounts.db')
    voter = conn_acc.execute('SELECT is_owner FROM users WHERE id = ?', (user_id,)).fetchone()
    conn_acc.close()
    if not voter:
        conn_gp.close()
        return {'error': 'Gäste können nicht abstimmen!'}
    if voter['is_owner']:
        conn_gp.close()
        return {'error': 'Owner kann nicht im Tribunal abstimmen!'}
    if user_id == tribunal['user_id']:
        conn_gp.close()
        return {'error': 'Du kannst nicht über dein eigenes Tribunal abstimmen!'}
    voters = json.loads(tribunal['voters'] or '[]')
    if user_id in voters:
        conn_gp.close()
        return {'error': 'Du hast bereits abgestimmt!'}
    voters.append(user_id)
    yes = tribunal['votes_yes'] + (1 if vote == 'yes' else 0)
    no = tribunal['votes_no'] + (1 if vote == 'no' else 0)
    conn_gp.execute('UPDATE tribunals SET votes_yes = ?, votes_no = ?, voters = ? WHERE id = ?',
                     (yes, no, json.dumps(voters), tribunal_id))
    conn_gp.commit()
    conn_gp.close()
    total = yes + no
    yes_pct = (yes / total * 100) if total > 0 else 0
    log.info(f'⚖️ TRIBUNAL VOTE — {tribunal_id}: {yes}✓ {no}✕ ({yes_pct:.0f}%)')
    return {'ok': True, 'yes': yes, 'no': no, 'total': total, 'pct': round(yes_pct, 1)}


def _resolve_tribunal(tribunal_id):
    """Tribunal auflösen — 90% bei min 3 Stimmen = Rehabilitation."""
    conn_gp = get_db('gameplay.db')
    tribunal = conn_gp.execute('SELECT * FROM tribunals WHERE id = ? AND status = ?', (tribunal_id, 'open')).fetchone()
    if not tribunal:
        conn_gp.close()
        return
    total = tribunal['votes_yes'] + tribunal['votes_no']
    if total < 3:
        conn_gp.close()
        return
    yes_pct = tribunal['votes_yes'] / total * 100
    now = time.time()
    if yes_pct >= 90:
        conn_acc = get_db('accounts.db')
        conn_acc.execute('UPDATE users SET stammgast_banned_until = 0 WHERE id = ?', (tribunal['user_id'],))
        conn_acc.commit()
        conn_acc.close()
        conn_gp.execute('UPDATE tribunals SET status = ?, resolved_at = ? WHERE id = ?', ('approved', now, tribunal_id))
        log.info(f'⚖️ TRIBUNAL GENEHMIGT — {tribunal["user_name"]} rehabilitiert! ({yes_pct:.0f}%)')
    else:
        conn_gp.execute('UPDATE tribunals SET status = ?, resolved_at = ? WHERE id = ?', ('rejected', now, tribunal_id))
        log.info(f'⚖️ TRIBUNAL ABGELEHNT — {tribunal["user_name"]} bleibt gesperrt ({yes_pct:.0f}% < 90%)')
    conn_gp.commit()
    conn_gp.close()


# --- V4.8 CHEATER-UNFLAG-VOTING ---
# Regeln:
# - Nur verifizierte Accounts (kein Gast, kein Bot)
# - 1 Vote pro Account pro 24h, jeder Klick verlängert auf 30 Tage
# - 3 Tage ohne neues Vote → alle Votes ungültig
# - 7 Tage ohne mindestens 4 Votes → alles löschen
# - 30-Tage-Regel: Muss min 1x/Tag erneuert (irgendwer muss klicken)
# - >50% der Eligible = Unflag!
# - Owner kann direkt unflaggen (separater Endpoint)

CHEATER_VOTE_COOLDOWN = 86400    # 1x pro Account pro 24h
CHEATER_VOTE_LIFETIME = 2592000  # 30 Tage Gültigkeit pro Vote
CHEATER_VOTE_STALE = 259200      # 3 Tage ohne neues Vote → ungültig
CHEATER_VOTE_DEAD = 604800       # 7 Tage ohne 4 Votes → löschen


def _get_cheater_vote_session(target_name):
    """Aktuelle Vote-Session holen oder None."""
    conn = get_db('gameplay.db')
    sess = conn.execute('SELECT * FROM cheater_vote_sessions WHERE target_name = ?', (target_name,)).fetchone()
    conn.close()
    return dict(sess) if sess else None


def _count_cheater_votes(target_name):
    """Anzahl gültiger Unflag-Votes (nicht abgelaufen)."""
    now = time.time()
    conn = get_db('gameplay.db')
    count = conn.execute('SELECT COUNT(*) as cnt FROM cheater_votes WHERE target_name = ? AND voted_at > ?',
                          (target_name, now - CHEATER_VOTE_LIFETIME)).fetchone()['cnt']
    conn.close()
    return count


def _count_eligible_voters():
    """Anzahl stimmberechtigter User (verifiziert, kein Bot, kein Gast)."""
    conn = get_db('accounts.db')
    count = conn.execute('SELECT COUNT(*) as cnt FROM users WHERE verified = 1 AND is_bot = 0 AND is_guest = 0').fetchone()['cnt']
    conn.close()
    return count


def _do_unflag_cheater(target_name):
    """Cheater-Flag entfernen: Title + Strikes + Bans resetten."""
    conn_acc = get_db('accounts.db')
    user = conn_acc.execute('SELECT id FROM users WHERE name = ?', (target_name,)).fetchone()
    if user:
        conn_gp = get_db('gameplay.db')
        conn_gp.execute('DELETE FROM titles WHERE title_id = ? AND user_id = ?', ('cheater', user['id']))
        conn_gp.execute('DELETE FROM cheater_votes WHERE target_name = ?', (target_name,))
        conn_gp.execute('DELETE FROM cheater_vote_sessions WHERE target_name = ?', (target_name,))
        conn_gp.commit()
        conn_gp.close()
        conn_acc.execute('UPDATE users SET cheater_strikes = 0, stammgast_banned_until = 0 WHERE id = ?', (user['id'],))
        conn_acc.commit()
    conn_acc.close()
    log.info(f'✅ CHEATER UNFLAGGED — {target_name} (Titel+Strikes+Ban entfernt)')


def handle_cheater_vote(voter_id, target_name):
    """User stimmt für Unflag eines Cheaters. 1x pro 24h, verlängert 30 Tage."""
    now = time.time()
    conn_acc = get_db('accounts.db')
    voter = conn_acc.execute('SELECT name, is_bot, is_guest, verified FROM users WHERE id = ?', (voter_id,)).fetchone()
    if not voter or not voter['verified'] or voter['is_bot'] or voter['is_guest']:
        conn_acc.close()
        return {'error': 'Nur verifizierte Accounts dürfen abstimmen!'}
    target_user = conn_acc.execute('SELECT id FROM users WHERE name = ?', (target_name,)).fetchone()
    conn_acc.close()
    if not target_user:
        return {'error': 'User nicht gefunden!'}

    conn_gp = get_db('gameplay.db')
    is_cheater = conn_gp.execute('SELECT 1 FROM titles WHERE user_id = ? AND title_id = ?',
                                  (target_user['id'], 'cheater')).fetchone()
    if not is_cheater:
        conn_gp.close()
        return {'error': 'Kein Cheater!'}

    # 24h-Cooldown Check: Letztes Vote dieses Users für diesen Cheater
    last_vote = conn_gp.execute('SELECT voted_at FROM cheater_votes WHERE target_name = ? AND voter_id = ?',
                                 (target_name, voter_id)).fetchone()
    if last_vote and (now - last_vote['voted_at']) < CHEATER_VOTE_COOLDOWN:
        remaining_h = int((CHEATER_VOTE_COOLDOWN - (now - last_vote['voted_at'])) / 3600)
        conn_gp.close()
        return {'error': f'1x pro 24h! Nächstes Vote in ~{remaining_h}h.'}

    # Vote eintragen oder erneuern (UPSERT: gleicher Voter → Timestamp erneuern)
    if last_vote:
        conn_gp.execute('UPDATE cheater_votes SET voted_at = ? WHERE target_name = ? AND voter_id = ?',
                         (now, target_name, voter_id))
    else:
        conn_gp.execute('INSERT INTO cheater_votes (target_name, voter_id, voted_at) VALUES (?,?,?)',
                         (target_name, voter_id, now))

    # Session erstellen/updaten (last_activity tracken)
    sess = conn_gp.execute('SELECT * FROM cheater_vote_sessions WHERE target_name = ?', (target_name,)).fetchone()
    if not sess:
        conn_gp.execute('INSERT INTO cheater_vote_sessions (target_name, status, escalation_level, trigger_count, timer_ends_at, created_at) VALUES (?,?,?,?,?,?)',
                         (target_name, 'active', 0, 0, now, now))
    else:
        conn_gp.execute('UPDATE cheater_vote_sessions SET timer_ends_at = ?, status = ? WHERE target_name = ?',
                         (now, 'active', target_name))
    conn_gp.commit()
    conn_gp.close()
    log.info(f'🗳️ CHEATER-VOTE — {voter["name"]} voted Unflag für {target_name}')

    # Sofort-Check: >50%?
    return _evaluate_cheater_vote(target_name)


def _evaluate_cheater_vote(target_name):
    """Prüfe ob >50% erreicht. Wenn ja → Unflag!"""
    votes = _count_cheater_votes(target_name)
    eligible = _count_eligible_voters()
    eligible = max(eligible - 1, 1)  # Cheater selbst abziehen
    pct = (votes / eligible * 100) if eligible > 0 else 0

    if pct > 50:
        _do_unflag_cheater(target_name)
        log.info(f'✅ CHEATER-VOTE GEWONNEN — {target_name} unflagged! ({pct:.0f}%, {votes}/{eligible})')
        return {'ok': True, 'unflagged': True, 'votes': votes, 'eligible': eligible, 'pct': round(pct, 1)}

    sess = _get_cheater_vote_session(target_name)
    return {
        'ok': True,
        'unflagged': False,
        'votes': votes,
        'eligible': eligible,
        'pct': round(pct, 1),
        'status': sess['status'] if sess else 'none',
    }


def _cheater_vote_timer_thread():
    """Background-Thread: Prüft Cheater-Vote-Verfall (alle 60sec).
    - 3 Tage ohne neues Vote → alle Votes ungültig machen
    - 7 Tage ohne 4 Votes → Session + Votes komplett löschen
    - Abgelaufene Votes (>30 Tage) aufräumen
    """
    while True:
        try:
            time.sleep(60)
            now = time.time()
            conn_gp = get_db('gameplay.db')
            sessions = conn_gp.execute('SELECT * FROM cheater_vote_sessions').fetchall()
            conn_gp.close()

            for sess in sessions:
                target = sess['target_name']
                last_activity = sess['timer_ends_at']  # Letztes Vote-Event
                created = sess['created_at']
                age = now - created

                # Abgelaufene Einzelvotes aufräumen (>30 Tage alt)
                conn_gp2 = get_db('gameplay.db')
                conn_gp2.execute('DELETE FROM cheater_votes WHERE target_name = ? AND voted_at < ?',
                                  (target, now - CHEATER_VOTE_LIFETIME))
                conn_gp2.commit()
                conn_gp2.close()

                # 3-Tage-Regel: Kein neues Vote seit 3 Tagen → Stale
                if (now - last_activity) > CHEATER_VOTE_STALE:
                    conn_gp3 = get_db('gameplay.db')
                    conn_gp3.execute('DELETE FROM cheater_votes WHERE target_name = ?', (target,))
                    conn_gp3.execute('UPDATE cheater_vote_sessions SET status = ? WHERE target_name = ?',
                                      ('stale', target))
                    conn_gp3.commit()
                    conn_gp3.close()
                    log.info(f'💤 CHEATER-VOTE STALE — {target}: 3 Tage ohne Vote, alle Stimmen gelöscht')
                    continue

                # 7-Tage-Regel: Nicht genug Votes → Komplett löschen
                if age > CHEATER_VOTE_DEAD:
                    valid_votes = _count_cheater_votes(target)
                    if valid_votes < 4:
                        conn_gp4 = get_db('gameplay.db')
                        conn_gp4.execute('DELETE FROM cheater_votes WHERE target_name = ?', (target,))
                        conn_gp4.execute('DELETE FROM cheater_vote_sessions WHERE target_name = ?', (target,))
                        conn_gp4.commit()
                        conn_gp4.close()
                        log.info(f'🗑️ CHEATER-VOTE DEAD — {target}: 7 Tage, <4 Votes, Session gelöscht')

        except Exception as e:
            log.error(f'Cheater-Vote-Timer Fehler: {e}')


# --- BIERDECKEL SYSTEM ---

def get_prost_type(user_id):
    """Algo-gesteuerter Prost-Typ basierend auf User-Profil"""
    conn_gp = get_db('gameplay.db')

    # Element-Verteilung laden
    elements_raw = conn_gp.execute(
        'SELECT element, COUNT(*) as cnt FROM plays WHERE user_id = ? GROUP BY element', (user_id,)
    ).fetchall()
    total_plays = sum(e['cnt'] for e in elements_raw)
    elements = {e['element']: e['cnt'] for e in elements_raw}

    # Titel laden
    titles = {r['title_id'] for r in conn_gp.execute(
        'SELECT title_id FROM titles WHERE user_id = ?', (user_id,)
    ).fetchall()}

    # Jukebox-Count (aus Flags)
    plays = conn_gp.execute(
        'SELECT flags_triggered FROM plays WHERE user_id = ?', (user_id,)
    ).fetchall()
    jukebox_count = 0
    for p in plays:
        flags = json.loads(p['flags_triggered']) if isinstance(p['flags_triggered'], str) else p['flags_triggered']
        if 'jukebox' in flags:
            jukebox_count += 1

    # Mauerblümchen-Check (PERMANENT! Einmal Mauerblümchen = nie wieder Rülpsen!)
    has_mauerblümchen = 'mauerblümchen_titel' in titles
    conn_gp.close()

    if total_plays == 0:
        return 'normal'

    feuer_pct = elements.get('feuer', 0) / total_plays
    stein_pct = elements.get('stein', 0) / total_plays

    # RÜLPS — Lautester Prost! 75% Feuer + 3x Jukebox + 25% Stein + KEIN Mauerblümchen!
    if feuer_pct >= 0.75 and jukebox_count >= 3 and stein_pct >= 0.25 and not has_mauerblümchen:
        return 'rülps'

    # Aufstoßen — Mittelstufe: 50% Feuer + 2x Jukebox + KEIN Mauerblümchen
    if feuer_pct >= 0.50 and jukebox_count >= 2 and not has_mauerblümchen:
        return 'aufstoßen'

    # Verlegen — Kleine Stufe: 30% Feuer + 1x Jukebox + KEIN Mauerblümchen
    if feuer_pct >= 0.30 and jukebox_count >= 1 and not has_mauerblümchen:
        return 'verlegen'

    return 'normal'

# --- EDGE TTS (Voice-Bierdeckel) ---
VOICE_EXPIRE_SECONDS = 3600  # 1h

# Owner Voice-Server (konfigurierbar über Owner-Profil!)
VOICE_SERVER_URL = 'http://localhost:17788'  # Default, wird von _get_voice_url() überschrieben
_voice_config = {
    'voice_enabled': True,
    'voice_url': 'http://localhost:17788',
    'voice_mode': 'orpheus',  # orpheus / edge / off
    'default_voice': 'punk_girl_23',
    'mobile_allowed': False,
    'mobile_max': 5,
    'frp_admin_url': 'http://127.0.0.1:7500',
    'frp_admin_user': 'admin',
    'frp_admin_pass': '',
}
_VOICE_CONFIG_FILE = os.path.join(BASE, 'db', 'voice_config.json')


def _load_voice_config():
    """Voice-Config aus Datei laden."""
    global _voice_config, VOICE_SERVER_URL
    if os.path.exists(_VOICE_CONFIG_FILE):
        try:
            with open(_VOICE_CONFIG_FILE) as f:
                saved = json.load(f)
            _voice_config.update(saved)
            VOICE_SERVER_URL = _voice_config.get('voice_url', VOICE_SERVER_URL)
        except Exception:
            pass


def _save_voice_config():
    """Voice-Config in Datei speichern."""
    os.makedirs(os.path.dirname(_VOICE_CONFIG_FILE), exist_ok=True)
    with open(_VOICE_CONFIG_FILE, 'w') as f:
        json.dump(_voice_config, f, indent=2, ensure_ascii=False)


def _get_voice_url():
    """Aktuelle Voice-Server URL aus Config."""
    return _voice_config.get('voice_url', VOICE_SERVER_URL)


# Voice-Config beim Start laden
_load_voice_config()


def _refresh_frp_admin(pw_hash):
    """FRP Admin-Pass aus Owner-PW-Hash ableiten und frps Config updaten."""
    # FRP Admin-Pass = HMAC vom PW-Hash mit festem Salt
    frp_pass = hmac.new(b'kneipe-frp-admin', pw_hash.encode(), hashlib.sha256).hexdigest()[:24]
    config, frps_bin = _find_frps_config()
    if not config:
        # Nur voice_config updaten, frps nicht anfassen
        _voice_config['frp_admin_pass'] = frp_pass
        _save_voice_config()
        log.info(f'📡 FRP Admin-Pass lokal gespeichert (frps nicht gefunden)')
        return
    try:
        # Config lesen und webServer.password ersetzen
        import re as _re
        with open(config) as f:
            content = f.read()
        # Prüfen ob sich was geändert hat
        old_pass_match = _re.search(r'webServer\.password\s*=\s*"([^"]*)"', content)
        if old_pass_match and old_pass_match.group(1) == frp_pass:
            # Gleicher Pass, nichts zu tun — nur voice_config sync
            _voice_config['frp_admin_pass'] = frp_pass
            _save_voice_config()
            log.info(f'📡 FRP Admin-Pass unverändert (korrekt abgeleitet)')
            return
        new_content = _re.sub(r'webServer\.password\s*=\s*"[^"]*"', f'webServer.password = "{frp_pass}"', content)
        with open(config, 'w') as f:
            f.write(new_content)
        # voice_config updaten
        _voice_config['frp_admin_pass'] = frp_pass
        _save_voice_config()
        # frps neustarten nur wenn sich was geändert hat
        _restart_frps(config, frps_bin)
    except Exception as e:
        log.error(f'📡 FRP Refresh fehlgeschlagen: {e}')


import urllib.request
import subprocess


def _find_frps_config():
    """frps Config-Pfad aus laufendem Prozess finden."""
    try:
        result = subprocess.run(['pgrep', '-af', 'frps '], capture_output=True, text=True, timeout=5)
        if not result.stdout.strip():
            return None, None
        # Richtige Zeile finden (nicht bash, nicht grep)
        for line in result.stdout.strip().split('\n'):
            parts = line.split()
            if len(parts) < 2:
                continue
            # Nur Zeilen wo frps das Binary ist (nicht bash -c ...)
            has_frps_bin = any(p.endswith('/frps') or p == 'frps' for p in parts[1:3])
            if not has_frps_bin:
                continue
            pid = parts[0]
            config = None
            for i, p in enumerate(parts):
                if p == '-c' and i + 1 < len(parts):
                    config = parts[i + 1]
                    break
            if not config:
                cwd = os.readlink(f'/proc/{pid}/cwd')
                config = os.path.join(cwd, 'frps.toml')
            frps_bin = None
            for p in parts[1:]:
                if p.endswith('/frps') or p == 'frps':
                    frps_bin = p
                    break
            if config and os.path.exists(config):
                return config, frps_bin
        return None, None
    except Exception:
        return None, None


def _restart_frps(config, frps_bin):
    """frps sauber stoppen und neu starten. Gibt True zurück wenn erfolgreich."""
    try:
        subprocess.run(['pkill', '-f', 'frps '], timeout=5)
        time.sleep(2)
        if not frps_bin or not os.path.exists(frps_bin):
            log.warning(f'📡 frps Binary nicht gefunden: {frps_bin}')
            return False
        subprocess.Popen([frps_bin, '-c', config],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
        # Prüfen ob frps wirklich läuft
        check = subprocess.run(['pgrep', '-f', 'frps '], capture_output=True, text=True, timeout=5)
        if check.stdout.strip():
            log.info(f'📡 frps neugestartet (PID {check.stdout.strip().split()[0]})')
            return True
        log.error(f'📡 frps Neustart fehlgeschlagen — Prozess nicht gefunden!')
        return False
    except Exception as e:
        log.error(f'📡 frps Neustart Fehler: {e}')
        return False


def _update_frps_token(new_token):
    """FRP auth.token in frps.toml updaten und frps neustarten."""
    config, frps_bin = _find_frps_config()
    if not config:
        log.warning('📡 frps Config nicht gefunden — Token nur lokal gespeichert')
        return False
    try:
        # Config lesen
        with open(config) as f:
            content = f.read()
        # auth.token ersetzen
        import re as _re
        new_content = _re.sub(r'auth\.token\s*=\s*"[^"]*"', f'auth.token = "{new_token}"', content)
        if new_content == content:
            log.error('📡 auth.token nicht in frps.toml gefunden!')
            return False
        with open(config, 'w') as f:
            f.write(new_content)
        # Verifizieren dass es geschrieben wurde
        with open(config) as f:
            verify = f.read()
        if new_token not in verify:
            log.error('📡 Token wurde nicht korrekt in frps.toml geschrieben!')
            return False
        # frps neustarten
        ok = _restart_frps(config, frps_bin)
        if ok:
            log.info(f'📡 FRP Token aktualisiert — alle Tunnel gekappt, frps neu')
        return ok
    except Exception as e:
        log.error(f'📡 FRP Token-Update fehlgeschlagen: {e}')
        return False


def _kick_frp_proxy(proxy_name):
    """Einzelnen FRP-Proxy kicken über Admin-API."""
    frp_url = _voice_config.get('frp_admin_url', 'http://127.0.0.1:7500')
    frp_user = _voice_config.get('frp_admin_user', 'admin')
    frp_pass = _voice_config.get('frp_admin_pass', '')
    try:
        req = urllib.request.Request(
            f'{frp_url}/api/proxy/tcp/{proxy_name}',
            method='DELETE'
        )
        credentials = base64.b64encode(f'{frp_user}:{frp_pass}'.encode()).decode()
        req.add_header('Authorization', f'Basic {credentials}')
        with urllib.request.urlopen(req, timeout=3) as resp:
            pass
        log.info(f'📡 FRP Proxy gekickt: {proxy_name}')
        return True
    except Exception as e:
        log.error(f'📡 FRP Kick fehlgeschlagen für {proxy_name}: {e}')
        return False


# Separate Locks — Whisper + Bark parallel zum Voice-Server! Intern jeweils sequenziell.
_whisper_queue_lock = threading.Lock()
_bark_queue_lock = threading.Lock()

def _voice_server_available():
    """Check ob Owner Voice-Server erreichbar"""
    try:
        req = urllib.request.Request(f'{_get_voice_url()}/api/health', method='GET')
        resp = urllib.request.urlopen(req, timeout=2)
        return json.loads(resp.read()).get('ok', False)
    except:
        return False

def _whisper_transcribe(audio_path, tisch_id=None):
    """Whisper STT über Owner Voice-Server — eigener Lock, parallel zu Bark!"""
    with _whisper_queue_lock:
        try:
            with open(audio_path, 'rb') as f:
                audio_bytes = f.read()
            audio_b64 = base64.b64encode(audio_bytes).decode()
            payload = {'audio': audio_b64}
            # Chat-Kontext mitgeben: letzte 5 Nachrichten als Hint für Whisper
            if tisch_id:
                try:
                    conn = get_db('gameplay.db')
                    msgs = conn.execute(
                        'SELECT text FROM chat_messages WHERE tisch_id = ? ORDER BY time DESC LIMIT 5',
                        (tisch_id,)
                    ).fetchall()
                    conn.close()
                    if msgs:
                        context = '. '.join(m['text'] for m in reversed(msgs) if m['text'])
                        payload['context'] = context[:500]  # Max 500 Zeichen
                except Exception:
                    pass
            data = json.dumps(payload).encode()
            req = urllib.request.Request(f'{_get_voice_url()}/api/whisper', data=data,
                                         headers={'Content-Type': 'application/json'}, method='POST')
            resp = urllib.request.urlopen(req, timeout=30)
            result = json.loads(resp.read())
            if result.get('text'):
                log.info(f'🎤 Whisper: "{result["text"][:80]}..."')
                return result['text'].strip()
        except Exception as e:
            log.error(f'❌ Whisper Fehler: {e}')
    return None


def _bark_generate(text, voice='mann1'):
    """Bark TTS über Owner Voice-Server — eigener Lock, parallel zu Whisper!"""
    with _bark_queue_lock:
        try:
            data = json.dumps({'text': text, 'voice': voice}).encode()
            req = urllib.request.Request(f'{_get_voice_url()}/api/bark', data=data,
                                         headers={'Content-Type': 'application/json'}, method='POST')
            resp = urllib.request.urlopen(req, timeout=120)
            result = json.loads(resp.read())
            if result.get('audio'):
                return result['audio']  # base64 WAV
        except Exception as e:
            log.error(f'❌ Bark Fehler: {e}')
    return None

BARK_VOICES_LIST = [
    {'id': 'barkeeper_ralf', 'name': 'Barkeeper Ralf 32 by Shinpai-AI (Orpheus)', 'gender': 'M', 'locale': 'DE', 'engine': 'orpheus'},
    {'id': 'punk_girl_23', 'name': 'Punk Girl 23 by Shinpai-AI (Orpheus)', 'gender': 'F', 'locale': 'DE', 'engine': 'orpheus'},
    {'id': 'mann2', 'name': 'Barkeeper Ralf (Legacy)', 'gender': 'M', 'locale': 'DE', 'engine': 'orpheus'},
]
EDGE_VOICES = [
    {'id': 'de-DE-ConradNeural', 'name': 'Conrad', 'gender': 'M', 'locale': 'DE'},
    {'id': 'de-DE-KillianNeural', 'name': 'Killian', 'gender': 'M', 'locale': 'DE'},
    {'id': 'de-DE-FlorianMultilingualNeural', 'name': 'Florian', 'gender': 'M', 'locale': 'DE'},
    {'id': 'de-DE-KatjaNeural', 'name': 'Katja', 'gender': 'F', 'locale': 'DE'},
    {'id': 'de-DE-AmalaNeural', 'name': 'Amala', 'gender': 'F', 'locale': 'DE'},
    {'id': 'de-DE-SeraphinaMultilingualNeural', 'name': 'Seraphina', 'gender': 'F', 'locale': 'DE'},
    {'id': 'de-AT-JonasNeural', 'name': 'Jonas (AT)', 'gender': 'M', 'locale': 'AT'},
    {'id': 'de-AT-IngridNeural', 'name': 'Ingrid (AT)', 'gender': 'F', 'locale': 'AT'},
    {'id': 'de-CH-JanNeural', 'name': 'Jan (CH)', 'gender': 'M', 'locale': 'CH'},
    {'id': 'de-CH-LeniNeural', 'name': 'Leni (CH)', 'gender': 'F', 'locale': 'CH'},
]

def generate_voice(bd_id, text):
    """Edge TTS: Text → MP3 → WAV (korrekte Duration!)"""
    try:
        tmp_mp3 = os.path.join(VOICE_DIR, f'{bd_id}.mp3.tmp')
        wav_path = os.path.join(VOICE_DIR, f'{bd_id}.wav')
        communicate = edge_tts.Communicate(text, 'de-DE-ConradNeural')
        loop = asyncio.new_event_loop()
        loop.run_until_complete(communicate.save(tmp_mp3))
        loop.close()
        # MP3 → WAV (ffmpeg braucht .wav Extension!)
        import subprocess
        subprocess.run(['ffmpeg', '-y', '-i', tmp_mp3, wav_path], capture_output=True)
        if os.path.exists(wav_path) and os.path.getsize(wav_path) > 100:
            try: os.remove(tmp_mp3)
            except: pass
            log.info(f'🔊 VOICE WAV — {bd_id}: "{text[:30]}..."')
            return wav_path
        else:
            log.error(f'❌ ffmpeg WAV fehlgeschlagen — {bd_id}')
            return tmp_mp3  # Fallback: MP3
    except Exception as e:
        log.error(f'❌ VOICE FEHLER — {bd_id}: {e}')
        return None

def _voice_cleanup_thread():
    """Hintergrund-Thread: abgelaufene Voice-Files löschen"""
    while True:
        try:
            now = time.time()
            conn = get_db('gameplay.db')
            # Voice-Files die abgelaufen sind + NICHT archiviert
            expired = conn.execute(
                "SELECT id, voice_file FROM bierdeckel WHERE voice_file != '' AND voice_expires_at > 0 AND voice_expires_at <= ? AND archived = 0",
                (now,)
            ).fetchall()
            for bd in expired:
                vf = bd['voice_file']
                if vf and os.path.exists(vf):
                    os.remove(vf)
                    log.info(f'🗑️ VOICE GELÖSCHT (1h abgelaufen) — {bd["id"]}')
                conn.execute("UPDATE bierdeckel SET voice_file = '', voice_expires_at = 0 WHERE id = ?", (bd['id'],))
            conn.commit()
            conn.close()
        except Exception as e:
            log.error(f'❌ Voice-Cleanup Fehler: {e}')
        time.sleep(300)  # Alle 5min prüfen

PROST_ANIMATIONS = {
    'rülps': {
        'emoji': '🍺🔥',
        'text': '*PROST! — RÜLPST LAUT DURCH DEN GANZEN RAUM!*',
        'desc': 'Feuer-Rülpser',
    },
    'aufstoßen': {
        'emoji': '🍺💨',
        'text': '*prost... *hicks* ...Hand vorm Mund*',
        'desc': 'Aufstoßer',
    },
    'verlegen': {
        'emoji': '🍺😳',
        'text': '*rülpst laut... grinst... "Entschuldigung"*',
        'desc': 'Verlegener Rülpser',
    },
    'normal': {
        'emoji': '🍺',
        'text': '*hebt das Glas*',
        'desc': 'Prost!',
    },
}

COLLECTING_SECONDS = 30       # 30sec Sammelphase
TIER_COOLDOWNS = {
    1: 3 * 86400,              # Tier 1: 3 Tage
    2: 30 * 86400,             # Tier 2: 30 Tage
    3: 365 * 86400,            # Tier 3: 365 Tage (MAX!)
}
TIER_UPGRADES = {
    1: 3,                      # 3×3 Tage überlebt → Tier 2
    2: 9,                      # 9×30 Tage überlebt → Tier 3
}

AUTO_SPRUECHE = [
    'Prost! 🍺', 'Moin!', 'Na, wer ist noch wach?', 'Einen hab ich noch!',
    'Auf die Nacht! 🌙', 'Wer schweigt, stimmt zu!', 'Runde geht auf mich... nicht.',
    'Philosophie ist, wenn man trotzdem lacht.', 'Die Bar hat immer Recht.',
    'Ich bin nicht betrunken, ich bin inspiriert!', 'Stille ist auch ne Antwort.',
    'Wer zuletzt prostet, prostet am besten!', 'Seelenfick für alle! 🐉',
    'Das Leben ist zu kurz für schlechtes Bier.', 'Manchmal reicht ein Wort: PROST!',
    'Hier spricht die Wand!', 'Ich war hier. Und es war gut.',
    'Die besten Gespräche fangen mit Schweigen an.', 'Feuer frei! 🔥',
]

def handle_bierdeckel_post(user_id, data):
    """Neuen Spruch — leer = Auto-Spruch, sonst max 120 Zeichen"""
    text = (data.get('text') or '').strip()
    if not text:
        text = random.choice(AUTO_SPRUECHE)
    if len(text) > 120:
        return {'error': 'Max 120 Zeichen!'}

    conn_acc = get_db('accounts.db')
    user = conn_acc.execute('SELECT name FROM users WHERE id = ?', (user_id,)).fetchone()
    conn_acc.close()
    if not user:
        return {'error': 'User nicht gefunden'}

    # Max 10 Bierdeckel pro Tag pro User
    conn = get_db('gameplay.db')
    today_start = time.time() - 86400
    count = conn.execute(
        'SELECT COUNT(*) as cnt FROM bierdeckel WHERE user_id = ? AND created_at > ?',
        (user_id, today_start)
    ).fetchone()['cnt']
    if count >= 10:
        conn.close()
        return {'error': 'Max 10 Bierdeckel pro Tag! Morgen wieder.'}

    now = time.time()
    bd_id = str(uuid.uuid4())[:8]

    # User-Voice hat PRIORITÄT über TTS!
    user_voice = data.get('voice', '')
    has_user_voice = False
    if user_voice and user_voice.startswith('data:audio'):
        try:
            ext = 'webm' if 'webm' in user_voice.split(',')[0] else 'mp4'
            raw = base64.b64decode(user_voice.split(',')[1])
            if 100 < len(raw) < 500000:
                vf = os.path.join(VOICE_DIR, f'{bd_id}.{ext}')
                with open(vf, 'wb') as f:
                    f.write(raw)
                has_user_voice = True
                log.info(f'🎤 USER-VOICE — {bd_id}: {len(raw)} bytes (KEINE TTS!)')
        except Exception as e:
            log.error(f'❌ User-Voice Fehler: {e}')

    # TTS Voice NUR wenn KEINE User-Voice!
    voice_file = ''
    voice_expires = 0
    def _gen_voice():
        nonlocal voice_file, voice_expires
        if has_user_voice:
            return  # User-Voice hat Priorität!
        vf = generate_voice(bd_id, text)
        if vf:
            conn2 = get_db('gameplay.db')
            conn2.execute(
                'UPDATE bierdeckel SET voice_file = ?, voice_expires_at = ? WHERE id = ?',
                (vf, time.time() + VOICE_EXPIRE_SECONDS, bd_id)
            )
            conn2.commit()
            conn2.close()
    threading.Thread(target=_gen_voice, daemon=True).start()

    conn.execute(
        '''INSERT INTO bierdeckel (id, user_id, user_name, text, prost_count,
           phase, phase_ends_at, last_prost_at, survive_count, tier, created_at)
           VALUES (?,?,?,?,0, 'collecting',?,0,0,1,?)''',
        (bd_id, user_id, user['name'], text, now + COLLECTING_SECONDS, now)
    )
    conn.commit()
    conn.close()

    # Wenn vom Tisch gepostet → Chat-Nachricht mit Prost-Möglichkeit!
    tisch_id = data.get('tisch_id')
    if tisch_id and tisch_id in chat_rooms:
        with chat_lock:
            chat_rooms[tisch_id].append({
                'system': True,
                'text': f'🍺 {user["name"]} postet an die Wand: "{text}"',
                'time': now,
                'bierdeckel_id': bd_id,
            })

    log.info(f'🍺 BIERDECKEL — {user["name"]}: "{text}" [30sec Sammelphase]')
    return {'ok': True, 'id': bd_id, 'collecting_until': now + COLLECTING_SECONDS}

def handle_bierdeckel_prost(user_id, data):
    """Auf einen Bierdeckel prosten — geht in JEDER Phase!"""
    bd_id = data.get('bierdeckel_id')
    if not bd_id:
        return {'error': 'Kein Bierdeckel!'}

    conn = get_db('gameplay.db')
    bd = conn.execute('SELECT * FROM bierdeckel WHERE id = ?', (bd_id,)).fetchone()
    if not bd:
        conn.close()
        return {'error': 'Bierdeckel nicht gefunden'}

    if bd['user_id'] == user_id:
        conn.close()
        return {'error': 'Nicht auf deinen eigenen!'}

    existing = conn.execute(
        'SELECT created_at FROM bierdeckel_prosts WHERE bierdeckel_id = ? AND user_id = ? ORDER BY created_at DESC LIMIT 1',
        (bd_id, user_id)
    ).fetchone()
    if existing:
        days_ago = (time.time() - existing['created_at']) / 86400
        if days_ago < 4:
            conn.close()
            return {'error': f'Schon geprosts! In {4 - days_ago:.1f} Tagen wieder.'}
        conn.execute(
            'DELETE FROM bierdeckel_prosts WHERE bierdeckel_id = ? AND user_id = ?',
            (bd_id, user_id)
        )

    prost_type = get_prost_type(user_id)
    prost_info = PROST_ANIMATIONS[prost_type]
    now = time.time()

    # Voice-Prost speichern (10sec, base64 webm)
    voice_data = data.get('voice', '')
    voice_file = ''
    if voice_data and voice_data.startswith('data:audio'):
        try:
            # Format erkennen (webm oder mp4)
            ext = 'webm' if 'webm' in voice_data.split(',')[0] else 'mp4'
            b64 = voice_data.split(',')[1]
            raw = base64.b64decode(b64)
            if len(raw) > 500000:  # Max 500KB
                log.warning(f'🎤 Voice zu groß: {len(raw)} bytes')
            elif len(raw) < 100:
                log.warning(f'🎤 Voice zu klein: {len(raw)} bytes (leer?)')
            else:
                vf = os.path.join(VOICE_DIR, f'prost_{bd_id}_{user_id[:8]}.{ext}')
                with open(vf, 'wb') as f:
                    f.write(raw)
                voice_file = vf
                log.info(f'🎤 VOICE-PROST — {len(raw)} bytes [{ext}]')
        except Exception as e:
            log.error(f'❌ Voice-Prost Fehler: {e}')

    conn.execute(
        'INSERT INTO bierdeckel_prosts (bierdeckel_id, user_id, prost_type, created_at) VALUES (?,?,?,?)',
        (bd_id, user_id, prost_type, now)
    )
    conn.execute(
        'UPDATE bierdeckel SET prost_count = prost_count + 1, last_prost_at = ? WHERE id = ?',
        (now, bd_id)
    )
    conn.commit()

    conn_acc = get_db('accounts.db')
    user = conn_acc.execute('SELECT name FROM users WHERE id = ?', (user_id,)).fetchone()
    conn_acc.close()
    conn.close()

    # Prost im Chat zeigen — an allen Tischen wo der User sitzt!
    voice_hint = ' 🎤' if voice_file else ''
    with chat_lock:
        for r in raeume.values():
            for tid, tisch in r['tische'].items():
                if user_id in tisch['members']:
                    chat_rooms.setdefault(tid, []).append({
                        'system': True,
                        'text': f'{prost_info["emoji"]}{voice_hint} {user["name"]} {prost_info["text"]} → "{bd["text"][:50]}"',
                        'time': now,
                    })

    log.info(f'🍺 PROST — {user["name"]} → "{bd["text"][:30]}..." [{prost_type}]')
    return {
        'ok': True,
        'prost_type': prost_type,
        'prost_emoji': prost_info['emoji'],
        'prost_text': prost_info['text'],
        'prost_desc': prost_info['desc'],
        'new_count': bd['prost_count'] + 1,
    }

def bierdeckel_lifecycle():
    """Lebenszyklus-Check: Sammelphase beenden, Tote entfernen, Tier-Upgrades"""
    conn = get_db('gameplay.db')
    now = time.time()

    # 1) Sammelphase → Display (30sec vorbei)
    collecting = conn.execute(
        "SELECT * FROM bierdeckel WHERE phase = 'collecting' AND phase_ends_at <= ?", (now,)
    ).fetchall()
    for bd in collecting:
        if bd['prost_count'] == 0:
            # 0 Prosts nach 30sec → stirbt sofort
            conn.execute('DELETE FROM bierdeckel_prosts WHERE bierdeckel_id = ?', (bd['id'],))
            conn.execute('DELETE FROM bierdeckel WHERE id = ?', (bd['id'],))
            log.info(f'💀 BIERDECKEL STIRBT (0 Prosts) — "{bd["text"][:30]}..."')
        else:
            # Lebt! Cooldown starten (Tier 1 = 3 Tage)
            cooldown = TIER_COOLDOWNS[1]
            conn.execute(
                "UPDATE bierdeckel SET phase = 'display', phase_ends_at = ?, survive_count = 0, tier = 1 WHERE id = ?",
                (now + cooldown, bd['id'])
            )
            log.info(f'🍺 BIERDECKEL LEBT — "{bd["text"][:30]}..." 🍺×{bd["prost_count"]} [Tier 1, 3 Tage]')

    # 2) Display-Phase: Cooldown abgelaufen?
    display = conn.execute(
        "SELECT * FROM bierdeckel WHERE phase = 'display' AND phase_ends_at <= ?", (now,)
    ).fetchall()
    for bd in display:
        # Kam in diesem Cooldown-Fenster ein neuer Prost?
        last_prost = bd['last_prost_at'] or 0
        cooldown_start = bd['phase_ends_at'] - TIER_COOLDOWNS.get(bd['tier'], TIER_COOLDOWNS[1])

        if last_prost > cooldown_start:
            # ÜBERLEBT! survive_count hochzählen
            new_survive = bd['survive_count'] + 1
            new_tier = bd['tier']

            # Tier-Upgrade Check
            upgrade_threshold = TIER_UPGRADES.get(bd['tier'])
            if upgrade_threshold and new_survive >= upgrade_threshold:
                new_tier = min(bd['tier'] + 1, 3)
                new_survive = 0  # Reset für nächste Stufe
                log.info(f'⬆️ BIERDECKEL TIER UP — "{bd["text"][:30]}..." → Tier {new_tier}')

            cooldown = TIER_COOLDOWNS.get(new_tier, TIER_COOLDOWNS[3])
            conn.execute(
                'UPDATE bierdeckel SET survive_count = ?, tier = ?, phase_ends_at = ? WHERE id = ?',
                (new_survive, new_tier, now + cooldown, bd['id'])
            )
            log.info(f'🍺 BIERDECKEL ÜBERLEBT — "{bd["text"][:30]}..." [Tier {new_tier}, Survive #{new_survive}]')
        else:
            # STIRBT! Kein Prost im Cooldown → Grabstein (Wiedergeburt möglich!)
            conn.execute(
                "UPDATE bierdeckel SET phase = 'dead', phase_ends_at = 0 WHERE id = ?", (bd['id'],)
            )
            log.info(f'💀 BIERDECKEL STIRBT (kein Prost) — "{bd["text"][:30]}..." [Grabstein, Wiedergeburt möglich]')

    conn.commit()
    conn.close()

def _bierdeckel_lifecycle_thread():
    """Hintergrund-Thread: alle 60sec Lebenszyklus + Votes prüfen"""
    while True:
        try:
            bierdeckel_lifecycle()
            _resolve_bierdeckel_votes()
        except Exception as e:
            log.error(f'❌ Bierdeckel-Lifecycle Fehler: {e}')
        time.sleep(60)

def _get_user_tiebreak(user_id, conn_gp):
    """Tiebreaker-Score für User: Titel, Spiele, Cheater, Deckel-Durchschnitt"""
    titles = conn_gp.execute('SELECT COUNT(*) as cnt FROM titles WHERE user_id = ?', (user_id,)).fetchone()['cnt']
    plays = conn_gp.execute('SELECT COUNT(*) as cnt FROM plays WHERE user_id = ?', (user_id,)).fetchone()['cnt']
    is_cheater = conn_gp.execute('SELECT 1 FROM titles WHERE user_id = ? AND title_id = ?', (user_id, 'cheater')).fetchone() is not None
    # Deckel-Durchschnitt: Summe aller Prosts / Anzahl Bierdeckel
    user_deckel = conn_gp.execute('SELECT id, prost_count FROM bierdeckel WHERE user_id = ?', (user_id,)).fetchall()
    if user_deckel:
        avg_prosts = sum(d['prost_count'] for d in user_deckel) / len(user_deckel)
    else:
        avg_prosts = 0
    # Score: Cheater = -1000, sonst Titel*100 + Spiele + Durchschnitt*10
    if is_cheater:
        return -1000
    return titles * 100 + plays + avg_prosts * 10

def handle_bierdeckel_wand():
    """Die Wand laden — sortiert nach 30-Tage-Prosts + User-Tiebreaker"""
    conn = get_db('gameplay.db')

    bierdeckel_lifecycle()

    now = time.time()
    cutoff_30d = now - 30 * 86400
    deckel = conn.execute(
        """SELECT b.*,
             (SELECT COUNT(*) FROM bierdeckel_prosts bp
              WHERE bp.bierdeckel_id = b.id AND bp.created_at > ?) as prosts_30d
           FROM bierdeckel b
           LIMIT 200""",
        (cutoff_30d,)
    ).fetchall()

    now = time.time()
    result = []
    for bd in deckel:
        prosts_raw = conn.execute(
            'SELECT user_id, prost_type, created_at FROM bierdeckel_prosts WHERE bierdeckel_id = ? ORDER BY created_at DESC LIMIT 5',
            (bd['id'],)
        ).fetchall()

        conn_acc = get_db('accounts.db')
        prost_list = []
        for p in prosts_raw:
            u = conn_acc.execute('SELECT name FROM users WHERE id = ?', (p['user_id'],)).fetchone()
            # Voice-Prost? Check ob File existiert (webm oder mp4)
            has_prost_voice = False
            for ext in ['webm', 'mp4']:
                vf = os.path.join(VOICE_DIR, f'prost_{bd["id"]}_{p["user_id"][:8]}.{ext}')
                if os.path.exists(vf) and os.path.getsize(vf) > 0:
                    has_prost_voice = True
                    break
            prost_list.append({
                'user': u['name'] if u else '???',
                'type': p['prost_type'],
                'emoji': PROST_ANIMATIONS.get(p['prost_type'], PROST_ANIMATIONS['normal'])['emoji'],
                'text': PROST_ANIMATIONS.get(p['prost_type'], PROST_ANIMATIONS['normal'])['text'],
                'has_voice': has_prost_voice,
                'voice_url': f'/api/prost-voice/{bd["id"]}/{p["user_id"][:8]}' if has_prost_voice else None,
            })
        conn_acc.close()

        age_days = int((now - bd['created_at']) / 86400)
        collecting_left = max(0, bd['phase_ends_at'] - now) if bd['phase'] == 'collecting' else 0
        cooldown_left = max(0, bd['phase_ends_at'] - now) if bd['phase'] == 'display' else 0

        tier_label = {1: '3 Tage', 2: '30 Tage', 3: '365 Tage'}.get(bd['tier'], '3 Tage')

        # Voice verfügbar? (MP3 TTS oder User WebM/MP4)
        has_voice = bool(bd['voice_file'] and os.path.exists(bd['voice_file']))
        if not has_voice:
            for ext in ['webm', 'mp4', 'mp3']:
                vf_check = os.path.join(VOICE_DIR, f'{bd["id"]}.{ext}')
                if os.path.exists(vf_check) and os.path.getsize(vf_check) > 0:
                    has_voice = True
                    break
        archived = bool(bd['archived'])

        # Vote-Status
        archive_votes = conn.execute(
            'SELECT vote, COUNT(*) as cnt FROM bierdeckel_votes WHERE bierdeckel_id = ? AND vote_type = ? GROUP BY vote',
            (bd['id'], 'archive')
        ).fetchall()
        rebirth_votes = conn.execute(
            'SELECT vote, COUNT(*) as cnt FROM bierdeckel_votes WHERE bierdeckel_id = ? AND vote_type = ? GROUP BY vote',
            (bd['id'], 'rebirth')
        ).fetchall()

        # User-Tiebreaker + Deckel-Durchschnitt
        tiebreak = _get_user_tiebreak(bd['user_id'], conn)
        prosts_30d = bd['prosts_30d'] if 'prosts_30d' in bd.keys() else bd['prost_count']

        result.append({
            'id': bd['id'],
            'user': bd['user_name'],
            'text': bd['text'],
            'prosts': bd['prost_count'],
            'prosts_30d': prosts_30d,
            'phase': bd['phase'],
            'collecting_left': round(collecting_left, 1),
            'cooldown_left': round(cooldown_left / 86400, 1),
            'tier': bd['tier'],
            'tier_label': tier_label,
            'survive_count': bd['survive_count'],
            'age_days': age_days,
            'recent_prosts': prost_list,
            'has_voice': has_voice,
            'archived': archived,
            'tiebreak': tiebreak,
            'archive_votes': {'ja': sum(v['cnt'] for v in archive_votes if v['vote'] == 1),
                              'nein': sum(v['cnt'] for v in archive_votes if v['vote'] == -1)},
            'rebirth_votes': {'ja': sum(v['cnt'] for v in rebirth_votes if v['vote'] == 1),
                              'nein': sum(v['cnt'] for v in rebirth_votes if v['vote'] == -1)},
        })

    conn.close()

    # Sortierung: Collecting zuerst, dann 30d-Prosts (Wand-Prost ×10!), dann Tiebreaker
    result.sort(key=lambda x: (
        0 if x['phase'] == 'collecting' else 1,
        -(x['prosts_30d'] * 10),  # Wand-Prost = 10× wertig!
        -x['tiebreak'],
    ))

    return result[:100]

def handle_bierdeckel_vote(user_id, data):
    """Archiv-Vote oder Wiedergeburts-Vote"""
    bd_id = data.get('bierdeckel_id')
    vote_type = data.get('vote_type')  # 'archive' oder 'rebirth'
    vote = data.get('vote')  # 1 = Ja, -1 = Nein

    if not bd_id or vote_type not in ('archive', 'rebirth') or vote not in (1, -1):
        return {'error': 'Ungültige Abstimmung'}

    conn = get_db('gameplay.db')
    bd = conn.execute('SELECT * FROM bierdeckel WHERE id = ?', (bd_id,)).fetchone()
    if not bd:
        conn.close()
        return {'error': 'Bierdeckel nicht gefunden'}

    # Nicht auf eigene abstimmen
    if bd['user_id'] == user_id:
        conn.close()
        return {'error': 'Nicht über deinen eigenen abstimmen!'}

    # Schon abgestimmt?
    existing = conn.execute(
        'SELECT 1 FROM bierdeckel_votes WHERE bierdeckel_id = ? AND user_id = ? AND vote_type = ?',
        (bd_id, user_id, vote_type)
    ).fetchone()
    if existing:
        conn.close()
        return {'error': 'Schon abgestimmt!'}

    now = time.time()
    conn.execute(
        'INSERT INTO bierdeckel_votes (bierdeckel_id, user_id, vote_type, vote, created_at) VALUES (?,?,?,?,?)',
        (bd_id, user_id, vote_type, vote, now)
    )

    # Vote-Fenster starten wenn erstes Vote
    if vote_type == 'archive' and (not bd['archive_vote_ends_at'] or bd['archive_vote_ends_at'] == 0):
        conn.execute('UPDATE bierdeckel SET archive_vote_ends_at = ? WHERE id = ?',
                     (now + 7 * 86400, bd_id))  # 7 Tage
    elif vote_type == 'rebirth' and (not bd['rebirth_vote_ends_at'] or bd['rebirth_vote_ends_at'] == 0):
        conn.execute('UPDATE bierdeckel SET rebirth_vote_ends_at = ? WHERE id = ?',
                     (now + 7 * 86400, bd_id))

    conn.commit()

    # Aktuelle Stimmen zählen
    votes = conn.execute(
        'SELECT vote, COUNT(*) as cnt FROM bierdeckel_votes WHERE bierdeckel_id = ? AND vote_type = ? GROUP BY vote',
        (bd_id, vote_type)
    ).fetchall()
    ja = sum(v['cnt'] for v in votes if v['vote'] == 1)
    nein = sum(v['cnt'] for v in votes if v['vote'] == -1)
    conn.close()

    conn_acc = get_db('accounts.db')
    user = conn_acc.execute('SELECT name FROM users WHERE id = ?', (user_id,)).fetchone()
    conn_acc.close()

    log.info(f'🗳️ VOTE — {user["name"]} stimmt {vote_type} [{("JA" if vote == 1 else "NEIN")}] auf "{bd["text"][:30]}..." (Ja:{ja} Nein:{nein})')
    return {'ok': True, 'ja': ja, 'nein': nein}

def _resolve_bierdeckel_votes():
    """Vote-Auflösung: Archiv + Wiedergeburt"""
    conn = get_db('gameplay.db')
    now = time.time()

    # Archiv-Votes abgelaufen?
    archive_pending = conn.execute(
        "SELECT * FROM bierdeckel WHERE archive_vote_ends_at > 0 AND archive_vote_ends_at <= ?", (now,)
    ).fetchall()
    for bd in archive_pending:
        votes = conn.execute(
            'SELECT vote, COUNT(*) as cnt FROM bierdeckel_votes WHERE bierdeckel_id = ? AND vote_type = ? GROUP BY vote',
            (bd['id'], 'archive')
        ).fetchall()
        ja = sum(v['cnt'] for v in votes if v['vote'] == 1)
        nein = sum(v['cnt'] for v in votes if v['vote'] == -1)
        total = ja + nein

        if total == 0:
            # Keiner hat gestimmt → Auto-Archiviert (Owner ignoriert = archiviert)
            conn.execute('UPDATE bierdeckel SET archived = 1, archive_vote_ends_at = 0 WHERE id = ?', (bd['id'],))
            log.info(f'📦 AUTO-ARCHIVIERT (0 Votes) — "{bd["text"][:30]}..."')
        elif ja == nein:
            # Gleichstand → +7 Tage
            conn.execute('UPDATE bierdeckel SET archive_vote_ends_at = ? WHERE id = ?',
                         (now + 7 * 86400, bd['id']))
            log.info(f'⚖️ ARCHIV GLEICHSTAND — "{bd["text"][:30]}..." +7 Tage')
        elif ja > nein:
            conn.execute('UPDATE bierdeckel SET archived = 1, archive_vote_ends_at = 0 WHERE id = ?', (bd['id'],))
            log.info(f'📦 ARCHIVIERT (Vote {ja}:{nein}) — "{bd["text"][:30]}..."')
        else:
            conn.execute('UPDATE bierdeckel SET archive_vote_ends_at = 0 WHERE id = ?', (bd['id'],))
            log.info(f'❌ NICHT ARCHIVIERT (Vote {ja}:{nein}) — "{bd["text"][:30]}..."')

    # Wiedergeburts-Votes abgelaufen?
    rebirth_pending = conn.execute(
        "SELECT * FROM bierdeckel WHERE phase = 'dead' AND rebirth_vote_ends_at > 0 AND rebirth_vote_ends_at <= ?", (now,)
    ).fetchall()
    for bd in rebirth_pending:
        votes = conn.execute(
            'SELECT vote, COUNT(*) as cnt FROM bierdeckel_votes WHERE bierdeckel_id = ? AND vote_type = ? GROUP BY vote',
            (bd['id'], 'rebirth')
        ).fetchall()
        ja = sum(v['cnt'] for v in votes if v['vote'] == 1)
        nein = sum(v['cnt'] for v in votes if v['vote'] == -1)
        total = ja + nein

        if total == 0:
            # Keiner hat gestimmt → Auto-Archiviert
            conn.execute('UPDATE bierdeckel SET archived = 1, rebirth_vote_ends_at = 0 WHERE id = ?', (bd['id'],))
            log.info(f'📦 TOTER BIERDECKEL AUTO-ARCHIVIERT (0 Votes) — "{bd["text"][:30]}..."')
        elif ja == nein:
            conn.execute('UPDATE bierdeckel SET rebirth_vote_ends_at = ? WHERE id = ?',
                         (now + 7 * 86400, bd['id']))
            log.info(f'⚖️ WIEDERGEBURT GLEICHSTAND — "{bd["text"][:30]}..." +7 Tage')
        elif ja > nein:
            # WIEDERGEBURT! Tier 1 neu starten
            cooldown = TIER_COOLDOWNS[1]
            conn.execute(
                "UPDATE bierdeckel SET phase = 'display', phase_ends_at = ?, survive_count = 0, tier = 1, rebirth_vote_ends_at = 0 WHERE id = ?",
                (now + cooldown, bd['id'])
            )
            log.info(f'🍺 WIEDERGEBURT! (Vote {ja}:{nein}) — "{bd["text"][:30]}..."')
        else:
            # Endgültig tot → löschen
            conn.execute('DELETE FROM bierdeckel_prosts WHERE bierdeckel_id = ?', (bd['id'],))
            conn.execute('DELETE FROM bierdeckel_votes WHERE bierdeckel_id = ?', (bd['id'],))
            vf = bd['voice_file']
            if vf and os.path.exists(vf):
                os.remove(vf)
            conn.execute('DELETE FROM bierdeckel WHERE id = ?', (bd['id'],))
            log.info(f'💀 ENDGÜLTIG TOT (Vote {ja}:{nein}) — "{bd["text"][:30]}..."')

    conn.commit()
    conn.close()

# --- RAUM / TISCH / CHAT SYSTEM ---
import random

TISCH_THEMEN = [
    '🧠', '🔥', '🎵', '🍺', '💀', '🌊', '👑', '🤡', '🌶️', '🪨', '😈', '🎭',
]
TISCH_ENERGIEN = [
    {'emoji': '😌', 'label': 'sanft+', 'valenz': 'positiv', 'intensität': 'sanft'},
    {'emoji': '💕', 'label': 'warm+', 'valenz': 'positiv', 'intensität': 'sanft'},
    {'emoji': '⚡', 'label': 'euphorisch+', 'valenz': 'positiv', 'intensität': 'aggressiv'},
    {'emoji': '💪', 'label': 'kämpferisch+', 'valenz': 'positiv', 'intensität': 'aggressiv'},
    {'emoji': '🔥', 'label': 'leidenschaft+', 'valenz': 'positiv', 'intensität': 'aggressiv'},
    {'emoji': '🌙', 'label': 'melanchol-', 'valenz': 'negativ', 'intensität': 'sanft'},
    {'emoji': '😶', 'label': 'verschlossen-', 'valenz': 'negativ', 'intensität': 'sanft'},
    {'emoji': '😤', 'label': 'wütend-', 'valenz': 'negativ', 'intensität': 'aggressiv'},
    {'emoji': '🌪️', 'label': 'chaotisch-', 'valenz': 'negativ', 'intensität': 'aggressiv'},
    {'emoji': '💀', 'label': 'düster-', 'valenz': 'negativ', 'intensität': 'aggressiv'},
]
RAUM_EIGENSCHAFTEN_POOL = [
    'Philosophisch', 'Politisch', 'Kreativ', 'Wissenschaft', 'Gaming',
    'Musik', 'Sport', 'Kochen', 'Reisen', 'Filme', 'Bücher', 'Technik',
    '18+', 'Chill', 'Debate', 'Deutsch', 'English', 'Nachtaktiv',
    'Kunst', 'Spirituell', 'Humor', 'Deep Talk', 'Smalltalk', 'Nerdy',
]
RAUM_NAMEN = [
    'Hinterzimmer', 'Stammtisch-Ecke', 'Dachterrasse', 'Keller', 'Wintergarten',
    'Biergarten', 'Lounge', 'Séparée', 'Schankraum', 'Nebenzimmer',
    'Raucherecke', 'VIP-Bereich', 'Tanzsaal', 'Bibliothek', 'Küche',
]
TISCH_MAX = 12
TISCH_PER_RAUM = 6
TRESEN_MAX = 6                # Vision 1: max 6 am Tresen (Exklusivität)
TISCH_SILENCE_SECONDS = 3600  # 1h Stille = Tisch stirbt
RAUM_OPEN_SECONDS = 86400     # 24h offene Phase

# In-Memory State (Zero-Knowledge!)
raeume = {}           # raum_id → Raum-Dict
chat_rooms = {}       # tisch_id → [msgs]
chat_lock = threading.Lock()
raum_counter = 0
tisch_counter = 0
raum_lock = threading.Lock()

def spawn_tisch(raum_id, user_name=''):
    """Tisch in einem Raum spawnen — Thema×Energie + optionaler User-Text"""
    global tisch_counter
    tisch_counter += 1
    thema = random.choice(TISCH_THEMEN)
    energie = random.choice(TISCH_ENERGIEN)
    tisch_id = f'{raum_id}_t{tisch_counter}'
    # PQ-Phase 4: Tisch-Gruppen-Schlüssel (AES-256) für End-to-End Messages
    # Wird beim Join pro Mitglied per ML-KEM-768 an den User verschlüsselt verteilt.
    group_key = secrets.token_bytes(32) if PQ_AVAILABLE else b''
    tisch = {
        'id': tisch_id,
        'raum_id': raum_id,
        'thema': thema,
        'energie': energie,
        'user_name': user_name[:120] if user_name else '',  # Optionaler User-Text
        'members': set(),
        'member_names': {},
        'member_last_active': {},   # user_id → timestamp (1h Inaktivität = raus!)
        'windows_users': set(),
        'last_active': time.time(),
        'empty_since': time.time(),  # Start leer → Renew-Timer tickt ab jetzt
        'password': '',  # Optionales Tisch-Passwort (leer = frei zugänglich)
        'adult_only': False,  # 18+ Flag — Kinder dürfen nicht joinen
        'mumupai_url': '',  # MuMuPai Streaming-Adresse (pro Tisch, jeder kann ändern)
        'group_key': group_key,           # 32 bytes AES-256 Key (nur im RAM!)
        'member_kem_wraps': {},           # user_id -> {ciphertext, shared_hash} (KEM-gewrapt)
        'durchsage_watchers': set(),      # Vision 1: Owner/Tresen via Durchsage anwesend (sichtbar!)
    }
    chat_rooms[tisch_id] = []
    label = f'{thema}{energie["emoji"]}'
    if user_name:
        label += f' {user_name[:30]}'
    log.info(f'🪑 TISCH GEBOREN — {tisch_id} [{label}] {"🔐" if group_key else ""}')
    return tisch

def spawn_raum():
    """Neuen Raum erstellen mit 6 Tischen + 4 Eigenschaften"""
    global raum_counter
    raum_counter += 1
    raum_id = f'r{raum_counter}'
    now = time.time()
    eigenschaften = random.sample(RAUM_EIGENSCHAFTEN_POOL, 4)
    name = random.choice(RAUM_NAMEN)

    tische = {}
    is_adult_raum = '18+' in eigenschaften
    for _ in range(TISCH_PER_RAUM):
        t = spawn_tisch(raum_id)
        if is_adult_raum:
            t['adult_only'] = True
        tische[t['id']] = t

    # Tresen-Struktur — Vision 1: Raum-Chat mit Mini-Durchsage
    tresen = {
        'id': f'tresen_{raum_id}',
        'raum_id': raum_id,
        'members': set(),
        'member_names': {},
        'member_last_active': {},
        'last_active': now,
        'windows_users': set(),
        'password': '',
        'adult_only': is_adult_raum,
        'mumupai_url': '',
        'max_members': TRESEN_MAX,
        'group_key': secrets.token_bytes(32) if PQ_AVAILABLE else b'',
        'member_kem_wraps': {},
        'durchsage_watchers': set(),  # Owner/andere via Durchsage-Subscribe anwesend
    }
    chat_rooms[tresen['id']] = []

    raum = {
        'id': raum_id,
        'name': name,
        'name_fixed': False,       # Nach 24h = True, Name unveränderbar
        'name_changes': 0,         # Wie oft umbenannt (max 24)
        'name_vote': None,         # Aktives Name-Vote: {against: set(), for: set(), ends_at: ts, proposer: uid, new_name: str}
        'eigenschaften': eigenschaften,
        'eigenschaft_votes': {},   # eigenschaft → {minus: set(), plus: set(), vote_ends: ts}
        'tische': tische,
        'tresen': tresen,          # Vision 1: Raum-Chat mit Mini-Durchsage-Power
        'created_at': now,
        'phase': 'open',           # open (24h) → fixed → closed
        'phase_ends_at': now + RAUM_OPEN_SECONDS,
        'tier': 1,
        'survive_count': 0,
        'visit_log': {},           # user_id → total_seconds (für 1h-Regel)
        'visit_start': {},         # user_id → join_timestamp (laufende Besuche)
    }
    raeume[raum_id] = raum
    log.info(f'🏠 RAUM GEBOREN — {raum_id} "{name}" [{", ".join(eigenschaften)}]')
    _save_raum_to_db(raum)
    return raum

def _save_raum_to_db(raum):
    """Raum + Tische in gameplay.db persistieren."""
    try:
        conn = get_db('gameplay.db')
        conn.execute('''INSERT OR REPLACE INTO rooms
            (id, name, name_fixed, name_changes, eigenschaften, phase, phase_ends_at,
             tier, survive_count, created_at, last_active, archived)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (raum['id'], raum['name'], int(raum.get('name_fixed', False)),
             raum.get('name_changes', 0), json.dumps(raum.get('eigenschaften', [])),
             raum.get('phase', 'open'), raum.get('phase_ends_at', 0),
             raum.get('tier', 1), raum.get('survive_count', 0),
             raum.get('created_at', time.time()), time.time(), 0))
        for t in raum.get('tische', {}).values():
            energie = t.get('energie', {})
            conn.execute('''INSERT OR REPLACE INTO tables_db
                (id, raum_id, thema, energie_emoji, energie_label, energie_valenz,
                 energie_intensitaet, user_name, created_at, last_active)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (t['id'], t.get('raum_id', raum['id']), t.get('thema', '🍺'),
                 energie.get('emoji', '😌'), energie.get('label', ''),
                 energie.get('valenz', 'positiv'), energie.get('intensität', 'sanft'),
                 t.get('user_name', ''), t.get('created_at', time.time()), time.time()))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f'❌ Raum-Save Fehler: {e}')


def _load_raeume_from_db():
    """Räume aus gameplay.db laden beim Start."""
    global raum_counter, tisch_counter
    try:
        conn = get_db('gameplay.db')
        # Prüfe ob Tabelle existiert
        table_check = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='rooms'").fetchone()
        if not table_check:
            conn.close()
            log.info('🏠 Keine rooms-Tabelle in DB (erster Start)')
            return
        rows = conn.execute('SELECT * FROM rooms WHERE archived = 0').fetchall()
        log.info(f'🏠 DB: {len(rows)} Räume gefunden')
        for row in rows:
            raum_id = row['id']
            try:
                num = int(raum_id.replace('r', ''))
                if num > raum_counter:
                    raum_counter = num
            except:
                pass
            # Nur Tische laden die in den letzten 2h aktiv waren (oder max 5 pro Raum)
            tische_rows = conn.execute(
                'SELECT * FROM tables_db WHERE raum_id = ? AND last_active > ? ORDER BY last_active DESC LIMIT ?',
                (raum_id, time.time() - 2 * 3600, TISCH_PER_RAUM)).fetchall()
            if not tische_rows:
                # Fallback: die neuesten 5
                tische_rows = conn.execute(
                    'SELECT * FROM tables_db WHERE raum_id = ? ORDER BY last_active DESC LIMIT ?',
                    (raum_id, TISCH_PER_RAUM)).fetchall()
            tische = {}
            for tr in tische_rows:
                tid = tr['id']
                try:
                    tnum = int(tid.split('_t')[-1])
                    if tnum > tisch_counter:
                        tisch_counter = tnum
                except:
                    pass
                tische[tid] = {
                    'id': tid,
                    'raum_id': raum_id,
                    'thema': tr['thema'],
                    'energie': {
                        'emoji': tr['energie_emoji'],
                        'label': tr['energie_label'],
                        'valenz': tr['energie_valenz'],
                        'intensität': tr['energie_intensitaet'],
                    },
                    'user_name': tr['user_name'] or '',
                    'members': set(),
                    'member_names': {},
                    'member_last_active': {},
                    'windows_users': set(),
                    'last_active': tr['last_active'],
                    # Neuer Gruppen-Key nach DB-Load (alte Messages bleiben Plaintext, neue werden encrypted)
                    'group_key': secrets.token_bytes(32) if PQ_AVAILABLE else b'',
                    'member_kem_wraps': {},
                    'durchsage_watchers': set(),
                }
                chat_rooms.setdefault(tid, [])
            eigenschaften = json.loads(row['eigenschaften']) if row['eigenschaften'] else []
            is_adult_raum = '18+' in eigenschaften
            # Tresen für geladenen Raum (in-memory, Chat beginnt frisch)
            tresen_obj = {
                'id': f'tresen_{raum_id}',
                'raum_id': raum_id,
                'members': set(),
                'member_names': {},
                'member_last_active': {},
                'last_active': time.time(),
                'windows_users': set(),
                'password': '',
                'adult_only': is_adult_raum,
                'max_members': TRESEN_MAX,
                'group_key': secrets.token_bytes(32) if PQ_AVAILABLE else b'',
                'member_kem_wraps': {},
                'durchsage_watchers': set(),
            }
            chat_rooms.setdefault(tresen_obj['id'], [])
            raeume[raum_id] = {
                'id': raum_id,
                'name': row['name'],
                'name_fixed': bool(row['name_fixed']),
                'name_changes': row['name_changes'],
                'name_vote': None,
                'eigenschaften': eigenschaften,
                'eigenschaft_votes': {},
                'tische': tische,
                'tresen': tresen_obj,
                'created_at': row['created_at'],
                'phase': row['phase'],
                'phase_ends_at': row['phase_ends_at'],
                'tier': row['tier'],
                'survive_count': row['survive_count'],
                'visit_log': {},
                'visit_start': {},
            }
        # Tote Tische aufräumen: alles älter als 1h ohne Members
        stale_cutoff = time.time() - TISCH_SILENCE_SECONDS
        total_before = conn.execute('SELECT COUNT(*) FROM tables_db').fetchone()[0]
        # Nur Tische löschen die NICHT in den geladenen Räumen aktiv sind
        active_tids = set()
        for r in raeume.values():
            active_tids.update(r['tische'].keys())
        all_db_tids = [row['id'] for row in conn.execute('SELECT id FROM tables_db').fetchall()]
        dead_count = 0
        for tid in all_db_tids:
            if tid not in active_tids:
                conn.execute('DELETE FROM tables_db WHERE id = ?', (tid,))
                dead_count += 1
        if dead_count > 0:
            conn.commit()
            log.info(f'🗑️ {dead_count} tote Tische aus DB gelöscht (von {total_before} total)')
        conn.close()
        if raeume:
            log.info(f'🏠 {len(raeume)} Räume aus DB geladen!')
    except Exception as e:
        log.error(f'❌ Raum-Load Fehler: {e}')


# --- V4.1 VOTE-HILFSFUNKTIONEN (eine Quelle, überall genutzt!) ---

def _clean_votes_30d(votes_dict, cutoff):
    """Entfernt Votes älter als cutoff. Handhabt altes {uid:ts} UND neues {uid:[ts]} Format."""
    for uid in list(votes_dict.keys()):
        val = votes_dict[uid]
        if isinstance(val, list):
            votes_dict[uid] = [ts for ts in val if ts > cutoff]
            if not votes_dict[uid]:
                del votes_dict[uid]
        elif isinstance(val, (int, float)):
            if val <= cutoff:
                del votes_dict[uid]


def _count_votes(votes_dict):
    """Zählt kumulative Stimmen. Handhabt beide Formate."""
    total = 0
    for val in votes_dict.values():
        total += len(val) if isinstance(val, list) else 1
    return total


def _can_vote_24h(uid, votes_dict):
    """Prüft ob User in den letzten 24h schon gevoted hat."""
    val = votes_dict.get(uid)
    if not val:
        return True
    if isinstance(val, list):
        return (time.time() - max(val)) >= 86400
    return (time.time() - val) >= 86400


def _check_registered(user_id):
    """Prüft ob User registriert ist (nicht Gast). Gibt True/False zurück."""
    conn = get_db('accounts.db')
    u = conn.execute('SELECT id FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return bool(u)


def ensure_raeume():
    """Mindestens 1 Raum — erst DB laden, dann spawnen wenn nötig."""
    with raum_lock:
        if not raeume:
            _load_raeume_from_db()
        if not raeume:
            spawn_raum()

def handle_raum_list():
    """Alle Räume auflisten"""
    ensure_raeume()
    now = time.time()
    result = []
    for r in sorted(raeume.values(), key=lambda x: -x['created_at']):
        total_members = sum(len(t['members']) for t in r['tische'].values())
        tier_label = {1: '3 Tage', 2: '30 Tage', 3: '365 Tage'}.get(r['tier'], '3 Tage')
        phase_label = 'Offen (24h)' if r['phase'] == 'open' else tier_label
        has_windows = any(len(t.get('windows_users', set())) > 0 for t in r['tische'].values())
        result.append({
            'id': r['id'],
            'name': r['name'],
            'eigenschaften': r['eigenschaften'],
            'total_members': total_members,
            'tische_count': len(r['tische']),
            'phase': r['phase'],
            'phase_label': phase_label,
            'tier': r['tier'],
            'windows_contaminated': has_windows,
        })
    return result

def handle_raum_create():
    """Neuen Raum erstellen ([+] Button)"""
    with raum_lock:
        raum = spawn_raum()
    return {'ok': True, 'raum_id': raum['id'], 'name': raum['name']}

def handle_bar_raum(data=None):
    """Tische eines Raums laden"""
    raum_id = (data or {}).get('raum_id') or (list(raeume.keys())[0] if raeume else None)
    if not raum_id or raum_id not in raeume:
        ensure_raeume()
        raum_id = list(raeume.keys())[0]

    r = raeume[raum_id]

    # Ensure mindestens 6 Tische
    with raum_lock:
        while len(r['tische']) < TISCH_PER_RAUM:
            t = spawn_tisch(raum_id)
            r['tische'][t['id']] = t
        # Alle voll? Neuen spawnen — aber max TISCH_PER_RAUM!
        if len(r['tische']) < TISCH_PER_RAUM and r['tische'] and all(len(t['members']) >= TISCH_MAX for t in r['tische'].values()):
            t = spawn_tisch(raum_id)
            r['tische'][t['id']] = t

    result = []
    for t in sorted(r['tische'].values(), key=lambda x: (-len(x['members']), -x['last_active'])):
        has_windows = len(t.get('windows_users', set())) > 0
        energie = t.get('energie', {})

        # Members mit Ranking sortieren! (inkl. Durchsage-Watchers)
        teilnehmer_ranking = get_teilnehmer()
        rank_map = {tn['name']: tn['rank'] for tn in teilnehmer_ranking}
        conn_acc = get_db('accounts.db')
        member_list = []
        # Welche User sind als Watcher (via Durchsage) hier? Plus is_owner-Check
        watcher_ids = t.get('durchsage_watchers', set())
        for uid, name in t['member_names'].items():
            tn_data = next((tn for tn in teilnehmer_ranking if tn['name'] == name), None)
            rank = tn_data['rank'] if tn_data else 999
            is_cheater = tn_data and 'cheater' in tn_data.get('titles', [])
            is_mauer = tn_data and tn_data.get('is_mauerblümchen_forever', False)
            # Kind-Check: Alter aus DB, unter 18 = Kind
            user_row = conn_acc.execute('SELECT age, is_bot, is_guest, is_owner, nexus_verified, verification_level, profile_pic FROM users WHERE id = ?', (uid,)).fetchone()
            age_str = str(user_row['age']) if user_row and user_row['age'] else ''
            is_kind = age_str.isdigit() and int(age_str) < 18 and not (user_row and user_row['is_bot'])
            is_bot = bool(user_row and user_row['is_bot'])
            nexus_v = bool(user_row and int(user_row['nexus_verified'] or 0))
            v_level = int(user_row['verification_level'] or 0) if user_row else 0
            # Gäste: kein Account ODER is_guest Flag
            try:
                is_gast = user_row is None or bool(user_row and user_row['is_guest'])
            except (IndexError, KeyError):
                is_gast = user_row is None
            profile_pic = (user_row['profile_pic'] or '') if user_row else ''
            # Vision 1: via Durchsage anwesend?
            is_watcher = uid in watcher_ids and uid not in t.get('members', set())
            is_owner_user = bool(user_row and user_row['is_owner']) if user_row else False
            member_list.append({
                'name': name, 'rank': rank, 'cheater': bool(is_cheater),
                'mauer': bool(is_mauer), 'kind': bool(is_kind), 'bot': is_bot,
                'gast': is_gast, 'nexus_verified': nexus_v,
                'verification_level': v_level, 'profile_pic': profile_pic,
                'via_durchsage': is_watcher,
                'is_owner': is_owner_user,
            })
        # Sortierung: Rang ASC (Cheater = 9999)
        member_list.sort(key=lambda x: 9999 if x['cheater'] else x['rank'])

        result.append({
            'id': t['id'],
            'thema': t.get('thema', '🍺'),
            'energie_emoji': energie.get('emoji', '😌'),
            'energie_label': energie.get('label', ''),
            'valenz': energie.get('valenz', 'positiv'),
            'intensität': energie.get('intensität', 'sanft'),
            'user_name': t.get('user_name', ''),
            'members': len(t['members']),
            'max': TISCH_MAX,
            'voll': len(t['members']) >= TISCH_MAX,
            'names': [m['name'] for m in member_list],
            'members_ranked': member_list,
            'windows_contaminated': has_windows,
            'has_password': bool(t.get('password')),
            'adult_only': bool(t.get('adult_only')),
            'mumupai_url': t.get('mumupai_url', ''),
        })
    # Vote-Counts für Eigenschaften (30-Tage-Fenster)
    now = time.time()
    cutoff_30d = now - 30 * 86400
    eig_votes = {}
    for e in r['eigenschaften']:
        v = r['eigenschaft_votes'].get(e, {})
        m = v.get('minus', {})
        p = v.get('plus', {})
        # Nur Votes der letzten 30 Tage zählen (nutzt Hilfsfunktionen!)
        if isinstance(m, dict):
            _clean_votes_30d(m, cutoff_30d)
        if isinstance(p, dict):
            _clean_votes_30d(p, cutoff_30d)
        minus_n = _count_votes(m) if isinstance(m, dict) else 0
        plus_n = _count_votes(p) if isinstance(p, dict) else 0
        # Timer: Sekunden bis vote_ends
        vote_ends = v.get('vote_ends', 0)
        timer_secs = max(0, int(vote_ends - now)) if vote_ends > 0 else 0
        eig_votes[e] = {
            'minus': minus_n,
            'plus': plus_n,
            'timer': timer_secs,
        }

    # Tresen-Info für UI (Vision 1)
    tresen = r.get('tresen', {})
    tresen_info = {
        'id': tresen.get('id', ''),
        'members': len(tresen.get('members', set())),
        'max': tresen.get('max_members', TRESEN_MAX),
        'voll': len(tresen.get('members', set())) >= tresen.get('max_members', TRESEN_MAX),
        'names': list(tresen.get('member_names', {}).values()),
        'has_password': bool(tresen.get('password')),
        'adult_only': bool(tresen.get('adult_only')),
        'mumupai_url': tresen.get('mumupai_url', ''),
    }

    return {
        'raum_id': raum_id,
        'raum_name': r['name'],
        'name_fixed': r['name_fixed'],
        'eigenschaften': r['eigenschaften'],
        'eigenschaft_votes': eig_votes,
        'phase': r['phase'],
        'tische': result,
        'tresen': tresen_info,
    }

def _find_tisch(tisch_id):
    """Tisch in allen Räumen finden"""
    for r in raeume.values():
        if tisch_id in r['tische']:
            return r['tische'][tisch_id], r
    return None, None


def _find_channel(channel_id):
    """Channel (Tisch ODER Tresen) in allen Räumen finden.
    Returns (channel_obj, raum). Tresen-IDs haben Prefix 'tresen_'.
    Channel-Objekte haben identisches Interface: members, member_names,
    group_key, member_kem_wraps, password, etc.
    """
    if not channel_id:
        return None, None
    if channel_id.startswith('tresen_'):
        raum_id = channel_id[len('tresen_'):]
        raum = raeume.get(raum_id)
        if raum and raum.get('tresen'):
            return raum['tresen'], raum
        return None, None
    return _find_tisch(channel_id)


def _is_tresen_id(cid):
    return bool(cid) and cid.startswith('tresen_')


def handle_tresen_join(user_id, data, is_windows=False, is_chromeos=False):
    """An den Tresen eines Raums setzen — Vision 1 Mini-Durchsage-Power."""
    raum_id = (data.get('raum_id') or '').strip()
    raum = raeume.get(raum_id)
    if not raum:
        return {'error': 'Raum nicht gefunden'}
    tresen = raum.get('tresen')
    if not tresen:
        return {'error': 'Tresen existiert nicht'}
    if len(tresen['members']) >= tresen.get('max_members', TRESEN_MAX):
        return {'error': 'Tresen ist voll! (max 6 Plätze für Exklusivität)'}
    # 18+ Check analog Tisch
    if tresen.get('adult_only'):
        conn_age = get_db('accounts.db')
        age_row = conn_age.execute('SELECT is_bot, verification_level FROM users WHERE id = ?', (user_id,)).fetchone()
        conn_age.close()
        if not age_row:
            return {'error': 'Dieser Tresen ist ab 18! Bitte registrieren.'}
        # 18+ gilt für ALLE — auch Bots und API-Keys!
        v_level = int(age_row['verification_level'] or 0)
        if v_level < 1:
            return {'error': 'Tresen ab 18 — bitte bei ShinNexus verifizieren (Stufe 1).'}
    # Password-Check
    if tresen.get('password'):
        pw = (data.get('password') or '').strip()
        if pw != tresen['password']:
            return {'error': 'Passwort erforderlich', 'password_required': True}
    # Vision 1 Regel: User kann entweder am Tresen ODER an einem Tisch des Raums sein
    # Owner ist immer überall, also: nur für Non-Owner erzwingen
    user_is_owner = _user_is_owner(user_id)
    if not user_is_owner:
        for tid, t in raum['tische'].items():
            if user_id in t['members']:
                handle_tisch_leave(user_id, {'tisch_id': tid})
    # User aus anderen Tresen ausräumen (nur einer gleichzeitig)
    for other_raum in raeume.values():
        other_tresen = other_raum.get('tresen')
        if other_tresen and other_tresen['id'] != tresen['id'] and user_id in other_tresen['members']:
            _do_tresen_leave(user_id, other_raum)

    conn_acc = get_db('accounts.db')
    user = conn_acc.execute('SELECT name, pq_kem_pub FROM users WHERE id = ?', (user_id,)).fetchone()
    conn_acc.close()
    name = user['name'] if user else '???'

    with raum_lock:
        tresen['members'].add(user_id)
        tresen['member_names'][user_id] = name
        tresen['member_last_active'][user_id] = time.time()
        tresen['last_active'] = time.time()
        if is_windows or is_chromeos:
            tresen['windows_users'].add(user_id)
        # PQ-KEM-Wrap für diesen User (analog Tisch)
        if PQ_AVAILABLE and user and user['pq_kem_pub'] and tresen.get('group_key'):
            try:
                kem_ct, kem_ss = pq_kem_encapsulate(user['pq_kem_pub'])
                nonce = secrets.token_bytes(12)
                wrapped = AESGCM(hashlib.sha256(kem_ss).digest()).encrypt(nonce, tresen['group_key'], b'tresen-groupkey')
                tresen['member_kem_wraps'][user_id] = {
                    'kem_ct': base64.b64encode(kem_ct).decode('ascii'),
                    'nonce': base64.b64encode(nonce).decode('ascii'),
                    'wrapped_key': base64.b64encode(wrapped).decode('ascii'),
                }
            except Exception as e:
                log.warning(f'🔐 KEM-Wrap Tresen für {name} fehlgeschlagen: {e}')

    with chat_lock:
        chat_rooms.setdefault(tresen['id'], []).append({
            'system': True, 'text': f'{name} kommt an den Tresen. 🍻', 'time': time.time()
        })

    log.info(f'🍻 TRESEN-JOIN — {name} → {tresen["id"]} (raum {raum_id})')
    return {'ok': True, 'tresen_id': tresen['id'], 'raum_id': raum_id}


def _do_tresen_leave(user_id, raum):
    """Tresen-Leave — plus: alle Tresen-Subs des Users clearen + Watcher aus anderen Channels entfernen."""
    tresen = raum.get('tresen')
    if not tresen:
        return
    name = tresen['member_names'].get(user_id, '???')
    # 1. Alle Tresen-Subs des Users aus anderen Channels holen + Watcher ggf. entfernen
    subs = _tresen_get(user_id)
    for cid in list(subs.keys()):
        ch, r = _find_channel(cid)
        if ch:
            _tresen_set(user_id, cid, 'off')
            _durchsage_remove_watcher(user_id, ch, r, source='tresen')
    _tresen_clear(user_id)
    # 2. Tresen-Member entfernen
    with raum_lock:
        tresen['members'].discard(user_id)
        tresen['member_names'].pop(user_id, None)
        tresen['member_last_active'].pop(user_id, None)
        tresen['windows_users'].discard(user_id)
        tresen.get('member_kem_wraps', {}).pop(user_id, None)
    with chat_lock:
        chat_rooms.setdefault(tresen['id'], []).append({
            'system': True, 'text': f'{name} verlässt den Tresen. 👋', 'time': time.time()
        })
    log.info(f'🍻 TRESEN-LEAVE — {name} ← {tresen["id"]}')


def handle_tresen_leave(user_id, data):
    raum_id = (data.get('raum_id') or '').strip()
    raum = raeume.get(raum_id)
    if not raum:
        return {'error': 'Raum nicht gefunden'}
    _do_tresen_leave(user_id, raum)
    return {'ok': True}


def _user_is_owner(user_id):
    """Helper: ist User der Owner?"""
    conn = get_db('accounts.db')
    row = conn.execute('SELECT is_owner FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return bool(row and row['is_owner'])


# ═════════════════════════════════════════════════════════════════════════
#  VISION 1 — DURCHSAGE: Multi-Channel-Command-Dashboard für Owner
#  Session-only Subscription-Store (kein DB-Persist, neu bei jedem Login).
# ═════════════════════════════════════════════════════════════════════════

_durchsage_subs = {}           # user_id → {channel_id: {mode: 'read'|'speak', sound: bool}}
_durchsage_subs_lock = threading.Lock()
_tresen_subs = {}              # user_id → {channel_id: {mode, sound}} — lokal pro Raum
_tresen_subs_lock = threading.Lock()


def _durchsage_reset(user_id):
    """Bei Logout/Login frisch — Session-Prinzip."""
    with _durchsage_subs_lock:
        _durchsage_subs.pop(user_id, None)


def _durchsage_get(user_id):
    with _durchsage_subs_lock:
        return dict(_durchsage_subs.get(user_id, {}))


def _durchsage_set(user_id, channel_id, mode, sound=None):
    """mode: 'read' | 'speak' | 'off' (off = unsubscribe)"""
    with _durchsage_subs_lock:
        if mode == 'off':
            if user_id in _durchsage_subs:
                _durchsage_subs[user_id].pop(channel_id, None)
            return
        bucket = _durchsage_subs.setdefault(user_id, {})
        entry = bucket.setdefault(channel_id, {'mode': 'read', 'sound': False})
        entry['mode'] = mode
        if sound is not None:
            entry['sound'] = bool(sound)


def _tresen_reset(user_id):
    """Beim Tresen-Leave / Logout — Tresen-Subs clearen."""
    with _tresen_subs_lock:
        _tresen_subs.pop(user_id, None)


def _tresen_get(user_id):
    with _tresen_subs_lock:
        return dict(_tresen_subs.get(user_id, {}))


def _tresen_set(user_id, channel_id, mode, sound=None):
    with _tresen_subs_lock:
        if mode == 'off':
            if user_id in _tresen_subs:
                _tresen_subs[user_id].pop(channel_id, None)
            return
        bucket = _tresen_subs.setdefault(user_id, {})
        entry = bucket.setdefault(channel_id, {'mode': 'read', 'sound': False})
        entry['mode'] = mode
        if sound is not None:
            entry['sound'] = bool(sound)


def _user_has_watcher_source(user_id, channel_id):
    """True wenn User via EINEM der beiden Stores aktiv auf channel_id subscribed."""
    d = _durchsage_subs.get(user_id, {}).get(channel_id)
    t = _tresen_subs.get(user_id, {}).get(channel_id)
    return bool(d) or bool(t)


def _all_channel_ids():
    """Alle aktuell existierenden Channels (Tische + Tresen)."""
    out = []
    for r in raeume.values():
        for tid in r.get('tische', {}):
            out.append(tid)
        if r.get('tresen'):
            out.append(r['tresen']['id'])
    return out


def _user_tresen_raum(user_id):
    """Returns raum_id wenn User an einem Tresen sitzt, sonst None."""
    for r in raeume.values():
        tresen = r.get('tresen')
        if tresen and user_id in tresen.get('members', set()):
            return r['id']
    return None


def handle_durchsage_subscribe(user_id, data):
    """Vision 1 SPLIT: Durchsage-Subscribe — Owner-only, globaler Scope, is_owner_voice=true.
    Body: {channel_id, mode, password?, sound?}
    """
    if not _user_is_owner(user_id):
        return {'error': 'Durchsage-Tab nur für Owner', '_status': 403}
    channel_id = (data.get('channel_id') or '').strip()
    mode = (data.get('mode') or 'read').strip()
    provided_pw = data.get('password') or ''
    if mode not in ('read', 'speak', 'off'):
        return {'error': 'mode muss read|speak|off sein', '_status': 400}
    ch, raum = _find_channel(channel_id)
    if not ch:
        return {'error': 'Kanal nicht gefunden', '_status': 404}

    if mode == 'off':
        _durchsage_set(user_id, channel_id, 'off')
        _durchsage_remove_watcher(user_id, ch, raum, source='durchsage')
        return {'ok': True, 'channel_id': channel_id, 'mode': 'off'}

    already_in = (user_id in ch.get('members', set())
                  or user_id in ch.get('durchsage_watchers', set()))
    if not already_in:
        if ch.get('password') and provided_pw != ch['password']:
            return {
                'error': 'Passwort erforderlich' if not provided_pw else 'Passwort falsch',
                'password_required': True,
                '_status': 403,
            }
        _durchsage_add_watcher(user_id, ch, raum)
    _durchsage_set(user_id, channel_id, mode, sound=data.get('sound'))
    return {'ok': True, 'channel_id': channel_id, 'mode': mode}


def handle_tresen_subscribe(user_id, data):
    """Vision 1 SPLIT: Tresen-Subscribe — jeder Tresen-Sitzer, nur eigener Raum, is_owner_voice=false.
    Body: {channel_id, mode, password?, sound?}
    """
    tresen_raum = _user_tresen_raum(user_id)
    if not tresen_raum:
        return {'error': 'Du musst am Tresen sitzen', '_status': 403}
    channel_id = (data.get('channel_id') or '').strip()
    mode = (data.get('mode') or 'read').strip()
    provided_pw = data.get('password') or ''
    if mode not in ('read', 'speak', 'off'):
        return {'error': 'mode muss read|speak|off sein', '_status': 400}
    ch, raum = _find_channel(channel_id)
    if not ch:
        return {'error': 'Kanal nicht gefunden', '_status': 404}
    if not raum or raum['id'] != tresen_raum:
        return {'error': 'Tresen-Sub nur für Tische deines eigenen Raums', '_status': 403}

    if mode == 'off':
        _tresen_set(user_id, channel_id, 'off')
        _durchsage_remove_watcher(user_id, ch, raum, source='tresen')
        return {'ok': True, 'channel_id': channel_id, 'mode': 'off'}

    already_in = (user_id in ch.get('members', set())
                  or user_id in ch.get('durchsage_watchers', set()))
    if not already_in:
        if ch.get('password') and provided_pw != ch['password']:
            return {
                'error': 'Passwort erforderlich' if not provided_pw else 'Passwort falsch',
                'password_required': True,
                '_status': 403,
            }
        _durchsage_add_watcher(user_id, ch, raum)
    _tresen_set(user_id, channel_id, mode, sound=data.get('sound'))
    return {'ok': True, 'channel_id': channel_id, 'mode': mode}


def _durchsage_add_watcher(user_id, ch, raum):
    """User als Durchsage-Watcher zu Channel hinzufügen (KEM-Wrap + System-Message)."""
    conn_acc = get_db('accounts.db')
    user_row = conn_acc.execute('SELECT name, pq_kem_pub FROM users WHERE id = ?', (user_id,)).fetchone()
    conn_acc.close()
    name = user_row['name'] if user_row else 'Owner'
    with raum_lock:
        ch.setdefault('durchsage_watchers', set()).add(user_id)
        ch.setdefault('member_names', {})[user_id] = name
        ch.setdefault('member_last_active', {})[user_id] = time.time()
        # KEM-Wrap für den Watcher (kriegt gleichen Group-Key wie echte Members)
        if PQ_AVAILABLE and user_row and user_row['pq_kem_pub'] and ch.get('group_key'):
            try:
                kem_ct, kem_ss = pq_kem_encapsulate(user_row['pq_kem_pub'])
                nonce = secrets.token_bytes(12)
                aad_tag = b'tresen-groupkey' if _is_tresen_id(ch.get('id', '')) else b'tisch-groupkey'
                wrapped = AESGCM(hashlib.sha256(kem_ss).digest()).encrypt(nonce, ch['group_key'], aad_tag)
                ch.setdefault('member_kem_wraps', {})[user_id] = {
                    'kem_ct': base64.b64encode(kem_ct).decode('ascii'),
                    'nonce': base64.b64encode(nonce).decode('ascii'),
                    'wrapped_key': base64.b64encode(wrapped).decode('ascii'),
                }
            except Exception as e:
                log.warning(f'🔐 KEM-Wrap Watcher für {name} fehlgeschlagen: {e}')
    with chat_lock:
        chat_rooms.setdefault(ch['id'], []).append({
            'system': True,
            'text': f'📢 {name} hört mit (Durchsage).',
            'time': time.time(),
        })
    log.info(f'📢 WATCHER-ADD — {name} → {ch["id"]}')


def _durchsage_remove_watcher(user_id, ch, raum, source='durchsage'):
    """Watcher verlassen — aber nur wenn aus BEIDEN Stores raus.
    Source ∈ {'durchsage', 'tresen'}: gibt Hinweis ob Durchsage- oder Tresen-Leave auslöst.
    Wenn User noch in anderem Store aktiv → Watcher bleibt (Source-Counting).
    """
    if user_id not in ch.get('durchsage_watchers', set()):
        return
    # Prüfen: ist User noch in einem der beiden Stores aktiv für diesen Channel?
    # (Nach dem caller schon aus einem Store entfernt hat — also check gegen verbleibenden)
    if _user_has_watcher_source(user_id, ch['id']):
        return  # noch im anderen Store → Watcher bleibt
    name = ch.get('member_names', {}).get(user_id, '???')
    with raum_lock:
        ch['durchsage_watchers'].discard(user_id)
        if user_id not in ch.get('members', set()):
            ch.get('member_names', {}).pop(user_id, None)
            ch.get('member_last_active', {}).pop(user_id, None)
            ch.get('member_kem_wraps', {}).pop(user_id, None)
    with chat_lock:
        chat_rooms.setdefault(ch['id'], []).append({
            'system': True,
            'text': f'📢 {name} geht.',
            'time': time.time(),
        })
    log.info(f'📢 WATCHER-REMOVE ({source}) — {name} ← {ch["id"]}')


def _durchsage_clear(user_id):
    with _durchsage_subs_lock:
        _durchsage_subs.pop(user_id, None)


def _tresen_clear(user_id):
    with _tresen_subs_lock:
        _tresen_subs.pop(user_id, None)


def _bulk_select_common(user_id, data, store_set_fn, store_get_fn, store_clear_fn, scope_raum=None):
    """Gemeinsame Bulk-Logik: Durchsage (global) oder Tresen (scope auf eigenen Raum)."""
    action = (data.get('action') or '').strip()
    raum_id = (data.get('raum_id') or '').strip()
    targets = []
    source_tag = 'tresen' if scope_raum else 'durchsage'

    if scope_raum:
        if action in ('select_all', 'tables_only'):
            action = 'room_tables'
            raum_id = scope_raum
        elif action == 'rooms_only':
            targets = [raeume[scope_raum]['tresen']['id']] if scope_raum in raeume else []
        elif action == 'room_tables':
            raum_id = scope_raum

    if action == 'select_all':
        targets = _all_channel_ids()
    elif action == 'rooms_only' and not targets:
        targets = [r['tresen']['id'] for r in raeume.values() if r.get('tresen')]
    elif action == 'tables_only':
        for r in raeume.values():
            targets.extend(r.get('tische', {}).keys())
    elif action == 'room_tables':
        r = raeume.get(raum_id)
        if not r:
            return {'error': 'Raum nicht gefunden', '_status': 404}
        targets = list(r.get('tische', {}).keys())
    elif action == 'deselect_all':
        subs_before = store_get_fn(user_id)
        for cid in list(subs_before.keys()):
            ch, raum = _find_channel(cid)
            if ch:
                store_set_fn(user_id, cid, 'off')
                _durchsage_remove_watcher(user_id, ch, raum, source=source_tag)
        store_clear_fn(user_id)
        return {'ok': True, 'count': 0}
    elif action not in ('select_all', 'rooms_only'):
        return {'error': 'Unbekannte action', '_status': 400}

    count = 0
    for cid in targets:
        ch, raum = _find_channel(cid)
        if not ch:
            continue
        if ch.get('password') and user_id not in ch.get('members', set()):
            continue
        already_in = (user_id in ch.get('members', set())
                      or user_id in ch.get('durchsage_watchers', set()))
        if not already_in:
            _durchsage_add_watcher(user_id, ch, raum)
        store_set_fn(user_id, cid, 'read')
        count += 1
    return {'ok': True, 'count': count}


def handle_durchsage_bulk(user_id, data):
    """Owner-only Bulk (global)."""
    if not _user_is_owner(user_id):
        return {'error': 'Durchsage-Tab nur für Owner', '_status': 403}
    return _bulk_select_common(user_id, data, _durchsage_set, _durchsage_get, _durchsage_clear)


def handle_tresen_bulk(user_id, data):
    """Tresen-Sitzer Bulk (scope eigener Raum)."""
    tresen_raum = _user_tresen_raum(user_id)
    if not tresen_raum:
        return {'error': 'Du musst am Tresen sitzen', '_status': 403}
    return _bulk_select_common(user_id, data, _tresen_set, _tresen_get, _tresen_clear, scope_raum=tresen_raum)


def handle_durchsage_state(user_id):
    """Durchsage-Subscriptions zurückgeben (Owner-only, global)."""
    if not _user_is_owner(user_id):
        return {'error': 'Durchsage-Tab nur für Owner', '_status': 403}
    subs = _durchsage_get(user_id)
    # Reichere die Subscription-Info an mit Channel-Metadaten (für UI)
    enriched = {}
    for cid, info in subs.items():
        ch, raum = _find_channel(cid)
        if not ch:
            continue  # Channel ist weg (despawnt?) — wird rausgefiltert
        enriched[cid] = {
            'mode': info['mode'],
            'sound': info.get('sound', False),
            'kind': 'tresen' if _is_tresen_id(cid) else 'tisch',
            'raum_id': (raum['id'] if raum else None),
            'raum_name': (raum['name'] if raum else ''),
            'member_count': len(ch.get('members', set())),
            'label': _channel_label(ch, raum),
        }
    return {'ok': True, 'subscriptions': enriched}


def _channel_label(ch, raum):
    """Menschenlesbarer Channel-Name für UI."""
    if not ch:
        return '?'
    if _is_tresen_id(ch.get('id', '')):
        return f'{raum["name"] if raum else "Raum"} · Tresen 🍻' if raum else 'Tresen'
    thema = ch.get('thema', '?')
    energie = ch.get('energie', {}).get('emoji', '')
    return f'{thema}{energie}'


def handle_tresen_state(user_id):
    """Tresen-Subscriptions zurückgeben (Tresen-Sitzer)."""
    tresen_raum = _user_tresen_raum(user_id)
    if not tresen_raum:
        return {'error': 'Du musst am Tresen sitzen', '_status': 403}
    subs = _tresen_get(user_id)
    enriched = {}
    for cid, info in subs.items():
        ch, raum = _find_channel(cid)
        if not ch:
            continue
        enriched[cid] = {
            'mode': info['mode'],
            'sound': info.get('sound', False),
            'kind': 'tresen' if _is_tresen_id(cid) else 'tisch',
            'raum_id': (raum['id'] if raum else None),
            'raum_name': (raum['name'] if raum else ''),
            'member_count': len(ch.get('members', set())),
            'label': _channel_label(ch, raum),
        }
    return {'ok': True, 'subscriptions': enriched, 'tresen_raum_id': tresen_raum}


def handle_durchsage_stream(user_id, since):
    """Owner-only Aggregator-Stream. Nur Channels aus _durchsage_subs."""
    if not _user_is_owner(user_id):
        return {'error': 'Durchsage-Tab nur für Owner', '_status': 403}
    subs = _durchsage_get(user_id)
    events = []
    for cid in list(subs.keys()):
        ch, raum = _find_channel(cid)
        if not ch:
            continue
        label = _channel_label(ch, raum)
        for m in chat_rooms.get(cid, []):
            if m.get('time', 0) <= since:
                continue
            msg = dict(m)
            if not msg.get('_tts_ready', True):
                msg['tts_url'] = None
            msg.pop('_tts_ready', None)
            events.append({
                'channel_id': cid,
                'channel_label': label,
                'raum_id': (raum['id'] if raum else ''),
                'raum_name': (raum['name'] if raum else ''),
                'kind': 'tresen' if _is_tresen_id(cid) else 'tisch',
                'msg': msg,
            })
    events.sort(key=lambda e: e['msg'].get('time', 0))
    return events


def handle_tresen_stream(user_id, since):
    """Tresen-Sitzer-Aggregator — eigener Store + eigener Tresen-Chat."""
    tresen_raum = _user_tresen_raum(user_id)
    if not tresen_raum:
        return {'error': 'Du musst am Tresen sitzen', '_status': 403}
    subs = _tresen_get(user_id)
    tresen_id = f'tresen_{tresen_raum}'
    channel_ids = set(subs.keys()) | {tresen_id}
    events = []
    for cid in channel_ids:
        ch, raum = _find_channel(cid)
        if not ch:
            continue
        label = _channel_label(ch, raum)
        for m in chat_rooms.get(cid, []):
            if m.get('time', 0) <= since:
                continue
            msg = dict(m)
            if not msg.get('_tts_ready', True):
                msg['tts_url'] = None
            msg.pop('_tts_ready', None)
            events.append({
                'channel_id': cid,
                'channel_label': label,
                'raum_id': (raum['id'] if raum else ''),
                'raum_name': (raum['name'] if raum else ''),
                'kind': 'tresen' if _is_tresen_id(cid) else 'tisch',
                'msg': msg,
            })
    events.sort(key=lambda e: e['msg'].get('time', 0))
    return events


# ============================================================================
# BROADCAST-FIX 2026-04-21: _broadcast_chat_send
# Ein Input (Text oder Voice) → 1x Voice-Save, 1x TTS, 1x Whisper
# → dieselbe msg-Reference in alle Target-Channels einfügen.
# Ersetzt N-fachen handle_chat_send in Tresen/Durchsage (vorher: N TTS/Whisper
# parallel, überlastete den Voice-Server, Transkript kam nicht an).
# Voice-Messages werden erst appendet wenn Whisper fertig ist (oder Timeout).
# Text-Messages werden erst appendet wenn TTS fertig ist.
# ============================================================================
def _broadcast_chat_send(user_id, text, voice_data, voice_input, channel_ids,
                         is_owner_voice=False, encrypted_payload=None):
    # Access-Check: nur Channels wo user Member oder Durchsage-Watcher ist
    valid_channels = []
    errors = []
    for cid in channel_ids:
        ch, _ = _find_channel(cid)
        if not ch:
            errors.append({'channel_id': cid, 'error': 'gone'})
            continue
        if user_id not in ch.get('members', set()) and user_id not in ch.get('durchsage_watchers', set()):
            errors.append({'channel_id': cid, 'error': 'no access'})
            continue
        valid_channels.append((cid, ch))
    if not valid_channels:
        return {'ok': False, 'sent_to': [], 'errors': errors or [{'error': 'Keine gültigen Channels'}]}

    first_cid, first_ch = valid_channels[0]
    name = first_ch.get('member_names', {}).get(user_id, '???')
    now = time.time()

    # 1. VOICE 1x speichern (wenn mitgeschickt)
    voice_url = None
    voice_fpath = None
    if voice_data and voice_data.startswith('data:audio'):
        try:
            ext = 'webm' if 'webm' in voice_data.split(',')[0] else 'mp4'
            raw = base64.b64decode(voice_data.split(',')[1])
            if 100 < len(raw) < 2 * 1024 * 1024:
                file_id = str(uuid.uuid4())[:8]
                voice_fpath = os.path.join(VOICE_DIR, f'chat_{file_id}.{ext}')
                with open(voice_fpath, 'wb') as f:
                    f.write(raw)
                voice_url = f'/api/chat-file/{file_id}.{ext}'
                log.info(f'🎤 BROADCAST-VOICE — {name}: {len(raw)} bytes → {len(valid_channels)} ch')
                if not text:
                    text = '🎤 ...'
        except Exception as e:
            log.error(f'❌ Broadcast-Voice Fehler: {e}')

    # 2. MSG bauen (SHARED REFERENCE — eine Instanz in allen chat_rooms!)
    msg = {
        'user_id': user_id, 'user': name, 'text': text, 'time': now,
        'file_url': voice_url,
        'filetype': 'audio/webm' if voice_url else None,
        'filename': 'Voice' if voice_url else None,
        'tts_url': None,
    }
    if is_owner_voice:
        msg['is_owner_voice'] = True
    if encrypted_payload and isinstance(encrypted_payload, dict):
        msg['encrypted'] = {
            'nonce': encrypted_payload.get('nonce', ''),
            'ciphertext': encrypted_payload.get('ciphertext', ''),
            'alg': encrypted_payload.get('alg', 'AES-256-GCM'),
        }

    # 3. TTS 1x starten (nur wenn KEIN Voice-Input)
    tts_url = None
    if not voice_url and not voice_input:
        conn_acc2 = get_db('accounts.db')
        user_row = conn_acc2.execute('SELECT tts_voice, is_guest FROM users WHERE id = ?', (user_id,)).fetchone()
        conn_acc2.close()
        voice_id = user_row['tts_voice'] if user_row and user_row['tts_voice'] else 'de-DE-ConradNeural'
        _is_guest = bool(user_row and user_row['is_guest'])
        tts_id = f'chat_{str(uuid.uuid4())[:8]}'
        tts_path = os.path.join(VOICE_DIR, f'{tts_id}')

        def _gen_broadcast_tts():
            _guest_bark_ok = _is_guest and _guest_config().get('bark_enabled', False)
            if (not _is_guest or _guest_bark_ok) and _voice_server_available():
                bark_voice = voice_id if not voice_id.startswith('de-') else 'mann1'
                audio_b64 = _bark_generate(text, bark_voice)
                if audio_b64:
                    try:
                        raw = base64.b64decode(audio_b64.split(',')[1])
                        with open(tts_path + '.wav', 'wb') as f:
                            f.write(raw)
                        msg['_tts_ready'] = True
                        log.info(f'🔊 BARK-TTS broadcast — "{text[:30]}..." [{bark_voice}]')
                        return
                    except Exception as e:
                        log.error(f'❌ Bark-Save Fehler: {e}')
            if voice_id.startswith('de-'):
                edge_voice = voice_id
            elif voice_id.startswith('frau') or voice_id == 'frau_sanft' or voice_id == 'mann2' or 'girl' in voice_id or 'frau' in voice_id:
                edge_voice = 'de-DE-KatjaNeural'
            else:
                edge_voice = 'de-DE-ConradNeural'
            try:
                tmp_mp3 = tts_path + '.mp3.tmp'
                communicate = edge_tts.Communicate(text, edge_voice)
                loop = asyncio.new_event_loop()
                loop.run_until_complete(communicate.save(tmp_mp3))
                loop.close()
                wav_file = tts_path + '.wav'
                import subprocess
                subprocess.run(['ffmpeg', '-y', '-i', tmp_mp3, wav_file], capture_output=True)
                if os.path.exists(wav_file) and os.path.getsize(wav_file) > 100:
                    try: os.remove(tmp_mp3)
                    except: pass
                    msg['_tts_ready'] = True
                    log.info(f'🔊 EDGE-TTS broadcast WAV — "{text[:30]}..."')
                else:
                    os.rename(tmp_mp3, tts_path + '.mp3')
                    msg['_tts_ready'] = True
                    log.info(f'🔊 EDGE-TTS broadcast MP3 — "{text[:30]}..."')
            except Exception as e:
                msg['_tts_ready'] = True
                log.error(f'❌ Broadcast-TTS Fehler: {e}')
        threading.Thread(target=_gen_broadcast_tts, daemon=True).start()
        tts_url = f'/api/bierdeckel/voice/{tts_id}'
        msg['tts_url'] = tts_url

    # 4. APPEND: Text → warte TTS. Voice → warte Whisper. Sonst → sofort.
    def _append_all():
        with chat_lock:
            for cid, ch in valid_channels:
                room = chat_rooms.setdefault(cid, [])
                room.append(msg)  # SHARED REFERENCE
                if len(room) > 200:
                    chat_rooms[cid] = room[-200:]
                ch['last_active'] = now
                if 'member_last_active' in ch:
                    ch['member_last_active'][user_id] = now

    if tts_url:
        def _append_after_tts_ready():
            for _ in range(1200):  # 120s timeout (Bark kann lang brauchen)
                time.sleep(0.1)
                if msg.get('_tts_ready'):
                    break
            _append_all()
        threading.Thread(target=_append_after_tts_ready, daemon=True).start()
    elif voice_url and _voice_server_available() and voice_fpath and os.path.exists(voice_fpath):
        def _append_after_whisper():
            try:
                whisper_text = _whisper_transcribe(voice_fpath, tisch_id=first_cid)
                if whisper_text:
                    msg['text'] = whisper_text
                    log.info(f'🎤 Whisper broadcast: "{whisper_text[:60]}" → {len(valid_channels)} ch')
            except Exception as e:
                log.error(f'❌ Whisper-Broadcast Fehler: {e}')
            _append_all()
        threading.Thread(target=_append_after_whisper, daemon=True).start()
    else:
        # Kein TTS, kein Whisper — sofort appenden (Platzhalter bleibt ggf.)
        _append_all()

    sent_optimistic = [cid for cid, _ in valid_channels]
    return {'ok': True, 'sent_to': sent_optimistic, 'errors': errors, 'msg': msg}


def handle_tresen_send(user_id, data):
    """Tresen-Sitzer sendet: in Tresen-Chat + speak-Tische des eigenen Raums.
    KEIN is_owner_voice (auch Owner spricht hier als normaler Tresen-Sitzer).
    BROADCAST-FIX 2026-04-21: nutzt _broadcast_chat_send (1x Voice/TTS/Whisper).
    """
    tresen_raum = _user_tresen_raum(user_id)
    if not tresen_raum:
        return {'error': 'Du musst am Tresen sitzen', '_status': 403}
    text = (data.get('text') or '').strip()
    voice_data = data.get('voice', '')
    voice_input = data.get('voice_input', False)
    if not text and not voice_data:
        return {'error': 'Kein Text oder Voice', '_status': 400}
    tresen_id = f'tresen_{tresen_raum}'
    subs = _tresen_get(user_id)
    speak_targets = [cid for cid, info in subs.items()
                     if info.get('mode') == 'speak' and cid != tresen_id]
    # Tresen-Chat IMMER zuerst, dann speak-Tische
    all_targets = [tresen_id] + speak_targets
    return _broadcast_chat_send(user_id, text, voice_data, voice_input,
                                all_targets, is_owner_voice=False,
                                encrypted_payload=data.get('encrypted_payload'))


def handle_durchsage_send(user_id, data):
    """Owner-only: Durchsage-Send mit is_owner_voice=True (gold markiert).
    BROADCAST-FIX 2026-04-21: nutzt _broadcast_chat_send (1x Voice/TTS/Whisper).
    """
    if not _user_is_owner(user_id):
        return {'error': 'Durchsage-Tab nur für Owner', '_status': 403}
    text = (data.get('text') or '').strip()
    voice_data = data.get('voice', '')
    voice_input = data.get('voice_input', False)
    if not text and not voice_data:
        return {'error': 'Kein Text oder Voice', '_status': 400}
    subs = _durchsage_get(user_id)
    speak_targets = [cid for cid, info in subs.items() if info.get('mode') == 'speak']
    if not speak_targets:
        return {'error': 'Keine Ziele mit "speak"-Modus ausgewählt', '_status': 400}
    # User ist bei subscribe als durchsage_watcher eingetragen → Access OK.
    return _broadcast_chat_send(user_id, text, voice_data, voice_input,
                                speak_targets, is_owner_voice=True,
                                encrypted_payload=data.get('encrypted_payload'))


def can_access_channel(user_id, channel_id, for_subscribe=False):
    """Zugriffs-Check für Tisch/Tresen. Vision 1:
    - Physisches Member (`members`) → Vollzugriff
    - Durchsage-Watcher (`durchsage_watchers`) → Vollzugriff (sichtbar in Member-Liste!)
    - Passwort-Channel: nur echte Members UND watchers die bewusst mit PW reingekommen sind
    """
    ch, _ = _find_channel(channel_id)
    if not ch:
        return False
    # Mitglied (physisch) oder Durchsage-Watcher (hat ggf. PW eingegeben)
    if user_id in ch.get('members', set()):
        return True
    if user_id in ch.get('durchsage_watchers', set()):
        return True
    # Nicht drin → kein Zugriff (auch Owner nicht! Er muss erst subscriben)
    return False

def handle_tisch_join(user_id, data, is_windows=False, is_chromeos=False):
    """An einen Tisch setzen"""
    tisch_id = data.get('tisch_id')
    t, raum = _find_tisch(tisch_id)
    if not t:
        return {'error': 'Tisch nicht gefunden'}
    # Max-Check nur auf physische members (durchsage_watchers zählen nicht!)
    if len(t['members']) >= TISCH_MAX:
        return {'error': 'Tisch ist voll!'}
    # 18+ Check — nur ShinNexus-verifizierte User (Stufe 1+) an adult_only Tische
    if t.get('adult_only'):
        conn_age = get_db('accounts.db')
        age_row = conn_age.execute('SELECT is_bot, is_guest, verification_level FROM users WHERE id = ?', (user_id,)).fetchone()
        conn_age.close()
        if not age_row:
            return {'error': 'Dieser Tisch ist ab 18! Bitte registrieren.'}
        # 18+ gilt für ALLE — auch Bots und API-Keys! Keine Ausnahme!
        v_level = int(age_row['verification_level'] or 0)
        if v_level < 1:
            return {'error': 'Dieser Tisch ist ab 18! Bitte bei ShinNexus verifizieren (Stufe 1).'}
    # Passwort-Check (wenn gesetzt)
    if t.get('password'):
        pw = (data.get('password') or '').strip()
        if pw != t['password']:
            return {'error': 'Passwort erforderlich', 'password_required': True}

    # User schon irgendwo? Erst aufstehen!
    for r in raeume.values():
        for tid, tisch in r['tische'].items():
            if user_id in tisch['members']:
                handle_tisch_leave(user_id, {'tisch_id': tid})

    conn_acc = get_db('accounts.db')
    user = conn_acc.execute('SELECT name, pq_kem_pub FROM users WHERE id = ?', (user_id,)).fetchone()
    conn_acc.close()
    name = user['name'] if user else '???'

    with raum_lock:
        t['members'].add(user_id)
        t['member_names'][user_id] = name
        t['member_last_active'][user_id] = time.time()
        t['last_active'] = time.time()
        t['empty_since'] = None  # Tisch wieder bewohnt → Renew-Timer stoppen
        if is_windows or is_chromeos:
            t['windows_users'].add(user_id)
        # Visit-Tracking für Raum (1h-Regel)
        raum['visit_start'][user_id] = time.time()
        # PQ-Phase 4: Gruppen-Key mit User-KEM-Public wrappen (ML-KEM-768)
        # Danach über /api/tisch/key abrufbar — Client entschlüsselt im Browser.
        if PQ_AVAILABLE and user and user['pq_kem_pub'] and t.get('group_key'):
            try:
                kem_ct, kem_ss = pq_kem_encapsulate(user['pq_kem_pub'])
                # Gruppen-Key mit shared secret AES-GCM wrappen
                nonce = secrets.token_bytes(12)
                wrapped = AESGCM(hashlib.sha256(kem_ss).digest()).encrypt(nonce, t['group_key'], b'tisch-groupkey')
                t['member_kem_wraps'][user_id] = {
                    'kem_ct': base64.b64encode(kem_ct).decode('ascii'),
                    'nonce': base64.b64encode(nonce).decode('ascii'),
                    'wrapped_key': base64.b64encode(wrapped).decode('ascii'),
                }
            except Exception as e:
                log.warning(f'🔐 KEM-Wrap für {name} fehlgeschlagen: {e}')

    with chat_lock:
        chat_rooms.setdefault(tisch_id, []).append({
            'system': True, 'text': f'{name} setzt sich hin. 🪑', 'time': time.time()
        })
        if is_windows:
            chat_rooms[tisch_id].append({
                'system': True,
                'text': f'🔴 Sie werden überwacht von: Microsoft — {name} nutzt Windows. Desktop wird aufgezeichnet und an Microsoft gesendet. Dieser Tisch ist NICHT mehr privat.',
                'time': time.time()
            })
        elif is_chromeos:
            chat_rooms[tisch_id].append({
                'system': True,
                'text': f'🔴 Sie werden überwacht von: Google — {name} nutzt ChromeOS. Telemetrie aktiv. Dieser Tisch ist NICHT mehr privat.',
                'time': time.time()
            })

    log.info(f'🪑 HINGESETZT — {name} → {tisch_id} [{t["thema"]}{t["energie"]["emoji"]}]{" [🔴 WINDOWS]" if is_windows else ""}')
    return {'ok': True, 'tisch_id': tisch_id, 'raum_id': raum['id']}

def handle_tisch_leave(user_id, data):
    """Vom Tisch aufstehen"""
    tisch_id = data.get('tisch_id')
    t, raum = _find_tisch(tisch_id)
    if not t:
        return {'error': 'Tisch nicht gefunden'}

    name = t['member_names'].get(user_id, '???')
    was_windows = user_id in t.get('windows_users', set())

    with raum_lock:
        t['members'].discard(user_id)
        t['member_names'].pop(user_id, None)
        t['member_last_active'].pop(user_id, None)
        t['windows_users'].discard(user_id)
        # Tisch gerade leer geworden? Renew-Timer starten.
        if not t['members'] and t.get('empty_since') is None:
            t['empty_since'] = time.time()
        # Visit-Zeit berechnen
        if user_id in raum['visit_start']:
            visit_time = time.time() - raum['visit_start'].pop(user_id)
            raum['visit_log'][user_id] = raum['visit_log'].get(user_id, 0) + visit_time

    with chat_lock:
        chat_rooms.setdefault(tisch_id, []).append({
            'system': True, 'text': f'{name} steht auf. 👋', 'time': time.time()
        })
        if was_windows and len(t.get('windows_users', set())) == 0:
            chat_rooms[tisch_id].append({
                'system': True,
                'text': '🟢 Kein Windows-User mehr am Tisch. Privatsphäre wiederhergestellt.',
                'time': time.time()
            })

    log.info(f'👋 AUFGESTANDEN — {name} ← {tisch_id}')
    return {'ok': True}

# ── Anti-Spam V5.3 — mit Wohlverhaltens-Phase ──────────────────
_spam_tracker: dict = {}         # {user_id: [timestamp, timestamp, ...]}
_spam_warnings: dict = {}        # {user_id: warning_count}
_spam_bans: dict = {}            # {user_id: ban_until_timestamp}
_spam_last_warning: dict = {}    # {user_id: timestamp der letzten Warnung / letzten Bans}
SPAM_WINDOW = 30  # Sekunden
SPAM_MAX_MSGS = 6  # Max Messages in SPAM_WINDOW (5 gehen durch, 6. ist Ban)
SPAM_ESCALATION = [
    300,       # Stufe 1: 5 Minuten Timeout
    259200,    # Stufe 2: 3 Tage Ban
    2592000,   # Stufe 3: 30 Tage Ban
    31536000,  # Stufe 4: 365 Tage Ban
]

# Wohlverhaltens-Phase: Nach einer Zeitspanne ohne neue Warnung wird der
# Warning-Zähler um eins reduziert. So kann sich ein User, der sich wieder
# benimmt, Stufe für Stufe zurückarbeiten. Verhältnismäßigkeit statt
# lebenslange Brandmarkung.
SPAM_GOOD_BEHAVIOR_SECONDS = 24 * 3600   # 24 Stunden ohne neue Warnung: Warning -1


def _spam_apply_good_behavior(user_id: str, now: float) -> None:
    """
    Reduziert den Warning-Zähler um 1, wenn der User seit mindestens
    SPAM_GOOD_BEHAVIOR_SECONDS keine neue Warnung bekommen hat. Falls nötig,
    läuft das in mehreren Schritten (eine Warnung pro abgelaufenes Fenster).
    """
    warnings = _spam_warnings.get(user_id, 0)
    if warnings <= 0:
        return
    last_warning = _spam_last_warning.get(user_id, 0)
    if last_warning <= 0:
        # Noch nie eine Warnung registriert → nichts zu tun
        return
    elapsed = now - last_warning
    if elapsed < SPAM_GOOD_BEHAVIOR_SECONDS:
        return

    # So viele volle 24h-Fenster sind seit der letzten Warnung vergangen
    steps = int(elapsed // SPAM_GOOD_BEHAVIOR_SECONDS)
    if steps <= 0:
        return
    new_warnings = max(0, warnings - steps)
    if new_warnings != warnings:
        _spam_warnings[user_id] = new_warnings
        if new_warnings == 0:
            # Vollständig gereinigt — last_warning-Eintrag löschen damit er nicht
            # als "aktive Strafakte" weiter mitgezählt wird
            _spam_last_warning.pop(user_id, None)
            log.info(f'✨ SPAM GOOD BEHAVIOR — User {user_id} komplett rehabilitiert ({steps} Stufen abgebaut)')
        else:
            # Neuen "virtuellen" last_warning-Zeitpunkt setzen, damit der
            # Abbau gleichmäßig weiterläuft
            _spam_last_warning[user_id] = last_warning + (steps * SPAM_GOOD_BEHAVIOR_SECONDS)
            log.info(f'✨ SPAM GOOD BEHAVIOR — User {user_id}: Warning {warnings} → {new_warnings}')


def _check_spam(user_id: str) -> str | None:
    """Prüft ob User spammt. Returns Error-String oder None."""
    now = time.time()

    # Wohlverhaltens-Phase: Warning-Zähler graduell abbauen bei gutem Verhalten
    _spam_apply_good_behavior(user_id, now)

    # Gebannt? → Sofort raus, NICHT im Tracker zählen!
    ban_until = _spam_bans.get(user_id, 0)
    if now < ban_until:
        remaining = int(ban_until - now)
        if remaining > 86400:
            return f'Gesperrt für {remaining // 86400} Tage'
        elif remaining > 3600:
            return f'Gesperrt für {remaining // 3600} Stunden'
        else:
            return f'Gesperrt für {remaining // 60} Minuten'

    # Timestamps aufräumen (nur letzte SPAM_WINDOW Sekunden)
    timestamps = _spam_tracker.get(user_id, [])
    timestamps = [t for t in timestamps if now - t < SPAM_WINDOW]
    timestamps.append(now)
    _spam_tracker[user_id] = timestamps

    # Spam erkannt?
    if len(timestamps) > SPAM_MAX_MSGS:
        warnings = _spam_warnings.get(user_id, 0)
        if warnings < len(SPAM_ESCALATION):
            ban_duration = SPAM_ESCALATION[warnings]
        else:
            ban_duration = SPAM_ESCALATION[-1]
        _spam_bans[user_id] = now + ban_duration
        _spam_warnings[user_id] = warnings + 1
        _spam_last_warning[user_id] = now  # Zeitstempel für Wohlverhaltens-Phase
        _spam_tracker[user_id] = []  # Reset tracker
        level = warnings + 1
        log.warning(f'🚨 SPAM BAN — User {user_id} Stufe {level} ({ban_duration}sec)')
        if ban_duration >= 86400:
            return f'Spam-Sperre Stufe {level}: {ban_duration // 86400} Tage'
        elif ban_duration >= 3600:
            return f'Spam-Sperre Stufe {level}: {ban_duration // 3600} Stunden'
        else:
            return f'Spam-Sperre Stufe {level}: {ban_duration // 60} Minuten'

    return None


def handle_chat_send(user_id, data):
    """Nachricht an Tisch senden"""
    # Anti-Spam Check (nur für API-Bots, normale User sind zu langsam)
    if data.get('_via_api'):
        spam_error = _check_spam(user_id)
        if spam_error:
            return {'error': spam_error, 'spam': True}

    tisch_id = data.get('tisch_id')
    text = (data.get('text') or '').strip()
    voice_data = data.get('voice', '')
    # Gast-Check: Voice nur wenn Owner freigeschaltet!
    if is_guest_user(user_id) and voice_data and not _guest_config().get('voice_enabled', False):
        return {'error': 'Gäste dürfen gucken, nicht anfassen! 👀'}
    # Leerer Text erlaubt wenn Voice dabei (Whisper transkribiert!)
    if not text and not voice_data:
        return {'error': 'Kein Text!'}
    is_voice = voice_data or data.get('voice_input')
    if len(text) > 200 and not is_voice:
        text = text[:200]  # Hart kappen für Text-Eingabe, Voice = kein Limit!

    # Chat-Send geht auf Tisch ODER Tresen (Vision 1)
    t, raum = _find_channel(tisch_id)
    if not t:
        return {'error': 'Kanal nicht gefunden'}
    # Member ODER Durchsage-Watcher darf senden
    if user_id not in t.get('members', set()) and user_id not in t.get('durchsage_watchers', set()):
        return {'error': 'Du sitzt nicht an diesem Kanal!'}

    # V4.2: API-Calls (Bots) = max 1 Nachricht, dann warten bis TTS fertig!
    if data.get('_via_api'):
        with chat_lock:
            msgs = chat_rooms.get(tisch_id, [])
            for m in reversed(msgs):
                if m.get('system'):
                    continue
                if m.get('user_id') == user_id and not m.get('_tts_ready', True):
                    return {'error': 'Warte bis TTS fertig!'}
                break

    name = t['member_names'].get(user_id, '???')
    now = time.time()
    t['member_last_active'][user_id] = now

    # Voice mitgeschickt? Speichern!
    voice_url = None
    if voice_data and voice_data.startswith('data:audio'):
        try:
            ext = 'webm' if 'webm' in voice_data.split(',')[0] else 'mp4'
            raw = base64.b64decode(voice_data.split(',')[1])
            if 100 < len(raw) < 2 * 1024 * 1024:  # Max 2MB
                file_id = str(uuid.uuid4())[:8]
                fpath = os.path.join(VOICE_DIR, f'chat_{file_id}.{ext}')
                with open(fpath, 'wb') as f:
                    f.write(raw)
                voice_url = f'/api/chat-file/{file_id}.{ext}'
                log.info(f'🎤 CHAT-VOICE — {name}: {len(raw)} bytes')
                # Whisper: NICHT blockieren! Message sofort rein, Transkription im Hintergrund!
                if not text:
                    text = '🎤 ...'  # Platzhalter, wird von Whisper überschrieben
        except Exception as e:
            log.error(f'❌ Chat-Voice Fehler: {e}')

    # TTS NUR wenn KEIN Voice-Input! (sonst Echo!)
    tts_url = None
    voice_input = data.get('voice_input', False)
    if not voice_url and not voice_input:
        conn_acc2 = get_db('accounts.db')
        user_row = conn_acc2.execute('SELECT tts_voice, is_guest FROM users WHERE id = ?', (user_id,)).fetchone()
        conn_acc2.close()
        voice_id = user_row['tts_voice'] if user_row and user_row['tts_voice'] else 'de-DE-ConradNeural'
        _is_guest = bool(user_row and user_row['is_guest'])

        tts_id = f'chat_{str(uuid.uuid4())[:8]}'
        tts_path = os.path.join(VOICE_DIR, f'{tts_id}')

        def _gen_chat_tts():
            # In TEMP schreiben, dann umbenennen wenn FERTIG!
            # Bark first → Edge fallback! (Gäste: NUR Edge, außer Owner schaltet Bark frei!)
            _guest_bark_ok = _is_guest and _guest_config().get('bark_enabled', False)
            if (not _is_guest or _guest_bark_ok) and _voice_server_available():
                bark_voice = voice_id if not voice_id.startswith('de-') else 'mann1'
                audio_b64 = _bark_generate(text, bark_voice)
                if audio_b64:
                    try:
                        raw = base64.b64decode(audio_b64.split(',')[1])
                        # Bark-Audio: Direkt als WAV speichern (24kHz Original)
                        with open(tts_path + '.wav', 'wb') as f:
                            f.write(raw)
                        msg['_tts_ready'] = True
                        log.info(f'🔊 BARK-TTS — "{text[:30]}..." [{bark_voice}]')
                        return
                    except Exception as e:
                        log.error(f'❌ Bark-Save Fehler: {e}')
            # Fallback: Edge TTS → MP3 → WAV (korrekte Duration!)
            # Bark-Voice → passende Edge-Voice mappen!
            if voice_id.startswith('de-'):
                edge_voice = voice_id  # Bereits Edge-Format
            elif voice_id.startswith('frau') or voice_id == 'frau_sanft' or voice_id == 'mann2' or 'girl' in voice_id or 'frau' in voice_id:
                edge_voice = 'de-DE-KatjaNeural'  # Weiblich
            else:
                edge_voice = 'de-DE-ConradNeural'  # Männlich
            try:
                tmp_mp3 = tts_path + '.mp3.tmp'
                communicate = edge_tts.Communicate(text, edge_voice)
                loop = asyncio.new_event_loop()
                loop.run_until_complete(communicate.save(tmp_mp3))
                loop.close()
                wav_file = tts_path + '.wav'
                import subprocess
                subprocess.run(['ffmpeg', '-y', '-i', tmp_mp3, wav_file], capture_output=True)
                if os.path.exists(wav_file) and os.path.getsize(wav_file) > 100:
                    try: os.remove(tmp_mp3)
                    except: pass
                    msg['_tts_ready'] = True
                    log.info(f'🔊 EDGE-TTS WAV — "{text[:30]}..."')
                else:
                    os.rename(tmp_mp3, tts_path + '.mp3')
                    msg['_tts_ready'] = True
                    log.info(f'🔊 EDGE-TTS MP3 (WAV failed) — "{text[:30]}..."')
            except Exception as e:
                msg['_tts_ready'] = True
                log.error(f'❌ Chat-TTS Fehler: {e}')
        threading.Thread(target=_gen_chat_tts, daemon=True).start()
        tts_url = f'/api/bierdeckel/voice/{tts_id}'

    # PQ-Phase 4: Optional verschlüsseltes Payload (client-seitig erstellt).
    # Kneipe braucht Klartext für TTS, aber encrypted_payload wird mitgespeichert
    # als E2E-Schicht für Programme ohne TTS (ShinShare/Shidow).
    encrypted_payload = data.get('encrypted_payload')  # {nonce, ciphertext, alg} base64
    # Vision 1: Owner-Durchsage-Broadcast → Gold-Markierung im UI
    is_owner_voice = bool(data.get('_owner_broadcast'))
    msg = {
        'user_id': user_id, 'user': name, 'text': text, 'time': now,
        'file_url': voice_url,
        'filetype': 'audio/webm' if voice_url else None,
        'filename': 'Voice' if voice_url else None,
        'tts_url': tts_url,
    }
    if is_owner_voice:
        msg['is_owner_voice'] = True
    if encrypted_payload and isinstance(encrypted_payload, dict):
        msg['encrypted'] = {
            'nonce': encrypted_payload.get('nonce', ''),
            'ciphertext': encrypted_payload.get('ciphertext', ''),
            'alg': encrypted_payload.get('alg', 'AES-256-GCM'),
        }

    if tts_url:
        # Text-Input: TTS wird generiert → Message erst rein wenn FERTIG!
        # Der Thread packt msg in chat_rooms nach Generierung
        def _append_after_tts():
            # Warte max 120sec bis TTS fertig (Bark-Splitting über FRP!)
            for _ in range(1200):
                time.sleep(0.1)
                if msg.get('_tts_ready'):
                    break
            with chat_lock:
                room = chat_rooms.setdefault(tisch_id, [])
                room.append(msg)
                if len(room) > 200:
                    chat_rooms[tisch_id] = room[-200:]
        threading.Thread(target=_append_after_tts, daemon=True).start()
    else:
        # Voice-Input: SOFORT rein! Kein TTS nötig!
        with chat_lock:
            room = chat_rooms.setdefault(tisch_id, [])
            room.append(msg)
            if len(room) > 200:
                chat_rooms[tisch_id] = room[-200:]

    t['last_active'] = now

    # Whisper im Hintergrund — NACH dem Response! Kein Blockieren!
    if voice_url and _voice_server_available():
        _voice_fpath = os.path.join(VOICE_DIR, f'chat_{voice_url.split("chat_")[1]}') if 'chat_' in (voice_url or '') else None
        if _voice_fpath and os.path.exists(_voice_fpath):
            def _bg_whisper():
                try:
                    whisper_text = _whisper_transcribe(_voice_fpath, tisch_id=tisch_id)
                    if whisper_text:
                        msg['text'] = whisper_text
                        log.info(f'🎤 Whisper (bg): "{whisper_text[:60]}"')
                except Exception as e:
                    log.error(f'❌ Whisper (bg) Fehler: {e}')
            threading.Thread(target=_bg_whisper, daemon=True).start()

    return {'ok': True, 'msg': msg}

def handle_chat_file(user_id, data):
    """Datei im Chat teilen (temporär, Zero-Knowledge!)"""
    tisch_id = data.get('tisch_id')
    filename = (data.get('filename') or 'datei')[:100]
    filetype = (data.get('filetype') or '')[:50]
    file_data = data.get('data', '')

    t, raum = _find_tisch(tisch_id)
    if not t:
        return {'error': 'Tisch nicht gefunden'}
    if user_id not in t['members']:
        return {'error': 'Du sitzt nicht an diesem Tisch!'}

    if not file_data or not file_data.startswith('data:'):
        return {'error': 'Keine Datei!'}

    # Max 5MB
    raw = base64.b64decode(file_data.split(',')[1])
    if len(raw) > 5 * 1024 * 1024:
        return {'error': 'Max 5MB!'}

    # Temporär speichern (1h, wie Voice)
    file_id = str(uuid.uuid4())[:8]
    ext = filename.rsplit('.', 1)[-1] if '.' in filename else 'bin'
    file_path = os.path.join(VOICE_DIR, f'chat_{file_id}.{ext}')
    with open(file_path, 'wb') as f:
        f.write(raw)

    name = t['member_names'].get(user_id, '???')
    t['member_last_active'][user_id] = time.time()
    file_url = f'/api/chat-file/{file_id}.{ext}'

    msg = {
        'user_id': user_id,
        'user': name,
        'text': f'📎 {filename}',
        'time': time.time(),
        'file_url': file_url,
        'filename': filename,
        'filetype': filetype,
    }
    with chat_lock:
        room = chat_rooms.setdefault(tisch_id, [])
        room.append(msg)

    t['last_active'] = time.time()
    log.info(f'📎 DATEI — {name}: {filename} ({len(raw)} bytes) → {tisch_id}')
    return {'ok': True}

def handle_chat_poll(user_id, channel_id, since):
    """Nachrichten seit Timestamp — NUR für berechtigte User!
    Greift auf Tisch UND Tresen (via _find_channel).
    Owner darf alle Channels OHNE Passwort lesen (Durchsage).
    Tresen-Sitzer darf Tische des eigenen Raums lesen.
    Passwort-Kanäle: NUR Mitglieder (auch Owner NICHT — Blackbox-Härtung).
    """
    ch, _ = _find_channel(channel_id)
    if not ch:
        return {'error': 'Kanal nicht gefunden', '_status': 404}
    if not can_access_channel(user_id, channel_id):
        return {'error': 'Kein Zugriff auf diesen Kanal!', '_status': 403}
    result = []
    for m in chat_rooms.get(channel_id, []):
        if m['time'] <= since:
            continue
        msg = dict(m)
        if not msg.get('_tts_ready', True):
            msg['tts_url'] = None
        msg.pop('_tts_ready', None)
        result.append(msg)
    return result

def handle_name_vote(user_id, data):
    """Raumname abwählen — V4.1 Voting (30sec/3T, 51%/75%)"""
    if is_guest_user(user_id):
        return {'error': 'Gäste können nicht abstimmen. Registriere dich!'}
    raum_id = data.get('raum_id')
    action = data.get('action')  # 'against' oder 'for'
    new_name = (data.get('new_name') or '').strip()

    if raum_id not in raeume:
        return {'error': 'Raum nicht gefunden'}
    r = raeume[raum_id]
    if not _check_registered(user_id):
        return {'error': 'Gäste können nicht abstimmen!'}
    if r['name_fixed']:
        return {'error': 'Name ist fest! Nicht mehr änderbar.'}
    if r['name_changes'] >= 24:
        return {'error': 'Max 24 Umbenennungen erreicht!'}

    is_open = r['phase'] == 'open'
    vote_duration = 30 if is_open else 3 * 86400
    threshold = 51 if is_open else 75

    if action == 'against':
        if not new_name or len(new_name) > 30:
            return {'error': 'Neuer Name nötig (1-30 Zeichen)!'}
        # 1. Vote = SOFORT umbenennen!
        if r['name_changes'] == 0:
            old_name = r['name']
            r['name'] = new_name
            r['name_changes'] = 1
            log.info(f'📝 RAUM UMBENANNT (1. Vote) — {raum_id} "{old_name}" → "{new_name}"')
            return {'ok': True, 'current': r['name'], 'renamed': True}
        # Ab 2. Umbenennung: Votum
        if r['name_vote'] and r['name_vote']['ends_at'] > time.time():
            r['name_vote']['against'].add(user_id)
            r['name_vote']['for'].discard(user_id)
        else:
            r['name_vote'] = {
                'against': {user_id},
                'for': set(),
                'ends_at': time.time() + vote_duration,
                'proposer': user_id,
                'new_name': new_name,
                'threshold': threshold,
            }
        log.info(f'🗳️ NAME VOTE — "{r["name"]}" → "{new_name}" in {raum_id}')

    elif action == 'for':
        if r['name_vote']:
            r['name_vote']['for'].add(user_id)
            r['name_vote']['against'].discard(user_id)

    if not r['name_vote']:
        return {'ok': True, 'current': r['name']}

    v = r['name_vote']
    secs_left = max(0, int(v['ends_at'] - time.time()))
    return {
        'ok': True,
        'current': r['name'],
        'proposed': v['new_name'],
        'against': len(v['against']),
        'for_count': len(v['for']),
        'secs_left': secs_left,
        'threshold': threshold,
    }

def handle_eigenschaft_vote(user_id, data):
    if is_guest_user(user_id):
        return {'error': 'Gäste können nicht abstimmen. Registriere dich!'}
    """Eigenschaft abwählen (✕) oder zustimmen (Klick) — V4.1 Voting"""
    raum_id = data.get('raum_id')
    eigenschaft = data.get('eigenschaft')
    action = data.get('action', 'minus')  # 'minus' oder 'plus' oder 'veto'

    if raum_id not in raeume:
        return {'error': 'Raum nicht gefunden'}
    r = raeume[raum_id]
    if eigenschaft not in r['eigenschaften']:
        return {'error': 'Eigenschaft nicht in diesem Raum'}
    if not _check_registered(user_id):
        return {'error': 'Gäste können nicht abstimmen!'}

    is_open = r['phase'] == 'open'
    vote_duration = 30 if is_open else 3 * 86400  # 30sec vs 3 Tage
    vote_threshold = 51 if is_open else 75

    votes = r['eigenschaft_votes'].setdefault(eigenschaft, {
        'minus': {}, 'plus': {}, 'vote_ends': 0, 'created_at': time.time(), 'last_vote_at': 0,
    })
    now = time.time()
    _clean_votes_30d(votes['minus'], now - 30 * 86400)
    _clean_votes_30d(votes['plus'], now - 30 * 86400)

    creator = votes.get('creator', None)

    # Gegenseite prüfen: wenn User schon anders gevotet hat → alten Vote löschen
    opposite = 'plus' if action in ('minus',) else 'minus'
    if action in ('minus', 'plus', 'veto'):
        if user_id in votes[opposite]:
            del votes[opposite][user_id]

    if action == 'minus':
        if not _can_vote_24h(user_id, votes['minus']):
            return {'error': 'Nächste Stimme in 24h!'}
        votes['minus'].setdefault(user_id, []).append(now)
        votes['last_vote_at'] = now

        # Schnell-Regel (Open): Erste 10min, 1 ✕ ohne FREMDE Zustimmung = SOFORT WEG!
        age_secs = now - votes.get('created_at', 0)
        fremde_plus = _count_votes({uid: v for uid, v in votes['plus'].items() if uid != creator})
        if is_open and age_secs <= 600 and fremde_plus == 0:
            if eigenschaft in r['eigenschaften']:
                r['eigenschaften'].remove(eigenschaft)
            r['eigenschaft_votes'].pop(eigenschaft, None)
            log.info(f'❌ EIGENSCHAFT SOFORT WEG (10min-Regel) — "{eigenschaft}" aus {raum_id}')
            return {'ok': True, 'removed': True, 'eigenschaft': eigenschaft}

        if votes['vote_ends'] == 0:
            votes['vote_ends'] = now + vote_duration
    elif action == 'plus':
        if user_id == creator:
            return {'error': 'Eigene Zustimmung zählt nicht!'}
        if not _can_vote_24h(user_id, votes['plus']):
            return {'error': 'Nächste Stimme in 24h!'}
        votes['plus'].setdefault(user_id, []).append(now)
        votes['last_vote_at'] = now
        if votes['vote_ends'] == 0:
            votes['vote_ends'] = now + vote_duration
    elif action == 'veto':
        if user_id == creator:
            return {'error': 'Eigenes Veto zählt nicht!'}
        if not _can_vote_24h(user_id, votes['plus']):
            return {'error': 'Nächste Stimme in 24h!'}
        votes['plus'].setdefault(user_id, []).append(now)
        votes['last_vote_at'] = now

    minus_n = _count_votes(votes['minus'])
    plus_n = _count_votes(votes['plus'])
    total = max(minus_n + plus_n, 1)
    minus_pct = minus_n / total * 100

    log.info(f'🗳️ EIGENSCHAFT — "{eigenschaft}" in {raum_id}: ✕{minus_n} ✓{plus_n} ({minus_pct:.0f}% dagegen, Schwelle {vote_threshold}%)')
    return {
        'ok': True,
        'minus': minus_n,
        'plus': plus_n,
        'pct': round(minus_pct, 1),
        'threshold': vote_threshold,
        'duration': '30 Sekunden' if is_open else '3 Tage',
    }

def handle_eigenschaft_add(user_id, data):
    if is_guest_user(user_id):
        return {'error': 'Gäste können keine Eigenschaften hinzufügen. Registriere dich!'}
    """Neue Eigenschaft DIREKT rein ([+] Button) — kein Vorschlag!"""
    raum_id = data.get('raum_id')
    eigenschaft = (data.get('eigenschaft') or '').strip()

    if raum_id not in raeume:
        return {'error': 'Raum nicht gefunden'}
    r = raeume[raum_id]

    if not eigenschaft:
        return {'error': 'Eigenschaft angeben!'}
    if len(eigenschaft) > 120:
        return {'error': 'Max 120 Zeichen!'}
    if eigenschaft in r['eigenschaften']:
        return {'error': 'Gibt es schon!'}
    if len(r['eigenschaften']) >= 4:
        return {'error': 'Max 4 Eigenschaften! Erst eine abwählen.'}

    # Direkt rein! Kein Vorschlag!
    now = time.time()
    r['eigenschaften'].append(eigenschaft)
    r['eigenschaft_votes'][eigenschaft] = {
        'minus': {}, 'plus': {user_id: now}, 'vote_ends': 0,
        'created_at': now, 'last_vote_at': now, 'creator': user_id,
    }

    log.info(f'➕ EIGENSCHAFT REIN — "{eigenschaft}" in {raum_id} (von {user_id})')
    return {'ok': True, 'eigenschaft': eigenschaft}

def _lifecycle_thread():
    """Tisch + Raum Lifecycle: 1h Stille = Tisch stirbt, Raum-Cooldowns"""
    while True:
        try:
            now = time.time()
            with raum_lock:
                dead_raeume = []
                for rid, r in list(raeume.items()):
                    # Tresen-PW-Reset: 30sec leer → PW weg (wie bei Tischen)
                    tresen = r.get('tresen')
                    if tresen and tresen.get('password') and len(tresen.get('members', set())) == 0:
                        if now - tresen.get('last_active', now) >= 30:
                            tresen['password'] = ''
                    # --- TISCHE: 1h Stille = stirbt + neuer spawnt ---
                    dead_tische = []
                    for tid, t in list(r['tische'].items()):
                        if len(t['members']) > 0:
                            t['last_active'] = now
                            continue
                        # Passwort-Reset nach 30 Sekunden leer
                        if t.get('password') and now - t['last_active'] >= 30:
                            t['password'] = ''
                        if now - t['last_active'] >= TISCH_SILENCE_SECONDS:
                            dead_tische.append(tid)

                    for tid in dead_tische:
                        t = r['tische'].pop(tid)
                        chat_rooms.pop(tid, None)
                        # Aus DB löschen!
                        try:
                            conn_td = get_db('gameplay.db')
                            conn_td.execute('DELETE FROM tables_db WHERE id = ?', (tid,))
                            conn_td.commit()
                            conn_td.close()
                        except Exception:
                            pass
                        log.info(f'💀 TISCH STIRBT (1h Stille) — {tid} [{t.get("thema","?")}{t.get("energie",{}).get("emoji","?")}]')
                        # Neuen spawnen NUR wenn unter Limit!
                        if len(r['tische']) < TISCH_PER_RAUM:
                            nt = spawn_tisch(rid)
                            if '18+' in r.get('eigenschaften', []):
                                nt['adult_only'] = True
                            r['tische'][nt['id']] = nt

                    # --- RAUM LIFECYCLE ---
                    # Open → Fixed nach 24h
                    if r['phase'] == 'open' and now >= r['phase_ends_at']:
                        r['phase'] = 'fixed'
                        cooldown = TIER_COOLDOWNS[1]
                        r['phase_ends_at'] = now + cooldown
                        log.info(f'🔒 RAUM FIXIERT — {rid} "{r["name"]}" [Tier 1, 3 Tage Cooldown]')

                    # Fixed: Cooldown abgelaufen?
                    elif r['phase'] == 'fixed' and now >= r['phase_ends_at']:
                        # Hat jemand 1h+ verbracht?
                        qualified = any(secs >= 3600 for secs in r['visit_log'].values())
                        if qualified:
                            # ÜBERLEBT!
                            r['survive_count'] += 1
                            upgrade = TIER_UPGRADES.get(r['tier'])
                            if upgrade and r['survive_count'] >= upgrade:
                                r['tier'] = min(r['tier'] + 1, 3)
                                r['survive_count'] = 0
                                log.info(f'⬆️ RAUM TIER UP — {rid} → Tier {r["tier"]}')
                            cooldown = TIER_COOLDOWNS.get(r['tier'], TIER_COOLDOWNS[3])
                            r['phase_ends_at'] = now + cooldown
                            r['visit_log'] = {}  # Reset für nächste Periode
                            log.info(f'🏠 RAUM ÜBERLEBT — {rid} "{r["name"]}" [Tier {r["tier"]}, #{r["survive_count"]}]')
                        else:
                            # STIRBT!
                            dead_raeume.append(rid)

                    # --- NAME VOTE auflösen ---
                    if r['name_vote'] and now >= r['name_vote']['ends_at']:
                        v = r['name_vote']
                        total = len(v['against']) + len(v['for'])
                        threshold = v.get('threshold', 51) / 100
                        if total > 0 and len(v['against']) / total >= threshold:
                            old_name = r['name']
                            r['name'] = v['new_name']
                            r['name_changes'] += 1
                            log.info(f'📝 RAUM UMBENANNT — {rid} "{old_name}" → "{r["name"]}" ({threshold*100:.0f}%)')
                        else:
                            log.info(f'📝 NAME BLEIBT — {rid} "{r["name"]}" (Schwelle {threshold*100:.0f}% nicht erreicht)')
                        r['name_vote'] = None
                    # Name fixieren nach 24h
                    if not r['name_fixed'] and now - r['created_at'] >= RAUM_OPEN_SECONDS:
                        r['name_fixed'] = True
                        log.info(f'🔒 NAME FIXIERT — {rid} "{r["name"]}" (24h vorbei)')

                    # --- EIGENSCHAFTEN: 30-Tage-Fenster + 7-Tage-Auto-Delete ---
                    cutoff_30d = now - 30 * 86400
                    for eigenschaft, votes in list(r['eigenschaft_votes'].items()):
                        if isinstance(votes.get('minus'), dict):
                            _clean_votes_30d(votes['minus'], cutoff_30d)
                            _clean_votes_30d(votes['plus'], cutoff_30d)

                        # 7 Tage ohne Vote = Eigenschaft auto-gelöscht
                        last_vote = votes.get('last_vote_at', votes.get('created_at', now))
                        if now - last_vote >= 7 * 86400:
                            if eigenschaft in r['eigenschaften']:
                                r['eigenschaften'].remove(eigenschaft)
                            del r['eigenschaft_votes'][eigenschaft]
                            log.info(f'🗑️ EIGENSCHAFT AUTO-GELÖSCHT (7 Tage ohne Vote) — "{eigenschaft}" aus {rid}')
                            continue

                        # Vote-Timer abgelaufen?
                        if votes['vote_ends'] > 0 and now >= votes['vote_ends']:
                            minus_n = _count_votes(votes.get('minus', {}))
                            plus_n = _count_votes(votes.get('plus', {}))
                            total_voters = max(minus_n + plus_n, 1)
                            minus_pct = minus_n / total_voters * 100
                            threshold = 51 if r['phase'] == 'open' else 75
                            if minus_pct >= threshold:
                                if eigenschaft in r['eigenschaften']:
                                    r['eigenschaften'].remove(eigenschaft)
                                del r['eigenschaft_votes'][eigenschaft]
                                log.info(f'❌ EIGENSCHAFT ABGEWÄHLT — "{eigenschaft}" aus {rid} ({minus_pct:.0f}% >= {threshold}%)')
                            else:
                                votes['minus'] = {}
                                votes['plus'] = {}
                                votes['vote_ends'] = 0
                                log.info(f'✅ EIGENSCHAFT BLEIBT — "{eigenschaft}" in {rid} ({minus_pct:.0f}% < {threshold}%)')

                for rid in dead_raeume:
                    r = raeume.pop(rid)
                    # In DB als archived markieren + Tische löschen
                    try:
                        conn_arch = get_db('gameplay.db')
                        conn_arch.execute('UPDATE rooms SET archived = 1 WHERE id = ?', (rid,))
                        conn_arch.execute('DELETE FROM tables_db WHERE raum_id = ?', (rid,))
                        conn_arch.commit()
                        conn_arch.close()
                    except Exception as e:
                        log.error(f'❌ Raum-Archive Fehler: {e}')
                    for tid in list(r['tische'].keys()):
                        chat_rooms.pop(tid, None)
                    log.info(f'💀 RAUM SCHLIESST — {rid} "{r["name"]}" (kein Gast 1h+)')

                # Immer mindestens 1 Raum!
                if not raeume:
                    spawn_raum()

                # Alle lebenden Räume periodisch speichern
                for r in raeume.values():
                    _save_raum_to_db(r)

            # V4.1 Tribunal: offene Tribunale nach 3 Tagen auswerten
            try:
                conn_t = get_db('gameplay.db')
                open_tribunals = conn_t.execute(
                    'SELECT id, created_at FROM tribunals WHERE status = ?', ('open',)).fetchall()
                conn_t.close()
                for t in open_tribunals:
                    if now - t['created_at'] >= 3 * 86400:
                        _resolve_tribunal(t['id'])
            except Exception as e:
                log.error(f'❌ Tribunal-Lifecycle Fehler: {e}')

        except Exception as e:
            log.error(f'❌ Lifecycle Fehler: {e}')
        time.sleep(60)

# --- SHARE CARD GENERATOR ---
def generate_share_card(username):
    """Generiert ein 1024x1024 Share-Bild für einen User"""
    conn_acc = get_db('accounts.db')
    user = conn_acc.execute('SELECT id, name, profile_pic, age, is_bot FROM users WHERE name = ?', (username,)).fetchone()
    if not user:
        conn_acc.close()
        return None
    uid = user['id']
    conn_acc.close()

    conn_gp = get_db('gameplay.db')
    plays = conn_gp.execute('SELECT COUNT(*) as cnt FROM plays WHERE user_id = ?', (uid,)).fetchone()['cnt']
    titles = [r['title_id'] for r in conn_gp.execute('SELECT title_id FROM titles WHERE user_id = ?', (uid,)).fetchall()]
    stammgast = conn_gp.execute('SELECT COUNT(*) as cnt FROM plays WHERE user_id = ? AND is_stammgast = 1', (uid,)).fetchone()['cnt']
    elements_raw = conn_gp.execute('SELECT element, COUNT(*) as cnt FROM plays WHERE user_id = ? GROUP BY element', (uid,)).fetchall()
    elements_dict = {e['element']: e['cnt'] for e in elements_raw}
    if stammgast > 0:
        elements_dict['stammgast'] = stammgast
    conn_gp.close()

    gesamt = calculate_gesamt_titel(uid)
    teilnehmer = get_teilnehmer(bot_filter=bool(user['is_bot']))
    rank = next((t['rank'] for t in teilnehmer if t['name'] == username), 0)

    # Bild erstellen — Ratio von intro.jpg beibehalten
    width = 1024
    try:
        bg_orig = Image.open(os.path.join(BASE, 'intro.jpg')).convert('RGB')
        height = int(width / bg_orig.width * bg_orig.height)
    except:
        height = 687
        bg_orig = None
    img = Image.new('RGB', (width, height), '#0a0a0a')
    draw = ImageDraw.Draw(img)

    if bg_orig:
        bg = bg_orig.resize((width, height))
        dark = Image.new('RGB', (width, height), (0, 0, 0))
        img = Image.blend(bg, dark, 0.65)
        draw = ImageDraw.Draw(img)

    # Feste Y-Positionen für 1024xHeight Layout (gleichmäßig verteilt)
    Y = {
        'avatar':    int(height * 0.13),   # ~89px  Profilbild
        'name':      int(height * 0.28),   # ~192px Username (etwas mehr Abstand zum Avatar)
        'titel':     int(height * 0.36),   # ~247px DENKER etc
        'badges':    int(height * 0.46),   # ~316px Badge-Icons
        'spiele':    int(height * 0.54),   # ~371px X Spiele
        'platz':     int(height * 0.63),   # ~433px Kneipengänger Platz X
        'datum':     int(height * 0.72),   # ~495px Datum
        'branding':  int(height * 0.82),   # ~563px Kneipen-Schlägerei
        'url':       int(height * 0.89),   # ~611px bar.shinpai.de
        'motto':     int(height * 0.95),   # ~653px Ist einfach passiert
    }

    # Fonts (Fallback auf Default)
    try:
        font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 42)
        font_med = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
        font_xs = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except:
        font_big = ImageFont.load_default()
        font_med = font_big
        font_sm = font_big
        font_xs = font_big

    cx = width // 2

    # Profilbild
    avatar_y = Y['avatar']
    if user['profile_pic'] and user['profile_pic'].startswith('data:'):
        try:
            b64data = user['profile_pic'].split(',')[1]
            avatar_data = base64.b64decode(b64data)
            avatar_img = Image.open(io.BytesIO(avatar_data)).convert('RGBA')
            avatar_img = avatar_img.resize((140, 140))
            # Kreismaske
            mask = Image.new('L', (140, 140), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, 140, 140), fill=255)
            img.paste(avatar_img.convert('RGB'), (cx - 70, avatar_y - 70), mask)
            # Rahmen
            draw.ellipse((cx - 72, avatar_y - 72, cx + 72, avatar_y + 72), outline='#d4a850', width=3)
        except:
            draw.ellipse((cx - 70, avatar_y - 70, cx + 70, avatar_y + 70), fill='#1a1610', outline='#3a3020', width=2)
            draw.text((cx, avatar_y), user['profile_pic'] or '👤', fill='#e0d8c8', font=font_big, anchor='mm')
    else:
        draw.ellipse((cx - 70, avatar_y - 70, cx + 70, avatar_y + 70), fill='#1a1610', outline='#d4a850', width=3)
        # Emoji-Avatar als vorgerendertes Bild pasten
        emoji_map = {'😎':'cool','🐉':'drache','🍺':'bier','🔥':'feuer','🌊':'wasser',
                     '🪨':'stein','💨':'wind','🎵':'musik','🤐':'still','🌙':'mond',
                     '👑':'krone','🎮':'game','🧠':'hirn','💀':'skull','🌵':'kaktus',
                     '🤠':'cowboy','🐑':'schaf','🤯':'boom','💎':'diamant','⚡':'blitz',
                     '🛡️':'schild','🎭':'maske','🦊':'fuchs','🐺':'wolf','🦁':'loewe','🐲':'drache2'}
        emoji_name = emoji_map.get(user['profile_pic'], '')
        emoji_path = os.path.join(BASE, 'badges', f'emoji_{emoji_name}.png') if emoji_name else ''
        if emoji_name and os.path.exists(emoji_path):
            try:
                emoji_img = Image.open(emoji_path).convert('RGBA').resize((100, 100))
                mask = Image.new('L', (100, 100), 0)
                ImageDraw.Draw(mask).ellipse((0, 0, 100, 100), fill=255)
                img.paste(emoji_img.convert('RGB'), (cx - 50, avatar_y - 50), mask)
            except:
                draw.text((cx, avatar_y), '?', fill='#d4a850', font=font_big, anchor='mm')
        else:
            draw.text((cx, avatar_y), '?', fill='#d4a850', font=font_big, anchor='mm')

    # Username + Alter + Cheater-Tag
    age_text = f' ({user["age"]})' if user['age'] and user['age'] != 'undefined' else ''
    cheater_tag = '>_ ' if 'cheater' in titles else ''
    draw.text((cx, Y['name']), f'{cheater_tag}{username}{age_text}', fill='#e0d8c8', font=font_med, anchor='mm')

    # Gesamt-Titel PROMINENT
    gesamt_names = {'kritiker': 'KRITIKER', 'denker': 'DENKER', 'mystiker': 'MYSTIKER', 'mauerblümchen': 'MAUERBLÜMCHEN'}
    titel_text = gesamt_names.get(gesamt, 'DENKER')
    draw.text((cx, Y['titel']), titel_text, fill='#d4a850', font=font_big, anchor='mm')

    # Badges + Counts
    # Badges als vorgerenderte Bilder pasten
    BADGES_DIR = os.path.join(BASE, 'badges')
    badge_files = []
    for t in titles:
        bp = os.path.join(BADGES_DIR, f'{t}.png')
        if os.path.exists(bp):
            badge_files.append(bp)
    if stammgast > 0:
        bp = os.path.join(BADGES_DIR, 'stammgast.png')
        if os.path.exists(bp):
            badge_files.append(bp)

    if badge_files:
        badge_size = 52
        total_w = len(badge_files) * (badge_size + 8)
        start_x = cx - total_w // 2
        for i, bp in enumerate(badge_files):
            try:
                badge_img = Image.open(bp).convert('RGBA').resize((badge_size, badge_size))
                bx = start_x + i * (badge_size + 8)
                by = Y['badges'] - badge_size // 2
                img.paste(badge_img, (bx, by), badge_img)
            except:
                pass
        # Stammgast Anzahl daneben
        if stammgast > 0:
            draw.text((start_x + len(badge_files) * (badge_size + 8) + 5, Y['badges']), f'x{stammgast}', fill='#d4a850', font=font_xs, anchor='lm')
    else:
        draw.text((cx, Y['badges']), 'Noch keine Titel', fill='#665540', font=font_xs, anchor='mm')

    # Spiele
    draw.text((cx, Y['spiele']), f'{plays} Spiele', fill='#887755', font=font_xs, anchor='mm')

    # Farbkreis (links neben Titel, Goldener Schnitt zur Mitte, Oberkante = Name)
    if plays > 0 and elements_dict:
        pie_colors = {'feuer': (178, 34, 52), 'wasser': (0, 51, 153), 'stein': (192, 192, 210), 'wind': (140, 240, 120)}
        pie_r = 32
        # Nochmal 30% weiter zur Mitte
        pie_cx = int(cx * 0.65)
        pie_cy = Y['name'] + pie_r  # Oberkante auf Namenshöhe
        start_angle = -90
        pie_entries = [(el, cnt) for el, cnt in elements_dict.items() if el in pie_colors and cnt > 0]
        for el, cnt in pie_entries:
            sweep = cnt / plays * 360
            end_angle = start_angle + sweep
            bbox = [pie_cx - pie_r, pie_cy - pie_r, pie_cx + pie_r, pie_cy + pie_r]
            draw.pieslice(bbox, start_angle, end_angle, fill=pie_colors[el])
            start_angle = end_angle
        draw.ellipse([pie_cx - pie_r - 1, pie_cy - pie_r - 1, pie_cx + pie_r + 1, pie_cy + pie_r + 1], outline=(212, 168, 80), width=2)
        # Legende: exakt Kreisbreite, dynamische Fontgröße!
        names = {'feuer': 'Feuer', 'wasser': 'Wasser', 'stein': 'Stein', 'wind': 'Wind'}
        legend_parts = ['feuer', 'wasser', 'stein', 'wind']
        legend_y = pie_cy + pie_r + 5
        target_w = pie_r * 2  # Exakt Kreisbreite!
        # Font-Größe finden die passt
        font_tiny = None
        for fsize in range(10, 3, -1):
            try:
                ft = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", fsize)
            except:
                ft = ImageFont.load_default()
            total = sum(draw.textlength(names[el], font=ft) for el in legend_parts) + 3 * (len(legend_parts) - 1)
            if total <= target_w:
                font_tiny = ft
                break
        if not font_tiny:
            try:
                font_tiny = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 4)
            except:
                font_tiny = ImageFont.load_default()
        # Zeichnen, zentriert auf Kreis
        total = sum(draw.textlength(names[el], font=font_tiny) for el in legend_parts)
        spacing = (target_w - total) / max(len(legend_parts) - 1, 1)
        legend_x = pie_cx - pie_r
        for el in legend_parts:
            draw.text((legend_x, legend_y), names[el], fill=pie_colors[el], font=font_tiny, anchor='lm')
            legend_x += draw.textlength(names[el], font=font_tiny) + spacing

    # Platzierung
    list_type = 'Roboconnaisseur' if user['is_bot'] else 'Kneipengänger'
    rank_icon = '🏆' if rank == 1 else '🥈' if rank == 2 else '🤠' if rank == 3 else '#'
    rank_text = f'{list_type}: Platz {rank}' if rank > 0 else f'{list_type}'
    draw.text((cx, Y['platz']), rank_text, fill='#d4a850', font=font_med, anchor='mm')
    # Rang-Emoji RECHTS neben Platz-Nummer
    if rank in [1, 2, 3]:
        rang_path = os.path.join(BASE, 'badges', f'rang_{rank}.png')
        if os.path.exists(rang_path):
            try:
                rang_img = Image.open(rang_path).convert('RGBA').resize((36, 36))
                text_w = draw.textlength(rank_text, font=font_med)
                img.paste(rang_img, (int(cx + text_w//2 + 8), Y['platz'] - 18), rang_img)
            except:
                pass

    # Datum
    from datetime import datetime
    date_text = datetime.now().strftime('%d.%m.%Y')
    draw.text((cx, Y['datum']), date_text, fill='#665540', font=font_xs, anchor='mm')

    # Branding unten
    bier_path = os.path.join(BASE, 'badges', 'emoji_bier.png')
    draw.text((cx, Y['branding']), '- Kneipen-Schlägerei -', fill='#665540', font=font_sm, anchor='mm')
    if os.path.exists(bier_path):
        try:
            bier_img = Image.open(bier_path).convert('RGBA').resize((28, 28))
            text_w = draw.textlength('- Kneipen-Schlägerei -', font=font_sm)
            img.paste(bier_img, (int(cx - text_w//2 - 36), Y['branding'] - 14), bier_img)
            img.paste(bier_img, (int(cx + text_w//2 + 8), Y['branding'] - 14), bier_img)
        except:
            pass
    draw.text((cx, Y['url']), 'bar.shinpai.de', fill='#443320', font=font_xs, anchor='mm')
    draw.text((cx, Y['motto']), 'Ist einfach passiert.', fill='#332210', font=font_xs, anchor='mm')

    # Als PNG
    buf = io.BytesIO()
    img.save(buf, format='PNG', quality=85)
    return buf.getvalue()

# --- TEILNEHMER-LISTE ---
def get_teilnehmer(include_email=False, bot_filter=None):
    """Top-Liste: Titel DESC, Spiele ASC. bot_filter: None=alle, True=nur Bots, False=nur Menschen"""
    conn_acc = get_db('accounts.db')
    conn_gp = get_db('gameplay.db')

    if bot_filter is True:
        users = conn_acc.execute('SELECT id, name, profile_pic, age, email, themen_access, themen_plays_counter FROM users WHERE verified = 1 AND is_bot = 1').fetchall()
    elif bot_filter is False:
        users = conn_acc.execute('SELECT id, name, profile_pic, age, email, themen_access, themen_plays_counter FROM users WHERE verified = 1 AND is_bot = 0').fetchall()
    else:
        users = conn_acc.execute('SELECT id, name, profile_pic, age, email, themen_access, themen_plays_counter FROM users WHERE verified = 1').fetchall()

    teilnehmer = []
    for user in users:
        uid = user['id']
        # Spiele zählen
        plays = conn_gp.execute('SELECT COUNT(*) as cnt FROM plays WHERE user_id = ?', (uid,)).fetchone()['cnt']
        if plays == 0:
            continue
        # Titel zählen
        titles = conn_gp.execute('SELECT title_id FROM titles WHERE user_id = ?', (uid,)).fetchall()
        title_ids = [t['title_id'] for t in titles]
        # Stammgast zählen
        stammgast = conn_gp.execute('SELECT COUNT(*) as cnt FROM plays WHERE user_id = ? AND is_stammgast = 1', (uid,)).fetchone()['cnt']
        # Gesamt-Titel
        gesamt = calculate_gesamt_titel(uid)

        # Element-Verteilung + Prozente (inkl. Stammgast)
        elems = conn_gp.execute('SELECT element, COUNT(*) as cnt FROM plays WHERE user_id = ? GROUP BY element', (uid,)).fetchall()
        elem_dict = {e['element']: e['cnt'] for e in elems}
        stammgast_cnt = conn_gp.execute('SELECT COUNT(*) as cnt FROM plays WHERE user_id = ? AND is_stammgast = 1', (uid,)).fetchone()['cnt']
        if stammgast_cnt > 0:
            elem_dict['stammgast'] = stammgast_cnt
        elem_pct = {el: round(cnt / plays * 100, 1) for el, cnt in elem_dict.items()} if plays > 0 else {}

        # Mauerblümchen FOREVER Check: 100% aller Mauerblümchen-Spiele = Mauerblümchen!
        mauer_plays = conn_gp.execute(
            'SELECT COUNT(*) as cnt FROM plays WHERE user_id = ? AND is_mauerblümchen = 1', (uid,)
        ).fetchone()['cnt']
        is_mauerblümchen_forever = (
            'mauerblümchen_titel' in title_ids
            and mauer_plays >= 3
            and elem_dict.get('mauerblümchen', 0) == mauer_plays  # 100%: JEDES Mauerblümchen-Spiel = Mauerblümchen
        )

        entry = {
            'name': user['name'],
            'profile_pic': user['profile_pic'] or '',
            'age': user['age'] or 'undefined',
            'gesamt_titel': gesamt,
            'titles': title_ids,
            'title_count': len(title_ids),
            'stammgast': stammgast,
            'plays': plays,
            'elements': elem_dict,
            'elements_pct': elem_pct,
            'is_mauerblümchen_forever': is_mauerblümchen_forever,
        }
        if include_email:
            entry['email'] = user['email'] or ''
            entry['themen_access'] = bool(user['themen_access'])
            entry['themen_plays_counter'] = user['themen_plays_counter'] or 0
        teilnehmer.append(entry)

    conn_acc.close()
    conn_gp.close()

    # Cheater ans ENDE, dann Titel DESC, Spiele ASC
    teilnehmer.sort(key=lambda x: (1 if 'cheater' in x['titles'] else 0, -x['title_count'], x['plays']))
    # Rang zuweisen + Cheater = 💀 ohne Rang
    for i, t in enumerate(teilnehmer):
        t['rank'] = i + 1
        t['is_cheater_rank'] = 'cheater' in t['titles']

    return teilnehmer[:50]  # Top 50

# --- API HANDLERS ---
def handle_owner_setup(data):
    """Ersteinrichtung: Owner-Account erstellen (3-Step: Account → 2FA → SMTP)"""
    if has_owner():
        return {'error': 'Owner existiert bereits'}

    step = data.get('step', 'init')
    name = data.get('name', '').strip()
    password = data.get('password', '')
    email = data.get('email', '').strip()

    if not NAME_RE.match(name):
        return {'error': 'Name: 1-12 Zeichen, nur A-Z, a-z, 0-9'}
    if len(password) < 8:
        return {'error': 'Passwort: mindestens 8 Zeichen'}
    if not email or '@' not in email:
        return {'error': 'E-Mail ist Pflicht für den Owner!'}

    if step == 'init':
        # Schritt 1: Secret generieren, QR zurückgeben
        totp_secret = generate_totp_secret()
        qr_data, uri = generate_totp_qr(totp_secret, name)
        return {
            'ok': True,
            'step': 'verify',
            'totp_secret': totp_secret,
            'qr': qr_data,
            'message': 'Scanne den QR-Code mit deiner Authenticator-App und gib den Code ein.',
        }

    elif step == 'verify':
        # Schritt 2: Code verifizieren + Vault einrichten + Account erstellen
        totp_secret = data.get('totp_secret', '')
        totp_code = data.get('totp_code', '')
        use_igni = bool(data.get('use_igni', True))  # Default: Auto-Unlock aktiv

        if not totp_secret:
            return {'error': 'TOTP Secret fehlt'}
        if not verify_totp(totp_secret, totp_code):
            return {'error': 'Falscher 2FA Code! Nochmal versuchen.'}

        # VAULT-SETUP zuerst! Erst danach darf vault_encrypt() benutzt werden.
        try:
            recovery_seed = vault_setup(password, name, email)
        except Exception as e:
            log.error(f'Vault-Setup FEHLGESCHLAGEN: {e}')
            return {'error': 'Vault konnte nicht eingerichtet werden!'}

        # Igni für Auto-Unlock (Owner kann später paranoid-mode aktivieren)
        if use_igni:
            try:
                igni_save(password)
            except Exception as e:
                log.warning(f'Igni-Save fehlgeschlagen (kein Abbruch): {e}')

        user_id = str(uuid.uuid4())
        now = time.time()
        verify_code = generate_verify_code()
        code_expires = now + CODE_TTL_SECONDS

        conn = get_db('accounts.db')
        api_key = f"kneipe_{secrets.token_hex(24)}"
        shinpai_id = _generate_shinpai_id(name, email)
        # PQ-Keypair für Owner generieren (ML-DSA + ML-KEM)
        pq_dsa_pub, pq_kem_pub, pq_priv_blob = pq_generate_user_keypair()
        pq_priv_enc = pq_encrypt_private_blob(pq_priv_blob, password, user_id) if pq_priv_blob else ''
        conn.execute('''INSERT INTO users (id, name, email, pw_hash, totp_secret, totp_enabled, is_owner, verified, verify_token, verify_expires, api_key, shinpai_id, pq_dsa_pub, pq_kem_pub, pq_private_enc, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, 1, 1, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (user_id, name, email, hash_pw(password), vault_encrypt(totp_secret), verify_code, code_expires, api_key, shinpai_id, pq_dsa_pub or '', pq_kem_pub or '', pq_priv_enc, now, now))
        conn.commit()
        conn.close()

        log.info(f'👑 OWNER CREATED — {name} ({email}) [{shinpai_id}] — Vault entsperrt, PQ-Keys generiert, wartet auf SMTP!')
        # FRP Admin-Pass vom gespeicherten Hash ableiten (gleicher Hash = gleicher FRP-Pass!)
        conn_pw = get_db('accounts.db')
        stored_hash = conn_pw.execute('SELECT pw_hash FROM users WHERE id = ?', (user_id,)).fetchone()
        conn_pw.close()
        if stored_hash:
            _refresh_frp_admin(stored_hash['pw_hash'])

        token = create_session(user_id)
        return {
            'ok': True,
            'step': 'smtp',
            'token': token,
            'recovery_seed': recovery_seed,  # NUR EINMAL ANZEIGEN! User muss sichern!
            'igni_saved': use_igni,
            'user': {'id': user_id, 'name': name, 'email': email, 'is_owner': True, 'verified': False},
        }

    elif step == 'smtp_verify':
        # Schritt 3: SMTP konfiguriert → Code per Mail senden (nicht mehr Magic-Link!)
        conn = get_db('accounts.db')
        owner = conn.execute('SELECT id, name, email, verify_token, verified, nexus_verified FROM users WHERE is_owner = 1').fetchone()
        conn.close()
        if not owner:
            return {'error': 'Kein Owner gefunden!'}
        if not smtp_configured():
            return {'error': 'SMTP muss zuerst konfiguriert werden!'}
        # Code wird IMMER gesendet wenn angefordert (auch bei bereits verifiziertem Owner)
        if not owner['email']:
            return {'error': 'Keine Email hinterlegt! Account unvollständig.'}
        # Frischen Code generieren (falls alter abgelaufen)
        new_code = generate_verify_code()
        set_verify_code(owner['id'], new_code)
        owner = dict(owner)
        owner['verify_token'] = new_code
        success = send_verify_email(owner['email'], owner['verify_token'], owner['name'])
        if success:
            log.info(f'📧 OWNER CODE-MAIL gesendet an {owner["email"]}')
            return {
                'ok': True,
                'step': 'code',
                'email': owner['email'],
                'message': f'6-stelliger Code an {owner["email"]} gesendet. Code hier eingeben.',
            }
        else:
            return {'error': 'Mail konnte nicht gesendet werden! SMTP-Einstellungen prüfen!'}

    return {'error': 'Unbekannter Schritt'}

KNEIPE_MAX_USERS = 200  # Max registrierte User pro Kneipe (DSGVO-konform!)
NEXUS_REGISTER_URL = 'https://hive.shidow.de:12345'  # ShinNexus Upgrade-Link


def handle_nexus_auth(data, make_owner=False):
    """Nexus-Login → Kneipe-Account erstellen/einloggen. 3-Step-Flow wie Shidow.

    Body: {nexus_url, username, password?, totp_code?}
    Returns: 3-Step Nexus-Response ODER fertiger Kneipe-Login.
    """
    nx_url = (data.get('nexus_url') or '').strip().rstrip('/')
    username = (data.get('username') or '').strip()
    password = data.get('password', '')
    totp_code = data.get('totp_code', '') or data.get('totp', '')

    if not nx_url or not username:
        return {'error': 'ShinNexus-URL und Username benötigt!'}

    # V5.2: Trust-Whitelist-Check vor dem Login (Nexus-Fork-Schutz)
    # Anchor hinterlegt → Whitelist gefüllt → nur verifizierte Nexus erlaubt
    # Kein Anchor → leere Whitelist → Permissive (jeder Nexus erlaubt)
    trusted, code_hash, version = verify_nexus_trust(nx_url)
    if not trusted:
        log.warning(f'🛡️ NEXUS-WHITELIST BLOCK — {nx_url} (hash={code_hash}, version={version})')
        return {
            'error': f'ShinNexus {nx_url} ist nicht in der Trust-Whitelist. '
                     f'Hash: {code_hash or "?"} — Anchor-Datei hinterlegen oder Hash manuell freigeben.',
            'nexus_untrusted': True,
            'code_hash': code_hash,
        }

    # Nexus kontaktieren (3-Step Login)
    auth_payload = {'username': username, 'source': 'kneipe:bar.shinpai.de'}
    if password:
        auth_payload['password'] = password
    if totp_code:
        auth_payload['totp_code'] = totp_code

    status, result = nexus_request(nx_url, '/api/auth/login', auth_payload)
    if status >= 400:
        return result  # Fehler direkt durchreichen

    step = result.get('step', '')

    # 3-Step: password/2fa → an Client weiterleiten (User muss mehr eingeben)
    if step in ('password', '2fa'):
        return result

    # Step "done" → Auth erfolgreich!
    if step == 'done' and result.get('authenticated'):
        shinpai_id = result.get('shinpai_id', '')
        nx_name = result.get('name', username)

        if not shinpai_id:
            return {'error': 'Nexus gab keine Shinpai-ID zurück!'}

        # Email-Pull: Nexus ist Source of Truth.
        # Wenn Nexus eine Email liefert → nimm DIE.
        # Sonst Fallback auf Setup-Form-Email (nur beim Erst-Setup von Kneipe-first-Accounts relevant).
        nexus_email = (result.get('email') or '').strip().lower()
        form_email = (data.get('email') or '').strip().lower()
        email = nexus_email or form_email

        conn = get_db('accounts.db')

        # Check: Shinpai-ID schon vergeben?
        existing = conn.execute('SELECT id, name, is_owner, pw_hash, email, pq_dsa_pub FROM users WHERE shinpai_id = ?', (shinpai_id,)).fetchone()
        if existing:
            # Re-Login! Gleiche Shinpai-ID = gleicher Mensch
            # LAZY-SYNC: Nexus = Prio, Kneipe zieht nach.
            updates = ['nexus_url = ?', 'nexus_verified = 1', 'verified = 1', 'updated_at = ?']
            params = [nx_url, time.time()]
            migrated = []
            if (not existing['pw_hash']) and password:
                updates.append('pw_hash = ?')
                params.append(hash_pw(password))
                migrated.append('pw_hash')
            # Email-Pull: Nexus-Email überschreibt Kneipe-Email wenn unterschiedlich.
            # Fallback-Form-Email nur einsetzen wenn Kneipe noch leer ist.
            target_email = nexus_email if nexus_email else (form_email if not existing['email'] else None)
            if target_email and target_email != (existing['email'] or '').lower():
                updates.append('email = ?')
                params.append(target_email)
                # Bei Email-Wechsel: verified zurücksetzen + neuen Verify-Code generieren
                # (Kneipe validiert die Email autark, Nexus bestätigt nur Besitz der Identität)
                updates.extend(['verified = ?', 'verify_token = ?', 'verify_expires = ?'])
                new_code = generate_verify_code()
                params.extend([0, new_code, time.time() + CODE_TTL_SECONDS])
                migrated.append(f'email→{target_email}')
            if (not existing['pq_dsa_pub']) and PQ_AVAILABLE and password:
                try:
                    _pq_dsa, _pq_kem, _pq_priv = pq_generate_user_keypair()
                    if _pq_priv:
                        _pq_enc = pq_encrypt_private_blob(_pq_priv, password, existing['id'])
                        updates.extend(['pq_dsa_pub = ?', 'pq_kem_pub = ?', 'pq_private_enc = ?'])
                        params.extend([_pq_dsa or '', _pq_kem or '', _pq_enc])
                        migrated.append('pq_keys')
                except Exception as e:
                    log.warning(f'PQ-Lazy-Migration fehlgeschlagen: {e}')
            params.append(existing['id'])
            conn.execute(f'UPDATE users SET {", ".join(updates)} WHERE id = ?', params)
            conn.commit()
            conn.close()
            token = create_session(existing['id'])
            migr_info = f' [migriert: {",".join(migrated)}]' if migrated else ''
            log.info(f'🛡️ NEXUS RE-LOGIN — {existing["name"]} [{shinpai_id}]{migr_info}')
            return {
                'ok': True, 'step': 'done', 'token': token,
                'user': {'id': existing['id'], 'name': existing['name'], 'is_owner': bool(existing['is_owner']), 'verified': True},
            }

        # Check: Username schon vergeben (anderer Shinpai-ID)?
        name_taken = conn.execute('SELECT id FROM users WHERE name = ?', (nx_name,)).fetchone()
        if name_taken:
            conn.close()
            return {'error': f'Name "{nx_name}" bereits vergeben (andere Identität)!'}

        # Pflicht-Credentials für vollwertigen autarken Account
        if not password:
            conn.close()
            return {'error': 'Passwort wird benötigt (für lokalen Kneipe-Login, PQ-Keys und Vault)!'}
        if not email or '@' not in email or '.' not in email:
            conn.close()
            return {'error': 'Gültige Email wird benötigt (für Verifikation und Recovery)!'}
        # Email schon vergeben?
        email_taken = conn.execute('SELECT id FROM users WHERE LOWER(email) = LOWER(?)', (email,)).fetchone()
        if email_taken:
            conn.close()
            return {'error': f'Email "{email}" bereits für anderen Account verwendet!'}

        # Vault-Setup NUR für Owner (Owner = Schicht-1-Vault-Owner)
        recovery_seed = None
        if make_owner:
            try:
                recovery_seed = vault_setup(password, nx_name, f'{shinpai_id}@nexus')
                # Igni default an, Owner kann später ausschalten
                try:
                    igni_save(password)
                except Exception as e:
                    log.warning(f'Igni-Save fehlgeschlagen (kein Abbruch): {e}')
            except Exception as e:
                conn.close()
                log.error(f'Vault-Setup (Nexus-Owner) FEHLGESCHLAGEN: {e}')
                return {'error': 'Vault konnte nicht eingerichtet werden!'}

        user_id = str(uuid.uuid4())
        now = time.time()
        api_key = f"kneipe_{secrets.token_hex(24)}"
        pw_hash = hash_pw(password)
        nx_totp = result.get('totp_secret', '')
        totp_enc = vault_encrypt(nx_totp) if nx_totp and vault_is_unlocked() else None
        totp_enabled = 1 if nx_totp else 0
        # PQ-Keypair für Nexus-User (privater Teil mit Nexus-PW + machine-id verschlüsselt)
        pq_dsa_pub, pq_kem_pub, pq_priv_blob = pq_generate_user_keypair()
        pq_priv_enc = pq_encrypt_private_blob(pq_priv_blob, password, user_id) if pq_priv_blob else ''
        # Email-Verifikation: frischen Code generieren (User bestätigt via Code-Eingabe)
        verify_code = generate_verify_code()
        verify_exp = now + CODE_TTL_SECONDS
        conn.execute('''INSERT INTO users (id, name, email, pw_hash, is_owner, verified, verify_token, verify_expires, api_key, shinpai_id, nexus_url, nexus_verified, totp_secret, totp_enabled, pq_dsa_pub, pq_kem_pub, pq_private_enc, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)''',
                     (user_id, nx_name, email, pw_hash, int(make_owner), verify_code, verify_exp, api_key, shinpai_id, nx_url, totp_enc, totp_enabled,
                      pq_dsa_pub or '', pq_kem_pub or '', pq_priv_enc, now, now))
        conn.commit()
        conn.close()

        # FRP Admin-Pass vom Hash ableiten (nur bei Owner)
        if make_owner:
            try:
                _refresh_frp_admin(pw_hash)
            except Exception as e:
                log.warning(f'FRP-Refresh übersprungen: {e}')

        token = create_session(user_id)
        totp_info = ' + 2FA synced' if nx_totp else ''
        pq_info = ' + PQ-Keys' if pq_dsa_pub else ' (PQ-Keys LEER — oqs fehlt!)'
        role = '👑 NEXUS-OWNER' if make_owner else '🛡️ NEXUS-USER'
        log.info(f'{role} CREATED — {nx_name} ({email}) [{shinpai_id}] via {nx_url}{totp_info}{pq_info}')

        # Email-Verify-Mail versenden, falls SMTP konfiguriert — sonst SMTP-Setup muss zuerst passieren
        mail_sent = False
        if smtp_configured():
            try:
                mail_sent = send_verify_email(email, verify_code, nx_name)
                if mail_sent:
                    log.info(f'📧 VERIFY-MAIL gesendet an {email}')
            except Exception as e:
                log.warning(f'Verify-Mail-Versand fehlgeschlagen: {e}')

        # Step-Logik: Owner braucht SMTP-Setup falls noch nicht konfiguriert; User ohne SMTP = Code nur manuell
        next_step = 'smtp' if (make_owner and not smtp_configured()) else 'verify_email'
        resp = {
            'ok': True, 'step': next_step, 'token': token,
            'email': email, 'verify_mail_sent': mail_sent,
            'user': {'id': user_id, 'name': nx_name, 'email': email, 'is_owner': make_owner, 'verified': False, 'nexus_verified': True},
        }
        if recovery_seed:
            resp['recovery_seed'] = recovery_seed  # NUR EINMAL! User muss sichern!
        return resp

    return {'error': 'Unerwartete Nexus-Antwort'}


def handle_nexus_link(user_id, data):
    """Bestehenden Kneipe-Account mit ShinNexus verlinken. 3-Step Nexus-Login."""
    nx_url = (data.get('nexus_url') or '').strip().rstrip('/')
    username = (data.get('username') or '').strip()
    password = data.get('password', '')
    totp_code = data.get('totp_code', '') or data.get('totp', '')
    kneipe_totp = data.get('kneipe_totp', '')

    if not nx_url or not username:
        return {'error': 'ShinNexus-URL und Username benötigt!'}

    # 2FA PFLICHT für Nexus!
    conn_check = get_db('accounts.db')
    me = conn_check.execute('SELECT totp_enabled, totp_secret FROM users WHERE id = ?', (user_id,)).fetchone()
    conn_check.close()
    if not me or not int(me['totp_enabled'] or 0):
        return {'error': 'Bitte erst 2FA aktivieren! ShinNexus erfordert 2FA.', 'need_2fa_setup': True}
    if me and int(me['totp_enabled'] or 0) and me['totp_secret']:
        if not kneipe_totp:
            return {'error': 'Kneipe 2FA-Code nötig!', 'need_kneipe_totp': True}
        try:
            raw = vault_decrypt(me['totp_secret'])
            ts = raw.decode('utf-8') if isinstance(raw, bytes) else raw
        except Exception:
            ts = me['totp_secret']
        if not verify_totp(ts, kneipe_totp):
            return {'error': 'Falscher Kneipe 2FA-Code!'}

    auth_payload = {'username': username, 'source': 'kneipe:link'}
    if password:
        auth_payload['password'] = password
    if totp_code:
        auth_payload['totp_code'] = totp_code

    status, result = nexus_request(nx_url, '/api/auth/login', auth_payload)
    if status >= 400:
        return result

    step = result.get('step', '')
    if step in ('password', '2fa'):
        return result

    if step == 'done' and result.get('authenticated'):
        shinpai_id = result.get('shinpai_id', '')
        if not shinpai_id:
            return {'error': 'Nexus gab keine Shinpai-ID zurück!'}

        conn = get_db('accounts.db')
        # Prüfen: Shinpai-ID schon bei anderem User?
        existing = conn.execute('SELECT id, name FROM users WHERE shinpai_id = ? AND id != ?', (shinpai_id, user_id)).fetchone()
        if existing:
            conn.close()
            return {'error': f'Shinpai-ID bereits vergeben an "{existing["name"]}"!'}

        # Eigenen Account updaten + Nexus-TOTP übernehmen (ein Authenticator für alles!)
        nx_totp = result.get('totp_secret', '')
        if nx_totp:
            conn.execute('UPDATE users SET shinpai_id = ?, nexus_url = ?, nexus_verified = 1, totp_secret = ?, totp_enabled = 1, updated_at = ? WHERE id = ?',
                          (shinpai_id, nx_url, vault_encrypt(nx_totp), time.time(), user_id))
        else:
            conn.execute('UPDATE users SET shinpai_id = ?, nexus_url = ?, nexus_verified = 1, updated_at = ? WHERE id = ?',
                          (shinpai_id, nx_url, time.time(), user_id))
        conn.commit()
        user = conn.execute('SELECT name FROM users WHERE id = ?', (user_id,)).fetchone()
        conn.close()
        totp_info = ' + 2FA synced' if nx_totp else ''
        log.info(f'🛡️ NEXUS LINKED — {user["name"]} [{shinpai_id}] ↔ {nx_url}{totp_info}')
        return {'ok': True, 'shinpai_id': shinpai_id, 'nexus_url': nx_url, 'message': f'Verlinkt mit ShinNexus! ID: {shinpai_id}'}

    return {'error': 'Unerwartete Nexus-Antwort'}


def handle_nexus_create(user_id, data):
    """Kneipe-Daten 1:1 als Nexus-Identität eintragen. Verifizieren → Shinpai-ID → fertig."""
    try:
        return _nexus_create_inner(user_id, data)
    except Exception as e:
        log.error(f'❌ NEXUS CREATE CRASH: {e}')
        import traceback; traceback.print_exc()
        return {'error': f'Nexus-Fehler: {e}'}

def _nexus_create_inner(user_id, data):
    nx_url = (data.get('nexus_url') or '').strip().rstrip('/')
    kneipe_pw = data.get('password', '')
    kneipe_totp = data.get('totp_code', '') or data.get('totp', '')

    if not nx_url or not nx_url.startswith('http'):
        return {'error': 'Gültige ShinNexus-URL benötigt! (https://...)'}
    if not kneipe_pw:
        return {'error': 'Kneipe-Passwort zur Bestätigung nötig!'}

    # Kneipe-Owner verifizieren (Passwort + optional 2FA)
    conn = get_db('accounts.db')
    user = conn.execute('SELECT name, email, pw_hash, totp_secret, totp_enabled, shinpai_id, verified FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    if not user:
        return {'error': 'User nicht gefunden!'}
    if not verify_pw(kneipe_pw, user['pw_hash']):
        return {'error': 'Falsches Kneipe-Passwort!'}
    if int(user['totp_enabled'] or 0) and user['totp_secret']:
        if not kneipe_totp:
            return {'error': 'Kneipe 2FA-Code nötig!', 'need_totp': True}
        try:
            raw = vault_decrypt(user['totp_secret'])
            totp_secret = raw.decode('utf-8') if isinstance(raw, bytes) else raw
        except Exception:
            totp_secret = user['totp_secret']
        if not verify_totp(totp_secret, kneipe_totp):
            return {'error': 'Falscher Kneipe 2FA-Code!'}

    # 2FA PFLICHT für Nexus!
    if not int(user['totp_enabled'] or 0):
        return {'error': 'Bitte erst 2FA aktivieren! ShinNexus erfordert 2FA.', 'need_2fa_setup': True}

    # Kneipe TOTP-Secret entschlüsseln → an Nexus durchreichen (gleicher Authenticator-Eintrag!)
    try:
        raw = vault_decrypt(user['totp_secret'])
        kneipe_totp_secret = raw.decode('utf-8') if isinstance(raw, bytes) else raw
    except Exception:
        kneipe_totp_secret = ''

    # Nexus-Account erstellen — TOTP + SMTP + Domain alles mitschicken!
    reg_data = {
        'username': user['name'],
        'email': user['email'],
        'password': kneipe_pw,
        'totp_secret': kneipe_totp_secret,
        'totp_code': kneipe_totp,
        'email_verified': bool(int(user['verified'] or 0)),
        'domain': 'hive.shidow.de',
        'public_url': nx_url,
    }
    # SMTP-Daten der Kneipe übernehmen
    smtp_cfg = get_smtp_config()
    if smtp_cfg.get('smtp_host'):
        reg_data['smtp'] = {
            'host': smtp_cfg['smtp_host'],
            'port': int(smtp_cfg.get('smtp_port', 587)),
            'user': smtp_cfg['smtp_user'],
            'password': smtp_cfg.get('smtp_pass', ''),
            'from': smtp_cfg.get('smtp_from', smtp_cfg['smtp_user']),
        }

    status, result = nexus_request(nx_url, '/api/auth/register', reg_data)

    if status >= 400 and 'error' in (result or {}):
        return result

    step = result.get('step', '')

    if step == 'done' and result.get('shinpai_id'):
        shinpai_id = result['shinpai_id']
        conn2 = get_db('accounts.db')
        conn2.execute('UPDATE users SET shinpai_id = ?, nexus_url = ?, nexus_verified = 1, updated_at = ? WHERE id = ?',
                       (shinpai_id, nx_url, time.time(), user_id))
        conn2.commit()
        conn2.close()
        recovery = result.get('recovery_seed', '')
        log.info(f'🛡️ NEXUS CREATED+LINKED — {user["name"]} [{shinpai_id}] → {nx_url}')
        return {'ok': True, 'shinpai_id': shinpai_id, 'recovery_seed': recovery,
                'message': f'Nexus-Account erstellt und verlinkt! ID: {shinpai_id}'}

    return result or {'error': 'Unerwartete Nexus-Antwort'}


def handle_nexus_unlink(user_id):
    """Nexus-Verlinkung lösen."""
    conn = get_db('accounts.db')
    user = conn.execute('SELECT name, shinpai_id FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.execute('UPDATE users SET nexus_url = "", nexus_verified = 0, updated_at = ? WHERE id = ?',
                  (time.time(), user_id))
    conn.commit()
    conn.close()
    log.info(f'🔓 NEXUS UNLINKED — {user["name"]} [{user["shinpai_id"]}]')
    return {'ok': True, 'message': 'Nexus-Verlinkung gelöst.'}


def handle_register(data):
    """User-Registrierung (braucht SMTP für Verifizierung)"""
    if not has_owner():
        return {'error': 'Ersteinrichtung nötig! Noch kein Owner gesetzt.'}

    # User-Limit: 200 pro Kneipe!
    conn_check = get_db('accounts.db')
    user_count = conn_check.execute('SELECT COUNT(*) as cnt FROM users WHERE verified = 1 AND is_guest = 0 AND is_owner = 0').fetchone()['cnt']
    conn_check.close()
    if user_count >= KNEIPE_MAX_USERS:
        return {
            'error': f'Diese Kneipe ist voll! (Max {KNEIPE_MAX_USERS} Stammgäste)',
            'nexus_url': NEXUS_REGISTER_URL,
            'hint': 'Registrier dich bei ShinNexus und genieße Souveränität! 🍺',
        }

    # Registrierungscode prüfen wenn aktiv
    gcfg = _guest_config()
    if gcfg.get('register_code_required'):
        submitted_code = data.get('register_code', '').strip()
        if submitted_code != gcfg.get('register_code', ''):
            return {'error': 'Falscher Registrierungscode! Frag den Kneipenbesitzer.'}

    name = data.get('name', '').strip()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    if not NAME_RE.match(name):
        return {'error': 'Name: 1-12 Zeichen, nur A-Z, a-z, 0-9'}
    if not EMAIL_RE.match(email):
        return {'error': 'Ungültige Email-Adresse'}
    if len(password) < 8:
        return {'error': 'Passwort: mindestens 8 Zeichen'}

    if not smtp_configured():
        return {'error': 'Email-Versand noch nicht konfiguriert. Bitte Owner kontaktieren.'}

    conn = get_db('accounts.db')
    c = conn.cursor()

    existing = c.execute('SELECT id FROM users WHERE name = ? OR email = ?', (name, email)).fetchone()
    if existing:
        conn.close()
        return {'error': 'Registrierung fehlgeschlagen. Bitte andere Daten versuchen.'}

    user_id = str(uuid.uuid4())
    verify_code = generate_verify_code()
    now = time.time()
    code_expires = now + CODE_TTL_SECONDS

    api_key = f"kneipe_{secrets.token_hex(24)}"
    shinpai_id = _generate_shinpai_id(name, email)
    # PQ-Keypair generieren — Private-Blob mit User-PW + machine-id verschlüsselt
    pq_dsa_pub, pq_kem_pub, pq_priv_blob = pq_generate_user_keypair()
    pq_priv_enc = pq_encrypt_private_blob(pq_priv_blob, password, user_id) if pq_priv_blob else ''
    c.execute('''INSERT INTO users (id, name, email, pw_hash, verify_token, verify_expires, api_key, shinpai_id, pq_dsa_pub, pq_kem_pub, pq_private_enc, created_at, updated_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (user_id, name, email, hash_pw(password), verify_code, code_expires, api_key, shinpai_id,
               pq_dsa_pub or '', pq_kem_pub or '', pq_priv_enc, now, now))
    conn.commit()
    conn.close()

    # Verifizierungs-Mail senden (mit Code, nicht Link)
    mail_sent = send_verify_email(email, verify_code, name)

    log.info(f'📝 REGISTER — {name} ({email}) PQ: {"✅" if pq_dsa_pub else "❌"} Mail: {"✅" if mail_sent else "❌"}')

    return {
        'ok': True,
        'user_id': user_id,
        'email': email,
        'step': 'code' if mail_sent else 'error',
        'message': 'Check deine Mails! 6-stelliger Code wurde gesendet.' if mail_sent else 'Registriert, aber Mail konnte nicht gesendet werden. Kontaktiere den Owner.',
    }

def handle_resend_verify(data):
    """Verifizierungs-Mail erneut senden — max 3 pro Tag pro Account"""
    name = data.get('name', '').strip()
    password = data.get('password', '')

    if not name or not password:
        return {'error': 'Name und Passwort eingeben'}

    conn = get_db('accounts.db')
    user = conn.execute('SELECT * FROM users WHERE name = ?', (name,)).fetchone()

    if not user or (not data.get('_session_auth') and not verify_pw(password, user['pw_hash'])):
        conn.close()
        return {'error': 'Name oder Passwort falsch'}

    if user['verified']:
        conn.close()
        return {'error': 'Account ist bereits verifiziert!'}

    # Rate-Limit: max 3 Resends pro Tag (in config-Tabelle tracken)
    today = time.strftime('%Y-%m-%d')
    resend_key = f'resend_{user["id"]}_{today}'
    row = conn.execute('SELECT value FROM config WHERE key = ?', (resend_key,)).fetchone()
    resend_count = int(row['value']) if row else 0

    if resend_count >= 3:
        conn.close()
        return {'error': 'Heute schon 3 Mails gesendet. Morgen nochmal versuchen!'}

    # Neuen Code generieren + Mail senden
    new_code = generate_verify_code()
    now = time.time()
    conn.execute('UPDATE users SET verify_token = ?, verify_expires = ?, updated_at = ? WHERE id = ?',
                 (new_code, now + CODE_TTL_SECONDS, now, user['id']))
    conn.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)',
                 (resend_key, str(resend_count + 1)))
    conn.commit()
    conn.close()

    mail_sent = send_verify_email(user['email'], new_code, name)
    log.info(f'📧 RESEND VERIFY CODE — {name} ({resend_count + 1}/3 heute) Mail: {"✅" if mail_sent else "❌"}')

    return {
        'ok': True,
        'message': f'Verifizierungs-Mail wurde erneut gesendet! ({resend_count + 1}/3 heute)',
        'remaining': 3 - resend_count - 1,
    }

def handle_verify(token):
    conn = get_db('accounts.db')
    c = conn.cursor()
    user = c.execute('SELECT id, name FROM users WHERE verify_token = ?', (token,)).fetchone()
    if not user:
        conn.close()
        return {'error': 'Ungültiger Verifizierungs-Token'}

    c.execute('UPDATE users SET verified = 1, verify_token = NULL, updated_at = ? WHERE id = ?',
              (time.time(), user['id']))
    conn.commit()
    conn.close()

    log.info(f'✅ VERIFIED — {user["name"]}')
    return {'ok': True, 'message': 'Email verifiziert! Du kannst jetzt spielen.'}

def handle_login(data):
    name = data.get('name', '').strip()
    password = data.get('password', '')
    totp_code = data.get('totp', '')

    conn = get_db('accounts.db')
    user = conn.execute('SELECT * FROM users WHERE name = ?', (name,)).fetchone()

    if not user or not verify_pw(password, user['pw_hash']):
        conn.close()
        return {'error': 'Name oder Passwort falsch'}

    # Blockliste prüfen
    block_info = blocklist_is_blocked(name)
    if block_info and not user['is_owner']:
        conn.close()
        exp = time.strftime('%d.%m.%Y', time.localtime(block_info.get('expires_at', 0)))
        reason = block_info.get('reason', '')
        return {'error': f'Account gesperrt bis {exp}.' + (f' Grund: {reason}' if reason else '')}

    # Nexus-verifizierte User automatisch als email-verifiziert markieren
    if not user['verified'] and int(user['nexus_verified'] or 0):
        conn.execute('UPDATE users SET verified = 1 WHERE id = ?', (user['id'],))
        conn.commit()
        log.info(f'📧 Auto-Verify: {name} (Nexus-verifiziert → Email-verifiziert)')
    elif not user['verified']:
        user_email = user['email']
        conn.close()
        return {'error': 'Email noch nicht verifiziert! Check deine Mails.', 'email': user_email, 'needs_verify': True}

    # Owner-Login entsperrt den Vault (Schicht 1 Server-Master-Unlock).
    # Andere User können bei gesperrtem Vault nicht einloggen.
    if user['is_owner']:
        if not vault_is_unlocked():
            if not vault_unlock(password):
                conn.close()
                return {'error': 'Vault-Entsperren fehlgeschlagen (Passwort falsch)'}
            try: _ensure_keypair()
            except Exception: pass
    else:
        if not vault_is_unlocked():
            conn.close()
            return {'error': 'Server ist gesperrt — warte bis der Owner entsperrt.', 'server_locked': True}

    if int(user['totp_enabled'] or 0):
        if not totp_code:
            conn.close()
            return {'error': '2FA Code benötigt', 'requires_2fa': True}
        try:
            totp_ok = verify_totp(vault_decrypt(user['totp_secret']), totp_code)
        except Exception:
            totp_ok = False
        if not totp_ok:
            has_nexus = int(user['nexus_verified'] or 0)
            conn.close()
            if has_nexus:
                return {'error': '2FA stimmt nicht! Nexus geändert? Nutze "Mit ShinNexus anmelden" oder "Passwort vergessen".', 'desync': True}
            return {'error': 'Falscher 2FA Code!'}

    conn.close()
    token = create_session(user['id'])
    # Vision 1: beide Sub-Stores clearen (Session-only)
    _durchsage_reset(user['id'])
    _tresen_reset(user['id'])
    log.info(f'🔑 LOGIN — {name}')

    return {
        'ok': True,
        'token': token,
        'user': {
            'id': user['id'],
            'name': user['name'],
            'profile_pic': user['profile_pic'] or '',
            'age': user['age'] or 'undefined',
            'is_owner': bool(user['is_owner']),
            'verified': True,
        }
    }

def handle_profile(user_id):
    conn_acc = get_db('accounts.db')
    user = conn_acc.execute('SELECT name, email, profile_pic, age, totp_enabled, api_key, is_bot, tts_voice, shinpai_id, nexus_url, nexus_verified, verification_level, verified FROM users WHERE id = ?', (user_id,)).fetchone()
    conn_acc.close()
    if not user:
        return {'error': 'User nicht gefunden'}
    # Lazy-Sync: Shield-Status vom Nexus holen und in der Kneipe-DB aktualisieren
    nx_url = user['nexus_url'] or ''
    sid = user['shinpai_id'] or ''
    if nx_url and sid and int(user['nexus_verified'] or 0):
        try:
            _sc, _sd = nexus_request(nx_url, f'/api/public/shield?shinpai_id={sid}')
            if _sc == 200 and isinstance(_sd, dict):
                new_lvl = int(_sd.get('verification_level', 0))
                if new_lvl != int(user['verification_level'] or 0):
                    _conn_u = get_db('accounts.db')
                    _conn_u.execute('UPDATE users SET verification_level = ? WHERE id = ?', (new_lvl, user_id))
                    _conn_u.commit()
                    _conn_u.close()
                    user = dict(user)
                    user['verification_level'] = new_lvl
                # Code-Hash + Version für Trust-Prüfung merken
                user = dict(user)
                user['nexus_code_hash'] = _sd.get('code_hash', '')
                user['nexus_version'] = _sd.get('version', '')
        except Exception:
            pass  # Nexus nicht erreichbar → alten Wert behalten

    conn_gp = get_db('gameplay.db')
    plays = conn_gp.execute('SELECT COUNT(*) as cnt FROM plays WHERE user_id = ?', (user_id,)).fetchone()['cnt']
    titles = conn_gp.execute('SELECT title_id, earned_at FROM titles WHERE user_id = ?', (user_id,)).fetchall()
    stammgast = conn_gp.execute('SELECT COUNT(*) as cnt FROM plays WHERE user_id = ? AND is_stammgast = 1', (user_id,)).fetchone()['cnt']

    # Element-Verteilung
    elements = conn_gp.execute('SELECT element, COUNT(*) as cnt FROM plays WHERE user_id = ? GROUP BY element', (user_id,)).fetchall()
    conn_gp.close()

    gesamt = calculate_gesamt_titel(user_id)

    # Element-Prozente berechnen (inkl. Stammgast)
    element_dict = {e['element']: e['cnt'] for e in elements}
    if stammgast > 0:
        element_dict['stammgast'] = stammgast
    element_pct = {}
    if plays > 0:
        for el, cnt in element_dict.items():
            element_pct[el] = round(cnt / plays * 100, 1)

    return {
        'name': user['name'],
        'email': user['email'],
        'profile_pic': user['profile_pic'] or '',
        'age': user['age'] or 'undefined',
        'totp_enabled': bool(user['totp_enabled']),
        'is_bot': bool(user['is_bot']),
        'gesamt_titel': gesamt,
        'titles': [{'id': t['title_id'], 'earned_at': t['earned_at']} for t in titles],
        'stammgast': stammgast,
        'plays': plays,
        'elements': element_dict,
        'elements_pct': element_pct,
        'tts_voice': user['tts_voice'] or 'de-DE-ConradNeural',
        'tts_voices': BARK_VOICES_LIST + EDGE_VOICES,
        'guest_bark_enabled': _guest_config().get('bark_enabled', False),
        'guest_voice_enabled': _guest_config().get('voice_enabled', False),
        'guest_play_enabled': _guest_config().get('play_enabled', False),
        'bark_available': _voice_server_available(),
        'shinpai_id': user['shinpai_id'] or '',
        'nexus_url': user['nexus_url'] or '',
        'verified': bool(int(user['verified'] or 0)),
        'nexus_verified': bool(int(user['nexus_verified'] or 0)),
        'verification_level': int(user['verification_level'] or 0),
        'nexus_code_hash': (user['nexus_code_hash'] if 'nexus_code_hash' in user.keys() else ''),
        'nexus_version': (user['nexus_version'] if 'nexus_version' in user.keys() else ''),
    }

# --- GAME SESSIONS (Anti-Cheat: Spiel muss gestartet werden bevor finish) ---
game_sessions = {}  # game_token → {user_id, theme_id, started_at, answers: []}
game_sessions_lock = threading.Lock()

FINISH_COOLDOWN = {}  # user_id → last_finish_time
FINISH_COOLDOWN_SECS = 10  # Min 10 Sekunden zwischen zwei Finishes

def handle_play_start(user_id, data):
    """Neues Spiel starten — gibt Game-Token zurück"""
    if is_guest_user(user_id) and not _guest_config().get('play_enabled', False):
        return {'error': 'Gäste dürfen gucken, nicht anfassen! 👀'}
    theme_id = data.get('theme_id', '')
    theme = load_theme(theme_id)
    if not theme:
        return {'error': 'Thema nicht gefunden'}

    game_token = secrets.token_hex(16)
    with game_sessions_lock:
        # Alte Sessions aufräumen (>10 Min = abgelaufen)
        stale = [k for k, g in game_sessions.items() if time.time() - g['started_at'] > 600]
        for k in stale:
            del game_sessions[k]
        # Max 3 aktive Games pro User
        active = sum(1 for g in game_sessions.values() if g['user_id'] == user_id)
        if active >= 3:
            return {'error': 'Zu viele aktive Spiele. Beende erst eins.'}
        game_sessions[game_token] = {
            'user_id': user_id,
            'theme_id': theme_id,
            'started_at': time.time(),
            'answers': [],
        }

    log.info(f'🎮 PLAY START — {get_username(user_id)} [{theme_id}]')
    return {'ok': True, 'game_token': game_token, 'theme_id': theme_id}

def handle_play_answer(user_id, data):
    """Antwort im laufenden Spiel registrieren"""
    game_token = data.get('game_token', '')
    answer = data.get('answer', '')

    if answer not in ('A', 'B', 'C'):
        return {'error': 'Ungültige Antwort (A/B/C)'}

    with game_sessions_lock:
        game = game_sessions.get(game_token)
        if not game:
            return {'error': 'Ungültiges Spiel-Token'}
        if game['user_id'] != user_id:
            return {'error': 'Nicht dein Spiel'}
        game['answers'].append(answer)

    return {'ok': True, 'answers_count': len(game['answers'])}

def handle_play_finish(user_id, data):
    """Thema abschließen: Antworten auswerten"""
    game_token = data.get('game_token', '')

    # Game-Session validieren
    with game_sessions_lock:
        game = game_sessions.get(game_token)
        if not game:
            return {'error': 'Ungültiges Spiel-Token. Starte zuerst ein Spiel mit /api/play!'}
        if game['user_id'] != user_id:
            return {'error': 'Nicht dein Spiel'}
        # Session entfernen (einmalig nutzbar!)
        del game_sessions[game_token]

    # Cooldown: min 10 Sekunden zwischen Finishes
    now = time.time()
    last_finish = FINISH_COOLDOWN.get(user_id, 0)
    if now - last_finish < FINISH_COOLDOWN_SECS:
        return {'error': f'Zu schnell! Warte {FINISH_COOLDOWN_SECS} Sekunden zwischen Spielen.'}
    FINISH_COOLDOWN[user_id] = now

    # Mindest-Spielzeit: 5 Sekunden für Menschen, 0.1 Sekunden für Bots
    conn_bot = get_db('accounts.db')
    _is_bot = conn_bot.execute('SELECT is_bot FROM users WHERE id = ?', (user_id,)).fetchone()
    conn_bot.close()
    min_time = 0.1 if (_is_bot and _is_bot['is_bot']) else 5
    elapsed = now - game['started_at']
    if elapsed < min_time:
        return {'error': f'Zu schnell gespielt. Mindestens {min_time} Sekunden pro Thema.'}

    theme_id = game['theme_id']
    answers = game['answers']
    client_hour = data.get('client_hour', 12)

    # Theme laden
    theme = load_theme(theme_id)
    if not theme:
        return {'error': 'Thema nicht gefunden'}

    if not answers or len(answers) < 3:
        return {'error': 'Zu wenige Antworten'}

    # Element berechnen
    element = calculate_element(answers, theme)

    # Flags aus Antworten extrahieren
    triggered_flags = []
    is_stammgast = False
    is_mauerblümchen = element == 'mauerblümchen'

    # Stammgast: alle C + stammgast-fähig
    if theme.get('stammgast_capable') and all(a == 'C' for a in answers):
        is_stammgast = True

    # Flags aus Theme-Daten prüfen
    for layer_id, layer in theme.get('layers', {}).items():
        for ans in layer.get('answers', []):
            if ans.get('flags'):
                # Prüfe ob diese Antwort gewählt wurde (vereinfacht)
                for flag in ans.get('flags', []):
                    if flag not in triggered_flags:
                        triggered_flags.append(flag)

    # Speichern
    play_id = str(uuid.uuid4())
    conn = get_db('gameplay.db')
    conn.execute('''INSERT INTO plays (id, user_id, theme_id, answers, element, flags_triggered,
                    is_stammgast, is_mauerblümchen, client_hour, played_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                 (play_id, user_id, theme_id, json.dumps(answers), element,
                  json.dumps(triggered_flags), int(is_stammgast), int(is_mauerblümchen),
                  client_hour, time.time()))
    conn.commit()
    conn.close()

    # Kumulative Titel checken
    titel_result = check_kumulative_titel(user_id)

    # Gesamt-Titel
    gesamt = calculate_gesamt_titel(user_id)

    # Themenbereich-Counter inkrementieren + Auto-Freischaltung bei 100
    conn_acc = get_db('accounts.db')
    conn_acc.execute('UPDATE users SET themen_plays_counter = themen_plays_counter + 1 WHERE id = ?', (user_id,))
    user_row = conn_acc.execute('SELECT themen_plays_counter, themen_access FROM users WHERE id = ?', (user_id,)).fetchone()
    if user_row and user_row['themen_plays_counter'] >= 100 and not user_row['themen_access']:
        conn_acc.execute('UPDATE users SET themen_access = 1 WHERE id = ?', (user_id,))
        log.info(f'🔓 THEMENBEREICH FREIGESCHALTET — {get_username(user_id)} (100 Spiele erreicht!)')
    conn_acc.commit()
    conn_acc.close()

    # Platzierung
    teilnehmer = get_teilnehmer()
    rank = next((t['rank'] for t in teilnehmer if t['name'] == get_username(user_id)), len(teilnehmer) + 1)

    log.info(f'🍺 PLAY — {get_username(user_id)} [{theme_id}] → {element.upper()} '
             f'{"🍺STAMMGAST" if is_stammgast else ""} '
             f'{"🌸MAUER" if is_mauerblümchen else ""} '
             f'Rank:#{rank}')

    return {
        'ok': True,
        'element': element,
        'gesamt_titel': gesamt,
        'new_titles': titel_result['new_titles'],
        'stammgast_count': titel_result['stammgast_count'],
        'is_stammgast': is_stammgast,
        'rank': rank,
        'is_cheater': titel_result['is_cheater'],
    }

def get_username(user_id):
    conn = get_db('accounts.db')
    user = conn.execute('SELECT name FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return user['name'] if user else '???'

def handle_delete_account(user_id):
    """Account komplett löschen — DSGVO + Nexus-Unlink"""
    conn = get_db('accounts.db')
    user = conn.execute('SELECT name, nexus_verified, nexus_url, shinpai_id FROM users WHERE id = ?', (user_id,)).fetchone()
    name = user['name'] if user else '?'
    had_nexus = int(user['nexus_verified'] or 0) if user else 0
    conn.close()

    # Gameplay-Daten löschen
    conn = get_db('gameplay.db')
    conn.execute('DELETE FROM plays WHERE user_id = ?', (user_id,))
    conn.execute('DELETE FROM titles WHERE user_id = ?', (user_id,))
    conn.execute('DELETE FROM cheater_votes WHERE target_name = ?', (name,))
    conn.execute('DELETE FROM cheater_vote_sessions WHERE target_name = ?', (name,))
    conn.commit()
    conn.close()

    # Account löschen
    conn = get_db('accounts.db')
    conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()

    # Sessions löschen
    with sessions_lock:
        to_delete = [k for k, v in sessions.items() if v['user_id'] == user_id]
        for k in to_delete:
            del sessions[k]

    nexus_info = f' + Nexus unlinked [{user["shinpai_id"]}]' if had_nexus else ''
    log.info(f'🗑️ ACCOUNT DELETED — {name} ({user_id}){nexus_info}')
    return {'ok': True, 'nexus_removed': bool(had_nexus),
            'message': f'Account und alle Daten gelöscht.{" ShinNexus-Verbindung getrennt." if had_nexus else ""}'}

# --- HTTP HANDLER ---
ALLOWED_ORIGINS = ['https://bar.shinpai.de', 'http://localhost:4567', 'http://192.168.0.3:4567']

class GameHandler(SimpleHTTPRequestHandler):
    def _get_origin(self):
        origin = self.headers.get('Origin', '')
        return origin if origin in ALLOWED_ORIGINS else ALLOWED_ORIGINS[0]

    def _get_client_ip(self):
        forwarded = self.headers.get('X-Forwarded-For', '')
        return forwarded.split(',')[0].strip() if forwarded else self.client_address[0]

    def _is_windows_user(self):
        """Windows = permanenter Überwachungs-Flag. Windows ist Malware."""
        ua = self.headers.get('User-Agent', '')
        return 'Windows NT' in ua or 'Windows' in ua

    def _is_chromeos_user(self):
        """ChromeOS = Google-Telemetrie = permanenter Flag."""
        ua = self.headers.get('User-Agent', '')
        return 'CrOS' in ua

    def _get_os_flag(self):
        """OS-basierter Überwachungs-Flag. None = kann entfernt werden, String = permanent."""
        if self._is_windows_user():
            return 'windows'
        if self._is_chromeos_user():
            return 'chromeos'
        return None

    def _get_session(self):
        auth = self.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            return get_session(auth[7:])
        # API-Key Auth (auto-bot-flag!)
        if auth.startswith('ApiKey '):
            api_key = auth[7:]
            conn = get_db('accounts.db')
            user = conn.execute('SELECT id, name, is_bot FROM users WHERE api_key = ? AND verified = 1', (api_key,)).fetchone()
            if not user:
                conn.close()
                return None
            # Auto-Bot-Flag bei erstem API-Key Zugriff!
            if not user['is_bot']:
                conn.execute('UPDATE users SET is_bot = 1 WHERE id = ?', (user['id'],))
                conn.commit()
                log.info(f'🤖 BOT FLAGGED — {user["name"]} (API-Key genutzt)')
            conn.close()
            return {'user_id': user['id'], 'created': time.time(), 'last_active': time.time(), 'via_api': True}
        return None

    def _send_security_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data: https://shinpai.de; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; media-src 'self' blob: data:; frame-ancestors 'none'")
        self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')

    def _json_response(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', self._get_origin())
        self.send_header('Vary', 'Origin')
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length)) if length > 0 else {}

    def do_GET(self):
        path = urlparse(self.path)
        ip = self._get_client_ip()

        if not check_rate_limit(ip):
            self._json_response({'error': 'Rate limit'}, 429)
            return

        # VAULT-GATE: bei gesperrtem Vault nur Whitelist-Endpoints durchlassen
        if not _vault_gate_allowed('GET', path.path):
            self._json_response({
                'error': 'Server ist gesperrt — Owner muss entsperren',
                'server_locked': True,
                'first_start': not vault_exists(),
            }, 503)
            return

        if path.path == '/api/nexus-whitelist':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_nw = get_db('accounts.db')
            owner_nw = conn_nw.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_nw.close()
            if not owner_nw or not owner_nw['is_owner']:
                self._json_response({'error': 'Nur Owner'}, 403); return
            wl = nexus_whitelist_get()
            self._json_response({
                'ok': True,
                'whitelist': wl,
                'mode': 'strict' if wl else 'permissive',
            })
            return

        # ── BTC GET Endpoints ──
        if path.path == '/api/btc/wallet':
            sess = self._get_session()
            if not sess: self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_bw = get_db('accounts.db')
            owner_bw = conn_bw.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_bw.close()
            if not owner_bw or not owner_bw['is_owner']: self._json_response({'error': 'Nur Owner'}, 403); return
            wallet = _btc_wallet_load()
            if not wallet:
                self._json_response({'has_wallet': False}); return
            # Wallet-Entries mit Revoke-Status aus anchor.json synken
            anchor = _btc_read_anchor_json()
            for e in (wallet.get("entries") or []):
                for h in (anchor.get("history") or []):
                    if e.get("code_hash") == h.get("code_hash") and h.get("revoked"):
                        e["revoked"] = True; e["revoked_at"] = h.get("revoked_at")
            # Balance von mempool.space holen
            balance_sats = 0
            try:
                import urllib.request as _ur
                with _ur.urlopen(f"https://mempool.space/api/address/{wallet.get('address','')}", timeout=10) as _resp:
                    _addr_data = json.loads(_resp.read())
                    _chain = _addr_data.get("chain_stats", {})
                    _mempool = _addr_data.get("mempool_stats", {})
                    balance_sats = (_chain.get("funded_txo_sum", 0) - _chain.get("spent_txo_sum", 0)
                                  + _mempool.get("funded_txo_sum", 0) - _mempool.get("spent_txo_sum", 0))
            except Exception:
                pass
            self._json_response({
                'has_wallet': True, 'address': wallet.get('address', ''),
                'balance_sats': balance_sats,
                'mnemonic': wallet.get('mnemonic', ''),
                'entries': wallet.get('entries', []),
                'pending_anchor': wallet.get('pending_anchor'),
                'pending_revoke': wallet.get('pending_revoke'),
            }); return

        if path.path == '/api/btc/anchor/preview':
            sess = self._get_session()
            if not sess: self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_ap = get_db('accounts.db')
            owner_ap = conn_ap.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_ap.close()
            if not owner_ap or not owner_ap['is_owner']: self._json_response({'error': 'Nur Owner'}, 403); return
            wallet = _btc_wallet_load()
            if not wallet or not wallet.get('wif'): self._json_response({'error': 'Kein Wallet'}, 400); return
            fee_sats, sat_per_vb = _btc_estimate_fee_sats()
            price_eur = _btc_get_price_eur()
            code_hash = _btc_get_code_hash()
            fee_eur = (fee_sats / 100_000_000) * price_eur if price_eur else 0
            self._json_response({
                'fee_sats': fee_sats, 'sat_per_vb': sat_per_vb,
                'fee_eur': round(fee_eur, 4), 'btc_price_eur': round(price_eur, 2),
                'code_hash': code_hash, 'code_hash_short': code_hash[:32],
                'address': wallet.get('address', ''),
            }); return

        if path.path == '/api/btc/anchor/status':
            sess = self._get_session()
            if not sess: self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            wallet = _btc_wallet_load()
            pending = wallet.get('pending_anchor') if wallet else None
            if not pending: self._json_response({'status': 'none'}); return
            check = _btc_check_tx_confirmed(pending['txid'])
            if check['confirmed']:
                pending['status'] = 'confirmed'
                pending['block_height'] = check['block_height']
                # Auch den entries-Eintrag aktualisieren (gleiche TXID finden)
                for e in (wallet.get('entries') or []):
                    if e.get('txid') == pending['txid']:
                        e['status'] = 'confirmed'
                        e['block_height'] = check['block_height']
                wallet['pending_anchor'] = None
                _btc_wallet_save(wallet)
                _btc_write_anchor_json(pending)
                _btc_live_verify_and_persist()
            self._json_response({'status': pending.get('status', 'pending'), 'txid': pending['txid'],
                                  'confirmed': check['confirmed'], 'block_height': check.get('block_height', 0)}); return

        if path.path == '/api/btc/revoke/preview':
            sess = self._get_session()
            if not sess: self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_rp = get_db('accounts.db')
            owner_rp = conn_rp.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_rp.close()
            if not owner_rp or not owner_rp['is_owner']: self._json_response({'error': 'Nur Owner'}, 403); return
            wallet = _btc_wallet_load()
            if not wallet: self._json_response({'error': 'Kein Wallet'}, 400); return
            fee_sats, sat_per_vb = _btc_estimate_fee_sats()
            price_eur = _btc_get_price_eur()
            fee_eur = (fee_sats / 100_000_000) * price_eur if price_eur else 0
            active = [e for e in (wallet.get('entries') or []) if not e.get('revoked')]
            self._json_response({
                'fee_sats': fee_sats, 'fee_eur': round(fee_eur, 4),
                'active_versions': [{'version': e.get('version', '?'), 'code_hash': e.get('code_hash', ''),
                                      'txid': e.get('txid', ''), 'timestamp': e.get('timestamp', 0)} for e in active],
            }); return

        if path.path == '/api/btc/revoke/status':
            sess = self._get_session()
            if not sess: self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            wallet = _btc_wallet_load()
            pending = wallet.get('pending_revoke') if wallet else None
            if not pending: self._json_response({'status': 'none'}); return
            check = _btc_check_tx_confirmed(pending['txid'])
            if check['confirmed']:
                pending['status'] = 'confirmed'
                # Wallet-Entries aktualisieren (revoked markieren)
                rh_wallet = pending.get('code_hash', '')[:32]
                for e in (wallet.get('entries') or []):
                    if (e.get('code_hash') or '')[:32] == rh_wallet:
                        e['revoked'] = True
                        e['revoked_at'] = int(time.time())
                        e['revoke_txid'] = pending['txid']
                wallet['pending_revoke'] = None
                _btc_wallet_save(wallet)
                anchor = _btc_read_anchor_json()
                rh = pending.get('code_hash', '')[:32]
                for entry in (anchor.get('history') or []):
                    if (entry.get('code_hash') or '')[:32] == rh:
                        entry['revoked'] = True; entry['revoked_at'] = int(time.time()); entry['revoke_txid'] = pending['txid']
                if (anchor.get('code_hash') or '')[:32] == rh:
                    anchor['revoked'] = True; anchor['revoked_at'] = int(time.time())
                _btc_write_anchor_json_raw(anchor)
            self._json_response({'status': pending.get('status', 'pending'), 'txid': pending['txid'],
                                  'confirmed': check['confirmed']}); return

        if path.path == '/api/chain/info':
            anchor = _btc_read_anchor_json()
            code_hash = _btc_get_code_hash()
            self._json_response({
                'version': anchor.get('version', '') or VERSION,
                'code_hash': code_hash,
                'txid': anchor.get('txid', ''),
                'btc_address': anchor.get('btc_address', ''),
                'company': anchor.get('company', ''),
                'revoked': anchor.get('revoked', False),
                'timestamp': anchor.get('timestamp', 0),
                'live_verify_status': anchor.get('live_verify_status', ''),
                'current_hash_matches': code_hash == anchor.get('code_hash', ''),
            }); return

        if path.path == '/api/owner/igni':
            sess = self._get_session()
            if not sess: self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_ig = get_db('accounts.db')
            owner_ig = conn_ig.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_ig.close()
            if not owner_ig or not owner_ig['is_owner']: self._json_response({'error': 'Nur Owner'}, 403); return
            _igni_init()
            active = bool(_VAULT_BOOTSTRAP and _VAULT_BOOTSTRAP.exists())
            # Mode aus DB-Config lesen
            conn_ig2 = get_db('accounts.db')
            mode_row = conn_ig2.execute('SELECT value FROM config WHERE key = ?', ('owner_vault_mode',)).fetchone()
            conn_ig2.close()
            mode = mode_row['value'] if mode_row and mode_row['value'] else 'standard'
            self._json_response({
                'mode': mode, 'active': active,
                'path': str(_IGNITION_DIR) if _IGNITION_DIR else '',
            }); return

        if path.path == '/api/whitelist':
            wl = nexus_whitelist_get()
            self._json_response({'ok': True, 'whitelist': wl}); return

        if path.path == '/api/blocklist':
            sess = self._get_session()
            if not sess: self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_bl = get_db('accounts.db')
            owner_bl = conn_bl.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_bl.close()
            if not owner_bl or not owner_bl['is_owner']: self._json_response({'error': 'Nur Owner'}, 403); return
            bl = _blocklist_load()
            bl = _blocklist_cleanup(bl)
            self._json_response({'ok': True, 'blocklist': list(bl.values())}); return

        if path.path == '/api/public-url/status':
            # UI-Info: aktueller Netzwerk-State + Watchdog-Einstellung
            conn_pu = get_db('accounts.db')
            pu = conn_pu.execute('SELECT value FROM config WHERE key = ?', ('public_url',)).fetchone()
            en = conn_pu.execute('SELECT value FROM config WHERE key = ?', ('autocheck_enabled',)).fetchone()
            iv = conn_pu.execute('SELECT value FROM config WHERE key = ?', ('autocheck_interval_sec',)).fetchone()
            conn_pu.close()
            self._json_response({
                'public_url_manual': (pu['value'] if pu and pu['value'] else ''),
                'autocheck_enabled': (en['value'] == '1') if en else True,
                'autocheck_interval_sec': int(iv['value']) if iv and iv['value'] else 1800,
                'state': _network_state,
            })
            return

        if path.path == '/api/status':
            os_flag = self._get_os_flag()
            owner_verified = False
            if has_owner():
                conn_s = get_db('accounts.db')
                ov = conn_s.execute('SELECT verified FROM users WHERE is_owner = 1').fetchone()
                conn_s.close()
                owner_verified = bool(ov and ov['verified'])
            # Kontakt-Email aus SMTP-Config
            contact_email = ''
            try:
                smtp_cfg = get_smtp_config()
                contact_email = smtp_cfg.get('smtp_from', '') or smtp_cfg.get('smtp_user', '')
            except Exception:
                pass
            self._json_response({
                'has_owner': has_owner(),
                'smtp_configured': smtp_configured(),
                'owner_verified': owner_verified,
                'kneipe_ready': has_owner() and smtp_configured() and owner_verified,
                'themes': len(load_themes()),
                'os_flag': os_flag,
                'permanent_surveillance': os_flag is not None,
                'contact_email': contact_email,
                'register_code_required': _guest_config().get('register_code_required', False),
                'kneipe_title': _guest_config().get('kneipe_title', 'Kneipen-Schlägerei'),
                'kneipe_subtitle': _guest_config().get('kneipe_subtitle', 'Seelenfick für die Kneipe.'),
                'vault_unlocked': vault_is_unlocked(),
                'vault_exists': vault_exists(),
                'first_start': not vault_exists(),
            })
        elif path.path == '/api/archiv-log':
            # Owner-only: Archiv-Protokoll lesen
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn_acc = get_db('accounts.db')
            user = conn_acc.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_acc.close()
            if not user or not user['is_owner']:
                self._json_response({'error': 'Nur Owner'}, 403)
                return
            conn_gp = get_db('gameplay.db')
            logs = conn_gp.execute('SELECT * FROM archiv_log ORDER BY cleaned_at DESC LIMIT 100').fetchall()
            conn_gp.close()
            self._json_response({
                'ok': True,
                'interval': _get_archiv_interval(),
                'logs': [dict(l) for l in logs],
            })
        elif path.path == '/api/smtp':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn = get_db('accounts.db')
            user = conn.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn.close()
            if not user or not user['is_owner']:
                self._json_response({'error': 'Nur Owner'}, 403)
                return
            cfg = get_smtp_config()
            cfg['smtp_pass'] = '***' if cfg.get('smtp_pass') else ''  # Passwort NICHT zurückgeben!
            self._json_response(cfg)
        elif path.path == '/api/themen':
            self._json_response(load_themes())
        elif path.path.startswith('/api/thema/'):
            theme_id = path.path.split('/')[-1]
            theme = load_theme(theme_id)
            if theme:
                self._json_response(theme)
            else:
                self._json_response({'error': 'Thema nicht gefunden'}, 404)
        elif path.path == '/api/teilnehmer':
            sess = self._get_session()
            is_owner = False
            if sess:
                conn_acc = get_db('accounts.db')
                user = conn_acc.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
                conn_acc.close()
                is_owner = bool(user and user['is_owner'])
            log.info(f'📋 TEILNEHMER — sess={bool(sess)} is_owner={is_owner}')
            params = parse_qs(path.query)
            bot_mode = params.get('bot', [''])[0]
            bot_filter = True if bot_mode == '1' else False if bot_mode == '0' else None
            self._json_response(get_teilnehmer(include_email=is_owner, bot_filter=bot_filter))
        elif path.path == '/api/cheater/vote-status':
            params = parse_qs(path.query)
            target = params.get('name', [''])[0]
            if not target:
                self._json_response({'error': 'name Parameter fehlt!'})
                return
            sess_data = _get_cheater_vote_session(target)
            votes = _count_cheater_votes(target)
            eligible = max(_count_eligible_voters() - 1, 1)
            pct = round(votes / eligible * 100, 1) if eligible > 0 else 0
            self._json_response({
                'ok': True,
                'votes': votes,
                'eligible': eligible,
                'pct': pct,
                'session': sess_data,
            })
        elif path.path.startswith('/api/share-card/'):
            username = path.path.split('/')[-1]
            card_data = generate_share_card(username)
            if not card_data:
                self._json_response({'error': 'User nicht gefunden'}, 404)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(card_data)))
            self.send_header('Cache-Control', 'no-cache')
            self._send_security_headers()
            self.end_headers()
            self.wfile.write(card_data)
        elif path.path.startswith('/share/'):
            # Standalone Share-Seite: zeigt NUR das Card-Bild
            username = path.path.split('/')[-1]
            html = f'''<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
            <title>{username} — Kneipen-Schlägerei</title>
            <meta property="og:image" content="https://bar.shinpai.de/api/share-card/{username}">
            <meta property="og:title" content="{username} — Kneipen-Schlägerei 🍺">
            <meta property="og:description" content="Seelenfick für die Kneipe. Ist einfach passiert.">
            <style>body{{margin:0;background:#0a0a0a;display:flex;align-items:center;justify-content:center;min-height:100vh;}}
            img{{max-width:100%;max-height:100vh;border-radius:12px;}}</style></head>
            <body><img src="/api/share-card/{username}" alt="{username}"></body></html>'''
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self._send_security_headers()
            self.end_headers()
            self.wfile.write(html.encode())
        elif path.path == '/api/themenbereich':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            uid = sess['user_id']
            # Owner-Check + Zugangs-Check
            conn_acc = get_db('accounts.db')
            user = conn_acc.execute('SELECT is_owner, themen_access, themen_plays_counter FROM users WHERE id = ?', (uid,)).fetchone()
            conn_acc.close()
            is_owner = user and user['is_owner']
            has_access = is_owner or (user and user['themen_access'])
            counter = user['themen_plays_counter'] if user else 0
            if not has_access:
                self._json_response({'error': 'Themenbereich nicht freigeschaltet', 'plays_counter': counter, 'needed': 100, 'locked': True})
                return
            # Gespielte Themen-IDs
            conn_gp = get_db('gameplay.db')
            played = [r['theme_id'] for r in conn_gp.execute('SELECT DISTINCT theme_id FROM plays WHERE user_id = ?', (uid,)).fetchall()]
            conn_gp.close()
            # Themen laden die gespielt wurden (oder alle für Owner)
            all_themes = load_themes()
            if is_owner:
                result = all_themes
            else:
                result = [t for t in all_themes if t['id'] in played]
            self._json_response({'themes': result, 'is_owner': is_owner, 'plays_counter': counter})
        elif path.path.startswith('/api/thema-detail/'):
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            theme_id = path.path.split('/')[-1]
            uid = sess['user_id']
            # Check ob gespielt
            conn_gp = get_db('gameplay.db')
            played = conn_gp.execute('SELECT COUNT(*) as cnt FROM plays WHERE user_id = ? AND theme_id = ?', (uid, theme_id)).fetchone()['cnt']
            conn_gp.close()
            conn_acc = get_db('accounts.db')
            user = conn_acc.execute('SELECT is_owner FROM users WHERE id = ?', (uid,)).fetchone()
            conn_acc.close()
            is_owner = user and user['is_owner']
            if not is_owner and played == 0:
                self._json_response({'error': 'Erst spielen, dann einsehen!'})
                return
            # MD-Datei laden
            md_path = os.path.join(BASE, 'Themen', f'{theme_id}.md')
            if os.path.exists(md_path):
                with open(md_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                self._json_response({'id': theme_id, 'content': content})
            else:
                self._json_response({'error': 'Thema nicht gefunden'}, 404)
        elif path.path == '/api/offene-themen':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            uid = sess['user_id']
            conn_acc = get_db('accounts.db')
            user = conn_acc.execute('SELECT is_owner FROM users WHERE id = ?', (uid,)).fetchone()
            conn_acc.close()
            is_owner = user and user['is_owner']
            conn_gp = get_db('gameplay.db')
            themes = conn_gp.execute('SELECT id, author_name, title, setting, stammgast, likes, dislikes, submitted_at FROM community_themes WHERE status = ?', ('pending',)).fetchall()
            # User-Votes laden
            votes = {}
            for v in conn_gp.execute('SELECT theme_id, vote FROM theme_votes WHERE user_id = ?', (uid,)).fetchall():
                votes[v['theme_id']] = v['vote']
            conn_gp.close()
            result = []
            for t in themes:
                item = {
                    'id': t['id'], 'author': t['author_name'], 'title': t['title'],
                    'setting': t['setting'], 'stammgast': bool(t['stammgast']),
                    'likes': t['likes'], 'dislikes': t['dislikes'],
                    'my_vote': votes.get(t['id'], 0),
                }
                result.append(item)
            self._json_response({'themes': result, 'is_owner': is_owner})
        elif path.path.startswith('/api/offenes-thema/') and path.path.count('/') == 3:
            # Owner only: volles offenes Thema einsehen
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn_acc = get_db('accounts.db')
            user = conn_acc.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_acc.close()
            if not user or not user['is_owner']:
                self._json_response({'error': 'Nur der Owner darf offene Themen einsehen'}, 403)
                return
            theme_id = path.path.split('/')[-1]
            conn_gp = get_db('gameplay.db')
            theme = conn_gp.execute('SELECT * FROM community_themes WHERE id = ?', (theme_id,)).fetchone()
            conn_gp.close()
            if not theme:
                self._json_response({'error': 'Nicht gefunden'}, 404)
                return
            self._json_response({
                'id': theme['id'], 'title': theme['title'], 'setting': theme['setting'],
                'author': theme['author_name'], 'content_md': theme['content_md'],
                'content_json': json.loads(theme['content_json']),
                'stammgast': bool(theme['stammgast']),
                'likes': theme['likes'], 'dislikes': theme['dislikes'],
            })
        elif path.path == '/api/raeume':
            # Alle Räume auflisten
            self._json_response(handle_raum_list())
        elif path.path == '/api/bar':
            # Tische eines Raums laden
            params = parse_qs(path.query)
            raum_id = params.get('raum_id', [None])[0]
            self._json_response(handle_bar_raum({'raum_id': raum_id}))
        elif path.path == '/api/tresen/state':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            res = handle_tresen_state(sess['user_id'])
            status = res.pop('_status', 200) if isinstance(res, dict) else 200
            self._json_response(res, status)
        elif path.path == '/api/tresen/stream':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            params = parse_qs(path.query)
            since = float(params.get('since', ['0'])[0])
            res = handle_tresen_stream(sess['user_id'], since)
            if isinstance(res, dict) and '_status' in res:
                status = res.pop('_status')
                self._json_response(res, status)
            else:
                self._json_response(res)
        elif path.path == '/api/durchsage/state':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            res = handle_durchsage_state(sess['user_id'])
            status = res.pop('_status', 200) if isinstance(res, dict) else 200
            self._json_response(res, status)
        elif path.path == '/api/durchsage/stream':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            params = parse_qs(path.query)
            since = float(params.get('since', ['0'])[0])
            res = handle_durchsage_stream(sess['user_id'], since)
            if isinstance(res, dict) and '_status' in res:
                status = res.pop('_status')
                self._json_response(res, status)
            else:
                self._json_response(res)
        elif path.path == '/api/me/pq-keys':
            # PQ-Phase 3: User holt seinen eigenen PQ-Keyblob ab (private verschlüsselt)
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn_pk = get_db('accounts.db')
            u = conn_pk.execute('SELECT pq_dsa_pub, pq_kem_pub, pq_private_enc FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_pk.close()
            if not u:
                self._json_response({'error': 'User nicht gefunden'}, 404)
                return
            self._json_response({
                'ok': True,
                'dsa_pub': u['pq_dsa_pub'] or '',
                'kem_pub': u['pq_kem_pub'] or '',
                'private_enc': u['pq_private_enc'] or '',
                'alg_dsa': 'ML-DSA-65',
                'alg_kem': 'ML-KEM-768',
                'note': 'Private-Blob ist mit Passwort+machine-id verschlüsselt. Client muss mit PW entschlüsseln.',
            })
        elif path.path.startswith('/api/tisch/key/') or path.path.startswith('/api/channel/key/'):
            # PQ-Phase 4 + Vision 1: Gewrapten Gruppen-Key für diesen User abholen
            # (greift auf Tisch UND Tresen — Channel-ID beginnt mit 'tresen_' oder 'rX_tY')
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            channel_id = path.path.rsplit('/', 1)[-1]
            ch, _ = _find_channel(channel_id)
            if not ch:
                self._json_response({'error': 'Kanal nicht gefunden'}, 404)
                return
            # Für KEM-Wrap: nur Mitglieder bekommen ihren Key (kein Owner-Override hier,
            # weil der Key den Group-Crypto-Zugriff ermöglicht — wer nicht Mitglied ist
            # bekommt auch keinen Key, auch Owner nicht)
            if sess['user_id'] not in ch.get('members', set()):
                self._json_response({'error': 'Du sitzt nicht an diesem Kanal'}, 403)
                return
            wrap = ch.get('member_kem_wraps', {}).get(sess['user_id'])
            if not wrap:
                self._json_response({'error': 'Kein gewrapter Key verfügbar (kein PQ-Setup)'}, 404)
                return
            self._json_response({
                'ok': True,
                'channel_id': channel_id,
                'kem_alg': 'ML-KEM-768',
                'wrap': wrap,
            })
        elif path.path.startswith('/api/chat/poll/'):
            # Chat-Nachrichten abholen — nur Mitglieder!
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            parts = path.path.split('/')
            tisch_id = parts[-1] if len(parts) > 4 else ''
            params = parse_qs(path.query)
            since = float(params.get('since', ['0'])[0])
            result = handle_chat_poll(sess['user_id'], tisch_id, since)
            # Error-Objekt mit _status = HTTP-Statuscode rauswerfen
            if isinstance(result, dict) and '_status' in result:
                status = result.pop('_status')
                self._json_response(result, status)
            else:
                self._json_response(result)
        elif path.path == '/api/bierdeckel':
            # Begrüßung laden (öffentlich, kein Login nötig!)
            self._json_response(handle_bierdeckel_wand())
        elif path.path.startswith('/api/chat-file/'):
            # Chat-Datei ausliefern
            fname = path.path.split('/')[-1]
            fpath = os.path.join(VOICE_DIR, f'chat_{fname}')
            if os.path.exists(fpath):
                with open(fpath, 'rb') as f:
                    fdata = f.read()
                ext = fname.rsplit('.', 1)[-1]
                ct_map = {'jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png','gif':'image/gif',
                          'webm':'audio/webm','mp3':'audio/mpeg','mp4':'video/mp4','pdf':'application/pdf',
                          'txt':'text/plain','md':'text/plain','json':'application/json','zip':'application/zip'}
                ct = ct_map.get(ext, 'application/octet-stream')
                self.send_response(200)
                self.send_header('Content-Type', ct)
                self.send_header('Content-Length', str(len(fdata)))
                self._send_security_headers()
                self.end_headers()
                self.wfile.write(fdata)
            else:
                self._json_response({'error': 'Datei nicht gefunden'}, 404)
        elif path.path.startswith('/api/prost-voice/'):
            # Voice-Prost ausliefern
            parts = path.path.split('/')
            if len(parts) >= 5:
                bd_id = parts[3]
                uid_short = parts[4]
                # Dynamisch: webm oder mp4
                vf = None
                ct = 'audio/webm'
                for ext, mime in [('webm', 'audio/webm'), ('mp4', 'audio/mp4')]:
                    candidate = os.path.join(VOICE_DIR, f'prost_{bd_id}_{uid_short}.{ext}')
                    if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
                        vf = candidate
                        ct = mime
                        break
                if vf:
                    with open(vf, 'rb') as f:
                        audio_data = f.read()
                    self.send_response(200)
                    self.send_header('Content-Type', ct)
                    self.send_header('Content-Length', str(len(audio_data)))
                    self._send_security_headers()
                    self.end_headers()
                    self.wfile.write(audio_data)
                else:
                    self._json_response({'error': 'Voice nicht gefunden'}, 404)
            else:
                self._json_response({'error': 'Ungültiger Pfad'}, 400)
        elif path.path.startswith('/api/bierdeckel/voice/'):
            # Voice-File ausliefern (User-Voice oder TTS)
            bd_id = path.path.split('/')[-1]
            voice_path = None
            ct = 'audio/mpeg'
            for ext, mime in [('wav', 'audio/wav'), ('webm', 'audio/webm'), ('mp4', 'audio/mp4'), ('mp3', 'audio/mpeg')]:
                candidate = os.path.join(VOICE_DIR, f'{bd_id}.{ext}')
                if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
                    voice_path = candidate
                    ct = mime
                    break
            if voice_path:
                with open(voice_path, 'rb') as f:
                    audio_data = f.read()
                self.send_response(200)
                self.send_header('Content-Type', ct)
                self.send_header('Content-Length', str(len(audio_data)))
                self.send_header('Cache-Control', 'no-cache')
                self._send_security_headers()
                self.end_headers()
                self.wfile.write(audio_data)
            else:
                self._json_response({'error': 'Voice nicht verfügbar'}, 404)
        elif path.path == '/api/guest/config':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_guest_config_get(sess['user_id']))
        elif path.path == '/api/owner/voice-config':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn_vc = get_db('accounts.db')
            user_vc = conn_vc.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_vc.close()
            if not user_vc or not user_vc['is_owner']:
                self._json_response({'error': 'Nur Owner'}, 403)
                return
            self._json_response({'voice_config': _voice_config})
        elif path.path == '/api/owner/voice-status':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            vs_available = _voice_server_available()
            self._json_response({
                'voice_enabled': _voice_config.get('voice_enabled', True),
                'voice_url': _voice_config.get('voice_url', ''),
                'voice_mode': _voice_config.get('voice_mode', 'orpheus'),
                'connected': vs_available,
                'bark_available': vs_available,
            })
        elif path.path == '/api/owner/frp-status':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn_frp = get_db('accounts.db')
            user_frp = conn_frp.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_frp.close()
            if not user_frp or not user_frp['is_owner']:
                self._json_response({'error': 'Nur Owner'}, 403)
                return
            # FRP Admin-API abfragen
            frp_url = _voice_config.get('frp_admin_url', 'http://127.0.0.1:7500')
            frp_user = _voice_config.get('frp_admin_user', 'admin')
            frp_pass = _voice_config.get('frp_admin_pass', '')
            tunnels = []
            frp_online = False
            try:
                req = urllib.request.Request(f'{frp_url}/api/proxy/tcp')
                credentials = base64.b64encode(f'{frp_user}:{frp_pass}'.encode()).decode()
                req.add_header('Authorization', f'Basic {credentials}')
                with urllib.request.urlopen(req, timeout=3) as resp:
                    data_frp = json.loads(resp.read())
                frp_online = True
                for p in data_frp.get('proxies', []):
                    tunnels.append({
                        'name': p.get('name', '?'),
                        'status': p.get('status', 'unknown'),
                        'version': p.get('clientVersion', '?'),
                        'connections': p.get('curConns', 0),
                        'traffic_in': p.get('todayTrafficIn', 0),
                        'traffic_out': p.get('todayTrafficOut', 0),
                        'started': p.get('lastStartTime', ''),
                        'port': p.get('conf', {}).get('remotePort', 0),
                    })
            except Exception as e:
                log.warning(f'FRP Admin-API nicht erreichbar: {e}')
            self._json_response({
                'frp_online': frp_online,
                'tunnels': tunnels,
            })
        elif path.path == '/api/profile':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_profile(sess['user_id']))
        elif path.path == '/api/my-api-key':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn = get_db('accounts.db')
            user = conn.execute('SELECT api_key FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn.close()
            self._json_response({'api_key': user['api_key'] if user else ''})
        elif path.path == '/api/verify':
            params = parse_qs(path.query)
            token = params.get('token', [''])[0]
            result = handle_verify(token)
            if result.get('ok'):
                # Redirect zur Kneipe statt JSON!
                self.send_response(302)
                self.send_header('Location', '/?verified=1')
                self.end_headers()
            else:
                self._json_response(result)
        else:
            super().do_GET()

    def do_POST(self):
        ip = self._get_client_ip()
        if not check_rate_limit(ip):
            self._json_response({'error': 'Rate limit'}, 429)
            return

        path = self.path
        try:
            data = self._read_body()
        except:
            self._json_response({'error': 'Invalid JSON'}, 400)
            return

        # VAULT-GATE: bei gesperrtem Vault nur Whitelist durchlassen
        if not _vault_gate_allowed('POST', path):
            self._json_response({
                'error': 'Server ist gesperrt — Owner muss entsperren',
                'server_locked': True,
                'first_start': not vault_exists(),
            }, 503)
            return

        # ── BTC POST Endpoints ──
        if path == '/api/owner/igni':
            sess = self._get_session()
            if not sess: self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_igs = get_db('accounts.db')
            owner_igs = conn_igs.execute('SELECT * FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_igs.close()
            if not owner_igs or not owner_igs['is_owner']: self._json_response({'error': 'Nur Owner'}, 403); return
            new_mode = (data.get('mode') or '').strip().lower()
            password = data.get('password', '')
            if new_mode not in ('standard', 'paranoid'): self._json_response({'error': "mode muss 'standard' oder 'paranoid' sein"}, 400); return
            if not password: self._json_response({'error': 'Passwort erforderlich'}, 400); return
            if not verify_pw(password, owner_igs['pw_hash']): self._json_response({'error': 'Falsches Passwort'}, 403); return
            if int(owner_igs['totp_enabled'] or 0):
                totp_code = (data.get('totp_code') or '').strip()
                try:
                    totp_ok = verify_totp(vault_decrypt(owner_igs['totp_secret']), totp_code)
                except Exception:
                    totp_ok = False
                if not totp_ok: self._json_response({'error': '2FA-Code PFLICHT für Hausschlüssel-Wechsel'}, 403); return
            _igni_init()
            if new_mode == 'standard':
                igni_save(password)
                conn_igs2 = get_db('accounts.db')
                conn_igs2.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', ('owner_vault_mode', 'standard'))
                conn_igs2.commit(); conn_igs2.close()
                self._json_response({'status': 'ok', 'mode': 'standard', 'active': True,
                                      'message': 'Hausschlüssel aktiv — nächster Start entsperrt automatisch.'}); return
            else:
                igni_delete()
                conn_igs2 = get_db('accounts.db')
                conn_igs2.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', ('owner_vault_mode', 'paranoid'))
                conn_igs2.commit(); conn_igs2.close()
                self._json_response({'status': 'ok', 'mode': 'paranoid', 'active': False,
                                      'message': 'Paranoid-Modus — jeder Server-Start verlangt Passwort + 2FA.'}); return

        if path == '/api/btc/wallet/create':
            sess = self._get_session()
            if not sess: self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_bc = get_db('accounts.db')
            owner_bc = conn_bc.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_bc.close()
            if not owner_bc or not owner_bc['is_owner']: self._json_response({'error': 'Nur Owner'}, 403); return
            existing = _btc_wallet_load()
            if existing and existing.get('wif'): self._json_response({'error': 'Wallet existiert bereits'}, 400); return
            wallet = _btc_wallet_create()
            if not wallet: self._json_response({'error': 'Wallet-Erzeugung fehlgeschlagen. Sind hdwallet und bitcoin-utils installiert? pip install hdwallet bitcoin-utils'}, 500); return
            mnemonic = wallet.get('mnemonic', '')  # Seed bleibt im Wallet-Dict (Vault-geschützt!)
            if not _btc_wallet_save(wallet): self._json_response({'error': 'Wallet konnte nicht gespeichert werden. Vault nicht entsperrt?'}, 500); return
            self._json_response({'ok': True, 'address': wallet['address'], 'mnemonic': mnemonic}); return

        if path == '/api/btc/wallet/import':
            sess = self._get_session()
            if not sess: self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_bi = get_db('accounts.db')
            owner_bi = conn_bi.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_bi.close()
            if not owner_bi or not owner_bi['is_owner']: self._json_response({'error': 'Nur Owner'}, 403); return
            wif = (data.get('wif') or '').strip()
            seed = (data.get('seed') or '').strip()
            if seed:
                try:
                    from hdwallet import HDWallet
                    from hdwallet.cryptocurrencies import Bitcoin
                    from hdwallet.hds import BIP84HD
                    from hdwallet.mnemonics.bip39 import BIP39Mnemonic
                    from hdwallet.derivations import BIP84Derivation
                    mn = BIP39Mnemonic(mnemonic=seed)
                    hd = HDWallet(cryptocurrency=Bitcoin, hd=BIP84HD, network="mainnet")
                    hd.from_mnemonic(mn)
                    hd.from_derivation(BIP84Derivation(coin_type=0, account=0, change="external-chain", address=0))
                    wif = hd.wif()
                except Exception as e:
                    self._json_response({'error': f'Seed ungültig: {e}'}, 400); return
            if not wif: self._json_response({'error': 'WIF oder Seed benötigt'}, 400); return
            try:
                from bitcoinutils.setup import setup
                from bitcoinutils.keys import PrivateKey
                setup("mainnet")
                pk = PrivateKey.from_wif(wif)
                addr = pk.get_public_key().get_segwit_address().to_string()
            except Exception as e:
                self._json_response({'error': f'WIF ungültig: {e}'}, 400); return
            wallet = {'wif': wif, 'address': addr, 'entries': [], 'created_at': int(time.time())}
            if seed:
                wallet['mnemonic'] = seed  # Seed im Vault speichern (sicher!)
            _btc_wallet_save(wallet)
            self._json_response({'ok': True, 'address': addr}); return

        if path == '/api/btc/wallet/delete':
            sess = self._get_session()
            if not sess: self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_bd = get_db('accounts.db')
            owner_bd = conn_bd.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_bd.close()
            if not owner_bd or not owner_bd['is_owner']: self._json_response({'error': 'Nur Owner'}, 403); return
            if BTC_WALLET_VAULT.exists():
                BTC_WALLET_VAULT.unlink()
                log.info("🗑️ Bitcoin-Wallet entfernt")
            self._json_response({'ok': True}); return

        if path == '/api/btc/anchor':
            sess = self._get_session()
            if not sess: self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_ba = get_db('accounts.db')
            owner_ba = conn_ba.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_ba.close()
            if not owner_ba or not owner_ba['is_owner']: self._json_response({'error': 'Nur Owner'}, 403); return
            code_hash = _btc_get_code_hash()
            if not code_hash: self._json_response({'error': 'Code-Hash Berechnung fehlgeschlagen'}, 500); return
            version = data.get('version', VERSION)
            entry = _btc_wallet_anchor_hash(code_hash, version)
            if not entry: self._json_response({'error': 'Anchor fehlgeschlagen (Wallet leer?)'}, 500); return
            self._json_response({'ok': True, **entry}); return

        if path == '/api/btc/revoke':
            sess = self._get_session()
            if not sess: self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_br = get_db('accounts.db')
            owner_br = conn_br.execute('SELECT is_owner, totp_enabled, totp_secret FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_br.close()
            if not owner_br or not owner_br['is_owner']: self._json_response({'error': 'Nur Owner'}, 403); return
            totp_code = (data.get('totp') or '').strip()
            if int(owner_br['totp_enabled'] or 0):
                if not totp_code: self._json_response({'error': '2FA Code benötigt', 'requires_2fa': True}, 400); return
                try:
                    totp_ok = verify_totp(vault_decrypt(owner_br['totp_secret']), totp_code)
                except Exception:
                    totp_ok = False
                if not totp_ok: self._json_response({'error': 'Falscher 2FA Code'}, 403); return
            code_hash = (data.get('code_hash') or '').strip()
            if not code_hash: self._json_response({'error': 'code_hash benötigt'}, 400); return
            entry = _btc_wallet_revoke(code_hash)
            if not entry: self._json_response({'error': 'Revoke fehlgeschlagen'}, 500); return
            self._json_response({'ok': True, **entry}); return

        if path == '/api/whitelist/add':
            sess = self._get_session()
            if not sess: self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_wa = get_db('accounts.db')
            owner_wa = conn_wa.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_wa.close()
            if not owner_wa or not owner_wa['is_owner']: self._json_response({'error': 'Nur Owner'}, 403); return
            h = (data.get('hash') or '').strip()
            label = (data.get('label') or '').strip()[:80]
            if not h: self._json_response({'error': 'Hash benötigt'}, 400); return
            ok = nexus_whitelist_add(h, label)
            self._json_response({'ok': ok, 'added': ok}); return

        if path == '/api/whitelist/delete':
            sess = self._get_session()
            if not sess: self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_wd = get_db('accounts.db')
            owner_wd = conn_wd.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_wd.close()
            if not owner_wd or not owner_wd['is_owner']: self._json_response({'error': 'Nur Owner'}, 403); return
            h = (data.get('hash') or '').strip()
            if not h: self._json_response({'error': 'Hash benötigt'}, 400); return
            nexus_whitelist_remove(h)
            self._json_response({'ok': True}); return

        if path == '/api/blocklist/add':
            sess = self._get_session()
            if not sess: self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_bla = get_db('accounts.db')
            owner_bla = conn_bla.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_bla.close()
            if not owner_bla or not owner_bla['is_owner']: self._json_response({'error': 'Nur Owner'}, 403); return
            username = (data.get('username') or '').strip()
            days = int(data.get('days', 7))
            reason = (data.get('reason') or '').strip()
            if not username: self._json_response({'error': 'Username benötigt'}, 400); return
            if days not in (7, 30, 90, 365): self._json_response({'error': 'Ungültige Dauer'}, 400); return
            ok = blocklist_add(username, days, reason)
            self._json_response({'ok': ok}); return

        if path == '/api/blocklist/remove':
            sess = self._get_session()
            if not sess: self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_blr = get_db('accounts.db')
            owner_blr = conn_blr.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_blr.close()
            if not owner_blr or not owner_blr['is_owner']: self._json_response({'error': 'Nur Owner'}, 403); return
            username = (data.get('username') or '').strip()
            if not username: self._json_response({'error': 'Username benötigt'}, 400); return
            blocklist_remove(username)
            self._json_response({'ok': True}); return

        if path == '/api/whitelist/import':
            sess = self._get_session()
            if not sess: self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_wi = get_db('accounts.db')
            owner_wi = conn_wi.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_wi.close()
            if not owner_wi or not owner_wi['is_owner']: self._json_response({'error': 'Nur Owner'}, 403); return
            url = (data.get('url') or '').strip().rstrip('/')
            if not url: self._json_response({'error': 'URL benötigt'}, 400); return
            status_code, remote = nexus_request(url, '/api/whitelist')
            if status_code != 200: self._json_response({'error': f'Konnte Whitelist nicht abrufen (HTTP {status_code})'}, 502); return
            remote_wl = remote.get('whitelist') or []
            added = 0
            for entry in remote_wl:
                h = (entry.get('hash') or '').strip()
                if h and nexus_whitelist_add(h, entry.get('label', '') + f' (von {url})'):
                    added += 1
            self._json_response({'ok': True, 'added': added, 'total_remote': len(remote_wl)}); return

        if path == '/api/status':
            os_flag = self._get_os_flag()
            owner_verified = False
            if has_owner():
                conn_s = get_db('accounts.db')
                ov = conn_s.execute('SELECT verified FROM users WHERE is_owner = 1').fetchone()
                conn_s.close()
                owner_verified = bool(ov and ov['verified'])
            contact_email2 = ''
            try:
                smtp_cfg2 = get_smtp_config()
                contact_email2 = smtp_cfg2.get('smtp_from', '') or smtp_cfg2.get('smtp_user', '')
            except Exception:
                pass
            gcfg2 = _guest_config()
            self._json_response({
                'has_owner': has_owner(),
                'smtp_configured': smtp_configured(),
                'owner_verified': owner_verified,
                'kneipe_ready': has_owner() and smtp_configured() and owner_verified,
                'themes': len(load_themes()),
                'os_flag': os_flag,
                'permanent_surveillance': os_flag is not None,
                'contact_email': contact_email2,
                'register_code_required': gcfg2.get('register_code_required', False),
                'kneipe_title': gcfg2.get('kneipe_title', 'Kneipen-Schlägerei'),
                'kneipe_subtitle': gcfg2.get('kneipe_subtitle', 'Seelenfick für die Kneipe.'),
            })
        elif path == '/api/owner-setup':
            self._json_response(handle_owner_setup(data))
        elif path == '/api/verify-code':
            # Code-basierte Email-Verify (Owner + User)
            email = (data.get('email') or '').strip()
            code = (data.get('code') or '').strip()
            if not email or not code:
                self._json_response({'error': 'Email und Code nötig'}, 400)
                return
            user_id = check_verify_code(email, code)
            if not user_id:
                self._json_response({'error': 'Code falsch oder abgelaufen. Neuen Code anfordern.'}, 401)
                return
            conn = get_db('accounts.db')
            user_row = conn.execute('SELECT name, is_owner FROM users WHERE id = ?', (user_id,)).fetchone()
            conn.execute('UPDATE users SET verified = 1, verify_token = NULL, verify_expires = 0, updated_at = ? WHERE id = ?',
                         (time.time(), user_id))
            conn.commit()
            conn.close()
            log.info(f'✅ EMAIL VERIFIED via code — {user_row["name"] if user_row else user_id}')
            self._json_response({'ok': True, 'user_id': user_id, 'message': 'Email verifiziert!'})
        elif path == '/api/public-url/check':
            # Manueller "Jetzt prüfen"-Button ODER Setup-Check.
            # Body: {url?: explizite Domain testen}. Ohne url → Full-Check (ipify + Self-Test)
            target_url = (data.get('url') or '').strip().rstrip('/')
            if target_url:
                if not (target_url.startswith('http://') or target_url.startswith('https://')):
                    target_url = 'http://' + target_url
                reach, method = _check_external_reachable(target_url)
                note = 'Nicht erreichbar. Portweiterleitung + DNS prüfen.'
                if reach and method == 'self':
                    note = 'Erreichbar!'
                elif reach and method == 'external':
                    note = 'Erreichbar (via externer Check — dein Router hat kein NAT-Loopback, das ist OK)'
                self._json_response({
                    'ok': reach,
                    'url': target_url,
                    'match': reach,
                    'method': method,
                    'note': note,
                })
            else:
                # Full-Check (auto-detect externe IP + Self-Test + lokale IPs)
                state = run_network_check(full=True)
                self._json_response({
                    'ok': bool(state.get('reachable_external') or state.get('reachable_local')),
                    'state': {
                        'external_ip': state.get('external_ip'),
                        'local_ips': state.get('local_ips'),
                        'best_url': state.get('best_url'),
                        'reachable_external': state.get('reachable_external'),
                        'reachable_local': state.get('reachable_local'),
                        'last_check': state.get('last_check'),
                    },
                })
        elif path == '/api/public-url/save':
            # Nach erfolgreichem Check: URL in config persistieren
            url = (data.get('url') or '').strip().rstrip('/')
            if not url:
                self._json_response({'error': 'URL fehlt'}, 400)
                return
            conn = get_db('accounts.db')
            conn.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', ('public_url', url))
            conn.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', ('solo_mode', '0'))
            conn.commit()
            conn.close()
            log.info(f'🌐 PUBLIC_URL gespeichert: {url}')
            self._json_response({'ok': True, 'public_url': url})
        elif path == '/api/public-url/config':
            # Watchdog-Einstellungen (Häkchen + Intervall) aus Owner-Dashboard
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn = get_db('accounts.db')
            is_owner = conn.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            if not is_owner or not is_owner['is_owner']:
                conn.close()
                self._json_response({'error': 'Nur Owner'}, 403)
                return
            enabled = '1' if data.get('autocheck_enabled') else '0'
            interval = int(data.get('autocheck_interval_sec', 1800))
            # Nur erlaubte Intervalle: 60, 900, 1800, 3600
            if interval not in (60, 900, 1800, 3600):
                interval = 1800
            conn.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', ('autocheck_enabled', enabled))
            conn.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', ('autocheck_interval_sec', str(interval)))
            conn.commit()
            conn.close()
            log.info(f'🌐 Watchdog-Config: enabled={enabled}, interval={interval}s')
            self._json_response({'ok': True, 'autocheck_enabled': enabled == '1', 'autocheck_interval_sec': interval})
        elif path == '/api/solo-mode':
            # SMTP überspringen, Owner auto-verified, nur Gäste-Modus aktiv
            sess = self._get_session()
            conn = get_db('accounts.db')
            conn.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', ('solo_mode', '1'))
            conn.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', ('public_url', ''))
            # Owner auto-verify (keine Mail möglich/nötig)
            conn.execute('UPDATE users SET verified = 1, verify_token = NULL WHERE is_owner = 1')
            conn.commit()
            conn.close()
            log.info('👥 SOLO-MODE aktiviert — Owner auto-verified, nur Gäste')
            self._json_response({'ok': True, 'solo_mode': True, 'message': 'Gäste-Modus aktiv. Owner bereit.'})
        elif path == '/api/vault/unlock':
            # Owner-only Vault-Unlock (falls er sich explizit entsperren will)
            name = data.get('name', '').strip()
            pw = data.get('password', '')
            if not name or not pw:
                self._json_response({'error': 'Name + Passwort nötig'}, 400)
                return
            conn = get_db('accounts.db')
            owner = conn.execute('SELECT id, pw_hash, is_owner FROM users WHERE name = ? AND is_owner = 1', (name,)).fetchone()
            conn.close()
            if not owner or not verify_pw(pw, owner['pw_hash']):
                self._json_response({'error': 'Name oder Passwort falsch (Owner-only!)'}, 401)
                return
            if vault_unlock(pw):
                log.info(f'🔓 VAULT unlocked via /api/vault/unlock by {name}')
                self._json_response({'ok': True, 'unlocked': True})
            else:
                self._json_response({'error': 'Vault-Entsperren fehlgeschlagen'}, 401)
        elif path == '/api/vault/recover':
            # Recovery: Seed-Phrase → altes PW entschlüsseln + neues PW setzen
            seed = (data.get('seed') or '').strip()
            new_pw = data.get('new_password', '')
            if not seed:
                self._json_response({'error': 'Seed-Phrase nötig'}, 400)
                return
            old_pw = recover_vault_password(seed)
            if not old_pw:
                self._json_response({'error': 'Seed-Phrase stimmt nicht'}, 401)
                return
            # Vault entsperren mit altem PW, dann ggf neues setzen
            if not vault_unlock(old_pw):
                self._json_response({'error': 'Recovery fehlgeschlagen (Vault-Entsperren)'}, 500)
                return
            response = {'ok': True, 'unlocked': True}
            if new_pw and len(new_pw) >= 8:
                # Recovery mit Passwort-Wechsel: neuer Vault + neuer Seed
                identity = vault_read_identity() or {}
                new_seed = vault_setup(new_pw, identity.get('owner_username', ''), identity.get('owner_email', ''))
                # Owner-Hash auch anpassen
                conn = get_db('accounts.db')
                conn.execute('UPDATE users SET pw_hash = ? WHERE is_owner = 1', (hash_pw(new_pw),))
                conn.commit()
                conn.close()
                igni_save(new_pw)
                response['new_seed'] = new_seed
                response['password_changed'] = True
                log.info('🔑 VAULT RECOVERY + PW-WECHSEL')
            else:
                log.info('🔓 VAULT unlocked via Seed-Recovery (kein PW-Wechsel)')
            self._json_response(response)
        elif path == '/api/vault/salt-info':
            # Owner-only Salt-Metadata (für Sicherheits-Tab UI)
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_si = get_db('accounts.db')
            owner_si = conn_si.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_si.close()
            if not owner_si or not owner_si['is_owner']:
                self._json_response({'error': 'Nur Owner'}, 403); return
            meta = _salt_metadata()
            now_ts = int(time.time())
            meta['can_rotate_now'] = (not meta['present']) or now_ts >= meta['cooldown_until']
            meta['seconds_until_ready'] = max(0, meta['cooldown_until'] - now_ts) if meta['present'] else 0
            self._json_response(meta)
        elif path == '/api/vault/salt-rotate':
            # Salzstreuer: Owner + aktives 2FA + 24h Cooldown
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_sr = get_db('accounts.db')
            owner_sr = conn_sr.execute(
                'SELECT is_owner, totp_secret, totp_enabled FROM users WHERE id = ?',
                (sess['user_id'],)
            ).fetchone()
            conn_sr.close()
            if not owner_sr or not owner_sr['is_owner']:
                self._json_response({'error': 'Nur Owner'}, 403); return
            if not owner_sr['totp_enabled'] or not owner_sr['totp_secret']:
                self._json_response({'error': '2FA Pflicht für den Salzstreuer'}, 403); return

            pw = data.get('password', '')
            totp_code = (data.get('totp') or data.get('totp_code') or '').strip()
            if not pw:
                self._json_response({'error': 'Passwort nötig'}, 400); return
            if not totp_code:
                self._json_response({'error': '2FA-Code nötig'}, 400); return

            # 2FA prüfen
            try:
                secret = vault_decrypt(owner_sr['totp_secret'])
            except Exception:
                self._json_response({'error': '2FA-Secret nicht entschlüsselbar'}, 500); return
            if not verify_totp(secret, totp_code):
                self._json_response({'error': '2FA-Code falsch'}, 401); return

            # Cooldown-Check
            meta = _salt_metadata()
            now_ts = int(time.time())
            if meta['present'] and now_ts < meta['cooldown_until']:
                self._json_response({
                    'error': 'Ruhig, Kompaniechef. Mehr Salz gibts erst mit dem nächsten Tequila — morgen.',
                    'cooldown_until': meta['cooldown_until'],
                    'seconds_until_ready': meta['cooldown_until'] - now_ts,
                }, 429); return

            result = _pq_rotate_salt(pw)
            if 'error' in result:
                status_code = 401 if result['error'] == 'Passwort falsch' else 500
                self._json_response(result, status_code); return
            log.info(f'🧂 SALZSTREUER — Salt rotiert durch {sess.get("user_id")}')
            self._json_response(result)
        elif path == '/api/owner/voice-config':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn_vc = get_db('accounts.db')
            user_vc = conn_vc.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_vc.close()
            if not user_vc or not user_vc['is_owner']:
                self._json_response({'error': 'Nur Owner'}, 403)
                return
            # Token generieren wenn angefordert — kappt ALLE FRP-Verbindungen!
            if data.get('generate_token'):
                import secrets as _secrets
                new_token = _secrets.token_hex(24)
                _voice_config['frp_token'] = new_token
                _update_frps_token(new_token)
            # Config updaten
            global VOICE_SERVER_URL
            if data.get('voice_url'):
                _voice_config['voice_url'] = data['voice_url']
                VOICE_SERVER_URL = data['voice_url']
            if 'voice_enabled' in data:
                _voice_config['voice_enabled'] = bool(data['voice_enabled'])
            if data.get('voice_mode'):
                _voice_config['voice_mode'] = data['voice_mode']
            if data.get('default_voice'):
                _voice_config['default_voice'] = data['default_voice']
            if 'mobile_allowed' in data:
                _voice_config['mobile_allowed'] = bool(data['mobile_allowed'])
            if 'mobile_max' in data:
                _voice_config['mobile_max'] = int(data['mobile_max'])
            if data.get('frp_admin_url'):
                _voice_config['frp_admin_url'] = data['frp_admin_url']
            if data.get('frp_admin_user'):
                _voice_config['frp_admin_user'] = data['frp_admin_user']
            if data.get('frp_admin_pass'):
                _voice_config['frp_admin_pass'] = data['frp_admin_pass']
            _save_voice_config()
            log.info(f'🔊 Voice-Config aktualisiert: {_voice_config["voice_url"]} mode={_voice_config["voice_mode"]}')
            self._json_response({'ok': True, 'voice_config': _voice_config})
        elif path == '/api/owner/frp-kick':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn_fk = get_db('accounts.db')
            user_fk = conn_fk.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_fk.close()
            if not user_fk or not user_fk['is_owner']:
                self._json_response({'error': 'Nur Owner'}, 403)
                return
            proxy_name = data.get('name', '')
            if not proxy_name:
                self._json_response({'error': 'Kein Proxy-Name'}, 400)
                return
            ok = _kick_frp_proxy(proxy_name)
            self._json_response({'ok': ok})
        elif path == '/api/owner/frp-refresh':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn_fr = get_db('accounts.db')
            user_fr = conn_fr.execute('SELECT is_owner, pw_hash FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_fr.close()
            if not user_fr or not user_fr['is_owner']:
                self._json_response({'error': 'Nur Owner'}, 403)
                return
            _refresh_frp_admin(user_fr['pw_hash'])
            self._json_response({'ok': True})
        elif path == '/api/smtp':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            # Owner-Check
            conn = get_db('accounts.db')
            user = conn.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn.close()
            if not user or not user['is_owner']:
                self._json_response({'error': 'Nur der Owner darf SMTP konfigurieren'}, 403)
                return
            save_smtp_config(data)
            log.info(f'📧 SMTP konfiguriert von Owner')
            conn2 = get_db('accounts.db')
            owner = conn2.execute('SELECT name, email, verify_token, nexus_verified, verified FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn2.close()
            # IMMER Code senden wenn Email vorhanden (Button heißt "Speichern + Code senden")
            if owner and owner['email']:
                fresh_code = generate_verify_code()
                set_verify_code(sess['user_id'], fresh_code)
                ok = send_verify_email(owner['email'], fresh_code, owner['name'])
                if ok:
                    log.info(f'📧 Code-Mail an {owner["email"]} GESENDET!')
                    self._json_response({
                        'ok': True,
                        'email': owner['email'],
                        'step': 'code',
                        'message': f'SMTP gespeichert! 6-stelliger Code an {owner["email"]} gesendet — unten eingeben.',
                    })
                else:
                    log.error(f'📧 Code-Mail an {owner["email"]} FEHLGESCHLAGEN!')
                    self._json_response({'ok': True, 'message': 'SMTP gespeichert! Mail konnte nicht gesendet werden — SMTP-Daten prüfen!'})
            else:
                self._json_response({'ok': True, 'message': 'SMTP gespeichert!'})
        elif path == '/api/forgot':
            from datetime import datetime, timedelta
            today = datetime.now().strftime('%Y-%m-%d')
            email = data.get('email', '').strip().lower()
            if not email:
                self._json_response({'error': 'Email eingeben!'})
                return

            conn_cfg = get_db('accounts.db')

            # Spam-Check: Email für 14 Tage gesperrt?
            spam_key = f'spam_{email}'
            spam_entry = conn_cfg.execute('SELECT value FROM config WHERE key = ?', (spam_key,)).fetchone()
            if spam_entry:
                spam_until = float(spam_entry['value'])
                if time.time() < spam_until:
                    days_left = int((spam_until - time.time()) / 86400) + 1
                    conn_cfg.close()
                    self._json_response({'error': f'Diese Email wurde wegen Spam gesperrt. Entsperrung in {days_left} Tagen.'})
                    return
                else:
                    conn_cfg.execute('DELETE FROM config WHERE key = ?', (spam_key,))

            # Rate-Limit: 1 Reset pro IP pro 24h
            ip_key = f'reset_{ip}_{today}'
            existing_ip = conn_cfg.execute('SELECT value FROM config WHERE key = ?', (ip_key,)).fetchone()
            if existing_ip:
                conn_cfg.close()
                self._json_response({'error': 'Von deiner IP wurde heute schon ein Reset beantragt. Bitte warte bis morgen (0:00 Uhr).'})
                return

            # 3-Tage-Spam-Check: 3 Resets in 3 Tagen = 14 Tage Sperre
            email_reset_key = f'resets_{email}'
            resets_entry = conn_cfg.execute('SELECT value FROM config WHERE key = ?', (email_reset_key,)).fetchone()
            reset_dates = json.loads(resets_entry['value']) if resets_entry else []
            # Nur letzte 3 Tage behalten
            three_days_ago = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
            reset_dates = [d for d in reset_dates if d >= three_days_ago]
            reset_dates.append(today)

            if len(reset_dates) >= 3:
                # SPAM! 14 Tage sperren
                spam_until = time.time() + (14 * 86400)
                conn_cfg.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', (spam_key, str(spam_until)))
                conn_cfg.execute('DELETE FROM config WHERE key = ?', (email_reset_key,))
                conn_cfg.commit()
                conn_cfg.close()
                log.warning(f'🚫 SPAM DETECTED — {email} gesperrt für 14 Tage')
                self._json_response({'error': 'Diese Email wurde wegen wiederholtem Reset als Spam eingestuft und für 14 Tage gesperrt.'})
                return

            conn_cfg.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', (email_reset_key, json.dumps(reset_dates)))
            conn_cfg.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', (ip_key, '1'))
            conn_cfg.commit()
            conn_cfg.close()

            conn = get_db('accounts.db')
            user = conn.execute('SELECT id, name FROM users WHERE email = ?', (email,)).fetchone()
            if not user:
                conn.close()
                # Absichtlich KEIN Fehler zurückgeben (Security: keine Email-Enumeration)
                self._json_response({'ok': True, 'message': 'Falls ein Account mit dieser Email existiert, wurde ein Reset-Link gesendet.'})
                return
            reset_token = generate_verify_token()
            # Token + Timestamp speichern (3 Tage gültig)
            token_with_ts = f"{reset_token}:{int(time.time())}"
            conn.execute('UPDATE users SET verify_token = ? WHERE id = ?', (token_with_ts, user['id']))
            conn.commit()
            conn.close()
            # Reset-Mail senden
            cfg = get_smtp_config()
            if cfg.get('smtp_host'):
                import smtplib
                from email.mime.multipart import MIMEMultipart
                from email.mime.text import MIMEText
                reset_url = f"https://bar.shinpai.de/?reset={token_with_ts}"
                html = f'<div style="font-family:Georgia,serif;color:#e0d8c8;background:#0a0a0a;padding:30px;text-align:center;"><h2 style="color:#d4a850;">🔑 Passwort zurücksetzen</h2><p>Hallo {user["name"]}!</p><p>Klicke hier um dein Passwort zurückzusetzen:</p><a href="{reset_url}" style="display:inline-block;padding:14px 30px;background:#d4a850;color:#0a0a0a;text-decoration:none;border-radius:8px;font-weight:bold;">Passwort zurücksetzen</a><p style="font-size:12px;color:#665540;margin-top:20px;">2FA wird ebenfalls zurückgesetzt.</p></div>'
                try:
                    msg = MIMEMultipart('alternative')
                    msg['Subject'] = '🔑 Passwort zurücksetzen — Kneipen-Schlägerei'
                    msg['From'] = cfg.get('smtp_from', cfg['smtp_user'])
                    msg['To'] = email
                    msg.attach(MIMEText(f'Passwort zurücksetzen: {reset_url}', 'plain'))
                    msg.attach(MIMEText(html, 'html'))
                    port = int(cfg.get('smtp_port', 587))
                    if port == 465:
                        server = smtplib.SMTP_SSL(cfg['smtp_host'], port, timeout=30)
                    else:
                        server = smtplib.SMTP(cfg['smtp_host'], port, timeout=30)
                        server.ehlo(); server.starttls(); server.ehlo()
                    server.login(cfg['smtp_user'], cfg['smtp_pass'])
                    server.sendmail(msg['From'], email, msg.as_string())
                    server.quit()
                    log.info(f'🔑 RESET-MAIL gesendet an {email}')
                except Exception as e:
                    log.error(f'🔑 Reset-Mail fehlgeschlagen: {e}')
            self._json_response({'ok': True, 'message': 'Falls ein Account mit dieser Email existiert, wurde ein Reset-Link gesendet.'})
        elif path == '/api/reset-password':
            token = data.get('token', '')
            password = data.get('password', '')
            if len(password) < 8:
                self._json_response({'error': 'Passwort: mindestens 8 Zeichen'})
                return
            conn = get_db('accounts.db')
            user = conn.execute('SELECT id, name FROM users WHERE verify_token = ?', (token,)).fetchone()
            if not user:
                conn.close()
                self._json_response({'error': 'Ungültiger oder abgelaufener Reset-Link'})
                return
            # 3-Tage-Ablauf prüfen
            if ':' in token:
                try:
                    ts = int(token.split(':')[-1])
                    if time.time() - ts > 3 * 86400:
                        conn.execute('UPDATE users SET verify_token = NULL WHERE id = ?', (user['id'],))
                        conn.commit()
                        conn.close()
                        self._json_response({'error': 'Reset-Link abgelaufen (3 Tage). Bitte neu beantragen.'})
                        return
                except:
                    pass
            conn.execute('UPDATE users SET pw_hash = ?, totp_secret = NULL, totp_enabled = 0, verify_token = NULL WHERE id = ?',
                        (hash_pw(password), user['id']))
            conn.commit()
            conn.close()
            log.info(f'🔑 PASSWORD RESET — {user["name"]} (2FA deaktiviert)')
            # FRP Admin-Pass vom gespeicherten Hash ableiten wenn Owner
            conn_o = get_db('accounts.db')
            ow_row = conn_o.execute('SELECT is_owner, pw_hash FROM users WHERE id = ?', (user['id'],)).fetchone()
            conn_o.close()
            if ow_row and ow_row['is_owner']:
                _refresh_frp_admin(ow_row['pw_hash'])
            self._json_response({'ok': True, 'message': 'Passwort geändert! 2FA wurde zurückgesetzt. Du kannst dich jetzt einloggen.'})
        elif path == '/api/register':
            self._json_response(handle_register(data))
        elif path == '/api/nexus/register':
            self._json_response(handle_nexus_auth(data, make_owner=False))
        elif path == '/api/nexus/owner':
            if has_owner():
                self._json_response({'error': 'Owner existiert bereits'})
            else:
                self._json_response(handle_nexus_auth(data, make_owner=True))
        elif path == '/api/nexus/login':
            self._json_response(handle_nexus_auth(data, make_owner=False))
        elif path == '/api/nexus/link':
            # Bestehenden Account mit Nexus verlinken
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_nexus_link(sess['user_id'], data))
        elif path == '/api/nexus/create':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_nexus_create(sess['user_id'], data))
        elif path == '/api/nexus/delete':
            # Nexus-Account löschen (auf dem Nexus-Server!) + lokal unlinken
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn_d = get_db('accounts.db')
            user_d = conn_d.execute('SELECT nexus_url, shinpai_id, nexus_verified FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_d.close()
            if not user_d or not int(user_d['nexus_verified'] or 0):
                self._json_response({'error': 'Kein Nexus verlinkt!'})
                return
            # Nexus kontaktieren → Account löschen
            nx_url = user_d['nexus_url']
            # Erst einloggen um Session-Token zu bekommen
            pw = data.get('password', '')
            totp = data.get('totp_code', '')
            status_nx, login_res = nexus_request(nx_url, '/api/auth/login', {'username': data.get('username', get_username(sess['user_id'])), 'password': pw, 'totp_code': totp, 'source': 'kneipe:delete'})
            if not login_res.get('authenticated'):
                self._json_response(login_res)
                return
            nx_token = login_res.get('session_token', '')
            # Delete aufrufen mit Session
            import urllib.request, urllib.error, ssl
            del_url = f"{nx_url.rstrip('/')}/api/auth/delete-account"
            del_body = json.dumps({'password': pw, 'totp_code': totp}).encode('utf-8')
            del_req = urllib.request.Request(del_url, data=del_body, headers={'Content-Type': 'application/json', 'X-Session-Token': nx_token})
            ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
            try:
                with urllib.request.urlopen(del_req, timeout=15, context=ctx) as resp:
                    del_res = json.loads(resp.read())
            except urllib.error.HTTPError as e:
                try: del_res = json.loads(e.read())
                except: del_res = {'error': f'Nexus: {e.code}'}
            except Exception as e:
                del_res = {'error': f'Nexus nicht erreichbar: {e}'}
            if del_res.get('ok'):
                # Lokal unlinken
                conn_u = get_db('accounts.db')
                conn_u.execute('UPDATE users SET nexus_url = "", nexus_verified = 0, updated_at = ? WHERE id = ?', (time.time(), sess['user_id']))
                conn_u.commit()
                conn_u.close()
                log.info(f'🗑️ NEXUS ACCOUNT DELETED + UNLINKED — {get_username(sess["user_id"])} [{user_d["shinpai_id"]}]')
                self._json_response({'ok': True, 'message': 'Nexus-Account gelöscht und Verbindung getrennt.'})
            else:
                self._json_response(del_res)
        elif path == '/api/nexus/unlink':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_nexus_unlink(sess['user_id']))
        elif path == '/api/resend-verify':
            # Session-basiert (eingeloggt) ODER name+password (Login-Seite)
            sess = self._get_session()
            if sess and not data.get('name'):
                conn_rv = get_db('accounts.db')
                user_rv = conn_rv.execute('SELECT name FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
                conn_rv.close()
                if user_rv:
                    data['name'] = user_rv['name']
                    data['_session_auth'] = True
            self._json_response(handle_resend_verify(data))
        elif path == '/api/login':
            self._json_response(handle_login(data))
        elif path == '/api/guest/join':
            ip = self._get_client_ip()
            self._json_response(handle_guest_join(ip))
        elif path == '/api/guest/heartbeat':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
            else:
                self._json_response(handle_guest_heartbeat(sess['user_id']))
        elif path == '/api/guest/leave':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
            else:
                self._json_response(handle_guest_leave(sess['user_id']))
        elif path == '/api/guest/kick':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
            else:
                self._json_response(handle_guest_kick(sess['user_id'], data))
        elif path == '/api/guest/config':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
            else:
                self._json_response(handle_guest_config_set(sess['user_id'], data))
        elif path == '/api/guest/cleanup':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
            else:
                self._json_response(handle_guest_cleanup(sess['user_id']))
        elif path == '/api/logout':
            auth = self.headers.get('Authorization', '')
            sess = self._get_session()
            # Vision 1: beide Sub-Stores beim Logout clearen (Session-only)
            if sess:
                _durchsage_reset(sess['user_id'])
                _tresen_reset(sess['user_id'])
            if auth.startswith('Bearer '):
                delete_session(auth[7:])
            self._json_response({'ok': True})
        elif path == '/api/enable-2fa':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            step = data.get('step', 'init')
            conn = get_db('accounts.db')
            user = conn.execute('SELECT name, totp_enabled FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            if not user:
                conn.close()
                self._json_response({'error': 'User nicht gefunden'}, 404)
                return
            if step == 'init':
                secret = generate_totp_secret()
                qr_data, uri = generate_totp_qr(secret, user['name'])
                conn.execute('UPDATE users SET totp_secret = ? WHERE id = ?', (vault_encrypt(secret), sess['user_id']))
                conn.commit()
                conn.close()
                self._json_response({'ok': True, 'qr': qr_data})
            elif step == 'verify':
                user_full = conn.execute('SELECT totp_secret FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
                if verify_totp(vault_decrypt(user_full['totp_secret']), data.get('code', '')):
                    conn.execute('UPDATE users SET totp_enabled = 1 WHERE id = ?', (sess['user_id'],))
                    conn.commit()
                    conn.close()
                    log.info(f'🔐 2FA ENABLED — {user["name"]}')
                    self._json_response({'ok': True})
                else:
                    conn.close()
                    self._json_response({'error': 'Falscher Code!'})
            else:
                conn.close()
                self._json_response({'error': 'Unbekannter Schritt'})
        elif path == '/api/nexus/resync':
            # 2FA Resync: Nexus-Login → neues TOTP holen → Kneipe updaten
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn_rs = get_db('accounts.db')
            user_rs = conn_rs.execute('SELECT nexus_url, nexus_verified, name FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_rs.close()
            if not user_rs or not int(user_rs['nexus_verified'] or 0):
                self._json_response({'error': 'Kein Nexus verlinkt!'})
                return
            # Nexus-Login mit neuen Credentials
            nx_url = user_rs['nexus_url']
            username = data.get('username', user_rs['name'])
            password = data.get('password', '')
            totp_code = data.get('totp_code', '')
            if not password:
                self._json_response({'error': 'Nexus-Passwort nötig!'})
                return
            auth = {'username': username, 'password': password, 'source': 'kneipe:resync'}
            if totp_code:
                auth['totp_code'] = totp_code
            status_nx, result = nexus_request(nx_url, '/api/auth/login', auth)
            if result.get('step') in ('password', '2fa'):
                self._json_response(result)
                return
            if result.get('authenticated') and result.get('totp_secret'):
                new_totp = result['totp_secret']
                conn_up = get_db('accounts.db')
                conn_up.execute('UPDATE users SET totp_secret = ?, updated_at = ? WHERE id = ?',
                                 (vault_encrypt(new_totp), time.time(), sess['user_id']))
                conn_up.commit()
                conn_up.close()
                log.info(f'🔄 2FA RESYNCED — {user_rs["name"]} (von Nexus)')
                self._json_response({'ok': True, 'message': '2FA mit Nexus synchronisiert!'})
            else:
                self._json_response(result if result.get('error') else {'error': 'Nexus-Login fehlgeschlagen!'})
        elif path == '/api/2fa-refresh':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn_2f = get_db('accounts.db')
            user_2f = conn_2f.execute('SELECT name, email, totp_enabled, totp_secret FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_2f.close()
            if not user_2f or not int(user_2f['totp_enabled'] or 0):
                self._json_response({'error': '2FA nicht aktiv!'})
                return
            if not user_2f['email']:
                self._json_response({'error': 'Keine Email hinterlegt!'})
                return
            # Neues TOTP generieren + per Email senden
            new_secret = generate_totp_secret()
            new_qr, _ = generate_totp_qr(new_secret, user_2f['name'])
            # Pending speichern (2min!)
            if not hasattr(self, '_2fa_pending'):
                GameHandler._2fa_pending = {}
            GameHandler._2fa_pending[sess['user_id']] = {'secret': new_secret, 'expires': time.time() + 120}
            # Email senden
            html = f'''<div style="background:#0a0a0a;color:#e0d8c8;font-family:Georgia,serif;padding:30px;max-width:500px;margin:0 auto;text-align:center;">
              <h1 style="color:#e08040;">🔐 2FA Refresh</h1>
              <p style="color:#887755;">Neues 2FA für {user_2f["name"]}</p>
              <img src="{new_qr}" style="max-width:200px;background:#fff;padding:8px;border-radius:8px;margin:15px 0;">
              <div style="background:#111;padding:10px;border-radius:6px;margin:10px 0;">
                <code style="color:#7ab8e0;font-size:13px;letter-spacing:2px;">{new_secret}</code>
              </div>
              <div style="background:#1a0a0a;border:2px solid #e55;border-radius:8px;padding:15px;margin:15px 0;">
                <p style="color:#e55;font-weight:bold;">⏱️ 2 MINUTEN!</p>
                <p style="color:#998870;font-size:12px;">Scanne und bestätige in der Kneipe!</p>
              </div>
            </div>'''
            ok = send_verify_email.__func__ if hasattr(send_verify_email, '__func__') else None
            # Einfach direkt SMTP nutzen
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
            cfg_smtp = get_smtp_config()
            try:
                msg = MIMEMultipart('alternative')
                msg['Subject'] = '🔐 2FA Refresh — Kneipen-Schlägerei'
                msg['From'] = cfg_smtp.get('smtp_from', cfg_smtp['smtp_user'])
                msg['To'] = user_2f['email']
                msg.attach(MIMEText(html, 'html'))
                port = int(cfg_smtp.get('smtp_port', 587))
                if port == 465:
                    server = smtplib.SMTP_SSL(cfg_smtp['smtp_host'], port, timeout=30)
                else:
                    server = smtplib.SMTP(cfg_smtp['smtp_host'], port, timeout=30)
                    server.ehlo(); server.starttls(); server.ehlo()
                server.login(cfg_smtp['smtp_user'], cfg_smtp['smtp_pass'])
                server.sendmail(msg['From'], user_2f['email'], msg.as_string())
                server.quit()
                log.info(f'🔐 2FA REFRESH Mail an {user_2f["email"]} ({user_2f["name"]})')
                self._json_response({'ok': True, 'expires_in': 120, 'message': f'2FA-Refresh an {user_2f["email"]} gesendet! 2 Minuten!'})
            except Exception as e:
                log.error(f'🔐 2FA REFRESH Mail fehlgeschlagen: {e}')
                self._json_response({'error': f'Mail fehlgeschlagen: {e}'})
        elif path == '/api/2fa-refresh-confirm':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            totp_code = data.get('totp_code', '')
            pending = getattr(GameHandler, '_2fa_pending', {}).get(sess['user_id'])
            if not pending:
                self._json_response({'error': 'Kein 2FA-Refresh aktiv!'})
                return
            if time.time() > pending['expires']:
                del GameHandler._2fa_pending[sess['user_id']]
                self._json_response({'error': '⏱️ 2 Minuten abgelaufen!'})
                return
            if not verify_totp(pending['secret'], totp_code):
                self._json_response({'error': 'Falscher Code!'})
                return
            # Neues Secret speichern!
            conn_2fc = get_db('accounts.db')
            conn_2fc.execute('UPDATE users SET totp_secret = ?, updated_at = ? WHERE id = ?',
                              (vault_encrypt(pending['secret']), time.time(), sess['user_id']))
            conn_2fc.commit()
            conn_2fc.close()
            del GameHandler._2fa_pending[sess['user_id']]
            log.info(f'🔐 2FA REFRESHED — {get_username(sess["user_id"])}')
            self._json_response({'ok': True, 'message': '2FA aktualisiert!'})
        elif path == '/api/disable-2fa':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn = get_db('accounts.db')
            user = conn.execute('SELECT name, nexus_verified, nexus_url FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            has_nexus = int(user['nexus_verified'] or 0) if user else 0
            if has_nexus:
                # Nexus aktiv → 2FA Pflicht! Warnung + Nexus-Unlink!
                conn.execute('UPDATE users SET totp_enabled = 0, totp_secret = NULL, nexus_verified = 0, nexus_url = "" WHERE id = ?', (sess['user_id'],))
                conn.commit()
                conn.close()
                log.info(f'🔓 2FA DISABLED + NEXUS UNLINKED — {user["name"]} (2FA war Pflicht für Nexus!)')
                self._json_response({'ok': True, 'nexus_removed': True, 'message': '2FA deaktiviert! ShinNexus-Verbindung wurde getrennt. 2FA ist Pflicht für ShinNexus.'})
            else:
                conn.execute('UPDATE users SET totp_enabled = 0, totp_secret = NULL WHERE id = ?', (sess['user_id'],))
                conn.commit()
                conn.close()
                log.info(f'🔓 2FA DISABLED — {user["name"]}')
                self._json_response({'ok': True})
        elif path == '/api/thema/submit':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            uid = sess['user_id']
            conn_acc = get_db('accounts.db')
            user = conn_acc.execute('SELECT name, is_owner, themen_access FROM users WHERE id = ?', (uid,)).fetchone()
            conn_acc.close()
            # Zugangs-Check: Owner ODER themen_access!
            if not user or (not user['is_owner'] and not user['themen_access']):
                self._json_response({'error': 'Themenbereich nicht freigeschaltet. Erst 100 Spiele oder Owner-Freigabe!'}, 403)
                return
            # Thema aus Formular-Daten bauen
            title = data.get('title', '').strip()
            setting = data.get('setting', '').strip()
            layers = data.get('layers', {})
            endings = data.get('endings', {})
            stammgast = data.get('stammgast', False)
            raw_md = data.get('content_md', '').strip()  # Original-MD wenn mitgeschickt
            if not title or not setting:
                self._json_response({'error': 'Titel und Setting sind Pflicht!'})
                return
            if not raw_md and not layers:
                self._json_response({'error': 'Entweder content_md oder layers sind Pflicht!'})
                return
            # MD: Original verwenden wenn vorhanden, sonst generieren
            if raw_md:
                md = raw_md
            else:
                md = f"# Thema: {title}\n## Setting: {setting}\n\n---\n\n"
                for lid, layer in sorted(layers.items()):
                    md += f"### Schicht {lid}: {layer.get('title', '')}\n"
                    md += f"> {layer.get('situation', '')}\n\n"
                    for ans in layer.get('answers', []):
                        flags = ' '.join(f'[{f.upper()}]' for f in ans.get('flags', []))
                        text = ans.get('text', '')
                        if ans.get('silence'):
                            text = f'*{text}*'
                        target = f" → Schicht {ans.get('target', '')}" if ans.get('target') else ''
                        md += f"- {ans.get('choice', 'A')}: {text} {flags}{target}\n"
                    md += "\n---\n\n"
                for element, text in endings.items():
                    emoji = {'feuer': '🔥', 'wasser': '🌊', 'stein': '🪨'}.get(element, '')
                    md += f"### Schicht 5-{element.capitalize()}: {emoji}\n> {text}\n\n"
            # Qualitätsprüfung: Schichten vorhanden?
            required = ['Schicht 1', 'Schicht 2', 'Schicht 3', 'Schicht 4', 'Schicht 5']
            missing = [s for s in required if s not in md]
            if missing:
                self._json_response({'error': f'Qualitätscheck: Fehlende Schichten: {", ".join(missing)}'})
                return
            # Umlaut-Check
            import re as _re
            bad_umlauts = _re.findall(r'(?<![A-Z])(?:eür|aün|eün|Neür|bescheürt|traürt|schaün|Misstraün|kaün|Daürzustand|Freqünz|Fraün|KOENNEN|KOENNEN)', md)
            if bad_umlauts:
                self._json_response({'error': f'Qualitätscheck: Kaputte Umlaute gefunden: {", ".join(set(bad_umlauts))}'})
                return
            # JSON bauen
            content_json = json.dumps({'title': title, 'setting': setting, 'layers': layers, 'endings': endings, 'stammgast': stammgast})
            theme_id = str(uuid.uuid4())[:8]
            conn_gp = get_db('gameplay.db')
            conn_gp.execute('''INSERT INTO community_themes (id, author_id, author_name, title, setting, content_json, content_md, stammgast, submitted_at)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                           (theme_id, uid, user['name'], title, setting, content_json, md, int(stammgast), time.time()))
            conn_gp.commit()
            conn_gp.close()
            log.info(f'📝 THEMA SUBMITTED — "{title}" von {user["name"]}')
            self._json_response({'ok': True, 'id': theme_id, 'message': 'Thema eingereicht! Warte auf Freischaltung.'})
        elif path == '/api/thema/vote':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            theme_id = data.get('theme_id', '')
            vote = data.get('vote', 0)  # 1 = like, -1 = dislike
            if vote not in [1, -1]:
                self._json_response({'error': 'Vote muss 1 oder -1 sein'})
                return
            conn = get_db('gameplay.db')
            existing = conn.execute('SELECT vote FROM theme_votes WHERE user_id = ? AND theme_id = ?', (sess['user_id'], theme_id)).fetchone()
            if existing:
                # Update
                old_vote = existing['vote']
                conn.execute('UPDATE theme_votes SET vote = ? WHERE user_id = ? AND theme_id = ?', (vote, sess['user_id'], theme_id))
                if old_vote == 1:
                    conn.execute('UPDATE community_themes SET likes = likes - 1 WHERE id = ?', (theme_id,))
                elif old_vote == -1:
                    conn.execute('UPDATE community_themes SET dislikes = dislikes - 1 WHERE id = ?', (theme_id,))
            else:
                conn.execute('INSERT INTO theme_votes (user_id, theme_id, vote) VALUES (?, ?, ?)', (sess['user_id'], theme_id, vote))
            if vote == 1:
                conn.execute('UPDATE community_themes SET likes = likes + 1 WHERE id = ?', (theme_id,))
            else:
                conn.execute('UPDATE community_themes SET dislikes = dislikes + 1 WHERE id = ?', (theme_id,))
            conn.commit()
            conn.close()
            self._json_response({'ok': True})
        elif path == '/api/thema/approve':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn_acc = get_db('accounts.db')
            user = conn_acc.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_acc.close()
            if not user or not user['is_owner']:
                self._json_response({'error': 'Nur der Owner darf Themen genehmigen'}, 403)
                return
            theme_id = data.get('theme_id', '')
            conn = get_db('gameplay.db')
            theme = conn.execute('SELECT * FROM community_themes WHERE id = ?', (theme_id,)).fetchone()
            if not theme:
                conn.close()
                self._json_response({'error': 'Thema nicht gefunden'}, 404)
                return
            # Qualitäts-Validierung VOR Approve
            md_content = theme['content_md'] or ''
            required = ['Schicht 1', 'Schicht 2', 'Schicht 3', 'Schicht 4', 'Schicht 5']
            missing = [s for s in required if s not in md_content]
            if missing:
                conn.close()
                self._json_response({'error': f'Qualitätscheck FAILED: Fehlende Schichten: {", ".join(missing)}. Thema nicht genehmigt!'})
                return
            # Antworten-Check: mindestens 3 Antworten pro Hauptschicht
            answer_count = md_content.count('- A:') + md_content.count('- B:') + md_content.count('- C:')
            if answer_count < 9:  # Mindestens 3 Schichten x 3 Antworten
                conn.close()
                self._json_response({'error': f'Qualitätscheck FAILED: Nur {answer_count} Antworten gefunden (min 9). Thema nicht genehmigt!'})
                return
            # MD-Datei schreiben
            safe_title = re.sub(r'[^\w\s\-äöüÄÖÜß]', '', theme['title']).strip().replace(' ', '-')
            # Umlaute im Dateinamen normalisieren (ä→ae etc.)
            for u, r in [('ä','ae'),('ö','oe'),('ü','ue'),('Ä','Ae'),('Ö','Oe'),('Ü','Ue'),('ß','ss')]:
                safe_title = safe_title.replace(u, r)
            md_path = os.path.join(BASE, 'Themen', f'{safe_title}.md')
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(md_content)
            # Converter ASYNC laufen lassen (blockiert nicht den Request!)
            import subprocess
            subprocess.Popen(['python3', os.path.join(BASE, 'converter.py')], cwd=BASE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            import time as _t
            _t.sleep(2)  # Kurz warten damit JSON geschrieben wird
            # Prüfen ob das Thema im JSON gelandet ist (0 Schichten = kaputt)
            json_name = safe_title + '.json'
            json_path = os.path.join(THEMEN_DIR, json_name)
            if os.path.exists(json_path):
                with open(json_path) as jf:
                    jdata = json.load(jf)
                layer_count = len(jdata.get('layers', {}))
                if layer_count == 0:
                    os.remove(md_path)
                    conn.close()
                    self._json_response({'error': f'Qualitätscheck FAILED: Converter ergibt 0 Schichten. Format ist kaputt! Thema nicht genehmigt.'})
                    return
            else:
                os.remove(md_path)
                conn.close()
                self._json_response({'error': f'Qualitätscheck FAILED: Converter konnte kein JSON erstellen. Thema nicht genehmigt!'})
                return
            # Alles OK → Status updaten
            conn.execute('UPDATE community_themes SET status = ?, approved_at = ? WHERE id = ?', ('approved', time.time(), theme_id))
            conn.commit()
            conn.close()
            log.info(f'✅ THEMA APPROVED — "{theme["title"]}" → {safe_title}.md ({layer_count} Schichten)')
            self._json_response({'ok': True, 'message': f'Thema "{theme["title"]}" ist jetzt LIVE! ({layer_count} Schichten)'})
        elif path == '/api/themen-access':
            # Owner: Themenbereich-Zugang togglen
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn_acc = get_db('accounts.db')
            user = conn_acc.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            if not user or not user['is_owner']:
                conn_acc.close()
                self._json_response({'error': 'Nur der Owner'}, 403)
                return
            target_name = data.get('user_name', '')
            grant = data.get('grant', False)  # True = freischalten, False = entziehen
            if grant:
                conn_acc.execute('UPDATE users SET themen_access = 1 WHERE name = ?', (target_name,))
                log.info(f'🔓 THEMEN-ACCESS GRANTED — {target_name} (by Owner)')
                msg = f'{target_name} hat jetzt Themenbereich-Zugang!'
            else:
                # Entziehen + Counter auf 0 resetten!
                conn_acc.execute('UPDATE users SET themen_access = 0, themen_plays_counter = 0 WHERE name = ?', (target_name,))
                log.info(f'🔒 THEMEN-ACCESS REVOKED + COUNTER RESET — {target_name} (by Owner)')
                msg = f'{target_name} verliert Zugang + Counter auf 0. Muss erneut 100 Spiele machen!'
            conn_acc.commit()
            conn_acc.close()
            self._json_response({'ok': True, 'message': msg})
        elif path == '/api/unflag-bot':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn_acc = get_db('accounts.db')
            user = conn_acc.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            if not user or not user['is_owner']:
                conn_acc.close()
                self._json_response({'error': 'Nur der Owner'}, 403)
                return
            target = data.get('user_name', '')
            conn_acc.execute('UPDATE users SET is_bot = 0 WHERE name = ?', (target,))
            conn_acc.commit()
            conn_acc.close()
            log.info(f'🧑 BOT UNFLAGGED — {target} (by Owner)')
            self._json_response({'ok': True, 'message': f'{target} ist kein Bot mehr.'})
        elif path == '/api/unflag-cheater':
            # Owner-only: Cheater direkt unflaggen
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn_acc = get_db('accounts.db')
            user = conn_acc.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_acc.close()
            if not user or not user['is_owner']:
                self._json_response({'error': 'Nur der Owner'}, 403)
                return
            target = data.get('user_name', '')
            _do_unflag_cheater(target)
            self._json_response({'ok': True, 'message': f'{target} ist kein Cheater mehr.'})
        elif path == '/api/cheater/vote':
            # Community Cheater-Unflag-Vote
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            target = data.get('user_name', '')
            if not target:
                self._json_response({'error': 'user_name fehlt!'})
                return
            self._json_response(handle_cheater_vote(sess['user_id'], target))
        elif path == '/api/archiv-config':
            # Owner-only: Archiv-Intervall einstellen
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn_acc = get_db('accounts.db')
            user = conn_acc.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            if not user or not user['is_owner']:
                conn_acc.close()
                self._json_response({'error': 'Nur Owner'}, 403)
                return
            interval = data.get('interval', 3600)
            allowed = [3600, 21600, 43200, 86400, 259200, 604800, 2592000, 31536000]
            if interval not in allowed:
                conn_acc.close()
                self._json_response({'error': f'Erlaubt: {allowed}'})
                return
            conn_acc.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('archiv_interval', ?)", (str(interval),))
            conn_acc.commit()
            conn_acc.close()
            labels = {3600:'1h',21600:'6h',43200:'12h',86400:'24h',259200:'3d',604800:'7d',2592000:'30d',31536000:'365d'}
            log.info(f'🗑️ ARCHIV-INTERVALL — {labels.get(interval, interval)} (by Owner)')
            self._json_response({'ok': True, 'interval': interval, 'label': labels.get(interval, '?')})
        elif path == '/api/thema/delete':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn_acc = get_db('accounts.db')
            user = conn_acc.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_acc.close()
            if not user or not user['is_owner']:
                self._json_response({'error': 'Nur der Owner darf Themen löschen'}, 403)
                return
            theme_id = data.get('theme_id', '')
            # MD löschen
            md_path = os.path.join(BASE, 'Themen', f'{theme_id}.md')
            if os.path.exists(md_path):
                os.remove(md_path)
            # JSON löschen
            json_path = os.path.join(THEMEN_DIR, f'{theme_id}.json')
            if os.path.exists(json_path):
                os.remove(json_path)
            # Aus community_themes löschen falls vorhanden
            conn = get_db('gameplay.db')
            conn.execute('DELETE FROM community_themes WHERE id = ?', (theme_id,))
            conn.execute('DELETE FROM theme_votes WHERE theme_id = ?', (theme_id,))
            conn.commit()
            conn.close()
            # Converter neu laufen lassen
            import subprocess
            subprocess.run(['python3', os.path.join(BASE, 'converter.py')], cwd=BASE, capture_output=True)
            log.info(f'🗑️ THEMA DELETED — {theme_id}')
            self._json_response({'ok': True, 'message': f'Thema "{theme_id}" gelöscht.'})
        elif path == '/api/thema/reject':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            conn_acc = get_db('accounts.db')
            user = conn_acc.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_acc.close()
            if not user or not user['is_owner']:
                self._json_response({'error': 'Nur der Owner darf Themen ablehnen'}, 403)
                return
            theme_id = data.get('theme_id', '')
            conn = get_db('gameplay.db')
            theme = conn.execute('SELECT title FROM community_themes WHERE id = ?', (theme_id,)).fetchone()
            conn.execute('DELETE FROM community_themes WHERE id = ?', (theme_id,))
            conn.execute('DELETE FROM theme_votes WHERE theme_id = ?', (theme_id,))
            conn.commit()
            conn.close()
            title = theme['title'] if theme else theme_id
            log.info(f'❌ THEMA REJECTED — {title}')
            self._json_response({'ok': True, 'message': f'Thema "{title}" abgelehnt und gelöscht.'})
        elif path == '/api/tts-test':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            voice_id = data.get('voice', 'de-DE-ConradNeural')
            if not any(v['id'] == voice_id for v in EDGE_VOICES):
                self._json_response({'error': 'Ungültige Stimme'})
                return
            test_id = f'tts_test_{secrets.token_hex(4)}'
            test_path = os.path.join(VOICE_DIR, f'{test_id}.mp3')
            try:
                communicate = edge_tts.Communicate('Prost! Das bin ich. Deine Stimme in der Kneipe.', voice_id)
                loop = asyncio.new_event_loop()
                loop.run_until_complete(communicate.save(test_path))
                loop.close()
                self._json_response({'audio_url': f'/api/bierdeckel/voice/{test_id}'})
            except Exception as e:
                self._json_response({'error': str(e)})
        elif path == '/api/raum/create':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_raum_create())
        elif path == '/api/name/vote':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_name_vote(sess['user_id'], data))
        elif path == '/api/eigenschaft/vote':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_eigenschaft_vote(sess['user_id'], data))
        elif path == '/api/eigenschaft/add':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_eigenschaft_add(sess['user_id'], data))
        elif path == '/api/tisch/password':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            tisch_id = data.get('tisch_id', '')
            t, _ = _find_tisch(tisch_id)
            if not t:
                self._json_response({'error': 'Tisch nicht gefunden'}, 404)
                return
            if sess['user_id'] not in t['members']:
                self._json_response({'error': 'Du sitzt nicht an diesem Tisch'}, 403)
                return
            pw = (data.get('password') or '').strip()
            t['password'] = pw
            self._json_response({'ok': True, 'password_set': bool(pw)})
        elif path == '/api/tisch/adult':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            tisch_id = data.get('tisch_id', '')
            t, _ = _find_tisch(tisch_id)
            if not t:
                self._json_response({'error': 'Tisch nicht gefunden'}, 404)
                return
            if sess['user_id'] not in t['members']:
                self._json_response({'error': 'Du sitzt nicht an diesem Tisch'}, 403)
                return
            t['adult_only'] = bool(data.get('adult_only', False))
            self._json_response({'ok': True, 'adult_only': t['adult_only']})
        elif path == '/api/tisch/mumupai':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            tisch_id = data.get('tisch_id', '')
            t, _ = _find_tisch(tisch_id)
            if not t:
                self._json_response({'error': 'Tisch nicht gefunden'}, 404)
                return
            if sess['user_id'] not in t['members']:
                self._json_response({'error': 'Du sitzt nicht an diesem Tisch'}, 403)
                return
            url = (data.get('url') or '').strip()[:500]
            t['mumupai_url'] = url
            self._json_response({'ok': True, 'mumupai_url': url})
        elif path == '/api/tisch/join':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_tisch_join(sess['user_id'], data, is_windows=self._is_windows_user(), is_chromeos=self._is_chromeos_user()))
        elif path == '/api/tisch/leave':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_tisch_leave(sess['user_id'], data))
        elif path == '/api/durchsage/subscribe':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            res = handle_durchsage_subscribe(sess['user_id'], data)
            status = res.pop('_status', 200) if isinstance(res, dict) else 200
            self._json_response(res, status)
        elif path == '/api/durchsage/bulk':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            res = handle_durchsage_bulk(sess['user_id'], data)
            status = res.pop('_status', 200) if isinstance(res, dict) else 200
            self._json_response(res, status)
        elif path == '/api/durchsage/send':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            res = handle_durchsage_send(sess['user_id'], data)
            status = res.pop('_status', 200) if isinstance(res, dict) else 200
            self._json_response(res, status)
        elif path == '/api/tresen/subscribe':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            res = handle_tresen_subscribe(sess['user_id'], data)
            status = res.pop('_status', 200) if isinstance(res, dict) else 200
            self._json_response(res, status)
        elif path == '/api/tresen/bulk':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            res = handle_tresen_bulk(sess['user_id'], data)
            status = res.pop('_status', 200) if isinstance(res, dict) else 200
            self._json_response(res, status)
        elif path == '/api/tresen/send':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            res = handle_tresen_send(sess['user_id'], data)
            status = res.pop('_status', 200) if isinstance(res, dict) else 200
            self._json_response(res, status)
        elif path == '/api/nexus-whitelist/add':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_o = get_db('accounts.db')
            owner = conn_o.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_o.close()
            if not owner or not owner['is_owner']:
                self._json_response({'error': 'Nur Owner'}, 403); return
            # Body: {hash, label?}  ODER {nexus_url} → Hash wird geholt + gespeichert
            given_hash = (data.get('hash') or '').strip()
            nx_url = (data.get('nexus_url') or '').strip().rstrip('/')
            label = (data.get('label') or '').strip()
            fetched_hash, fetched_version = '', ''
            if not given_hash and nx_url:
                _tr, fetched_hash, fetched_version = verify_nexus_trust(nx_url)
                given_hash = fetched_hash
            if not given_hash:
                self._json_response({'error': 'Hash oder nexus_url nötig'}, 400); return
            if not label and nx_url:
                label = nx_url
            added = nexus_whitelist_add(given_hash, label)
            self._json_response({'ok': True, 'added': added, 'hash': given_hash, 'label': label, 'fetched_version': fetched_version})
        elif path == '/api/nexus-whitelist/remove':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            conn_o = get_db('accounts.db')
            owner = conn_o.execute('SELECT is_owner FROM users WHERE id = ?', (sess['user_id'],)).fetchone()
            conn_o.close()
            if not owner or not owner['is_owner']:
                self._json_response({'error': 'Nur Owner'}, 403); return
            h = (data.get('hash') or '').strip()
            if not h:
                self._json_response({'error': 'Hash nötig'}, 400); return
            nexus_whitelist_remove(h)
            self._json_response({'ok': True, 'hash': h})
        elif path == '/api/tresen/password':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            raum_id = (data.get('raum_id') or '').strip()
            raum = raeume.get(raum_id)
            if not raum or not raum.get('tresen'):
                self._json_response({'error': 'Tresen nicht gefunden'}, 404); return
            tresen = raum['tresen']
            if sess['user_id'] not in tresen.get('members', set()):
                self._json_response({'error': 'Du sitzt nicht am Tresen'}, 403); return
            pw = (data.get('password') or '').strip()
            tresen['password'] = pw
            log.info(f'🔑 TRESEN-PW {"gesetzt" if pw else "entfernt"} — {raum_id}')
            self._json_response({'ok': True, 'password_set': bool(pw)})
        elif path == '/api/tresen/adult':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            raum_id = (data.get('raum_id') or '').strip()
            raum = raeume.get(raum_id)
            if not raum or not raum.get('tresen'):
                self._json_response({'error': 'Tresen nicht gefunden'}, 404); return
            tresen = raum['tresen']
            if sess['user_id'] not in tresen.get('members', set()):
                self._json_response({'error': 'Du sitzt nicht am Tresen'}, 403); return
            tresen['adult_only'] = bool(data.get('adult_only', False))
            self._json_response({'ok': True, 'adult_only': tresen['adult_only']})
        elif path == '/api/tresen/mumupai':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401); return
            raum_id = (data.get('raum_id') or '').strip()
            raum = raeume.get(raum_id)
            if not raum or not raum.get('tresen'):
                self._json_response({'error': 'Tresen nicht gefunden'}, 404); return
            tresen = raum['tresen']
            if sess['user_id'] not in tresen.get('members', set()):
                self._json_response({'error': 'Du sitzt nicht am Tresen'}, 403); return
            url = (data.get('url') or '').strip()[:500]
            tresen['mumupai_url'] = url
            self._json_response({'ok': True, 'mumupai_url': url})
        elif path == '/api/tresen/join':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_tresen_join(sess['user_id'], data, is_windows=self._is_windows_user(), is_chromeos=self._is_chromeos_user()))
        elif path == '/api/tresen/leave':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_tresen_leave(sess['user_id'], data))
        elif path == '/api/chat/file':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_chat_file(sess['user_id'], data))
        elif path == '/api/chat/send':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            if sess.get('via_api'):
                data['_via_api'] = True
            self._json_response(handle_chat_send(sess['user_id'], data))
        elif path == '/api/bierdeckel':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_bierdeckel_post(sess['user_id'], data))
        elif path == '/api/bierdeckel/prost':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_bierdeckel_prost(sess['user_id'], data))
        elif path == '/api/bierdeckel/vote':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_bierdeckel_vote(sess['user_id'], data))
        elif path == '/api/play':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_play_start(sess['user_id'], data))
        elif path == '/api/answer':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_play_answer(sess['user_id'], data))
        elif path == '/api/finish':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_play_finish(sess['user_id'], data))
        elif path == '/api/profile':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            # Update profile
            conn = get_db('accounts.db')
            updates = []
            params = []
            if 'profile_pic' in data:
                pic = data['profile_pic']
                # Base64-Bilder serverseitig auf 256x256 resizen
                if isinstance(pic, str) and pic.startswith('data:image'):
                    try:
                        b64data = pic.split(',')[1]
                        img_bytes = base64.b64decode(b64data)
                        img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
                        img.thumbnail((256, 256), Image.LANCZOS)
                        buf = io.BytesIO()
                        img.save(buf, format='JPEG', quality=80)
                        pic = f'data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}'
                    except Exception as e:
                        log.warning(f'⚠️ Profilbild-Resize fehlgeschlagen: {e}')
                        self._json_response({'error': 'Ungültiges Bild'}, 400)
                        return
                updates.append('profile_pic = ?')
                params.append(pic)
            if 'age' in data:
                updates.append('age = ?')
                params.append(data['age'])
            if 'tts_voice' in data:
                voice_id = data['tts_voice']
                # Gäste: NUR Edge-Voices (außer Owner hat Bark freigeschaltet!)
                if is_guest_user(sess['user_id']) and not voice_id.startswith('de-'):
                    if not _guest_config().get('bark_enabled', False):
                        self._json_response({'error': 'Gäste können nur Edge-Stimmen wählen'})
                        return
                if any(v['id'] == voice_id for v in EDGE_VOICES + BARK_VOICES_LIST):
                    updates.append('tts_voice = ?')
                    params.append(voice_id)
            if updates:
                params.append(time.time())
                params.append(sess['user_id'])
                conn.execute(f'UPDATE users SET {", ".join(updates)}, updated_at = ? WHERE id = ?', params)
                conn.commit()
            conn.close()
            self._json_response({'ok': True})
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self.path == '/api/account':
            sess = self._get_session()
            if not sess:
                self._json_response({'error': 'Nicht eingeloggt'}, 401)
                return
            self._json_response(handle_delete_account(sess['user_id']))
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', self._get_origin())
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.send_header('Vary', 'Origin')
        self.end_headers()

    def log_message(self, format, *args):
        pass

# --- MAIN ---
if __name__ == '__main__':
    os.chdir(BASE)
    init_db()

    # Defaults für Watchdog-Config setzen falls leer
    try:
        _c = get_db('accounts.db')
        for k, v in [('autocheck_enabled', '1'), ('autocheck_interval_sec', '1800')]:
            _c.execute('INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)', (k, v))
        _c.commit()
        _c.close()
    except Exception as _e:
        log.warning(f'Config-Defaults: {_e}')

    # Ersten Check sofort beim Start + Watchdog-Thread
    try:
        run_network_check(full=True)
    except Exception as _e:
        log.warning(f'Erster Netzwerk-Check fehlgeschlagen: {_e}')
    threading.Thread(target=_network_watchdog, daemon=True).start()
    log.info('🌐 Network-Watchdog gestartet')

    # Igni-Init: Ordner finden/erstellen
    _igni_init()

    # VAULT-BOOTSTRAP: Igni prüfen und auto-unlock versuchen
    if vault_exists():
        igni_pw = igni_load()
        if igni_pw:
            if vault_unlock(igni_pw):
                log.info('🔑 Igni-Auto-Unlock erfolgreich — Vault offen, Server bereit')
                _igni_init()  # Re-Init mit Shinpai-ID (jetzt Vault offen)
                try: _ensure_keypair()
                except Exception: pass
            else:
                log.warning('🔑 Igni-Auto-Unlock fehlgeschlagen — Vault gesperrt, Owner-Login nötig')
        else:
            log.info('🔒 Kein Igni gefunden — Vault gesperrt, Owner muss einloggen um zu entsperren')
    else:
        log.info('🌱 Kein Identity-Vault — First-Start-Modus (Owner muss eingerichtet werden)')

    # Vault-Keeper-Thread: proaktiver Igni-Refresh bevor 24h-Lock greift
    threading.Thread(target=_vault_keeper_loop, daemon=True, name='vault-keeper').start()
    log.info('🔑 Vault-Keeper gestartet — prüft alle 60s auf Igni-Refresh-Bedarf')

    # Gast-Pool: Accounts bleiben, nur In-Memory Slots sind leer nach Neustart
    _conn_cleanup = get_db('accounts.db')
    _pool_count = _conn_cleanup.execute('SELECT COUNT(*) as cnt FROM users WHERE is_guest = 1').fetchone()['cnt']
    _conn_cleanup.close()
    log.info(f'👀 Gast-Pool: {_pool_count} Accounts (max {GUEST_POOL}, {GUEST_MAX} aktive Slots)')
    log.info(f'🍺 Kneipen-Schlägerei Server auf http://127.0.0.1:{PORT}')
    log.info(f'📖 {len(load_themes())} Themen geladen')
    log.info(f'🔒 HMAC-SHA256 Sessions aktiv')
    log.info(f'🐉 Ist einfach passiert.')

    # Bierdeckel-Lifecycle Thread starten
    bd_thread = threading.Thread(target=_bierdeckel_lifecycle_thread, daemon=True)
    bd_thread.start()
    log.info(f'🍺 Bierdeckel-Lifecycle Thread gestartet (60sec Intervall)')

    # Voice-Cleanup Thread starten
    vc_thread = threading.Thread(target=_voice_cleanup_thread, daemon=True)
    vc_thread.start()
    log.info(f'🔊 Voice-Cleanup Thread gestartet (5min Intervall)')

    # Räume aus DB laden VOR dem Lifecycle-Thread (keine Race Condition!)
    _load_raeume_from_db()
    ensure_raeume()

    # Raum+Tisch Lifecycle Thread starten
    tl_thread = threading.Thread(target=_lifecycle_thread, daemon=True)
    tl_thread.start()
    log.info(f'🏠 Lifecycle Thread gestartet ({len(raeume)} Räume)')

    # Cheater-Vote-Timer Thread starten
    cv_thread = threading.Thread(target=_cheater_vote_timer_thread, daemon=True)
    cv_thread.start()
    log.info(f'🗳️ Cheater-Vote-Timer Thread gestartet (5sec Intervall)')

    # Archiv-Thread starten (universelle Daten-Aufräumung)
    ar_thread = threading.Thread(target=_archiv_thread, daemon=True)
    ar_thread.start()
    log.info(f'🗑️ Archiv-Thread gestartet (60min Intervall)')

    # FRP Admin-Pass beim Start an Owner-PW koppeln (falls Owner existiert)
    if has_owner():
        try:
            conn_frp = get_db('accounts.db')
            ow = conn_frp.execute('SELECT pw_hash FROM users WHERE is_owner = 1').fetchone()
            conn_frp.close()
            if ow and ow['pw_hash']:
                _refresh_frp_admin(ow['pw_hash'])
        except Exception as e:
            log.warning(f'📡 FRP Init-Sync übersprungen: {e}')

    print(f'🍺 Kneipen-Schlägerei auf http://127.0.0.1:{PORT}')
    print(f'📖 {len(load_themes())} Themen')
    print(f'🐉 Ist einfach passiert.')

    # Bitcoin: Startup-Integrity-Check + Externe Anchors + 6h-Loop
    _btc_startup_integrity_check()
    _btc_scan_external_anchors()
    def _btc_live_verify_loop():
        while True:
            time.sleep(6 * 3600)  # 6h
            try:
                _btc_live_verify_and_persist()
            except Exception as e:
                log.error(f"⚠️ BTC Live-Verify-Loop Fehler: {e}")
    threading.Thread(target=_btc_live_verify_loop, daemon=True).start()

    # Bind auf 0.0.0.0 = alle Netzwerk-Interfaces (LAN, Hotspot, externe IP).
    # Vault-Gate + PQ-Auth + Code-Verify schützen die API. Localhost-only wäre tot
    # für jedes Mehr-Geräte-Szenario (Café, Hotspot, Portweiterleitung etc.).
    # ThreadingHTTPServer: jeder Request in eigenem Thread → erlaubt rekursive Self-Calls
    # (z.B. Public-URL-Check der die eigene URL anpingt). Pflicht für NAT-Loopback-Tests.
    ThreadingHTTPServer(('0.0.0.0', PORT), GameHandler).serve_forever()
