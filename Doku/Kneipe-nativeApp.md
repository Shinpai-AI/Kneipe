# Kneipe Native App — Session-Dokumentation

> Stand: 2026-04-21 Nacht (Hasi + Ray)
> Arbeits-Ordner: `/media/shinpai/KI-Tools/Kneipen-Schlaegerei/installer/Android/`
> Repo-Release-Ordner: `/home/shinpai/pCloudDrive/Shinpai-AI/Projekte/Kneipen-Schlaegerei/`

---

## 1. Was wir heute gebaut haben

### Phase 1 — WebView-Wrapper (v1.5.6 → v1.5.8)
- Capacitor 6 + Android-Platform aufgesetzt
- `server.url = "https://bar.shinpai.de"` — WebView lädt original Kneipe-UI
- Keystore (`kneipe.keystore`) generiert, Signing-Workflow in GitHub Actions eingebaut
- Build-Pipeline: Linux AppImage + Windows Setup + Android APK in einem Workflow
- Debug-Sign-Fallback wenn keine Secrets, Release-Sign wenn Secrets da
- Mikrofon-Permission + Foreground-Service im AndroidManifest
- Capacitor Keyboard-Plugin installiert (`resize: "body"`)

### Phase 2 — Eigene Native-SPA (v1.6.0 → v1.6.2)
- `www/index.html` neu als eigenständige SPA mit 5 Screens
- Screens: Start (Favoriten), Login, Räume, Raum (Tische), Chat
- `hostname: "bar.shinpai.de"` in capacitor.config.json → Same-Origin-Trick
- `fetch()`-basiert, ruft `/api/...` am Server
- Features:
  - Server-Favoriten mit localStorage
  - Login (Name/PW) + Gast + ShinNexus-Login (+2FA)
  - Raum-Liste mit Tisch-Zähler
  - Tisch-Liste mit Thema, Energie, Valenz
  - Chat mit Text + Voice-Aufnahme (Browser-SpeechRecognition + MediaRecorder)
  - Audio-Player für eingehende Messages (TTS + File-URL)
  - Member-Liste mit Rang-Icons (🏆🥈🤠💀) + Owner-Crown + Nexus-Border
  - Prost-Button bei Bierdeckel-System-Messages
  - Profil-Editor via prompt() für TTS-Stimme
  - Owner-Voice-Markierung am Chat-Bubble

### Infrastruktur
- `.gitignore`: Node-Modules, Android-Build-Artefakte, `*.keystore`
- Workflow: Node 22, Java 21, gradlew chmod +x, Signing optional
- Release-Body in Kneipen-Feeling umgeschrieben

---

## 2. Was heute NICHT funktioniert hat

### Login-Fehler auf dem Handy
- Hasi konnte sich in der App v1.6.2 nicht einloggen
- "Login fehlgeschlagen" — vermutlich CORS oder Cookie-Problem

**Wahrscheinliche Ursache:**
- Capacitor `hostname: "bar.shinpai.de"` setzt Request-Host-Header, aber der **Browser-Origin** bleibt `https://localhost` oder `https://bar.shinpai.de` je nach Setup
- Server-CORS erlaubt nur `https://bar.shinpai.de` als Origin
- `credentials: 'include'` + CORS-Mismatch → Cookie wird nicht gesetzt → Login "erfolgreich" aber Session-Cookie fehlt → folgende Calls schlagen fehl
- Oder: Login selbst kommt nie durch weil CORS-Preflight abgelehnt

### Gast-Modus: keine Räume
- Nach "als Gast" zeigt Raum-Liste nichts
- `/api/raeume` gibt leeren Array oder Fehler — gleiche CORS-Ursache wahrscheinlich

### Tastatur-Overlay-Problem bleibt
- `interactive-widget=resizes-content` in viewport-meta → unterstützt nicht alles
- `android:windowSoftInputMode=adjustResize` in Manifest gesetzt
- `@capacitor/keyboard` Plugin mit `resize: "body"` installiert
- **Trotzdem:** Input-Feld wird von Tastatur überlagert
- Möglich: Das hostname-Trick + Body-Resize konfligieren, oder die CSS-Höhe des Chat-Screens (`height: 100%`) reagiert nicht auf Body-Resize

### Altes Handy (~10 Jahre)
- WebView veraltet, Chrome-Version <70
- Voice-Capture + SSE + TTS-Queue zu ressourcenhungrig
- Lags, keine Mic-Aufnahme

### Doppel-Session
- Shinpai am PC + gleicher Account am Handy = Nachrichten kommen doppelt
- Server hat keine Single-Session-Enforcement für Login-Accounts
- Erwartbar, aber verwirrend im Test

---

## 3. Architektur-Optionen für Multi-Server + CORS-Fix

Aktuell geht die App nur mit `bar.shinpai.de`. Für echtes Kneipen-Portal (beliebiger Server per Favoriten):

