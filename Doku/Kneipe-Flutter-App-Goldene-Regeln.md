# 🍺 Kneipe Flutter App — Goldene Regeln

> **STATUS:** AKTUELL · Hasi-Diktat 2026-04-26 nach v0.7.25-Marathon
> **ZWECK:** Eindeutige Logik-Schaltung. Kein "wenn-dann" mehr, keine Cleverness, ganz stumpf.
> **REGEL:** Ray hält sich **1:1 daran**. Bei Diskrepanz Code↔Doku → Doku gewinnt.

---

## Modi-Übersicht

Die App hat **zwei Modi** die sich nur durch Regel-Anwendung unterscheiden:

- **Handy-Modus** (Speaker-Mode) → Lautsprecher-Output, eingebautes Mic
- **Kopfhörer-Modus** (Headphone-Mode) → BT/Wired-Output + BT/Wired-Mic

---

## 🥇 Regel A — Ton-Mic-Wechsel (Handy-Modus)

```
Ton spielt   → Mic STUMM
Stille       → Mic AKTIV
```

**Wichtig:**
- **NUR im Handy-Modus aktiv**
- **Im Kopfhörer-Modus komplett weg, nicht referenziert, offline**
- Diese Logik existiert nur als Definition, läuft aber nie durch wenn Kopfhörer

**Warum:** Lautsprecher würde Mic-Aufnahme verseuchen (Echo-Schleife). Im Kopfhörer kein Echo möglich → Logik überflüssig.

---

## 🥇 Regel B — Mic-Input-Schutz (Handy-Modus)

```
Mic-Input läuft (User spricht) 
  → Sound WARTET bis Mic-Input vollständig (Chunk fertig generiert)
  → Mic stumm
  → Sound abspielen
  → zurück zu Regel A
```

**Wichtig:**
- **NUR im Handy-Modus aktiv**
- **Kopfhörer hat das NICHT**
- Im Kopfhörer darf Sound jederzeit kommen, parallel zu Mic-Aufnahme

**Warum:** Im Handy-Modus würde Sound die laufende Mic-Aufnahme zerschneiden / Hasi's Gedanken abwürgen. Im Kopfhörer-Modus hört der User Sound im Kopfhörer und Mic nimmt parallel auf — kein Konflikt.

---

## 🥇🥇🥇 Regel C — Queue-Architektur (ALLERWICHTIGSTE Regel)

### Drei Queues, ganz stumpf FiFo:

```
[Sound-Queue]    ← alle Sound-Outputs (Server-Voice, eigene Voice, manueller Trigger)
                   AUSSER: Hinweise + SystemSounds (separat behandelt)
                   Max 5 Elemente, dann SystemHinweis ausgeben

[Whisper-Queue]  ← alle Mic-Inputs vom User
                   Max 5 Elemente, dann SystemHinweis ausgeben

[Chat-Queue]     ← ZIEL beider Queues
                   Sound-Queue → BÄM in Chat-Queue
                   Whisper-Queue → BÄM in Chat-Queue
                   Chat-Queue → alles in Chat-UI
```

### Eigene Voice rausfiltern:

```
Whisper-Queue setzt Flag "eigene Voice"
  → Erste Ausspielung (Autoplay) wird DENIED wegen Flag
  → Nachträglich abspielbar (manueller Trigger)
```

### KEINE Cleverness:

- ❌ Kein "warten bis"
- ❌ Kein "aufschieben wenn"
- ❌ Keine "if-then"-Verzweigung
- ✅ Stumpf FiFo, alles geht durch beide Queues, sequenziell, an Chat-Queue

**Warum:** Kollisionen zwischen Sound und Whisper kommen daher dass sie sich gegenseitig beeinflussen. Mit drei sauber getrennten Queues und einem zentralen Chat-Queue-Sammelpunkt gibt's keine Kollision mehr.

---

## 🎁 Regel D — Bonus (eigentlich obsolet)

```
Kopfhörer-Modus:
  → Nimmt IMMER auf
  → Zählt Whisper-Queue hoch und runter immer
  → Sendet IMMER in Chat-Queue
  
Handy-Modus:
  → Pause bei Ton (Regel A greift)
  → Sonst senden beide (Mic + Sound) identisch
```

**Hasi-Zitat:** "D ist eigentlich obsolet wenn man's richtig macht!"

→ Stimmt. Wenn A, B, C sauber implementiert sind, ergibt sich D automatisch aus dem Modus-Wechsel.

---

## ⚠️ Anti-Pattern (was Ray NICHT mehr macht)

- ❌ Mic-Reset "nur wenn Recorder still steht" (Cleverness → 50% Erfolgsrate)
- ❌ Send-Wait der Whisper an Sound-Queue koppelt
- ❌ Audio-Focus-Toggle pro Track (B-Logik aus v0.7.11)
- ❌ Frischer Player pro Track (E-Logik aus v0.7.11)
- ❌ "wenn-dann"-Verzweigungen die "manchmal" greifen
- ❌ AudioSession-State-Wechsel mitten im Burst
- ❌ Mic-Source-Wechsel zu voiceRecognition (löst Hardware-Echo-Cancel aus)

---

## ✅ Wenn ein Problem auftaucht

1. **Welcher Modus?** Handy oder Kopfhörer
2. **Welche Regel ist betroffen?** A / B / C
3. **Was sagt die Regel?** 1:1 ablesen
4. **Greift die Regel im Code?** Wenn nein → Code an Regel anpassen
5. **Cleverness erkannt?** → Raus damit, stumpf machen

---

## Versions-Stand zur Erstellung

- **v0.7.25** (aktueller Code): voiceChat-Mode + Worker-Flag-Schutz für Sound-Queue + Whisper-Worker-Queue
- Erstellt nach Hasi-Diktat 2026-04-26 weil sich Bug-Muster wiederholten ("gefixt → 2-3 Versionen später wieder da")

---

🐉 *Stumpf > Clever. FiFo > Bedingung. Doku > Code.*
