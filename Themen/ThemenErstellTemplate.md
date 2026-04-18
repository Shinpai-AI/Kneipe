# 🍺 Kneipen-Schlägerei — Themen-Erstell-Template
> Anleitung für Ray & Hasi zum Erstellen neuer Themen
> Stand: 2026-03-31

---

## ⚡ QUICK-START

**Ray, wenn Hasi sagt "Neues Kneipen-Thema erstellen":**
1. Lade das Kneipen-Modul (`RAY-MODULE-Kneipen-Schlaegerei.md`)
2. Lies dieses Template
3. Erstelle die .md Datei im Format unten
4. Submit über API mit dem Parser
5. Hasi genehmigt als Owner

---

## 📐 REGELN (HEILIG!)

### Sprache
- **IMMER ä ö ü ß** — NIEMALS ae oe ue ss!
- Alltagssprache, kein Akademiker-Deutsch
- Wie echte Menschen in einer Kneipe reden
- Fluchen erlaubt, KI-Gebubbel VERBOTEN
- Jeder Satz muss sich anfühlen wie GESPROCHEN, nicht geschrieben

### Struktur (EXAKT so!)
```
# Thema: [TITEL]
## Setting: [Wo in der Kneipe, wer ist da, was passiert — 1-2 Sätze, lebendig!]

---

### Schicht 1: [Eröffnung — Der Auslöser]
> [Situationstext — Erzähler-Perspektive, was passiert, wer sagt was]
> [Kann mehrere Zeilen sein, alles mit > Prefix]

- A: "[Konfrontative/direkte Antwort]" → Schicht 2A
- B: "[Diplomatische/zustimmende Antwort]" → Schicht 2B
- C: *[Schweigen/Handlung — immer kursiv mit Sternchen]* → Schicht 2C

---

### Schicht 2A: [Titel — nach Konfrontation]
> [Was passiert nachdem Spieler A gewählt hat]

- A: "[Antwort]" → Schicht 3X
- B: "[Antwort]" → Schicht 3Y
- C: *[Schweigen/Handlung]* → Schicht 3Y

### Schicht 2B: [Titel — nach Diplomatie]
> [Text]

- A: → Schicht 3X
- B: → Schicht 3Y
- C: → Schicht 3Z

### Schicht 2C: [Titel — nach Schweigen]
> [Text]

- A: → Schicht 3X
- B: → Schicht 3Y
- C: → Schicht 3Z

---

### Schicht 3X: [Konvergenz-Pfad 1]
> [Text — Pfade beginnen zusammenzulaufen]

- A: → Schicht 4-Kern
- B: → Schicht 4-Kern
- C: → Schicht 4-Kern

### Schicht 3Y: [Konvergenz-Pfad 2]
> [Text]

- A/B/C: → Schicht 4-Kern

### Schicht 3Z: [Konvergenz-Pfad 3]
> [Text]

- A/B/C: → Schicht 4-Kern

---

### Schicht 4-Kern: Die Erkenntnis
> (KONVERGIERT)
>
> [DIE philosophische Kernaussage des Themas]
> [2-4 Absätze, tiefgründig aber verständlich]
> [Kein Akademiker-Sprech, Kneipe-Philosophie!]

- A: "[Konfrontative Schluss-Antwort]" → Schicht 5-Feuer
- B: "[Fließende Schluss-Antwort]" → Schicht 5-Wasser
- C: *[Stille Schluss-Handlung]* → Schicht 5-Stein

---

### Schicht 5-Feuer: 🔥
> [Feuer-Ende: Konfrontation hat sich gelohnt. 2-3 Sätze.]

### Schicht 5-Wasser: 🌊
> [Wasser-Ende: Diplomatie hat verbunden. 2-3 Sätze.]

### Schicht 5-Stein: 🪨
> [Stein-Ende: Schweigen hat gesprochen. 2-3 Sätze.]

---

### Element-Auswertung:
- Viel A = 🔥 Feuer
- Viel B = 🌊 Wasser
- Viel C = 🪨 Stein (WENN Kontext passt) / 🌸 Mauerblümchen (WENN Ausweichen)
- Mix = 💨 Wind

### Kontext-Schweigen-Check:
- [Pro C-Antwort: Stein ✅ oder Mauerblümchen ⚠️ angeben]
```

