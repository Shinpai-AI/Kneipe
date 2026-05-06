# Schlachtplan — Kneipe-Auth-Sprint

> Stand: 2026-05-02
> Sprint-Ziel: Kneipe-Auth-Schicht auf ShinNexus-Niveau hochziehen
> Bezug: `/pCloudDrive/Shinpai-AI/Doku/Programm-Entwicklung/Sicherheitskonzept.md`
> Anlass: Reset-Bug 2026-05-02 (User-Lookup `shidow@shinpai.de` schlug stumm fehl) als Symptom veralteter Auth-Schicht

---

## Sinn

Kneipe ist mit V2.0.0 LIVE, Server-Architektur und Marktplatz-/Chat-/Forum-Funktionen sind stabil. Was fehlt ist die Auth-Schicht auf demselben Niveau wie ShinNexus:

- **Heute:** klassisches Mail-Link-Reset, 2FA wird beim Reset pauschal geloescht, Recovery-Seed wird bei Registrierung zwar erzeugt aber im Reset-Flow nicht genutzt, Email-Lookup case-empfindlich, kein Lifecycle-Cleanup-Thread fuer Inaktivitaet.
- **Ziel:** Seed-basierter Reset (kein Mail-Link mehr), Email-Verify-Code-System, sauberer 2FA-Refresh-Flow, 10-Minuten-Sub-Fenster fuer Email-Change, Inaktivitaets-Lifecycle, klar getrennte UI-Reiter fuer User und Owner.

Stand vor Sprint: **4 von 26** Sicherheitskonzept-Endpoints implementiert (~15%).
Stand nach Sprint: **>=24 von 26** Endpoints (~92%, plus Kneipe-spezifische Erweiterungen wo sinnvoll).

ShinNexus dient als Code-Referenz. Pattern wird 1:1 ueberfuehrt, mit Anpassungen wo Kneipe-Eigenheiten greifen (Gast-Modus, Bierdeckel-Identifizierung, FRP-Admin-Refresh).

---

## Modul-Struktur (Ziel)

```
Kneipen-Schlaegerei/
├── server.py                 # bestehender Monolith — wird sektionsweise refactored
├── auth/                     # NEU — eigenes Sub-Paket
│   ├── __init__.py
│   ├── seed_reset.py         # Forgot + Seed-Unlock + PW-Reset-Set
│   ├── email_verify.py       # Verify-Code-System (statt Mail-Link)
│   ├── twofa_refresh.py      # 2FA-Refresh + Confirm
│   ├── lifecycle.py          # Lifecycle-Cleanup-Thread
│   └── normalization.py      # email.lower(), seed-strip, etc.
├── data/
│   └── accounts.db           # bestehend, Schema-Erweiterung noetig
└── Doku/
    └── Schlachtplan-Kneipe-Auth-Sprint.md  ← diese Datei
```

`server.py` bleibt monolithisch — neue Auth-Logik wandert ins `auth/`-Subpaket, alte Endpoints werden zu Pass-Through-Wrappern.

---

## Schema-Migrationen

### `users`-Tabelle erweitern

```sql
ALTER TABLE users ADD COLUMN recovery_seed_hash TEXT;
ALTER TABLE users ADD COLUMN pw_reset_pending INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN pw_reset_triggered_at INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN reset_post_pw_window INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN reset_post_pw_window_expires_at INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN email_verified_at INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN last_seen_at INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN verification_level TEXT DEFAULT 'unverified';
ALTER TABLE users ADD COLUMN deletion_due_at INTEGER DEFAULT 0;
```

### `email_verify_codes`-Tabelle (NEU)

```sql
CREATE TABLE IF NOT EXISTS email_verify_codes (
  user_id TEXT PRIMARY KEY,
  code_hash TEXT NOT NULL,            -- SHA256 des 6-stelligen Codes
  attempts_today INTEGER DEFAULT 0,
  expires_at INTEGER NOT NULL,
  last_request_date TEXT
);
```

### `twofa_refresh_pending`-Tabelle (NEU)

```sql
CREATE TABLE IF NOT EXISTS twofa_refresh_pending (
  user_id TEXT PRIMARY KEY,
  new_totp_secret TEXT NOT NULL,
  new_seed_hash TEXT NOT NULL,
  expires_at INTEGER NOT NULL          -- now + 600 (10 min)
);
```

