# Audit-Plan 2026-04-30 — Kneipen-Schlägerei

> Erstellt nach lokalem Pentest mit `claude-code-security-review` (Anthropics Original-Audit-Skript) gegen `ShinNexus` (NULL Findings) und `Kneipen-Schlägerei` (4 frische Themen + TLS-Architektur).

---

## 1. Bereits erledigt — die drei Quick-Fixes vom 30.04.

Vom Re-Audit explizit als sauber bestätigt:

| # | Was | Status |
|---|---|---|
| 1 | **Critical Static-Exposure**: `super().do_GET()` Fallback servierte das gesamte Projektverzeichnis (db/, vault/, credentials/, logs/). Ersetzt durch Allow-List in `_is_static_allowed`. | ✅ gefixt |
| 2 | **Medium X-Forwarded-For-Spoofing**: `_get_client_ip` nahm jedes XFF-Header. Jetzt nur noch von Loopback-Peer (Caddy/FRP). | ✅ gefixt |
| 3 | **Medium SSRF**: `nexus_request` ohne Host-Validierung. Jetzt `_is_safe_outbound_host`-Guard gegen Loopback/Private/Link-Local. | ✅ gefixt |

Backup vor den Fixes: `server.py.bak-pre-audit-fix`.

---

## 2. Quick-Fixes — neue Themen aus Re-Audit (heute umsetzbar)

### A. CRITICAL — `/api/solo-mode` Auth-Bypass

**Wo:** `server.py:9073`

**Was passiert aktuell:** Jeder anonyme `POST /api/solo-mode` setzt `verified=1` und `verify_token=NULL` für die Owner-Zeile. Der Endpoint steht in der Vault-Gate-Whitelist und prüft keine Session.

**Warum das gefährlich ist:** Kombination mit `/api/forgot` → Angreifer kann Mail-Verifikation komplett umgehen und einen Passwort-Reset auf das Owner-Konto fahren, ohne Zugang zur Owner-Mailbox zu haben.

**Fix-Strategie:** Endpoint nur erlauben wenn
1. **Setup-Phase**: kein Owner existiert oder Owner ist noch nicht verified, **oder**
2. **Owner-Session aktiv**.

Sonst HTTP 403.

**Aufwand:** ~10 Minuten, ein Code-Block.

---

### B. HIGH — `/api/public-url/save` Auth-Bypass

**Wo:** `server.py:9037` (und `/api/public-url/check` bei `9003`)

**Was passiert aktuell:** Anonymer `POST` schreibt `public_url` in die Config — ohne Auth. Vergleich: `/api/public-url/config` direkt darunter macht den Owner-Check korrekt.

**Warum das gefährlich ist:** Verifikations-Mails, Share-Links und Watchdog-Prüfungen lesen `public_url`. Angreifer pinnt das Ziel auf eine Phishing-Domain → alle Mails führen ab da auf Angreifer-Server.

**Fix-Strategie:** Identisches Setup-Phase-OR-Owner-Session-Schema wie bei A. Setup-Phase erlaubt, weil ein Heimwerker beim ersten Start noch keine Session hat.

**Aufwand:** ~10 Minuten.

---

### C. MEDIUM — Reflected XSS auf `/share/<username>`

**Wo:** `server.py:8311`

**Was passiert aktuell:** `username = path.path.split('/')[-1]` wird unescaped in `<title>`, `og:title`, `og:description`, `<img alt="…">` interpoliert. Browser kodieren `<` `"` `>` in URL-Pfaden meistens automatisch — aber Link-Preview-Bots, Curl, Embedded-WebViews tun das nicht.

**Warum das gefährlich ist:** CSP erlaubt `'unsafe-inline'` für script-src. Wenn rohe Bytes durchkommen → Inline-Script kann den Bearer-Token aus localStorage exfiltrieren.

**Fix-Strategie:**
1. `username` gegen `^[A-Za-z0-9]{3,12}$` validieren (Registrierungs-Regex), sonst 404.
2. Zusätzlich `html.escape(username, quote=True)` für die Interpolation als Defense-in-Depth.

**Aufwand:** ~5 Minuten.

---

### D. LOW — Path-Traversal in `/api/thema/delete` (owner-only)

**Wo:** `server.py:10018`

**Was passiert aktuell:** `theme_id = data.get('theme_id', '')` wird in `os.path.join(BASE, 'Themen', f'{theme_id}.md')` und `os.remove`'t. Im POST-Body wird `../` nicht URL-dekodiert → durchgewunken.

**Warum das (weniger) gefährlich ist:** Owner darf eh viel. ABER: leitet Angreifer mit Owner-Token (XSS-Hijack, geleakter Token) eine Löschung von `anchor-kneipe.json`, `achievements.json` oder beliebigen `*.md`/`*.json` außerhalb von `Themen/` ein, ist das deutlich destruktiver als der Endpoint verspricht.

