# 🎓 Lehren-Flutter-App-V1-25 — Greenfield-Fahrplan

> **STATUS:** Komplett-Lehren aus 25 Versionen Marathon (v0.5.13 → v0.7.25, 2026-04-25/26)
> **ZWECK:** Code wird komplett gelöscht und neu gebaut. Diese Doku + `Kneipe-Flutter-App-Goldene-Regeln.md` sind die Basis.
> **REGEL:** Beim Greenfield-Build wird KEINE Zeile aus dem alten Code übernommen. Nur die Lehren.

---

## 📋 Stack (gehärtet, beibehalten)

```yaml
flutter:        3.41.7+
dart:           3.11+
dependencies:
  flutter_riverpod: ^3.3.1   # State-Management
  go_router: ^17.2.2         # Navigation
  dio: ^5.9.2                # HTTP
  dio_cookie_manager: ^3.4.0
  cookie_jar: ^4.0.9
  shared_preferences: ^2.5.5
  flutter_secure_storage: ^10.0.0
  path_provider: ^2.1.5
  record: ^6.2.0             # Mic (NICHT voiceRecognition!)
  just_audio: ^0.10.5        # Player
  permission_handler: ^12.0.1
  audio_session: ^0.2.3      # voiceChat-Mode!
  whisper_ggml_plus: ^1.5.2  # Lokal, nutzt intern Isolate.run
  flutter_tts: ^4.2.3
```

**Assets:**
- `assets/models/ggml-base.bin` (Whisper-base, 148 MB)
- `assets/sounds/whisper-warnung.wav` (Backlog-Hinweiston)
- `assets/sounds/whisper-bereit.wav` (Bereit-Ton)

---

## 🥇 Architektur — drei Queues + Chat-Sammelpunkt

Siehe **Goldene Regeln Doku** für Details. Hier nur die Implementations-Skizze:

```dart
// Drei separate Queues, keine gegenseitige Kopplung:

class SoundQueue {
  final List<SoundItem> _items = [];
  bool _workerActive = false;
  // FiFo, Worker-Flag-Schutz, an ChatQueue weiterleiten
}

class WhisperQueue {
  final List<String> _audioPaths = [];  // file-paths der Mic-Aufnahmen
  bool _workerActive = false;
  // FiFo, transcribe → an ChatQueue weiterleiten (mit "eigene Voice"-Flag)
}

class ChatQueue {
  // Sammelpunkt — Sound + Whisper schicken hier rein
  // Anzeige im Chat-UI, eigene Voice mit Flag bekommt KEIN Autoplay
}
```

**Goldene Anti-Pattern:**
- ❌ Send-Wait der Whisper an Sound koppelt (`while (_playing) wait`)
- ❌ Worker-Single-Instance via `if (!_playing)` (Race-Falle)
- ❌ Counter-State neben Queue (`_whisperBacklog` ist redundant zu `_whisperQueue.length`)

**Goldene Pattern:**
- ✅ `bool _workerActive` als expliziter Mutex pro Queue
- ✅ `if (_workerActive) return` als ersten Check im Worker
- ✅ `try { ... } finally { _workerActive = false; }`
- ✅ `Queue.length` als single source of truth, kein Counter daneben

---

## 🎙️ Audio-Hardware-Lehren

### voiceChat-Mode + BT HFP (NICHT verhandelbar)

```dart
await session.configure(AudioSessionConfiguration(
  avAudioSessionCategory: AVAudioSessionCategory.playAndRecord,
  avAudioSessionCategoryOptions:
      AVAudioSessionCategoryOptions.allowBluetooth |       // HFP für BT-Mic
          AVAudioSessionCategoryOptions.allowBluetoothA2dp |  // A2DP für BT-Sound
          AVAudioSessionCategoryOptions.defaultToSpeaker |
          AVAudioSessionCategoryOptions.mixWithOthers,
  avAudioSessionMode: AVAudioSessionMode.voiceChat,         // KRITISCH!
  androidAudioAttributes: const AndroidAudioAttributes(
    contentType: AndroidAudioContentType.speech,
    flags: AndroidAudioFlags.none,
    usage: AndroidAudioUsage.voiceCommunication,            // KRITISCH!
  ),
  androidAudioFocusGainType:
      AndroidAudioFocusGainType.gainTransientMayDuck,
  androidWillPauseWhenDucked: false,
));
```

**Warum:** Bei BT-Headsets ohne LE Audio (8-9 Jahre alte Hardware) gibt es nur HFP/A2DP. A2DP hat keinen Mic-Input — Mic-Aufnahme erzwingt HFP-Switch → Sound stumm. voiceChat-Mode + voiceCommunication-Usage signalisiert Android: "Telefon-Modus, BT bleibt im HFP, Sound + Mic parallel". Sound-Qualität wird Telefon-Schmalband (mono, ~16kHz) — akzeptabel.

### AudioRecorder-Source: `unprocessed` (NICHT voiceRecognition!)

