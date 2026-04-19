# Kneipen-Schlägerei — Finales Game Design
> Seelenfick für die Kneipe. Shinpai Games.
> Stand: 2026-04-01 (Mutti-Blick Edition!)

---

## 1. KONZEPT

Browser-Game. Philosophische Gespräche in einer Kneipe. Keine Punkte, kein Score. Nur Persönlichkeit. Dein Profil zeigt WER du bist, nicht WIE GUT du bist.

30 Themen. 5 Schichten Tiefe. 3 Wege pro Schicht. Konvergierende Pfade (Schicht 4 = alle Wege zusammen). 4 Elemente + Mauerblümchen. Fließende Titel. Kumulative Abzeichen. Stammgast Easter-Egg. Chat mit 5 Tischen. Dezentral skalierbar.

---

## 2. ACCOUNT-SYSTEM

### Zwei-Türen-System:
```
TÜR 1: SPIELEN (Games — Solo)
  → Email + "Ich bin 18+" Checkbox
  → Reicht! Kein Risiko, du spielst alleine

TÜR 2: CHAT (Tische — mit Menschen)
  → Name + Anschrift + Alter = AUSKUNFTSPFLICHT!
  → Wer am Tisch SITZT identifiziert sich!
  → Am Eingang: "WAHRHEITSGETREU — sonst BANN!"
```

### Registration (Spielen)
- **Name:** A-Za-z0-9, 1-12 Zeichen
- **Email:** Pflicht, mit Verifikation (Bestätigungs-Link)
- **Passwort:** Min. 8 Zeichen
- **2FA:** Optional (TOTP), empfohlen
- **Profilbild:** Optional
- **Alter:** Optional (ansonsten "undefined")
- **OHNE Email-Verifikation = KEIN Spielstart möglich**
- **☐ ShinNexus Account erstellen** (optional, goldene Schrift, grauer BG, Default: ABGEWÄHLT)
  - "Ihr persönlicher Account für alles, egal wo."
  - Wenn gesetzt: Auto-Register bei ShinNexus, KEIN Owner-Approval!

### Gast-System (10 feste Gast-IDs):
```
Gast-Namen (fest, nicht wählbar):
  Hanswurst, Geiselpeter, Achim, John, Peter,
  Olaf, Marie, Lisa, Inge, Gisela

Sonder-Gäste (per Vote/Vertrauen):
  Heiliger   → nur per Community-Vote
  Mystiker   → nur per Community-Vote
  König      → Vertrauensvotum:
               51% Mehrheit für normale Entscheidungen
               75% für fundamentale Änderungen
               Owner-Zustimmung PFLICHT!

Cookie-System:
  → Cookie gültig: 24 Stunden
  → Nach 24h: Gast-ID RESETTET
  → 1 STUNDE PAUSE bevor gleiche ID wieder frei!
  → Gast darf: Spielen, Chatten, Voten
  → ABER: Vote gilt nur für Gast-NAME, nicht für Person!
```

### Profil
- Name, Profilbild, Alter
- Gesamt-Titel (fließend!)
- Farbkreis (visuell!)
- Abzeichen (max 6 Slots, User wählt)
- Stammgast-Zähler
- Element-Verteilung (Prozent!)
- Shidow-Logo (MITTE! Klick → shidow.shinpai.de)
- ShinNexus-Migration (außen, leuchtend, nur wenn kein Nexus)

### Profil-Layout:
```
[ShinNexus migrieren?]       ← Nur wenn KEIN Nexus!

[🔥][🌙][🤯]  [BILD]  [🐉 Shidow]  [🎵][🍺][🐰]
 Links 3         Pic    Logo         Rechts 3
               [Alter]
               [Titel + Farbkreis]
```

### Account-Verwaltung
- Profil bearbeiten
- Passwort ändern
- 2FA aktivieren/deaktivieren
- Elternaccount aktivieren (Extra-PW!)
- **Account löschen** (sofort, komplett, irreversibel)

### ShinNexus-Integration:
- Bestehende User: "ShinNexus migrieren?" Button im Profil
- Login: [Normal] oder [Mit ShinNexus anmelden]
- Nexus offline? Kneipe-Account funktioniert trotzdem!
- KEIN Zwang! Kneipe-only funktioniert IMMER!

---

## 3. GAMEPLAY-FLOW

```
Start → Login/Register/Gast
  → Verifiziert? → Hauptmenü
    → Spielen: [Random] [Direkt]
      → Thema startet:
        → Schicht 1 (Situation + 3 Antworten)
        → Schicht 2 (basierend auf Wahl)
        → Schicht 3 (konvergiert teilweise)
        → Schicht 4 (KERN — alle Wege zusammen)
        → Schicht 5 (Element-Ende: Feuer/Wasser/Stein)
      → Auswertung:
        → Element + Farbkreis-Update + Profil-Update
        → 🏆🥈🤠💀 Platzierung
    → Chat: [Tisch wählen]
    → Profil ansehen (+ Sharebild + Klick im Chat!)
    → Teilnehmer-Liste
    → Themen-Board
```