### `twofa_refresh_windows`-Tabelle (Rate-Limit-Tracking, NEU)

```sql
CREATE TABLE IF NOT EXISTS twofa_refresh_windows (
  user_id TEXT NOT NULL,
  attempt_at INTEGER NOT NULL
);
CREATE INDEX idx_2fa_refresh_user ON twofa_refresh_windows(user_id);
```

---

## Tickets

### TS0 — Vorbereitung (Tag 1, ~3 h)

| ID | Beschreibung | DoD |
|---|---|---|
| TS0.1 | Branch `kneipe-auth-sprint` aus `main` | Branch existiert |
| TS0.2 | `auth/`-Subpaket-Skelett anlegen (leere Module + `__init__.py`) | Imports funktionieren |
| TS0.3 | Schema-Migrations-Skript `migrations/002_auth_uplift.sql` schreiben | SQL kompiliert auf SQLite |
| TS0.4 | Bestehende User-Records mit Default-Werten fuer neue Spalten zurueckfuellen (Migration einmalig) | Alle existierenden Rows haben die neuen Felder |

### TS1 — Quick-Win: Email-Lookup case-insensitive (Tag 1, ~2 h)

**Anlass:** Reset-Bug 2026-05-02 — `Shidow@shinpai.de` (gross) wurde nicht gefunden weil DB `shidow@shinpai.de` (klein) speichert und Lookup case-empfindlich war.

| ID | Beschreibung | DoD |
|---|---|---|
| TS1.1 | Alle SQL-Queries die `email = ?` auf `LOWER(email) = LOWER(?)` aendern | grep liefert keinen direkten case-empfindlichen Vergleich mehr |
| TS1.2 | Bei jedem Schreib-Pfad (register, email-update) explizit `email = email.lower().strip()` | Tests gruen |
| TS1.3 | Bestand-Migration: alle Email-Felder einmalig auf lower() ziehen | `SELECT email FROM users WHERE email != LOWER(email)` ist leer |
| TS1.4 | Reset-Bug-Reproduzieren auf Test-Instanz mit `Shidow@shinpai.de` und `shidow@shinpai.de` — beide muessen reset triggern | Reproduzier-Test gruen |

### TS2 — Email-Verify-Code-System (Tag 2-3, ~6 h)

**Statt** Reset-Link in Mail: 6-stelliger Ziffern-Code, 30 min gueltig, max 3 Codes pro Tag pro User. Anti-Mail-Link-Drama.

| ID | Beschreibung | DoD |
|---|---|---|
| TS2.1 | `auth/email_verify.py`: Code generieren (6 Ziffern, kryptographisch random), Hash + Expiry in `email_verify_codes` ablegen | Unit-Test: Code-Generierung |
| TS2.2 | Endpoint `POST /api/email/send-verify` — Code erzeugen + Mail-Versand. Rate-Limit 3/Tag pro User-ID | Test: 4. Versuch wird abgelehnt |
| TS2.3 | Endpoint `POST /api/email/verify-code` — Code-Pruefung (constant-time), bei Erfolg `email_verified_at = now()` | Test: korrekter Code akzeptiert, falscher abgelehnt |
| TS2.4 | Mail-Template umschreiben: kein Link, nur Code anzeigen | Mail-Vorschau OK |

### TS3 — Seed-Reset-Flow (Tag 3-5, ~10 h)

| ID | Beschreibung | DoD |
|---|---|---|
| TS3.1 | `auth/seed_reset.py`: `POST /api/auth/forgot` (Email + Username, 3/h Rate-Limit, keine Mail) | Unit-Test |
| TS3.2 | `POST /api/auth/seed-unlock` (Seed-Eingabe, 5 Fehlversuche/10min/IP, constant-time-Compare) | Unit-Test |
| TS3.3 | Bei Erfolg: `pw_reset_pending=true`, `pw_reset_triggered_at=now()`, neue Session mit Reset-Flag | Integration-Test |
| TS3.4 | `POST /api/auth/pw-reset-set` — neues PW setzen, `pw_reset_pending=false`, `reset_post_pw_window=true`, Expiry = now+600 | Integration-Test |
| TS3.5 | Owner-Spezial-Pfad: zusaetzliche Seed-Phrase im Body, ML-KEM-Wrap (falls Kneipe Vault hat) atomisch neu schreiben | Integration-Test |
| TS3.6 | Recovery-Seed-Hash bei Registrierung in `recovery_seed_hash` ablegen — Seed wird User EINMAL beim Register angezeigt | Test: Register liefert seed in Response |
| TS3.7 | Alte Endpoints `/api/forgot` und `/api/reset-password` mit **Deprecation-Warnung** in Response, beide leiten ab Phase X auf neue Endpoints um | Deprecation-Header gesetzt |