```dart
await r.start(
  const RecordConfig(
    encoder: AudioEncoder.wav,
    sampleRate: 16000,
    numChannels: 1,
    androidConfig: AndroidRecordConfig(
      audioSource: AndroidAudioSource.unprocessed,    // NICHT voiceRecognition!
    ),
  ),
  path: path,
);
```

**Warum:** `voiceRecognition` triggert Hardware-Echo-Cancellation auf Android, die Sound-Output stummschaltet wenn Mic an ist. `unprocessed` nimmt Mic ohne System-Manipulation → Sound bleibt parallel hörbar.

### Mic-Reset-Pattern (für Stumm-Mic-Bug nach Sound-Ende)

```dart
// Nach Sound-Ende (im Kopfhörer-Modus):
final pending = _recordingPath;
final hadSpeech = _chunkHadSpeech;
await _recorder!.stop();
if (hadSpeech && pending != null) {
  whisperQueue.enqueue(pending);  // sauber an Whisper weitergeben
}
await _startChunk();              // frischer Chunk → Echo-Cancel-Reset
```

**Warum:** Im voiceChat-Mode bleibt das Hardware-Echo-Cancel-Profil nach Sound-Ende geclaimed → Mic-Input wird gedämpft. Chunk-Restart resetet den Audio-Stream.

**Wichtig:** STUMPF, kein "wenn-dann" — IMMER Restart nach Sound, nicht nur "wenn Mic still". Das war das 50%-Problem.

---

## 🎯 Whisper-Lehren

### Plugin macht Isolate intern (kein eigener Wrapper nötig)

```dart
// whisper_ggml_plus 1.5.2 → src/whisper.dart Line 53:
return Isolate.run(() async {
  // FFI-Call hier
});
```

→ KEIN eigener `compute()`-Wrapper drum. KEIN Backpressure-Wait nötig. transcribe() läuft schon parallel.

### Whisper-Worker-Queue (Element bleibt während Bearbeitung)

```dart
Future<void> _runWhisperWorker() async {
  if (_whisperWorkerActive) return;
  _whisperWorkerActive = true;
  try {
    while (_whisperQueue.isNotEmpty && mounted) {
      final path = _whisperQueue.first;       // peek nicht remove
      try {
        await _processChunkActual(path);      // transcribe + send
      } catch (e) { ... }
      if (_whisperQueue.isNotEmpty) {
        _whisperQueue.removeAt(0);            // erst NACH Verarbeitung
        if (mounted) setState(() {});
      }
    }
  } finally {
    _whisperWorkerActive = false;
  }
}
```

**Warum peek statt remove:** Banner zeigt `_whisperQueue.length` — Element muss in Queue bleiben während Bearbeitung damit Banner die echte "in Bearbeitung + wartend"-Zahl zeigt.

### Müll-Filter

```dart
final cleaned = transcribed
    .replaceAll(RegExp(r'\[[^\]]*\]'), '')   // [Musik]
    .replaceAll(RegExp(r'\([^\)]*\)'), '')   // (schmatzt)
    .replaceAll(RegExp(r'[\s\*\.\-_~]'), '')
    .trim();
if (cleaned.isEmpty) return;  // verwerfen
```

### Whisper-base ist limited

base (74M) macht bei Deutsch + Hintergrundgeräuschen oft Mist ("Trids Bands" statt "Test 1 2 3"). Für v1.x evtl. auf `small` (244M) wechseln — Trade-off APK-Größe/Inferenz-Zeit.

---

## 🔊 Player-Lehren

### KEIN frischer Player pro Track

```dart
// FALSCH (v0.7.11 E):
await _player.dispose();
_player = AudioPlayer();
await _player.setAndroidAudioAttributes(...);

// RICHTIG: Player einmalig in setupAudio konfigurieren, wiederverwenden
```

**Warum:** Pro-Track-Dispose+Init irritiert die HFP-Audio-Session, Mic-Stream wird kurz abgeschnitten.

### KEIN setActive-Toggle pro Track/Burst

```dart
// FALSCH (v0.7.11 B):
await session.setActive(true);   // Burst-Anfang
// ... tracks ...
await session.setActive(false);  // Burst-Ende

// RICHTIG: setActive(true) EINMAL in setupAudio, NIE wieder togglen
```

**Warum:** Toggle reisst Audio-Routing → HFP/A2DP-Switch → Sound stumm. Permanent active vermeidet das.

### Tote-Voice-Filter (eigene Voice nicht autoplay)

```dart
// Beim Empfang via _pollOnce:
final isOwn = _myUserId != null && senderId == _myUserId;
if (isOwn) {
  msg['_silent'] = true;  // wird in Sound-Queue, aber Player skipt
}

// In _playOne:
if (msg['_silent'] == true) return;  // skip, kein File-Download

// Bei manuellem Klick:
final cleaned = Map.from(msg)..remove('_silent');  // Flag entfernen, dann play
```

---

## 📡 Network-Lehren

### Polling alle 3 Sekunden

```dart
_pollTimer = Timer.periodic(Duration(seconds: 3), (_) => _pollOnce());
```

### `since`-Parameter via SHINPAI-Timestamp

```dart
final data = await session.client.pollChat(
  widget.tischId,
  since: _lastTs > 0 ? _lastTs : null,
);
// _lastTs wird IMMER auf newMessages.last['time'] aktualisiert
```