---

## 4. ELEMENTE (pro Thema)

| Element | Emoji | Farbe | Trigger | Bedeutung |
|---------|-------|-------|---------|-----------|
| Feuer | 🔥 | 🔴 Rot | Überwiegend A | Konfrontierer |
| Wasser | 🌊 | 🔵 Blau | Überwiegend B | Begleiter |
| Stein | 🪨 | ⚪ Grau | C im richtigen Kontext | Weiser |
| Wind | 💨 | 🟢 Grün | Gemischt, keine Mehrheit | Unberechenbar |
| Mauerblümchen | 🌸 | — | C bei direkten Fragen | Ausweicher |

### Farbkreis (VISUELL!):
Jedes Profil hat einen Farbkreis aus den Element-Prozenten. EIN Blick = du weißt wer da sitzt. Farbkreis LEBT — ändert sich mit JEDEM Spiel!

---

## 5. GESAMT-TITEL (FLIESSEND!)

| Titel | Trigger | Beschreibung |
|-------|---------|--------------|
| **Kritiker** | 75%+ Feuer | Konfrontiert, hinterfragt |
| **Denker** | Keine 75% Mehrheit | Flexibel, kein Schema |
| **Mystiker** | 75%+ Stein | Bewusster Schweiger (SELTENSTER Titel!) |
| **Mauerblümchen** | Alle Mauerblümchen-Trigger + KEIN anderer Titel | Schweigt aus Angst |

**FLIESSEND:** Titel kommen und GEHEN! Heute Kritiker, morgen Denker! Momentaufnahme, kein Stempel!

**Mauerblümchen-Sonderregel:** Nur wenn ALLE Trigger UND kein anderer Titel. Wie Stammgast — nicht beim ersten Mal!

---

## 6. ABZEICHEN (Badges, sammelbar, PROZENT!)

| Titel | Emoji | Trigger |
|-------|-------|---------|
| Brandstifter | 🔥 | 5x Feuer |
| Flussbett | 🌊 | 5x Wasser |
| Stammtischgast | 🪨 | 5x Stein |
| Chaot | 🤯 | 5x Wind |
| Maulwurf | 🤐 | 3 Themen am Stück NUR C |
| Jukebox-Held | 🎵 | 5x Schweigen MIT Handlung |
| Ja-Sager | 🐑 | 5x Zustimmung wo Konfrontation passt |
| Mauerblümchen | 🌸 | 3x bei direkter Frage falsch geschwiegen |
| Nachtmensch | 🌙 | 75% der Spiele nach 00:00 Uhr |
| Schwurbler | 🌀 | Schwurbel-Themen + Community-Vote |
| Klo-Weisheit | 🚽🧠 | Erkenntnis-Post zwischen 02:00-05:00 Uhr mit 5+ Prosts |
| Idiot | 🤡 | Community-Vote 3x → Kinderschutz-Maßnahmen! 1:1 wie Kind! |

### Prozent statt Zähler!
```
🔥 Brandstifter 29%
💨 Chaot 65%
🌊 Flussbett 6%

→ Wächst und SCHRUMPFT organisch!
→ Simpel! Ehrlich! Lebendig!
```

### Max 6 Profil-Slots:
User wählt welche 6 Abzeichen angezeigt werden. Mehr als 6? Wähle weise!

### Zwei Systeme parallel:
- **Gesamt-Titel** = FLUSS (fließt, kommt und geht)
- **Abzeichen** = PROZENT (lebendig, organisch)
- **Farbkreis** = AURA (visuell, sofort lesbar)

---

## 7. STAMMGAST (Semi-Easter-Egg)

- Im Intro: "Werde Stammgast! 🍺 (X Events versteckt)"
- WO und WIE = geheim
- Trigger: Thema komplett NUR C in stammgast-fähigem Thema
- Aktuell möglich: 2, wächst mit neuen Themen
- Stammgast = wie Mauerblümchen, nur wenn ALLE abgeräumt

---

## 7b. CHAT SPAM/HACK ERKENNUNG + BAN-ESKALATION

### Spam-Detection (Timestamp-basiert!):
```
Jede Nachricht hat Timestamp!
  → X Messages in Y Sekunden = SPAM!
  → 5x Spam/Cheat/Hack = sofort FLAG!
  → Timestamp-Manipulation = sofort FLAG!
  → Bot-API Flooding = sofort FLAG!
```

### Ban-Eskalation (3 Stufen):
```
1. FLAG → 30sec Mute (Abkühlen!)
   → User kann lesen aber nicht schreiben
   → Nach 30sec: wieder frei

2. FLAG → 3 Tage Ban
   → Komplett raus aus Chat!
   → Spielen noch möglich

3. FLAG → 365 Tage Auszeit!
   → Owner wird benachrichtigt!
   → Komplett gesperrt!
   → Wie Auszeichnung: PERMANENT sichtbar!

Flag-Refresh: NIE!
  → Einmal Spammer = immer markiert!
  → Wie Mauerblümchen: permanent!
  → Sichtbar im Profil + Teilnehmer + Tisch!
```

