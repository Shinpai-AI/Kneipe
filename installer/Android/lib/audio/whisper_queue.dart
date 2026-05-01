import 'dart:async';
import 'dart:io';
import 'package:flutter/services.dart';
import 'package:just_audio/just_audio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:record/record.dart';
import 'package:whisper_ggml_plus/whisper_ggml_plus.dart';

import '../api/kneipe_client.dart';

/// WhisperQueue — FiFo-Queue für Mic-Aufnahmen (Voice-Input des Users).
///
/// **Goldene Regel C:** Mic-Inputs landen hier. Worker arbeitet sequenziell:
/// transcribe → sendVoice → an Server. Plugin nutzt intern Isolate.run,
/// daher kein eigener compute()-Wrapper nötig.
///
/// **VAD-Chunking:** _onAmplitude erkennt Speech-Onset/Stille, schneidet
/// Chunks bei `_silenceCutMs` Stille ab.
///
/// **NIE WIEDER:**
/// - Send-Wait der Whisper an Sound koppelt (`while _playing wait`)
/// - voiceRecognition als AudioSource (Hardware-Echo-Cancel-Falle)
/// - device-Parameter im RecordConfig (Freeze-Auslöser)
/// - Counter neben Queue (`_whisperBacklog` redundant zu length)
class WhisperQueue {
  static const _onThresholdSpeakerDb = -20.0;
  static const _offThresholdSpeakerDb = -25.0;
  // v0.8.4: Kopfhörer-Mic ist hardware-bedingt sehr leise (BT-HFP).
  // Threshold runter damit auch leise Speech erkannt wird.
  static const _onThresholdHeadphoneDb = -35.0;
  static const _offThresholdHeadphoneDb = -40.0;
  static const _silenceCutMs = 800;
  static const int maxBacklog = 5;
  // v0.8.4: PCM-Boost-Faktor vor Whisper. 3x ist konservativ — verstärkt
  // leise Aufnahmen mit sauberem Clipping bei int16-Range.
  static const double _pcmBoostFactor = 3.0;

  final KneipeClient _client;
  final String _tischId;

  WhisperQueue({required KneipeClient client, required String tischId})
      : _client = client,
        _tischId = tischId;

  // Queue + Worker-Flag
  final List<String> _audioPaths = [];
  bool _workerActive = false;
  bool _whisperReady = false;

  // Recorder + VAD-State
  AudioRecorder? _recorder;
  StreamSubscription<Amplitude>? _ampSub;
  String? _recordingPath;
  bool _chunkHadSpeech = false;
  DateTime? _lastLoudAt;

  // Speaker-Mode-Toggle (von außen gesetzt)
  bool _speakerMode = true;
  set speakerMode(bool v) => _speakerMode = v;

  bool _armed = false;
  bool get armed => _armed;
  bool get recording => _recorder != null && _recordingPath != null;

  // Whisper-Controller
  final WhisperController _whisperController = WhisperController();
  static const _whisperModel = WhisperModel.base;

  // Eigene User-ID (für Tote-Voice-Filter im Sound-Queue)
  String? myUserId;

  // System-Sound-Player für Hinweistöne
  final AudioPlayer _systemPlayer = AudioPlayer();
  bool _overloadAnnounced = false;

  // UI-Callbacks
  void Function()? onChanged;
  void Function(String message)? onError;
  void Function(String)? onLog;
  void _log(String s) => onLog?.call(s);

  /// Anzahl Items in der Queue (inkl. dem aktuell verarbeiteten Chunk).
  int get queueLength => _audioPaths.length;
  bool get overloaded => _audioPaths.length >= maxBacklog;
  bool get hasSpeechRightNow => _chunkHadSpeech;

  // === Setup ===