### TS4 — Sauberer 2FA-Refresh (Tag 5-6, ~6 h)

**Statt** Pauschal-Loeschen von `totp_secret` beim Reset: ein eigener Refresh-Flow der den alten 2FA-Code nicht braucht und sauber rotiert.

| ID | Beschreibung | DoD |
|---|---|---|
| TS4.1 | `auth/twofa_refresh.py`: `POST /api/auth/2fa-refresh` — neuen Secret + Seed in Pending-Tabelle, Mail mit QR + Seed | Unit-Test |
| TS4.2 | Rate-Limit 3 Fenster/7 Tage pro User-ID, **keine Timer-Angabe** in Response bei Limit-Hit | Test: 4. Versuch in 7d wird abgelehnt mit vager Meldung |
| TS4.3 | `POST /api/auth/2fa-refresh-confirm` — TOTP-Code aus neuer App, bei Erfolg: `totp_secret=new`, `recovery_seed_hash=SHA256(new_seed)`, Pending leeren | Integration-Test |
| TS4.4 | Im `/api/reset-password`-Legacy-Pfad: NICHT mehr `totp_secret=NULL` setzen — das war der Bug. User muss 2FA-Refresh separat triggern | Code-Pfad entfernt |

### TS5 — 10-Min-Sub-Fenster fuer Email-Change (Tag 6, ~3 h)

| ID | Beschreibung | DoD |
|---|---|---|
| TS5.1 | `POST /api/auth/email` — wenn `reset_post_pw_window=true` UND `now < expires_at`: PW/2FA-Pflicht entfaellt | Unit-Test |
| TS5.2 | Sub-Flag wird nach erfolgreichem Email-Change geloescht (eine Aenderung pro Fenster) | Test: zweiter Aufruf im Fenster verlangt PW |
| TS5.3 | Nach Email-Change: `email_verified_at=0`, neuer Verify-Code automatisch an neue Email | Test: Verify-Code geht raus |

### TS6 — Lifecycle-Cleanup-Thread (Tag 7, ~3 h)

| ID | Beschreibung | DoD |
|---|---|---|
| TS6.1 | `auth/lifecycle.py`: Hintergrund-Thread alle 30 Minuten | Thread laeuft beim Server-Start |
| TS6.2 | Inaktivitaets-Cutoff: Stichtag `last_seen_at + Frist nach verification_level` (14d/90d/3J/unbegrenzt) | Test: alter User ist nach Frist weg |
| TS6.3 | Reset-Pending-Cutoff: 7 Tage seit `pw_reset_triggered_at` | Test: Account weg nach 7d ohne PW-Set |
| TS6.4 | Sub-Fenster-Cleanup: `reset_post_pw_window` nach Expiry-Timestamp auf false setzen (kein Account-Delete) | Test: Flag verfaellt sauber |
| TS6.5 | Owner-Account und Bot-Accounts (`is_bot=true`) niemals durch Auto-Cleanup loeschen | Test: Owner ueberlebt 100 Tage Inaktivitaet |

### TS7 — UI-Reiter Sicherheits-Tab (Tag 8-10, ~12 h)

**User-Reiter (jeder eingeloggt):**

| ID | Beschreibung | DoD |
|---|---|---|
| TS7.1 | Profil > Sicherheit-Tab anlegen mit Email-Kachel, PW-Kachel, 2FA-Kachel, Seed-Kachel | UI sichtbar |
| TS7.2 | Reset-Mode-Banner oben (wenn `pw_reset_pending=true`): Account funktional gesperrt, nur PW-Kachel | UI-Test |
| TS7.3 | Sub-Fenster-Banner mit Countdown (wenn `reset_post_pw_window=true`) | UI-Test |
| TS7.4 | Login-Seite: „Passwort vergessen?" oeffnet Forgot-Box, dann Seed-Unlock-Box | E2E: kompletter Reset-Flow |

**Owner-Reiter (zusaetzlich, nur fuer Owner sichtbar):**