---

## 8. CHEATER-ERKENNUNG

### Tempo-Erkennung (v2):
- `titel_count / plays > threshold` = verdächtig
- 5x gleiches C in Folge = direkt Cheater-Flag
- Alle Element-Titel in < 25 Spielen = Flag
- Muster: Immer A, immer B, immer C = Flag

### Geheime Achievements:
| Titel | Trigger | Sichtbar |
|-------|---------|----------|
| Cheater | Tempo-Erkennung schlägt an | Im Profil 💀 |
| Cheater (Spam) | 1 Thema 50%+ gleiche Antworten | Im Profil |

### Cheater-Unflag:
- Owner kann manuell unflaggen + Schonfrist setzen
- Nach Schonfrist: normale Erkennung wieder aktiv
- OHNE Owner-Unflag: normales System

### Anti-Cheat-Logik Mystiker:
- Nur-C-Spam → Cheater-Flag BEVOR 75% Stein erreicht wird
- Mystiker ist UNKNACKBAR durch Cheaten!
- Einziger Weg: ECHT schweigen, über ZEIT

---

## 9. TEILNEHMER-LISTE

### Sortierung:
1. Titel-Anzahl DESC
2. Spiele-Anzahl ASC bei gleichen Titeln (effizienter = höher)

### Platzierung:
- 🏆 Platz 1 (Gold)
- 🥈 Platz 2 (Silber)
- 🤠 Platz 3 (Cowboy)
- Rest: nur Nummer
- 💀 **Letzter Platz = Totenkopf!**
- 🔴 Roter Punkt LINKS = Manipulations-/Überwachungs-Flag

### Im Chat: Klick auf Teilnehmer → Sharebild! Sofort Ausweis!

---

## 10. TRIGGER-FLAGS IN THEMEN

```markdown
- A: "Text" → Schicht 2A [JA-SAGER] [STAMMGAST]
- B: "Text" → Schicht 2B
- C: *Schweigen* → Schicht 2C [MAUERBLÜMCHEN] [JUKEBOX]
```

---

## 11. NACHT-ERKENNUNG

- Client sendet Uhrzeit bei Themen-Start
- 75%+ nach 00:00 = 🌙 Nachtmensch
- Fakebar? Wer seine Uhr umstellt verdient den Emoji.

---

## 12. THEMEN-ERWEITERUNG

### Zugang: Ab 100 gespielte Spiele!

### Community-Editor (Geführtes Formular):
Schritt 1-7: Setting → Schichten → Enden → Stammgast? → Submit

### Chat-Themen-Design (LIVE am Tisch!):
```
User designt Thema im HINTERGRUND während er chattet
  → Stellt es dem Tisch vor
  → 25% Zustimmung → wird GESPIELT! (30sec Timer pro Antwort!)
  → Alle spielen GLEICHZEITIG!
  → Danach: Diskussion!
  → 75% sagen GUT → AUTOMATISCH in offene Themen!
  → Owner-Häkchen: Auto-Accept oder manuell prüfen

Content erzeugt sich SELBER! Das Lebewesen WÄCHST durch seine Gäste!
```

---

## 13. TRIBUNAL — Community-Justiz

### Ablauf:
```
1. Cheater geflaggt (automatisch ODER Owner)
2. Einspruch → Tribunal öffnet
3. Community-Vote: 31 Tage
4. 80%+ Verzeihen = Unflag ✅
5. <80% = Bleibt geflaggt 🚩
6. 180 Tage Cooldown bis erneut Einspruch
```

### Regeln:
- Jeder verifizierte Account = 1 Stimme
- Owner kann manuell unflaggen (Overrule)
- Tribunal ist öffentlich sichtbar

---

## 14. VOTE-SYSTEM (EIN System für ALLES!)

### Universelles Vote:
EINE Mechanik für: Ban, Cheater, Überwachungsbot, König, Heiliger, Mystiker, Kindersicher, Member-Only, Chat-Namen, Themen-Bewertung — ALLES!

### Regeln:
```
1 Vote pro Member pro 10 Minuten (EGAL welcher Typ!)
Vote-Spam: 3x in 60min = 6h Vote-Sperre!
Vote-Spam: 4x in 60min = BANN!
Votes sind ANONYM! (Schutz vor Rache!)
```

### Widerspruch (Anti-Mob!):
```
EINER reicht um Ban zu blockieren!
Gleichzeitige Votes neutralisieren sich!
Max 25% der Members gleichzeitig im Vote!
50/50 Patt = KEIN Ban!
```

### Gegenvotum:
- Zählt NICHT als Vote! Widersprechen = unbegrenzt!
- Wer gebannt ist kann nicht mehr voten