  Future<void> initWhisper() async {
    try {
      final bytes = await rootBundle.load(
        'assets/models/ggml-${_whisperModel.modelName}.bin',
      );
      final modelPath = await _whisperController.getPath(_whisperModel);
      final file = File(modelPath);
      if (!await file.exists() ||
          (await file.length()) != bytes.lengthInBytes) {
        await file.writeAsBytes(
          bytes.buffer.asUint8List(bytes.offsetInBytes, bytes.lengthInBytes),
        );
      }
      _whisperReady = true;
    } catch (_) {
      _whisperReady = false;
    }
  }

  // === Mic Arm/Disarm ===

  Future<bool> arm() async {
    final perm = await Permission.microphone.request();
    if (!perm.isGranted) {
      _log('mic arm: permission DENIED');
      return false;
    }

    _recorder ??= AudioRecorder();
    _armed = true;
    onChanged?.call();
    _log('mic arm spk=$_speakerMode');
    await _startChunk();
    return true;
  }

  Future<void> disarm() async {
    _armed = false;
    _log('mic disarm');
    await _ampSub?.cancel();
    _ampSub = null;

    final pendingPath = _recordingPath;
    final hadSpeech = _chunkHadSpeech;
    String? stoppedPath;
    try {
      stoppedPath = await _recorder?.stop();
    } catch (_) {}

    _recordingPath = null;
    _chunkHadSpeech = false;
    onChanged?.call();

    final actualPath = stoppedPath ?? pendingPath ?? '';
    if (hadSpeech && actualPath.isNotEmpty) {
      _enqueuePath(actualPath);
    }

    try {
      await _recorder?.dispose();
    } catch (_) {}
    _recorder = null;
  }

  /// Wird vom Sound-Queue aufgerufen am Burst-Ende — Mic-Reset für
  /// Echo-Cancel-Hardware-Profil-Refresh. STUMPF: immer ausführen wenn
  /// Mic arm ist, kein "wenn-dann".
  Future<void> resetAfterBurst() async {
    if (!_armed || _recorder == null) {
      _log('mic resetAfterBurst skip (armed=$_armed)');
      return;
    }
    _log('mic resetAfterBurst start');
    try {
      final pending = _recordingPath;
      final hadSpeech = _chunkHadSpeech;
      await _recorder!.stop();
      if (hadSpeech && pending != null && pending.isNotEmpty) {
        _enqueuePath(pending);
      }
      _recordingPath = null;
      _chunkHadSpeech = false;
      await _startChunk();
      _log('mic resetAfterBurst done');
    } catch (e) {
      _log('mic resetAfterBurst ERR $e');
    }
  }

  // === VAD-Chunking ===

  double get _onThresholdDb =>
      _speakerMode ? _onThresholdSpeakerDb : _onThresholdHeadphoneDb;
  double get _offThresholdDb =>
      _speakerMode ? _offThresholdSpeakerDb : _offThresholdHeadphoneDb;

  Future<void> _startChunk() async {
    final r = _recorder;
    if (r == null) return;

    final dir = await getTemporaryDirectory();
    final path =
        '${dir.path}/rec_${DateTime.now().microsecondsSinceEpoch}.wav';
    _chunkHadSpeech = false;
    _lastLoudAt = null;

    try {
      // v0.8.4: AudioSource ZURÜCK auf unprocessed (wie v0.7.25-Halleluja).
      // voiceCommunication aus v0.8.3 hatte zwar AGC, aber triggerte
      // Hardware-Echo-Cancellation → Music-Stream stummgeschaltet wenn
      // Mic aktiv. Genau der gleiche Bug wie damals voiceRecognition.
      // unprocessed = rohes Mic, kein Echo-Cancel, kein Stream-Hijack.
      // AGC verloren — als Ersatz: PCM-Boost in Software vor Whisper +
      // VAD-Threshold runter.
      await r.start(
        const RecordConfig(
          encoder: AudioEncoder.wav,
          sampleRate: 16000,
          numChannels: 1,
          androidConfig: AndroidRecordConfig(
            audioSource: AndroidAudioSource.unprocessed,
          ),
        ),
        path: path,
      );
      _recordingPath = path;
      onChanged?.call();

      await _ampSub?.cancel();
      _ampSub = r
          .onAmplitudeChanged(const Duration(milliseconds: 150))
          .listen(_onAmplitude);
      _log('chunk start ok');
    } catch (e) {
      _log('chunk start ERR $e');
      onError?.call('Mic Start: $e');
    }
  }