| ID | Beschreibung | DoD |
|---|---|---|
| TS7.5 | Owner-Tab mit Vault-Status, Salt-Rotation-Knopf, Igni-Mode-Toggle, Bot-Quota | UI sichtbar nur fuer Owner |
| TS7.6 | Endpoints `/api/vault/lock`, `/api/owner/igni/export`, `/api/owner/bot-quota`, `/api/owner/members*` portieren aus ShinNexus | Endpoints aktiv |

**Globaler Anchor-Footer (Hasi-Diktat 2026-05-02):**

| ID | Beschreibung | DoD |
|---|---|---|
| TS7.7 | Globaler Footer als letzte Zeile auf **jedem** Screen — durchgaengig, kein Screen ausgenommen. Inhalt: Anchor-Status (Version + TXID-Kurz + Block-Height) aus `anchor-kneipe.json`, klickbar fuer Detail-Popup mit voller Anchor-History | Footer auf Marktplatz, Tisch, Tresen, Profil, Login, Forum, Anschlagtafel sichtbar |

### TS8 — Migration / Bestand-User (Tag 11, ~4 h)

| ID | Beschreibung | DoD |
|---|---|---|
| TS8.1 | Bestand-User ohne `recovery_seed_hash` werden beim naechsten Login aufgefordert, sich einen Seed zu generieren | UI-Banner sichtbar |
| TS8.2 | Bestand-User ohne `email_verified_at` werden beim Login auf Verify-Code-Flow geleitet | UI-Banner sichtbar |
| TS8.3 | `verification_level` automatisch setzen: `email_verified_at != 0` ⇒ `standard`, sonst `unverified` | DB-Sweep einmalig |
| TS8.4 | Alte `verify_token`-Spalte als deprecated markieren, in Phase 2 entfernen | Spalte erkennbar als deprecated |

### TS9 — Tests (Tag 12-13, parallel zu TS3-TS7)

| ID | Beschreibung | DoD |
|---|---|---|
| TS9.1 | Unit-Tests fuer alle `auth/*`-Module (60% Coverage Pflicht) | pytest-cov >= 60% |
| TS9.2 | Integration-Tests fuer alle neuen Endpoints | Alle neuen Routes abgedeckt |
| TS9.3 | E2E-Tests: Reset-Flow PW-only, PW+2FA, alles-verloren | drei Szenarien gruen |
| TS9.4 | claude-code-security-review-Lauf gegen Branch | 0 kritische Findings |
| TS9.5 | Audit-Findings fixen, Re-Audit | 0 nach Re-Audit |

### TS10 — Deploy (Tag 14, ~3 h)

| ID | Beschreibung | DoD |
|---|---|---|
| TS10.1 | Test-Instanz unter `/Testareal/Kneipen-Schlaegerei/` mit neuem Auth-Stack | erreichbar via Test-URL |
| TS10.2 | Smoke-Test: Login, Reset-Flow komplett, Email-Change im Sub-Fenster, 2FA-Refresh | alles gruen |
| TS10.3 | Wartungsfenster-Ankuendigung an Live-User (Bierdeckel-Pin oben) | Ankuendigung sichtbar |
| TS10.4 | Deploy auf Live-VPS (`SAI:~/Kneipe/`) per rsync + `sudo systemctl restart kneipe` | Live-System auf neuer Auth-Schicht |
| TS10.5 | Bitcoin-Re-Anchor mit V2.1.0 (Auth-Sprint-Marker) | Anchor-TX confirmed |
| TS10.6 | Status-Page-Update + Bekanntmachung dass Reset jetzt Seed-basiert ist | User informiert |

---

## Reihenfolge-Empfehlung

1. **Sofort-Quick-Win**: TS1 (Email-Lookup case-insensitive) — fixt den heutigen Bug in Stunden, kein Schema-Aufwand
2. **Schema-Migration**: TS0 + Schema-Block — Voraussetzung fuer alle weiteren TSs
3. **Email-Verify-Code (TS2)**: Voraussetzung fuer 2FA-Refresh und Email-Change-Flows
4. **Seed-Reset (TS3)**: Kern des Sprints, ersetzt Mail-Link
5. **2FA-Refresh (TS4)**: braucht TS2 (Mail-Code) und TS3 (Reset-Session)
6. **Sub-Fenster (TS5)**: braucht TS3 (PW-Set setzt das Sub-Flag)
7. **Lifecycle (TS6)**: parallel ab TS3 entwickelbar
8. **UI (TS7)**: braucht alle Backend-Endpoints fertig
9. **Migration (TS8)** + **Tests (TS9)**: vor Deploy
10. **Deploy (TS10)**: am Ende