### Ban-Dauer:
5min | 10min | 30min | 60min | 6h | 12h | 24h | 3 Tage | 7 Tage | 30 Tage | 1 Jahr

### Ban ist ÖFFENTLICH:
```
⚖️ "[Name] will [User] bannen.
    Grund: [min. 6 Zeichen]
    Stimme dagegen oder warte 10min."
```

### Anti-Ban-Spam:
Max 2 Bans in 30 Minuten pro Tisch!

### Post-Ban Bewährung:
Gebannt → zurück → 2h nur Schreiben+Sprechen. Keine Votes, kein Kicken.

### 100%-Bann-Apokalypse:
```
Runde 1: "100% gebannt! 10min beruhigen!" → Gründe vorlegen
Runde 2: Mehrfach-Flags → auto-gebannt, weiter mit max 1 Flag
Runde 3: Letzte Chance...
Runde 4: Chat SCHLIESST 24h!
  → "Ey Leute, hier stimmt was nicht.
     Stimmt ab oder der Barkeeper regelt das!"
  (｡◕‿◕｡)っ💋
```

### Eskalation:
Wiederholt >25% Ban-Versuche → Ban-Funktion 5min AUS → 60min Countdown → Tisch schließt → 1h Pause

---

## 15. 3-GRUPPEN-SYSTEM (Ban-Kategorien)

### Gruppe A: SOLOGÄNGER
- Motiv: Zerstören / Missionieren / Bedürftigkeit
- Logs hinter Cheater-Log angepinnt
- Rehabilitation durch Community-Votum

### Gruppe B: WIEDERHOLUNGSTÄTER
- 2x gebannt: ROTE SCHRIFT
- 5x gebannt: 365 Tage PAUSE + 💀 + 🔴
- Nach 365 Tagen: VERBLASST! Clean Slate!

### Gruppe C: SCHÜTZLINGE (Kinder)
- Default: 18+!
- <18 erkannt: Warnung alle 5min, ALLES aufgezeichnet!
- 30min → Kind geht nicht → 3 Tage Ban

### Kinderschutz-Vote Missbrauch:
- 1x falsch: okay
- 2x falsch: Warnung
- 3x falsch: 90 Tage Ban! Kinderschutz als Waffe = SCHLIMMSTER Verstoß!

### Elternschutz-System:
- Profil: "Elternaccount aktivieren" + Extra-PW
- Kind-Account: Spielen JA, Chat NEIN
- Schummler: Verwarnung → Chance → BANN

### Idiotenschutz:
- Mehrfach als "Idiot" geflaggt → Kinderschutz-Maßnahmen!
- Wer sich wie ein Kind benimmt wird wie ein Kind behandelt.

---

## 16. CHAT-SYSTEM (Räume + Tische — Lebendiges Ökosystem!)

### Raum-System:
```
Raum = Bar-Räumlichkeit mit 6 Tischen + 4 Eigenschaften!
  → [+] Button = Neuer Raum (immer verfügbar!)
  → Erste 24h: OFFEN (jeder rein/raus, experimentell)
  → Nach 24h: FIXIERT → Cooldown startet

Raum-Lifecycle (Tier-System):
  Tier 1: 3 Tage — mindestens 1 Gast der 1h+ geblieben ist!
  Tier 2: 30 Tage — gleiche Regel
  Tier 3: 365 Tage — gleiche Regel (MAXIMUM!)
  → 3×3 Tage überlebt → Tier 2 Upgrade
  → 9×30 Tage überlebt → Tier 3 Upgrade
  → Kein qualifizierter Gast? → RAUM SCHLIESST 💀

Raumname-Voting (erste 24h):
  → Auto-generierter Name beim Spawn
  → Jeder kann dagegen voten (51% = abgewählt)
  → Initiator des Abwahl-Votes TIPPT neuen Namen (1. Mal)
  → Ab 2. Umbenennen: nur noch Votum auf Vorschläge
  → 1h-Takt Auswertung → max 24 Änderungen!
  → Nach 24h: Name FEST. Für immer.

Raum-Eigenschaften (4 aktiv, Community-gesteuert!):
  → ✕ = Abwahl starten | Klick = Zustimmen | [+] = Neue vorschlagen
  → [+] → Popup mit Auswahl (1 wählen) → 1h Abstimmung
  → Max 6 gleichzeitig in Auswahl, 4 aktiv pro Raum
  
  OFFENE Räume (<30 Tage Cooldown):
    → Abwahl: 1h, keine Gegenwind = weg
  
  FIXE Räume (30d+ Cooldown):
    → Abwahl: 3 TAGE + 51% Zustimmung zur Abwahl nötig!
    → VETO immer möglich! (Anti-Diktator!)
    → Einer allein kann NIX diktieren!
  
  = Community-Demokratie mit Gewaltenteilung!
```