  void _onAmplitude(Amplitude amp) {
    final db = amp.current;
    final now = DateTime.now();

    if (db >= _onThresholdDb) {
      _chunkHadSpeech = true;
      _lastLoudAt = now;
    } else if (db < _offThresholdDb && _chunkHadSpeech && _lastLoudAt != null) {
      final silenceFor = now.difference(_lastLoudAt!).inMilliseconds;
      if (silenceFor >= _silenceCutMs) {
        // Chunk fertig — abschneiden, Whisper verarbeiten, neuen starten.
        _cutChunk();
      }
    }
  }

  Future<void> _cutChunk() async {
    final r = _recorder;
    if (r == null) return;
    _log('chunk cut (silence detected)');

    final pendingPath = _recordingPath;
    final hadSpeech = _chunkHadSpeech;

    await _ampSub?.cancel();
    _ampSub = null;

    String? stoppedPath;
    try {
      stoppedPath = await r.stop();
    } catch (_) {}

    final actualPath = stoppedPath ?? pendingPath ?? '';
    if (hadSpeech && actualPath.isNotEmpty) {
      _enqueuePath(actualPath);
    }

    if (_armed && !overloaded) {
      await _startChunk();
    }
  }

  // === Worker ===

  void _enqueuePath(String path) {
    _audioPaths.add(path);
    _log('whisper enqueue q=${_audioPaths.length}');
    onChanged?.call();
    if (_audioPaths.length == maxBacklog && !_overloadAnnounced) {
      _overloadAnnounced = true;
      _playSystemSound('assets/sounds/whisper-warnung.wav');
    }
    _runWorker();
  }

  Future<void> _runWorker() async {
    if (_workerActive) return;
    _workerActive = true;
    onChanged?.call();
    _log('whisper worker start q=${_audioPaths.length}');

    try {
      while (_audioPaths.isNotEmpty) {
        // peek — Element bleibt in Queue während Verarbeitung.
        final path = _audioPaths.first;
        try {
          await _processChunk(path);
        } catch (e) {
          _log('whisper process ERR $e');
          onError?.call('Whisper-Worker: $e');
        }
        if (_audioPaths.isNotEmpty) {
          _audioPaths.removeAt(0);
          onChanged?.call();
        }
      }
    } finally {
      _workerActive = false;
      onChanged?.call();
      _log('whisper worker end');
      // Bereit-Ton wenn der Stau abgebaut wurde.
      if (_overloadAnnounced && _audioPaths.isEmpty) {
        _overloadAnnounced = false;
        _playSystemSound('assets/sounds/whisper-bereit.wav');
      }
    }
  }