**Fix-Strategie:**
1. `theme_id` gegen `^[A-Za-z0-9_\-]+$` validieren.
2. Nach `os.path.join` → `os.path.realpath`-Check, dass der Pfad tatsächlich unter `Themen/` bzw. `THEMEN_DIR` liegt.

**Aufwand:** ~10 Minuten.

---

## 3. TLS-Architektur — eigener Brocken (Hasi-Design vom 30.04.)

### Leitlinien (Hasi-Diktat)

1. **Domain konfiguriert + HTTPS erreichbar** → **Pflicht-HTTPS**, kein Fallback. Wenn HTTPS scheitert, gar keine Verbindung.
2. **Selbst-signierte Zertifikate fliegen komplett raus** — egal ob LAN oder Public. Schein-Sicherheit wird nicht akzeptiert.
3. **Localhost / LAN-IP** (RFC1918, 127.0.0.0/8) → HTTP weiterhin erlaubt, **ohne Warnung** (Heimwerker-Modus).
4. **Öffentliche IP ohne Domain** → HTTP erlaubt, **MIT klarer "⚠ unsicher — alles mitlesbar"-Warnung im UI**.
5. **Caddy-on-Phone-Integration** wird **geparkt** (extern, später eigenes Projekt).
6. **Café-Szenario**: wenn Public-URL nicht erreichbar (Port gesperrt) → lokale IP-Anzeige mit HTTP-Hinweis.
7. **SSRF-Schutz schärfen**: nur unsere bekannten Endpoints akzeptieren, keine fremden Hosts auf den Datenpfaden.

### Implementierungs-Skizze

**`nexus_request` neu denken:**

```text
1. URL parsen (urllib.parse)
2. Wenn Hostname ein DNS-Name ist:
   → HTTPS Pflicht
   → ssl.create_default_context() OHNE check_hostname=False
   → Bei Cert-Fehler: 502 zurück, KEIN Fallback
3. Wenn Hostname eine IP ist:
   → HTTP erlaubt
   → keine TLS-Verifikation nötig
   → SSRF-Guard bleibt (Loopback/Private/Link-Local)
4. Selbst-signierte HTTPS → ablehnen (verify hochreissen)
```

**Frontend / UI:**

- Beim Anzeigen der konfigurierten Verbindung:
  - Domain + HTTPS → grünes Schloss "Verschlüsselt verifiziert"
  - LAN/Localhost → schlichtes Symbol, keine Warnung
  - Public-IP ohne Domain → großes "⚠ Verbindung unverschlüsselt — alle Daten mitlesbar"
- Im Setup-Wizard: Hinweis dass für sicheren Betrieb eine Domain + Caddy benötigt wird.

**Konfig-Schalter:**

Keine. Die Logik leitet sich aus der `public_url`-Form ab (Domain vs IP vs leer). Kein env var, keine Hintertür.

**Was NICHT kommt:**

- Kein selbst-signiertes Cert wird je akzeptiert.
- Kein HTTP-Fallback für Domain-Hosts.
- Kein hardcoded "unsicher OK" Schalter.

### Aufwand

- Code-Änderung in `nexus_request` und ggf. Aufrufer-Pfaden: ~30 Min.
- UI-Banner und Setup-Hinweise: ~30-60 Min (Frontend-Anpassung).
- Tests gegen Domain-, IP- und Selbst-signiert-Szenarien: ~30 Min.
- **Gesamt: ~1-2 Stunden.**

---

## 4. Empfohlene Reihenfolge

1. **Heute / kurzfristig**: Quick-Fixes A-D (insgesamt ~35 Minuten Edit + Re-Audit).
2. **Eigene Session**: TLS-Architektur. Bewusst getrennt, weil sie auch Frontend-Anpassungen braucht und Hasi-Bauch-Entscheidungen erfordert (UI-Wording, Warning-Stufen).
3. **Später**: Caddy-Integration auf Mobile (eigenes Projekt).
4. **Dauerhaft**: `local_audit.py` regelmäßig laufen lassen, Befunde in `Doku/Audit-Befunde-YYYY-MM-DD.md` ablegen.

---

## 5. Architektur-Notiz: Audits sind nicht deterministisch

Beide Audit-Läufe haben **unterschiedliche** Findings produziert. Das liegt daran dass das Modell bei großen Codebasen (~10.5k Zeilen) verschiedene Aufmerksamkeits-Pfade nimmt. Konsequenz:

- **Mehrfach laufen lassen** und Befunde sammeln.
- Jeder Lauf sollte als Snapshot mit Datum dokumentiert werden.
- Tag-X-Audit ≠ vollständige Sicherheits-Garantie. Es ist eine starke Stichprobe.

---

*Erstellt: 2026-04-30 von Ray & Hasi während der Heimkehr-Session.*