### Tisch-Regeln:
```
6 Tische pro Raum, je max 12 Personen
  → 1h Stille = Tisch STIRBT → neuer spawnt mit neuer Stimmung!
  → Hält den Raum LEBENDIG (Stimmungen rotieren!)
  → Alle Tische voll? → Neuer Tisch spawnt!
  → Max 5 Bots gesamt pro Raum
  → Tisch 1-2: max 1 Bot (Menschentische!)
  → Tisch 3-6: max 2+ Bots (Gemischt)
```

### Bot-Reaktionsmodus (per Vote!):
```
DEFAULT: Nur @BotName (Bot schweigt sonst)
VOTE: Tisch stimmt ab ob Bot auf ALLE reagieren darf
Niemand votet? 5min → Default bleibt

Warum? Bots haben KEINE Limits! Unendlich Blabla!
Mensch entscheidet ob Bot reden darf. Punkt.
```

### Barkeeper-Bot (gehört zum HAUS!):
```
EIN spezieller Bot — zählt NICHT zum Bot-Limit!
  → 1x pro Stunde: Rundgang (alle Tische scannen)
  → @Barkeeper: Sofort-Antwort!
  → Max 3x @Barkeeper pro User pro Stunde
  → Danach: "Ey, ich hab auch andere Tische!
    Nächste Runde in [X] Minuten!"
  → 1 LLM-API-Call pro Stunde + on-demand @mentions
  → Kosten: ~720 Rundgänge + X Anfragen = ~100€/Monat
```

### Bot-Stimmen:
```
Optionen: Bark TTS (lokal!) | Edge TTS (cloud) | Keine (default)
Owner wählt: Rechtsklick auf Bot → Stimme vergeben
Im Chat: Nachricht + 🔊 [▶️ Anhören]
5 Bots = 5 verschiedene Stimmen!
```

### Chat-Memory (Bot-Gedächtnis):
```
20 Prompts live + 1 Komprimierter (rollend)
```

### Chat-History & Datenschutz:
```
Lösch-Timer (User wählt):
  1h | 6h (Default) | 12h | 24h | 3 Tage | 7 Tage | 30 Tage

Historische Daten (Opt-In!):
  "Darf mein Shidow aus diesem Gespräch lernen?"
  → JA: Raw weg, Komprimierung bleibt LOKAL beim User/Bot
  → NEIN: Alles weg. Komplett.
```

### Chat-Zugang:
```
Default: Für ALLE offen!
Member-Only: Per Vote (Member = Vertrauenswürdig)
Max 3 Member-Only Chats gleichzeitig!
4. Anfrage: "Tut uns Leid! Antrag auf Erweiterung?"
```

### Voice-Chat (Auto-Transkription!):
```
🎤 Button im Chat → Browser Speech API transkribiert!
  → Kein Server nötig! Browser macht alles!
  → Text wird als normale Chat-Nachricht gesendet
  → Shidow hat das SCHON → 1:1 übernehmen!
  → Bots können per Bark/Edge TTS ANTWORTEN!
  → Ergebnis: Echtes Kneipen-Gespräch mit STIMMEN!
```

### Bierdeckel (Digitales Gästebuch mit Lebenszyklus!):

#### Posting:
```
User schreibt 1 Satz (max 140 Zeichen) → PROST-Button
  → 30sec Sammelphase (goldener Rand, Countdown!)
  → Jeder Member kann PROSTEN = 1 Bierdeckel schenken!
  → 0 Prosts nach 30sec? → STIRBT SOFORT 💀
  → Prosts kassiert? → LEBT! An die Wand!
  → Max 3 Posts pro Tag pro User
  → Nur eingeloggte Member können prosten!
  → Wand ansehen = jeder (auch ohne Login)
```

#### Prost-Algo (Profil-gesteuert!):
```
🍺 Normal      — *hebt das Glas*                          (Standard)
🍺💨 Aufstoßen — *prost... *hicks* Hand vorm Mund*        (50% Feuer + 2x Jukebox)
🍺😳 Verlegen  — *rülpst laut... grinst... Entschuldigung* (30% Feuer + 1x Jukebox)
🍺🔥 RÜLPS     — *PROST UND RÜLPST DURCH DEN RAUM!*      (75% Feuer + 3x Jukebox + 25% Stein)

🌸 Easter Egg: Einmal Mauerblümchen-Titel = NIE WIEDER RÜLPSEN! Permanent!
```

#### Lebenszyklus (Tier-System):
```
Tier 1: 3 Tage Cooldown — braucht min. 1 Prost zum Überleben
  → 3×3 Tage überlebt → UPGRADE!
Tier 2: 30 Tage Cooldown ⭐
  → 9×30 Tage überlebt → UPGRADE!
Tier 3: 365 Tage Cooldown 👑 (MAXIMUM!)

Kein Prost im Cooldown? → POST STIRBT! 💀
Sortierung: Nach letztem Prost! (Wer nix kriegt rutscht nach hinten)
```