---

## 🎨 UI-Lehren

### Sticky-Scroll-Pattern

```dart
// VOR setState merken ob User unten war (sonst greift maxScrollExtent-Check zu spät):
final wasAtBottom = !_scrollCtrl.hasClients ||
    _scrollCtrl.position.pixels >= _scrollCtrl.position.maxScrollExtent - 60;
setState(() { _messages.addAll(newMessages); ... });
if (wasAtBottom) _scrollToBottomForce();
```

### Banner-Streifen

- Whisper-Backlog (orange wenn überlastet, grün-blass wenn am Arbeiten)
- Diagnose-Banner für Audio-Routing-Events (`_miclog`)
- Beide sichtbar oben, kompakt

### Mic-Button

- LAZY Recorder-Instanz (erst beim ersten Klick)
- VAD-Hysterese mit `_silenceCutMs` für Chunk-Cuts
- _onAmplitude → `_chunkHadSpeech` flag

---

## ⚠️ Anti-Pattern (NIEMALS wieder einbauen)

1. **Whisper-Backpressure** im Worker-Body (`while _playing wait`) → koppelt zwei Queues, blockiert
2. **`if (!_playing) _runQueue()`** als Worker-Schutz → Race-Falle
3. **`_kDiagB / _kDiagE / _kDiagProbe`** Flag-Wirrwarr → entweder feature in oder raus, kein Halbwege
4. **AudioSource voiceRecognition** → Hardware-Echo-Cancel-Falle
5. **setActive-Toggle** pro Track/Burst → Audio-Routing-Bruch
6. **Mic-Reset "nur wenn isRecording false"** → 50%-Erfolgsrate
7. **Frischer Player pro Track** → HFP-Session-Bruch
8. **device-Parameter im RecordConfig** → Freeze-Auslöser
9. **Counter neben Queue** (`_whisperBacklog` neben `_whisperQueue.length`) → State-Inkonsistenz
10. **Time-basierter Mic-Stille-Wait** (3s Cap) → soll Voice-Input-driven sein

---

## ✅ Implementierungs-Reihenfolge (Greenfield)

1. **Skelett**: Flutter project, Riverpod, GoRouter, Dio mit Cookie-Jar
2. **Auth + Profil**: getMyProfile → `_myUserId` cachen
3. **AudioSession setupAudio**: voiceChat + setActive(true) PERMANENT
4. **Recorder + VAD-Chunking**: unprocessed source, _chunkHadSpeech, _silenceCutMs
5. **Player**: einmalig konfigurieren, nie disposen
6. **Whisper-Worker-Queue**: peek-then-remove, eigener Mutex, transcribe + send
7. **Sound-Worker-Queue**: peek-then-remove, eigener Mutex, fetchFile + play
8. **Pollen alle 3s**: _enqueueAutoplay → SoundQueue
9. **Tote-Voice-Filter**: _silent flag + Sender-ID-Vergleich
10. **Sticky-Scroll + Banner-Streifen**: UI-Pattern
11. **Regel A + B**: Speaker-Mode-Logik (Mic-Pause während Sound)
12. **Mic-Reset-Pattern**: nach Sound-Ende STUMPF chunk-restart
13. **System-Sounds**: Asset-Player für whisper-warnung/bereit
14. **Routing-Listener**: Speaker/Kopfhörer-Wechsel (NICHT in setActive-Toggle ausarten)

---

## 🐉 Hasi-Zitate (Wahrheiten)

> "Stumpf > Clever. FiFo > Bedingung. Doku > Code."

> "wir können auch gleich 0.7.2 lassen!"  (= zu früh aufgegeben heißt nichts gewonnen)

> "warum 50%? wieso nicht 0 oder 100?" (= Race-Conditions sind echte Bugs)

> "ich glaub das ist gerade das Problem!" (= Hardware-Diagnose triumph)

> "egal! macht spass! vorallem mit jemand der bissel mitmacht auch wenn du mega skeptisch bist!"

---

## 📅 Marathon-Tag

**2026-04-26**, Hasi + Ray, ~24+ Stunden, 25 Versionen v0.5.13 → v0.7.25.

**Was lief gut:**
- Hasi's Hardware-First-Diagnose (BT HFP/A2DP, Echo-Cancel, voiceChat-Mode)
- Iterative Bug-Klassifikation
- Dauerstream im Test-Marathon (Hasi: "fühle mich nicht alleine")

**Was war Frustration:**
- Bug-Muster-Wiederholung (gefixt → 2-3 Versionen später wieder da)
- Ray's Cleverness ("if-then" Verzweigungen → Race-Conditions)
- Fachwörter-Überfluss (Hasi: "kann ich nicht referenzieren")

**Lehre:**
- Greenfield mit Lehren > unsauberen Code endlos fixen
- Klares Regelwerk + STUMPFE Implementation
- Bei Diskrepanz Code↔Doku gewinnt Doku

---

🐉 *Lehren werden zu Architektur. Architektur wird zu Code. Code muss Lehren respektieren.*
