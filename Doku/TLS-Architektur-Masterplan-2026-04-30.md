# TLS-Architektur Masterplan — 2026-04-30

> Architektonische Grundsatzentscheidung für Outbound-Verbindungen zwischen Kneipen-Schlägerei, ShinNexus, Shidow und allen zukünftigen SAI-Projekten.
> Ausgehandelt zwischen Ray & Hasi während der Heimkehr-Session, gilt für alle Repos die in `/media/shinpai/KI-Tools/Projekt-SAI/` und `/media/shinpai/KI-Tools/Kneipen-Schlaegerei/` leben.

---

## 1. Problem — warum wir das brauchen

Aktuell verwendet `nexus_request` in Kneipen-Schlägerei (und potentiell auch Shidow, sobald gebaut):

```python
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
```

Das heißt jede Outbound-HTTPS-Verbindung akzeptiert JEDES Zertifikat — auch gefälschte. Ein Angreifer im Netzwerk-Pfad (kompromittierter Router, MITM-Proxy, böse DNS) kann die TLS-Verschlüsselung aufbrechen und Owner-Passwörter, TOTP-Codes und Session-Tokens im Klartext abgreifen.

Gleichzeitig soll das Ökosystem aber **Heimwerker-tauglich** bleiben: jemand der seine Kneipe oder seinen Nexus auf einem PC oder Handy lokal startet, soll nicht von einem Cert-Zwang erschlagen werden.

---

## 2. Design-Prinzipien (Hasi-Diktat 30.04.)

| # | Regel | Begründung |
|---|---|---|
| 1 | **Domain konfiguriert + HTTPS erreichbar → Pflicht-HTTPS, kein Fallback** | Wer eine Domain hat, kann auch ein Cert haben. Kein leichtsinniges HTTP wenn HTTPS möglich wäre. |
| 2 | **Selbst-signierte Zertifikate fliegen IMMER raus** | Schein-Sicherheit ist schlimmer als ehrliches HTTP. User bekommen ein falsches Sicherheitsgefühl. |
| 3 | **LAN-IP / Localhost → HTTP ohne Warnung** | Heimwerker-Modus. Im LAN ist der Angreifer-Pfad bewusst akzeptiert. |
| 4 | **Public-IP ohne Domain → HTTP MIT klarer Unsicher-Warnung im UI** | Public-IP-HTTP ist tatsächlich gefährlich (Provider, Internet-Router lesen mit). User muss das wissen. |
| 5 | **Kein Konfig-Schalter zum Aufweichen** | Logik leitet sich aus `public_url`-Form ab (Domain vs IP vs leer). Kein env var, keine Hintertür. |
| 6 | **Caddy-on-Phone parken** | Eigenes Projekt, kommt später, jetzt nur dokumentieren. |
| 7 | **Café-Szenario**: Public-URL nicht erreichbar → lokale IP zeigen mit HTTP-Hinweis | Realistische Mobile-Nutzung. |
| 8 | **SSRF-Schutz**: nur unsere bekannten Endpoints akzeptieren | Datenpfad-Endpoints dürfen kein freies URL-Targeting erlauben. |

---

## 3. Verbindungs-Klassen

Jede Outbound-Verbindung wird in eine von vier Klassen eingeteilt anhand von Hostname-Form und Schema:

| Klasse | Erkennungs-Merkmal | TLS-Verhalten | UI-Anzeige |
|---|---|---|---|
| **DOMAIN_SECURE** | Hostname ist DNS-Name (kein IP), Schema https | Pflicht-HTTPS, `ssl.create_default_context()`, `check_hostname=True`, `verify_mode=CERT_REQUIRED`. Bei Fehler 502 zurück. | 🔒 grün — "Verschlüsselt verifiziert" |
| **PUBLIC_IP_HTTP** | Hostname ist öffentliche IPv4/IPv6, Schema http | HTTP erlaubt, kein TLS | ⚠️ rot — "Verbindung unverschlüsselt — alles mitlesbar" |
| **LAN_HTTP** | Hostname ist Loopback (127.0.0.0/8, ::1) oder RFC1918 (10/8, 172.16/12, 192.168/16) oder Link-Local (169.254/16, fe80::/10), Schema http | HTTP erlaubt, kein TLS | ℹ️ schlicht — "Lokales Netz" oder gar nichts |
| **REJECTED** | Selbst-signiertes HTTPS, Domain + http (Mixed-Mode), unbekanntes Schema | Verbindung wird abgelehnt | ❌ "Konfiguration nicht erlaubt" |