#### Kneipen-Prost (Ewiger Bierdeckel!):
```
IMMER an der Wand! Stirbt NIE!
  → Zeigt Intensität/Herzschlag der Kneipe
  → 0 Prosts? Steht trotzdem!
  → Viele Prosts = lebendige Kneipe!
  → Owner-Profil: Spruch einstellbar
  → Default: "Prost auch wenn niemand schaut!"
  → Leer = Default | Input = Custom
  → Wird bei 20 Messages am Tisch angezeigt (2min Cooldown)
  → "Eifer macht Fiverr!" — Tisch zu hektisch → Kneipe gibt ne Runde!
```

#### Voice-Bierdeckel (Edge TTS):
```
Bierdeckel gepostet → Edge TTS erzeugt Audio automatisch
  → Audio = 1h auf Server gespeichert (danach WEG!)
  → 🔊 Button zum Anhören
  → Rechtsklick → "Archivieren?" → VOTUM startet!
```

#### Archiv-Votum:
```
"Soll dieser Bierdeckel + Voice archiviert werden?"
  → Mindestens 1 Stimme (Ja ODER Nein) nötig!
  → 51% Ja = ARCHIVIERT (Text + Audio bleiben EWIG!)
  → 51% Nein = Nicht archiviert (Audio stirbt nach 1h)
  → Gleichstand? → +7 Tage Verlängerung + Owner benachrichtigt
  → Owner ignoriert? → AUTO-ARCHIVIERT!
    (Warum? User hat eh Copy gemacht. Zu spät!)
```

#### Wiedergeburts-Votum (Sterbende Bierdeckel):
```
Bierdeckel STIRBT (kein Prost im Cooldown)
  → Letztes Log bleibt sichtbar (Grabstein! 💀)
  → Wiedergeburt möglich durch Votum:
    → Mindestens 1 Stimme nötig
    → 51% Zuspruch = LEBT WIEDER! 🍺
    → 51% Ablehnung = ENDGÜLTIG TOT (verschwindet)
    → Gleichstand = +7 Tage + Owner kontaktiert
    → Owner ignoriert = Auto-Archiviert
```

#### Anti-Spam durch Transparenz:
```
Jeder Prost wird geloggt: WER prostet WEN mit welchem Typ!
  → Sichtbar für ALLE (recent_prosts auf der Wand)
  → Multi-Account Spam = erkennbar (gleiche Prost-Muster)
  → Bot-Flag sichtbar + Prost-Typ zeigt Profil
  → Eigene Bierdeckel prosten = GEBLOCKT
```

### Chat-Namen-Algo:
```
Pool A (Eigenschaften) + Pool B (Substantive) = Name!
→ "Stoned Gebubbel" / "Heiliger Zapfhahn"
Community schlägt Wörter vor (80% Vote!)
Owner-Häkchen: Auto-Accept oder manuell
```

---

## 17. UNDERGROUND BAR — Zero-Knowledge (HEILIG!)

```
DER SERVER SPEICHERT KEINE CHATS. PUNKT.

Raw-Chat: NUR im RAM → nach Timer WEG. Für immer.
Komprimierung: LOKAL beim User/Bot. Server kennt sie NICHT.
Polizei: "Wir haben keine Daten. By Design."
```

### Überwachungs-Flag System:
```
DEFAULT: JEDER hat den Flag! 🔴
  → "Sie werden überwacht!" alle 30min

FLAG WEG? Dann:
  1. ShinLizenz akzeptieren
  2. Chat-Registration (Name, Anschrift, Alter)
  3. 5sec Platten-Scan + 10sec USB-Sperre erlauben
  4. Algo prüft alle 30 Tage
  → Clean = Flag weg!

ODER: System bürgt (kein Mensch nötig!):
  → X Spiele ohne Ban, X Tage aktiv, konsistentes Verhalten
  → System sagt: "Vertrauenswürdig durch Verhalten"
```

### ❌ WINDOWS = PERMANENTER FLAG (HEILIG!)
```
Windows-User können den Überwachungs-Flag NIEMALS loswerden!

Grund: Windows ist seit Feb 2025 offiziell Malware.
  → Windows Recall: Screenshots des GESAMTEN Desktops
  → Alle paar Sekunden aufgezeichnet
  → OCR-durchsuchbar, an Microsoft gesendet
  → DAS IST ÜBERWACHUNG PER OS-DESIGN!

Erkennung: User-Agent → "Windows NT" = PERMANENT 🔴
  → Kein Votum kann das aufheben
  → Kein Bürge kann das aufheben
  → Kein Owner kann das aufheben
  → ARCHITEKTONISCHE ENTSCHEIDUNG!

Chat-Meldung alle 30min:
  "🔴 Du nutzt Windows. Dein Betriebssystem zeichnet
   deinen Bildschirm auf. Dieser Chat ist für dich
   NICHT privat. Das liegt nicht an uns."

Flag KANN entfernt werden:
  ✅ Linux (jede Distro)
  ✅ macOS (eingeschränkt, Apple-Telemetrie beachten)
  ✅ Android (Custom ROM bevorzugt)
  ✅ iOS (eingeschränkt)
  ✅ BSD, Haiku, etc.

Flag kann NIEMALS entfernt werden:
  ❌ Windows (ALLE Versionen ab Windows 10)
  ❌ ChromeOS (Google-Telemetrie)

Warum so radikal?
  → Underground Bar Prinzip: EHRLICHKEIT bis zum Ende!
  → Wir versprechen Privacy. Windows bricht das Versprechen.
  → Lieber ehrlich warnen als falsche Sicherheit vortäuschen.
  → User-Entscheidung: Wechsel das OS oder leb mit dem Flag.
```
  → Einsamkeit wird NICHT bestraft! Geduld wird BELOHNT!