Geschaetzt: **~14 Arbeitstage** fuer einen einzelnen Entwickler. Bei Parallel-Arbeit (UI + Backend gleichzeitig): **~9 Arbeitstage**.

---

## Folge-Sprint: Kneipe-PQ-Vault (Hasi-GO 2026-05-02)

Nach diesem Auth-Sprint folgt ein zweiter Sub-Sprint zur Anhebung der Kneipe-Vault-Schicht auf ShinNexus-Niveau:

- **Heute (Kneipe):** Argon2id-PW-Hash + AES-256-GCM fuer Messages + Salt + Igni. Klassisch, kein Post-Quantum.
- **Ziel (Kneipe-PQ-Vault):** zusaetzlich ML-KEM-768-Wrap fuer den DEK (analog ShinNexus), `vault_kem_priv.vault` plus `vault_kem_priv.seed.vault` fuer atomischen Owner-PW-Reset via Seed.
- **Performance-Erwartung:** ML-KEM-768 Encapsulation/Decapsulation jeweils 50-100 Mikrosekunden. Argon2id-KEK-Derivation bleibt mit 100-300 ms die einzige spuerbare Wartezeit (existiert heute schon). User-Login bleibt Millisekunden-schnell, kein Performance-Problem.
- **Begruendung:** Quanten-resistente Verschluesselung der Bestand-Daten (Marktplatz-Listings, Wallet, Forum-Posts) fuer Langzeit-Schutz. „Harvest-now-decrypt-later"-Risiko abdecken.
- **Reihenfolge:** Erst Auth-Sprint (dieser hier) komplett durch, dann PQ-Vault als eigener Sprint mit Migration der Bestand-DBs.
- **Eigene Doku:** Bei Sprint-Start `Schlachtplan-Kneipe-PQ-Vault-Sprint.md` analog zu diesem hier.

---

## Risiken

- **Bestand-User ohne Recovery-Seed**: muessen aktiv aufgefordert werden, einen zu generieren — sonst sind sie im Reset-Fall verloren. UI-Banner ist Pflicht ab Tag 1 nach Deploy.
- **2FA-Pauschal-Reset entfernen** koennte User-Verwirrung ausloesen, die sich auf das alte Verhalten verlassen haben („nach Reset ist 2FA weg"). Klarer Hinweis in Reset-Erfolgs-Mail dass 2FA separat refresht werden muss.
- **Vault-Layer in Kneipe**: aktuell minimal (Salt + Igni vorhanden, aber nicht im Reset-Flow). Fuer Owner-PW-Reset muss ggf. `vault_kem_priv.seed.vault` analog ShinNexus angelegt werden — Schema-Pruefung in TS0 noetig.
- **Mail-Provider-Reputation**: V2.0-Kneipe nutzt externen SMTP — Verify-Codes haeufig genug versendet koennten in Spam-Filter laufen. Test-Lauf mit verschiedenen Providern (Gmail, gmx, Outlook) vor Deploy.
- **FRP-Admin-Refresh**: Im aktuellen `/api/reset-password`-Code laeuft `_refresh_frp_admin(pw_hash)` automatisch. Im neuen `/api/auth/pw-reset-set` muss das auch ausgeloest werden, sonst geht FRP-Tunnel nach PW-Change kaputt.

---

## Querverweise

| Doku | Zweck |
|---|---|
| `/pCloudDrive/Shinpai-AI/Doku/Programm-Entwicklung/Sicherheitskonzept.md` | Dachdoku, Soll-Stand |
| `/pCloudDrive/Shinpai-AI/Doku/Programm-Entwicklung/PQ-Architektur.md` | KEK/DEK/ML-KEM-Schichten |
| `/pCloudDrive/Shinpai-AI/Doku/Programm-Entwicklung/start.sh-Philosophie.md` | Service-Lifecycle, Igni |
| `/Projekt-SAI/ShinNexus/ShinNexus.py` | Code-Referenz fuer alle Auth-Module |

---

*Geschrieben 2026-05-02 nach Reset-Bug-Befund und Hasi-Diktat „Kneipe muss Sicherheits-Reiter genauso wie ShinNexus haben". 🐉*