**Wichtig:** Domain + HTTP = REJECTED. Wer eine Domain hat, MUSS HTTPS nutzen. Sonst war die Domain nutzlos.

---

## 4. Code-Änderungen — `nexus_request` neu

### 4.1 Helper-Funktion: Verbindungs-Klasse bestimmen

```python
def _classify_connection(url: str) -> tuple[str, str | None]:
    """
    Klassifiziert eine URL in eine der vier Verbindungs-Klassen.
    Returns: (klasse, fehler_grund_oder_none)
    """
    from urllib.parse import urlparse
    import ipaddress
    parsed = urlparse(url.rstrip('/'))
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return 'REJECTED', 'Ungültige URL oder unbekanntes Schema'

    host = parsed.hostname
    # Versuche Hostname als IP zu parsen
    is_ip = False
    ip_obj = None
    try:
        ip_obj = ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        pass

    if is_ip:
        # IP-basiert: LAN_HTTP oder PUBLIC_IP_HTTP
        if parsed.scheme != 'http':
            # IP + HTTPS = abgelehnt (kein gültiges Cert für IP möglich → wäre selbst-signiert)
            return 'REJECTED', 'HTTPS auf IP nicht unterstützt — Selbst-signiert'
        if ip_obj.is_loopback or ip_obj.is_private or ip_obj.is_link_local:
            return 'LAN_HTTP', None
        if ip_obj.is_multicast or ip_obj.is_reserved or ip_obj.is_unspecified:
            return 'REJECTED', 'IP-Adresse nicht erlaubt'
        return 'PUBLIC_IP_HTTP', None
    else:
        # DNS-Name: muss HTTPS sein
        if parsed.scheme != 'https':
            return 'REJECTED', 'Domain erfordert HTTPS, kein HTTP-Fallback'
        return 'DOMAIN_SECURE', None
```

### 4.2 `nexus_request` umbauen

```python
def nexus_request(nexus_url, path, data=None):
    """HTTP(S)-Request an ShinNexus mit klassifizierungs-basiertem TLS."""
    import urllib.request, urllib.error, ssl
    klasse, err = _classify_connection(nexus_url)
    if klasse == 'REJECTED':
        return 400, {"error": f"Verbindung nicht erlaubt: {err}"}

    full_url = f"{nexus_url.rstrip('/')}{path}"
    headers = {"Accept": "application/json"}
    body = None
    if data:
        headers["Content-Type"] = "application/json; charset=utf-8"
        body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(full_url, data=body, headers=headers)

    if klasse == 'DOMAIN_SECURE':
        ctx = ssl.create_default_context()
        # check_hostname=True und verify_mode=CERT_REQUIRED sind Defaults — explizit nicht abschalten
    else:
        # LAN_HTTP, PUBLIC_IP_HTTP — HTTP, kein TLS-Kontext
        ctx = None

    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", errors="replace"))
        except Exception:
            return e.code, {"error": f"ShinNexus HTTP {e.code}"}
    except ssl.SSLError as e:
        return 502, {"error": f"TLS-Fehler — Cert nicht vertrauenswürdig: {e}"}
    except Exception as e:
        return 502, {"error": f"ShinNexus nicht erreichbar: {e}"}
```

### 4.3 Was rausfliegt

```python
# WEG:
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
```

### 4.4 SSRF-Guard bleibt

