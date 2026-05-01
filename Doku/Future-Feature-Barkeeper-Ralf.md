# 🍺 Future-Feature — Barkeeper Ralf als System-Sprecher

**Idee von:** Hasi, 2026-04-26 ~06:30 morgens, während Kneipe-App-Bug-Marathon
**Status:** Geparkt — wartet auf bessere TTS-Voices (Orpheus mit Tiefe und Bass, oder ähnlich)
**Aktuell stattdessen:** vorgebackene Klein-Roboter-Sounds via espeak-ng (assets/sounds/whisper-warnung.wav, whisper-bereit.wav) seit v0.6.7

---

## 🎯 Konzept

Statt steriler Klein-Roboter-Stimme spricht **Barkeeper Ralf** die System-Hinweise. Charm, In-World-Setting, und passt zur Kneipen-Atmosphäre. Voice-abhängige Anrede:

### Männliche Voice-Auswahl des Users

| Trigger | Spruch |
|---------|--------|
| Whisper überlastet | *"My Lord, eure Euphorie in allen Ehren, aber eine kleine Pause tut Not!"* |
| Wieder bereit | *"Dir Sir, die Kneipe wartet!"* |

### Weibliche Voice-Auswahl des Users

| Trigger | Spruch |
|---------|--------|
| Whisper überlastet | *"My Lady, euer Redefluss erdrückt die Gefolgschaft! Bitte einen kleinen Moment Ruhe!"* |
| Wieder bereit | *"Darling, wir lauschen weiter!"* |

---

## ✋ Voraussetzung

**Geht NICHT mit:** espeak-ng, edge-tts oder ähnlichen leichtgewichtigen TTS-Engines — die haben nicht die Tiefe und den Bass den ein "Barkeeper Ralf" braucht.

**Geht WENN:** Orpheus-Voice ist verfügbar (oder vergleichbare neuronale TTS mit männlicher tiefer Bariton-Stimme). Dann kann Ralf richtig in seinem In-World-Charakter sprechen.

---

## 🔧 Implementation-Plan (wenn die Stimmen da sind)

1. **Voice-Setting-Detection:** App muss wissen ob User männliche oder weibliche Voice-Auswahl hat. Aktuell zentral im Server — abrufbar via `/api/profile`.
2. **Vier Audio-Files vorgenerieren:**
   - `assets/sounds/ralf-warnung-mylord.wav`
   - `assets/sounds/ralf-bereit-mylord.wav`
   - `assets/sounds/ralf-warnung-mylady.wav`
   - `assets/sounds/ralf-bereit-mylady.wav`
3. **In `_playSystemSound()` Logik erweitern:** je nach Voice-Setting den passenden Pfad wählen.
4. **Aussprache testen:** "Dir Sir die Kneipe wartet" — Vorsicht, Whisper-Verschleppung beim Hörtest beachten.

---

## 🎭 Warum das genial ist

- **In-World-Setting:** Die Kneipe wird zur Bühne, Ralf ist Teil der Welt.
- **Charm statt Sterilität:** "My Lord/My Lady" gibt der App eine Identität.
- **Kontextueller Humor:** "Euer Redefluss erdrückt die Gefolgschaft" ist ein Augenzwinkern, kein Vorwurf.
- **Voice-Differenzierung:** Männliche/weibliche Form macht's persönlich, nicht generisch.
- **Trifft den Kneipen-Charm:** etwas was die aktuelle Robo-Sorgen-Stimme nicht kann.

---

## 📌 Erinnerung

**Wenn Orpheus oder ähnliche Voices verfügbar werden → diese Datei wieder ausgraben und einbauen.** Bis dahin reicht der Klein-Roboter-Backup aus v0.6.7 als Funktions-Garantie ohne Charm-Anspruch.

💛