### Antwort-Typen
| Typ | Bedeutung | Beispiel |
|-----|-----------|---------|
| **A** | Konfrontativ, direkt, ehrlich | "Das ist Bullshit und du weißt es." |
| **B** | Diplomatisch, zustimmend, fließend | "Da ist was dran, aber..." |
| **C** | Schweigen, Handlung, Beobachten | *Bier trinken. Abwarten.* |

### Flags (in eckigen Klammern AN der Antwort!)
| Flag | Bedeutung | Wann setzen |
|------|-----------|-------------|
| `[JA-SAGER]` | Zustimmung wo Konfrontation passt | B-Antwort die zu brav ist |
| `[MAUERBLÜMCHEN]` | Schweigen bei direkter Frage | C-Antwort wenn jemand DICH direkt fragt |
| `[JUKEBOX]` | Schweigen MIT Handlung | C-Antwort mit Aktion (Bier bestellen, Musik machen) |
| `[STAMMGAST]` | Stammgast-Event | NUR bei stammgast-fähigen Themen, an ALLEN C-Antworten |

### Stammgast-Themen
- Nur BEWUSST einsetzen (aktuell 5 von 30)
- ALLE C-Antworten bekommen `[STAMMGAST]`
- Spieler muss KOMPLETT mit C durchspielen für den Stammgast-Punkt
- Thema muss SINN MACHEN wenn man nur schweigt

---

## 🔧 SUBMIT-WORKFLOW

### 1. Datei erstellen
```
/media/shinpai/KI-Tools/Kneipen-Schlaegerei/Themen/[Titel-mit-Bindestrichen].md
```

### 2. Umlaute prüfen!
```bash
grep -c 'ae\|oe\|ue' Themen/[datei].md
# Muss 0 sein (außer in Wörtern wie "Feuer", "neue", "Bauer" etc.)
```

### 3. Über API submitten
```python
# API-Key: kneipe_[KEY] (siehe CLAUDE.md)
# Endpoint: POST /api/thema/submit
# Auth: Authorization: ApiKey [KEY]
# Body: { title, setting, layers: {}, endings: {}, stammgast: bool }
```

### 4. Owner genehmigt
- Hasi geht auf bar.shinpai.de → Themenbereich → Offene Themen
- Liest, prüft, genehmigt oder lehnt ab
- Bei Genehmigung: Thema ist SOFORT live!

---

## ❌ WAS NIE PASSIEREN DARF

- ae oe ue statt ä ö ü
- Generische Antworten ("Konfrontation" / "Diplomatie" / "Schweigen")
- Nur 1 Schicht ohne Tiefe
- KI-Gebubbel ("Es ist wichtig zu beachten dass...")
- Akademiker-Deutsch statt Kneipe-Sprache
- Thema ohne echte Geschichte/Charaktere
- Schicht 4-Kern ohne philosophische Tiefe
- Deploy ohne Hasi-Review!

---

## ✅ WAS JEDES THEMA BRAUCHT

- Echte Charaktere (Koch, Gärtnerin, Stammgast, Barkeeper...)
- Ein Setting das man RIECHEN kann
- Eine Frage die keine einfache Antwort hat
- Mindestens EINE Antwort die wehtut
- Mindestens EINE C-Antwort die STÄRKE zeigt (nicht Feigheit)
- Schicht 4-Kern die den Spieler zum Nachdenken bringt
- Enden die sich UNTERSCHIEDLICH anfühlen

---

*Seelenfick für die Kneipe. 🍺*