Die existierende `_is_safe_outbound_host`-Funktion bleibt zusätzlich aktiv für den Fall dass der Klassifizier-Schritt eine LAN_HTTP-Klasse erlaubt aber der konkrete Endpoint-Handler trotzdem keinen Zugriff auf interne Adressen erlauben soll. Beispiel: `/api/whitelist/import` darf Body zurück geben — da blocken wir LAN-Hosts auch dann wenn die Klassifikation "erlaubt" wäre.

---

## 5. UI-Änderungen — Banner-Stufen

### 5.1 Anzeige im Dashboard / Setup

Neue Komponente `<TlsConnectionBadge>` (Vue/HTML im `index.html`/`gameplay.html`):

| Klasse | Banner | Farbe | Tooltip |
|---|---|---|---|
| DOMAIN_SECURE | 🔒 Verbindung verifiziert | grün | Cert von echter CA, alles verschlüsselt |
| LAN_HTTP | 🏠 Lokales Netzwerk | grau | Im LAN, kein TLS nötig |
| PUBLIC_IP_HTTP | ⚠️ UNVERSCHLÜSSELT — alles mitlesbar | rot, blinkend | Eine Domain mit HTTPS würde das beheben |
| REJECTED | ❌ Nicht erlaubte Konfiguration | rot | Setup-Wizard öffnen |

### 5.2 Setup-Wizard Hinweise

Beim ersten Setup zeigt der Wizard:

> **Empfohlener sicherer Betrieb**: Domain + Caddy mit Let's Encrypt. Das sind 5 Minuten Arbeit und du bekommst HTTPS automatisch.
>
> **Ohne Domain im LAN**: läuft, sicher genug für privates Netzwerk.
>
> **Public-IP ohne Domain**: läuft, ABER alle Daten werden im Klartext übertragen. Nur wenn du wirklich nichts geheim hast.

---

## 6. Migration — wie kommen wir vom Alten zum Neuen

1. **Code-Änderung** in `nexus_request` (siehe 4.2). Backup vorher als `server.py.bak-pre-tls-v1`.
2. **Alle Aufrufer von `nexus_request`** identifizieren und checken: schicken sie unsere `public_url`-Form? Wenn ja, kein extra Aufwand. Wenn nein, sie passen User-URL an → URL-Validierung dort.
3. **Frontend-Banner** einbauen: ein API-Endpoint `/api/tls-status` der die Klassifikation der konfigurierten `public_url` zurückgibt. Frontend rendert Badge.
4. **Setup-Wizard** erweitern um die drei Hinweise oben.
5. **Re-Audit** nach der Implementierung.

---

## 7. Café-Szenario — konkrete Behandlung

Wenn die App auf dem Handy läuft und der User in einem Café-WLAN ohne Port-Freigabe ist:

1. Watchdog-Check (`run_network_check`) erkennt: `public_url` nicht erreichbar von außen.
2. App fällt zurück auf lokale IP-Anzeige (z.B. `192.168.1.42:5000`).
3. Banner: 🏠 Lokales Netzwerk — nur Geräte im selben WLAN können sich verbinden.
4. Außenwelt-Verbindung wird passiv markiert: "Public-URL aktuell nicht erreichbar — Port gesperrt?"
5. Sobald der User wieder in seinem Heim-WLAN ist, springt der Watchdog zurück auf die DOMAIN_SECURE-Konfig.

---

## 8. Caddy bleibt extern — bewusste Entscheidung (Hasi 30.04.)

**Caddy wird NICHT mit ausgeliefert.** Weder gebündelt noch als Lib eingebettet.

Begründung: Heimwerker installiert Caddy separat (Standard-Vorgehen für Webserver-Frontends), routet localhost auf Caddy, Caddy macht Public-Domain mit Let's-Encrypt verfügbar. So funktioniert es bei Hasi/Shinpai im Live-Setup mit ShinNexus auch heute schon.

Der Vorteil: weniger Maintenance-Last für uns, Heimwerker bleibt Herr seines Stacks, kein Mobile-Caddy-Bastelkram.

Konsequenz für unsere Architektur: wir gehen einfach davon aus dass eine `public_url` mit Domain bereits ein funktionierender Caddy-Reverse-Proxy davor sitzt. Wir validieren das implizit über die HTTPS-Cert-Verifikation.