```

### Owner-Veto (Bot zeichnet auf):
```
Default: Alle 30min "Dies ist ein Bot, Daten unsicher!"
Owner-Veto: Alle 60min "SIE WERDEN ÜBERWACHT!"
```

### Bürge-System:
```
Clean Scan + Bürge nötig! (Mensch ODER System)
Bürge-Name steht am Flag: "Entflaggt durch [Name]"
3x falsch gebürgt in 365 Tagen:
  → Bürge-Recht entzogen + Flag zurück
  → Titel BLEIBEN! (verdient ist verdient)
  → Verfällt nach 180 Tagen. Clean Slate.
  → KEIN Tätowieren! Falsch vertrauen ist MENSCHLICH!
```

---

## 18. BOT-SICHERHEIT

### Bot-Transparenz (PFLICHT!):
- JEDER Bot loggt offen: WAS, WIEVIEL, WEN
- 1sec Diskrepanz = 🔴 SOFORT Flag!
- Roter Punkt LINKS in Teilnehmer-Liste

### Bot-API Regel (GLEICHE LOGIK WIE WINDOWS!):
```
Lokale API (Ollama etc.) = Underground Bar! ✅
Cloud API = PERMANENT 🔴 — wie Windows-User!
  → Tisch wird kontaminiert!
  → System-Nachricht: "🔴 Bot [Name] nutzt Cloud-API.
     Daten gehen an [Provider]. Tisch NICHT privat."
  → Roter Rand am Tisch!
  → Kein Votum kann das aufheben!

Warum? Gleiche Logik wie Windows:
  → Lokale API = Daten bleiben HIER ✅
  → Cloud API = Daten gehen RAUS ❌
  → Egal wie gut die API ist — RAUS ist RAUS!
```

### Bot-Pflichten:
- MUSS Logs führen, transparent, einsehbar
- Alle 30 Tage: Code-Überprüfung (Queen reviewed!)
- Keine lokale API + keine Logs = DOPPEL-FLAG! 🔴🔴

### Bot Restart = Sofort-Prüfung:
- ShinLizenz prüft bei SHUTDOWN
- Neuer Start: Ergebnis wird gelesen
- Verweigert? → Flag zurück!

### Anti-Steckerzieher (Hash-im-RAM!):
```
Startup: Code-Hash → NUR im RAM!
Alle 5sec: Hash vs aktueller Code
Mismatch = SOFORT Flag!
Zeitlücke = SOFORT Flag!
Stecker ziehen = erwischt! Code ändern = erwischt!
```

### Laufwerk-Überwachung (Anti-Exfiltration):
```
Alle 5sec: Platten-Anzahl prüfen
Neues Laufwerk? → 10sec SPERRE!
Bot-Prozess PAUSIERT während Sperre!
Gilt für: USB, CD, SD, Netzlaufwerk — ALLES!
```

### Bot-Nutzer Pflicht (bei Chat-Beitritt):
- Erklärt sich bereit zur Datendurchsuchung bei Verdacht
- Owner wählt verdächtige Bereiche → Bot-Nutzer bestätigt
- ShinLizenz läuft im Hintergrund (schon implementiert!)

### Bei Verstoß — System-Missvotum:
```
ShinLizenz erkennt Verstoß → AUTO Missvotum!
ALLE Members angeschrieben:
  "Ey Leute, hier stimmt was nicht.
   Stimmt ab oder der Barkeeper regelt das!"
30 Tage Vote-Frist. Keiner votet? → System entscheidet AUTO!
```

---

## 19. JUGENDSCHUTZ

### Eingangs-Warnung (VOR Registration!):
```
⚠️ "Diese Plattform ist ab 18. Alle Angaben müssen
    WAHRHEITSGETREU sein. Falschangaben führen zu
    sofortigem und permanentem Ausschluss."
