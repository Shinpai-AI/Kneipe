#!/usr/bin/env python3
"""
Repair-Skript für Ghost-Owner (Nexus-Owner-Setup-Bug).

Repariert Owner-Account mit leerem pw_hash / email / PQ-Keys, der via
/api/nexus/owner erstellt wurde. Trägt nach:
  - pw_hash (aus eingegebenem Nexus-Passwort)
  - email (zur Bestätigung)
  - PQ-Keys (wenn oqs verfügbar, sonst leer gelassen)
  - verify_token (neuer 6-stelliger Code)
  - Mail-Versand an angegebene Email

Führe aus IM Kneipe-Verzeichnis (da wo server.py liegt):
  cd ~/Kneipe && python3 repair_ghost_owner.py
"""
import os, sys, sqlite3, time, getpass

# Import aus laufendem Kneipe-Code
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server as K


def main():
    print("=" * 60)
    print("  GHOST-OWNER REPAIR — Nexus-Owner-Setup-Bug-Fix")
    print("=" * 60)

    # Vault muss offen sein (für totp_secret etc.)
    if not K.vault_is_unlocked():
        print("⚠️  Vault ist gesperrt. Igni-Auto-Unlock versuchen...")
        try:
            K.igni_load_and_unlock()
        except Exception as e:
            print(f"❌ Auto-Unlock fehlgeschlagen: {e}")
            pw_for_vault = getpass.getpass("  Vault-Passwort (= Owner-Passwort): ").strip()
            if not K.vault_unlock(pw_for_vault):
                print("❌ Vault konnte nicht entsperrt werden. Abbruch.")
                sys.exit(1)
        if not K.vault_is_unlocked():
            print("❌ Vault immer noch gesperrt. Abbruch.")
            sys.exit(1)
    print("✅ Vault entsperrt.")

    # Owner suchen
    conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db', 'accounts.db'))
    conn.row_factory = sqlite3.Row
    owner = conn.execute("SELECT * FROM users WHERE is_owner=1").fetchone()
    if not owner:
        print("❌ Kein Owner in der DB. Abbruch.")
        sys.exit(1)
    print(f"\nOwner gefunden:")
    print(f"  id         = {owner['id']}")
    print(f"  name       = {owner['name']}")
    print(f"  shinpai_id = {owner['shinpai_id']}")
    print(f"  email      = {owner['email'] or '(LEER)'}")
    print(f"  pw_hash    = {'(LEER — Ghost!)' if not owner['pw_hash'] else '(gesetzt)'}")
    print(f"  pq_dsa_pub = {'(LEER)' if not owner['pq_dsa_pub'] else '(gesetzt)'}")
    print(f"  nexus_url  = {owner['nexus_url']}")
    print()

    # Bestätigung
    if input("Diesen Owner reparieren? [j/N]: ").strip().lower() not in ('j', 'ja', 'y', 'yes'):
        print("Abgebrochen.")
        sys.exit(0)

    # Passwort + Email einholen
    print("\n— Credentials nachtragen —")
    pw1 = getpass.getpass("  Dein Nexus-Passwort (= Kneipe-Login-Passwort): ").strip()
    pw2 = getpass.getpass("  Nochmal zur Bestätigung:                      ").strip()
    if pw1 != pw2:
        print("❌ Passwörter stimmen nicht überein. Abbruch.")
        sys.exit(1)
    if len(pw1) < 4:
        print("❌ Passwort zu kurz. Abbruch.")
        sys.exit(1)

    email = input("  Email-Adresse für Verifikation: ").strip()
    if '@' not in email or '.' not in email:
        print("❌ Ungültige Email. Abbruch.")
        sys.exit(1)

    # Hash + verify_code
    pw_hash = K.hash_pw(pw1)
    verify_code = K.generate_verify_code()
    verify_exp = time.time() + K.CODE_TTL_SECONDS

    # PQ-Keys generieren (wenn oqs da)
    pq_dsa_pub, pq_kem_pub, pq_priv_enc = '', '', ''
    if getattr(K, 'PQ_AVAILABLE', False):
        dsa_pub, kem_pub, priv_blob = K.pq_generate_user_keypair()
        if priv_blob:
            pq_dsa_pub = dsa_pub or ''
            pq_kem_pub = kem_pub or ''
            pq_priv_enc = K.pq_encrypt_private_blob(priv_blob, pw1, owner['id'])
            print(f"✅ PQ-Keys generiert (ML-DSA-65 + ML-KEM-768)")
    else:
        print("⚠️  oqs-Library nicht verfügbar — PQ-Keys bleiben leer (später nachrüstbar)")

    # Update DB
    now = time.time()
    conn.execute("""UPDATE users
                    SET pw_hash = ?, email = ?, verify_token = ?, verify_expires = ?,
                        verified = 0,
                        pq_dsa_pub = COALESCE(NULLIF(pq_dsa_pub, ''), ?),
                        pq_kem_pub = COALESCE(NULLIF(pq_kem_pub, ''), ?),
                        pq_private_enc = COALESCE(NULLIF(pq_private_enc, ''), ?),
                        updated_at = ?
                    WHERE id = ?""",
                 (pw_hash, email, verify_code, verify_exp,
                  pq_dsa_pub, pq_kem_pub, pq_priv_enc,
                  now, owner['id']))
    conn.commit()
    conn.close()
    print(f"\n✅ DB aktualisiert. pw_hash + email gesetzt, verify_token = {verify_code}")

    # FRP-Admin-Pass aus neuem Hash ableiten (analog Owner-Setup)
    try:
        K._refresh_frp_admin(pw_hash)
        print("✅ FRP-Admin-Pass aktualisiert")
    except Exception as e:
        print(f"⚠️  FRP-Refresh übersprungen: {e}")

    # Email versenden
    print(f"\n📧 Sende Verifikations-Code an: {email}")
    if K.smtp_configured():
        ok = K.send_verify_email(email, verify_code, owner['name'])
        if ok:
            print(f"✅ Mail gesendet! Check dein Postfach. Code ist 10 Min gültig.")
            print(f"   Nach Erhalt → Kneipe aufrufen, einloggen (Name={owner['name']}, PW=dein-Nexus-PW),")
            print(f"   dann Code eingeben zum Verifizieren.")
        else:
            print(f"⚠️  Mail-Versand fehlgeschlagen. Code manuell: {verify_code}")
    else:
        print(f"⚠️  SMTP nicht konfiguriert. Code manuell: {verify_code}")
        print(f"   (Du kannst dich trotzdem einloggen, verify_token läuft in 10 Min ab.)")

    print("\n" + "=" * 60)
    print("  Ghost-Owner repariert. Bitte einloggen mit:")
    print(f"    Name:     {owner['name']}")
    print(f"    Passwort: (dein Nexus-Passwort)")
    print(f"    Email:    {email}")
    print("=" * 60)


if __name__ == '__main__':
    main()