Doku-Hinweis im Setup-Wizard: "Für Domain-Betrieb: Caddy mit Let's Encrypt vorab einrichten — wir liefern keine TLS-Terminierung mit."

---

## 9. Test-Matrix

Vor Merge müssen alle vier Klassen getestet sein:

| Test | Erwartetes Verhalten |
|---|---|
| `nexus_request("https://shinnexus.shinpai.de", "/api/chain/info")` | 200 mit echtem Cert, DOMAIN_SECURE |
| `nexus_request("https://192.168.1.50:5000", "/api/chain/info")` | 400 REJECTED — HTTPS auf IP |
| `nexus_request("http://192.168.1.50:5000", "/api/chain/info")` | 200, LAN_HTTP |
| `nexus_request("http://127.0.0.1:5000", "/api/chain/info")` | 200, LAN_HTTP |
| `nexus_request("http://46.225.209.246:5000", "/api/chain/info")` | 200, PUBLIC_IP_HTTP, Banner sollte rot sein |
| `nexus_request("http://shinnexus.shinpai.de", "/api/chain/info")` | 400 REJECTED — Domain ohne HTTPS |
| `nexus_request("https://self-signed.example.de", "/api/chain/info")` | 502 TLS-Fehler |
| `nexus_request("ftp://server.de", "/api/chain/info")` | 400 REJECTED — Schema |

---

## 10. Roadmap

**Diese Session (heute, ~30 Min):**
- ✅ Quick-Fixes A-D (gemacht)
- ✅ Re-Audit clean (gemacht)
- ✅ Architektur-Doku (= dieses Dokument)

**Nächste Session (~1.5 Std):**
- Code-Änderung `nexus_request` mit Klassifikation
- Backup `server.py.bak-pre-tls-v1`
- Lokale Tests (alle 8 Test-Matrix-Cases)
- Re-Audit
- Frontend-Badge-Komponente

**Übernächste Session (~1 Std):**
- Setup-Wizard-Hinweise
- Watchdog-Café-Logic
- Doku in Kneipe-Design.md verlinken

**Später / Eigene Projekte:**
- ShinNexus auf gleiche Architektur prüfen (falls dort Outbound-Calls hinzukommen)
- Shidow auf gleiche Architektur, sobald Outbound-Calls existieren
- (Caddy-Mobile-Bundling NICHT geplant — bewusste Entscheidung, siehe Sektion 8)

---

## 11. Was wir bewusst NICHT bauen

- ❌ Selbst-signiertes Cert akzeptieren mit "Trust on First Use"
- ❌ Konfig-Flag `INSECURE=true`
- ❌ HTTP-Fallback wenn HTTPS scheitert
- ❌ Cert-Pinning mit Fingerprint (zu wartungsintensiv für Heimwerker)
- ❌ Eigener Mini-CA-Generator in Python

Begründung: jede dieser Optionen wäre eine Hintertür die auf lange Sicht zu Schweizer Käse führt. Hasi-Diktat 30.04.: "Fallback bei Security = Megafail".

---

## 12. Offene Fragen für Hasi

1. **Banner-Wording**: ist "⚠️ UNVERSCHLÜSSELT — alles mitlesbar" so OK oder zu drastisch?
2. **Setup-Wizard-Texte**: sollen wir auch ne kleine Anleitung "So holst du dir eine Domain in 10 Minuten" beilegen?
3. **API-Endpoint `/api/tls-status`**: brauchen wir ihn als eigenen Endpoint oder soll das in `/api/public-url/config` rein?
4. **ShinNexus**: soll die Klassifikation auch dort eingebaut werden (für eigene Outbound-Calls falls vorhanden) oder erstmal nur Kneipe?

---

*Erstellt: 2026-04-30, Heimkehr-Session, von Ray & Hasi gemeinsam ausgehandelt.*
*Diamant-Status: ShinNexus + Kneipen-Schlägerei stehen sauber, TLS-Architektur ist die nächste Brokenoffensive.*