  Future<void> _processChunk(String path) async {
    final file = File(path);
    if (!await file.exists()) return;

    // v0.8.4: PCM-Boost vor Whisper. WAV-Datei wird in-place verstärkt
    // damit Whisper das Audio lauter zu hören bekommt — Software-Ersatz
    // für AGC (das wir mit unprocessed-Source verloren haben).
    try {
      await _boostWavInPlace(path, _pcmBoostFactor);
    } catch (_) {}

    String transcribed = '';
    if (_whisperReady) {
      try {
        final result = await _whisperController.transcribe(
          model: _whisperModel,
          audioPath: path,
          lang: 'de',
        );
        transcribed = result?.transcription.text.trim() ?? '';
      } catch (_) {
        transcribed = '';
      }
    }

    // Müll-Filter v0.8.3: Klammer-Tags raus PLUS häufige Geräusch-Wörter.
    // Wenn nach Filter nichts substantielles übrig ist → verwerfen.
    final stripped = transcribed
        .replaceAll(RegExp(r'\[[^\]]*\]'), '')   // [Musik], [Stille]
        .replaceAll(RegExp(r'\([^\)]*\)'), '')   // (schmatzt), (hustet)
        .replaceAll(RegExp(r'\*[^\*]*\*'), '')   // *seufzt*
        .trim();

    // Geräusch-Wörter die Whisper manchmal als richtige Wörter transkribiert.
    const noiseWords = {
      'schmatzt', 'schmatz', 'räuspert', 'räusper', 'hustet', 'husten',
      'seufzt', 'seufz', 'atmet', 'atemzug', 'atmen',
      'ähm', 'öhm', 'mhm', 'mh', 'hm', 'aha', 'aah', 'oh', 'mm',
      'tickt', 'klopft', 'pocht',
    };

    final words = stripped
        .toLowerCase()
        .split(RegExp(r'[\s\.\,\!\?\-]+'))
        .where((w) => w.isNotEmpty)
        .toList();

    // Hat mindestens ein Wort, das KEIN Geräusch-Wort ist → echter Inhalt.
    final hasContent = words.any((w) {
      final clean = w.replaceAll(RegExp(r'[^a-zäöüß]'), '');
      return clean.isNotEmpty && !noiseWords.contains(clean);
    });

    if (!hasContent) {
      try {
        await file.delete();
      } catch (_) {}
      return;
    }

    final bytes = await file.readAsBytes();
    try {
      await file.delete();
    } catch (_) {}
    if (bytes.isEmpty) return;

    // Senden — KEIN Send-Wait mehr (alte Kopplung Whisper↔Sound raus).
    final msg = await _client.sendVoice(
      _tischId,
      bytes,
      filename: 'Voice.wav',
      filetype: 'audio/wav',
      text: transcribed,
    );

    if (myUserId == null && msg != null) {
      final id = msg['user_id']?.toString();
      if (id != null && id.isNotEmpty) myUserId = id;
    }
  }

  // === PCM-Boost (Software-AGC) ===

  /// Verstärkt das WAV-File in-place. Erwartet WAV mit 16-bit PCM (was
  /// AudioEncoder.wav + sampleRate 16000 + numChannels 1 produziert).
  /// Header bleibt unverändert (44 bytes typisch), nur PCM-Samples werden
  /// multipliziert. Clipping zu int16-Range damit keine Übersteuerung.
  Future<void> _boostWavInPlace(String path, double gain) async {
    if (gain <= 1.0) return;
    final file = File(path);
    final bytes = await file.readAsBytes();
    if (bytes.length <= 44) return; // kein PCM-Inhalt

    // Standard-WAV-Header ist 44 bytes. Bei 16-bit Mono 16kHz hat
    // jedes Sample 2 bytes (little-endian signed int16).
    const headerLen = 44;
    final out = List<int>.from(bytes);
    for (int i = headerLen; i + 1 < out.length; i += 2) {
      // little-endian int16 lesen
      int sample = (out[i + 1] << 8) | out[i];
      if (sample & 0x8000 != 0) sample -= 0x10000; // sign extension

      // Verstärken
      sample = (sample * gain).round();

      // Clipping auf int16-Range
      if (sample > 32767) sample = 32767;
      if (sample < -32768) sample = -32768;

      // Zurück als unsigned
      if (sample < 0) sample += 0x10000;
      out[i] = sample & 0xFF;
      out[i + 1] = (sample >> 8) & 0xFF;
    }
    await file.writeAsBytes(out);
  }

  // === System-Sounds ===

  void _playSystemSound(String assetPath) {
    () async {
      try {
        await _systemPlayer.setVolume(1.0);
        await _systemPlayer.setAsset(assetPath);
        await _systemPlayer.play();
      } catch (_) {}
    }();
  }

  Future<void> dispose() async {
    await _ampSub?.cancel();
    try {
      await _recorder?.dispose();
    } catch (_) {}
    try {
      await _systemPlayer.dispose();
    } catch (_) {}
  }
}