### Option A — Server-CORS erweitern
- `server.py` Middleware: `Access-Control-Allow-Origin: *` oder Allow-List mit Capacitor-Origins
- **Pro:** App bleibt browser-fetch-basiert, kein Plugin extra
- **Con:** server.py-Änderung nötig (Hasi's "explizit markieren" Regel)
- **Aufwand:** 1 Datei, ~20 Zeilen

### Option B — @capacitor/http Plugin (EMPFEHLUNG für Multi-Server)
- HTTP-Calls laufen durch native Android, nicht durch Browser
- Umgeht Browser-CORS komplett
- `fetch()` im Code durch `CapacitorHttp.request()` ersetzen
- **Pro:** Kein Server-Change, jeder Kneipe-Server kann angesprochen werden
- **Con:** Cookie-Handling anders — eigener Token-Manager nötig
- **Aufwand:** Plugin installieren + fetch-Layer umbauen, ~2-3 Stunden

### Option C — Dumb WebView (Rückfall)
- App öffnet direkt `bar.shinpai.de` im WebView (wie v1.5.5)
- Keine eigene UI, volle Kneipe-Erfahrung
- **Pro:** Alle Features sofort da (Spiele, Durchsage, Tresen)
- **Con:** Mobile-Layout-Probleme der original index.html bleiben
- **Für Single-Server-Szenario:** eigentlich der beste Weg

---

## 4. Offene Features (noch nicht in Native-SPA)

- [ ] **Bierdeckel-Wand** (`/api/bierdeckel?wall=1` — Liste aller Bierdeckel)
- [ ] **Spiele** (Themen starten, Play/Answer/Finish — `/api/play`, `/api/answer`, `/api/finish`)
- [ ] **Durchsage** (Owner-only, Multi-Channel-Send — ganze UI fehlt)
- [ ] **Tresen** (Raum-Chat + Mini-Durchsage — ganze UI fehlt)
- [ ] **Raum-Name-Voting** (`/api/name/vote`)
- [ ] **Eigenschaften-Voting** (`/api/eigenschaft/vote`, `/api/eigenschaft/add`)
- [ ] **Adult-Mode, Cheater-Vote** (Tisch-Optionen)
- [ ] **Themen-Wand & Einreichung** (`/api/themen`, `/api/thema/submit`)
- [ ] **MuMuPai-Player** (Hintergrundmusik)
- [ ] **Voice-Queue mit Auto-Play** (wie Original — Queue-Management + Overload-Banner)
- [ ] **Full Profile-Editor** (Avatar, Age, Password-Change — nicht nur TTS-Voice)
- [ ] **2FA Setup/Disable**
- [ ] **Register-Flow** (Owner-Setup, E-Mail-Verification)

---

## 5. Lessons Learned

1. **CORS beißt bei "eigenem Frontend + fremdem Server".** Immer vorher prüfen mit `curl -I OPTIONS ... -H "Origin: X"`.
2. **Capacitor hostname-Trick löst NICHT automatisch CORS.** Nur Host-Header, nicht Origin.
3. **Voice + SSE + WebView = leistungshungrig.** Alte Handys packen das nicht.
4. **Signing ist zwingend.** Unsigned APKs installiert Android 8+ ohne Fehlermeldung nicht.
5. **Keyboard-Overlay ist hartnäckig.** Weder viewport-meta noch windowSoftInputMode noch Capacitor-Plugin reichen allein. Braucht CSS-Layout das auf Body-Resize reagiert.
6. **Release-Iterationen sind normal.** Wir hatten 5 Retags bis Android-Build durchlief — jedes Mal neuer Fehler (Node-Version, Java-Version, gradlew-chmod, Signing-Mode). Das ist kein Drama, sondern das Leben bei einer neuen Pipeline.
7. **"Alles in einem Rutsch" ist verführerisch.** Die Lücke zwischen "WebView lädt Kneipe" und "eigene Native-App mit allen Features" ist enorm. Schichten zahlen sich aus: erst Connect-App (klein), dann Feature-Layer.
8. **pCloud vergisst Executable-Bits.** gradlew wird als nicht-executable gespeichert, Workflow muss `chmod +x` vorschieben.
9. **Double-Session ist Server-seitig ungeprüft.** Zwei Geräte = doppelte Events. Für saubere Tests nur ein Gerät drin.

---

## 6. Empfohlene Next Steps

**Kurzfristig (nächste Session):**
1. CORS-Test in der App: einfacher Request `GET /api/raeume` mit Dev-Tools-Log → confirm wo genau der Fehler liegt
2. Entscheiden: Option A (Server-CORS) vs Option B (Capacitor/HTTP) vs Option C (Dumb WebView für Single-Server)
3. Tastatur-Fix: `Keyboard.setResizeMode({mode: "native"})` statt "body" testen, plus `position: absolute; bottom: 0` für chat-input-row
4. Auf modernem Handy (nicht 10 Jahre alt) testen — vieles vom "Lag" liegt am Gerät

**Mittelfristig (Mai-Demo):**
- Wenn Option C: Hauptsache Mobile-CSS in original `index.html` verbessern, keine Rewrite nötig
- Wenn Option B: fetch-Layer auf CapacitorHttp umbauen, dann Features nachziehen
- Icons + Splash polishen, App-Name finalisieren
- README auf Deutsch

**Langfristig:**
- ShinNexus-App auf gleicher Basis (Template-Fabrik-Idee)
- Shidow-App analog
- Mac + iOS wenn Budget/Mac vorhanden

---

## 7. Release-Historie heute

| Tag | Inhalt |
|---|---|
| v1.5.5 (retagged) | Argon2id, Salzstreuer, setsid start.sh, Broadcast-Fix |
| v1.5.6 | Android-App via Capacitor + Kneipe-Feeling Release-Notes (mehrfach retagged für Build-Fixes) |
| v1.5.7 | App-Icon + Start-Screen mit Favoriten + Keyboard-Resize + 10 Gäste korrigiert |
| v1.5.8 | Capacitor-Keyboard-Plugin |
| v1.6.0 | Native SPA mit 5 Screens (ersetzt WebView-Wrapper) |
| v1.6.1 | Voice-Aufnahme + Member-Liste + Audio-Playback |
| v1.6.2 | Prost-Button + Profil-Editor + ShinNexus-Login + Layout-Fix |

---

*Dokument wird in der nächsten Session aktualisiert mit Plan-Entscheidungen.*