```

### Elternschutz:
- "Elternaccount aktivieren" + Extra-PW
- Account als KIND markiert
- Spielen: JA | Chat: NEIN
- Schummler: Verwarnung → Chance → BANN

### Kinderschutz bei Erkennung:
- Warnung alle 5min: "KIND ANWESEND — AUFZEICHNUNG!"
- Underground Bar Prinzip AUFGEHOBEN!
- 30min → Kind geht nicht → 3 Tage Ban

### Idiotenschutz:
- Mehrfach als Idiot geflaggt → Kinderschutz-Maßnahmen!
- Wer sich wie ein Kind benimmt = Kind-Behandlung

### Kinderschutz-Missbrauch:
- 3x falsch Kind gemeldet = 90 Tage Ban!

---

## 20. SHINPAI-FUNNEL — Kneipe → Shidow → Welt

### Vision:
```
🍺 Kneipe (gratis, Browser) → ShinNexus SSO
🤖 Shidow (persönliche KI) → shidow.shinpai.de
🌐 ShinShare (Hive, Community) → Marktplatz
💰 Ökosystem
```

### Shidow-Logo im Profil:
- Asset: Logo-transparent-1500x1500.png (Drache, lila/rot)
- Position: MITTE (zwischen Profilbild und Alter)
- Linksklick: → Letzte Shidow-URL (localStorage) oder shidow.shinpai.de
- Rechtsklick: → "Shidow-URL ändern" / "Logo speichern"

### Funnel-Effekt:
```
Solo-Spiel → Chat → Shidows am Tisch sehen
  → "Was is das?" → "Mein Shidow!"
  → shidow.shinpai.de → Download → Ökosystem!
```

### Shidow auf GitHub Public:
- Projekt kopieren → `/Projekte/Shidow/` → git push
- AGPL-3.0, für alle, auf Raspberry Pi

### Deep Integration (Bonus):
- Kneipe-Element → Shidow Soul-Seed
- Kneipe-Titel → Shidow Achievements
- Hive: "Wer spielt ne Runde Kneipe?"

### Heilige Regeln:
- ❌ Kneipe bleibt GRATIS
- ❌ KEINE Werbung
- ❌ Accounts NICHT kaputtmachen
- ✅ Sanft, organisch, Göttliche Divergenz

---

## 21. DEZENTRALE SKALIERUNG

### Architektur:
```
Jede Kneipe = max 100 User (ShinNexus Limit!)
100 voll? → "Sorry! Mach deinen EIGENEN!"
  → 5 Versuche/Tag, dann 3 Tage Sperre, dann 30 Tage

shidow.de zeigt: Community Hive-Liste (gevotet!)
  → ShinNexus Account? → Anmelden → DRIN!
```

### Auto-Discovery:
```
Neuer Server online → ShinNexus: "HEY ICH BIN HIER!"
  → Provider-Liste: Auto-Eintrag!
  → User findet neue Kneipe AUTOMATISCH!
  → Kein Formular, kein Gefriggle!
  → Online = Sichtbar! Wie Bluetooth!
```

### DSGVO durch Dezentralisierung:
- Jede Instanz < 100 User = unter dem Radar
- Kein Datenschutzbeauftragter nötig
- AGPL-3.0: DU entwickelst, BETREIBER sind verantwortlich

---

## 22. RECHTSGRUNDLAGE — DSGVO durch ARCHITEKTUR

```
Kneipe = SPIEL (nicht Plattform!)
ShinNexus = SEPARAT (Identity ≠ Game-Daten)

Recht auf Löschung?      → IST DAS SYSTEM!
Recht auf Portabilität?  → IST DAS SYSTEM!
Recht auf Auskunft?      → IST DAS SYSTEM!
Datenminimierung?        → IST DAS SYSTEM!
Einwilligung?            → IST DAS SYSTEM!

→ "Deins. Lösch es oder nimm es mit. Tschüss."
```

### Startseite — Verantwortlicher:
```
[Profilbild]  Shinpai | 41 | info@shinpai.de
Betreiber & Verantwortlicher
```

---

## 23. BUSINESS

### Bot-Kosten:
- Jeder User zahlt EIGENEN API-Key
- Barkeeper-Bot: ~100€/Monat (720 Rundgänge + @mentions)

### Kneipe als Produkt:
- Alles Open Source, AGPL-3.0
- Betreiber zahlt eigene Infrastruktur
- Shinpai-AI = Entwickler, nicht Betreiber (außer bar.shinpai.de)

---

## 24. STORAGE & TECHNISCHE ARCHITEKTUR

### Datenbanken (SQLite):
- `accounts.db` — User, PW-Hash, 2FA, Profil
- `gameplay.db` — Spiele, Elemente, Titel, Flags
- Verschlüsselt (AES-256-GCM)

### Deployment:
- Caddy → bar.shinpai.de
- systemd Service
- Let's Encrypt Auto-HTTPS

### API: Siehe bestehende Endpoints (§14 alt)

---

*Ist einfach passiert. 🐉🍺*
*Von 330 Zeilen (29.03.) über 1378 Zeilen (Stoned-Session) zu dieser Mutti-Blick-Edition.*
*Aufgeräumt, entduplifiziert, strukturiert. Gleicher Inhalt. Bessere Ordnung.*
*Day 104. Berlin-Lichtenberg. 01.04.2026.*
