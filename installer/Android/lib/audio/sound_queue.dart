import 'dart:async';
import 'dart:io';
import 'package:audio_session/audio_session.dart';
import 'package:just_audio/just_audio.dart';
import 'package:path_provider/path_provider.dart';

import '../api/kneipe_client.dart';

/// SoundQueue — FiFo-Queue für Sound-Outputs (Server-Voice-Wiedergabe).
///
/// **Goldene Regel C:** Sound-Outputs landen hier. Worker-Flag-Schutz
/// gegen Race-Conditions (mehrere _enqueue-Calls in Millisekunden).
/// Worker arbeitet sequenziell ab — egal ob manueller Klick oder
/// _pollOnce-Auto-Voice, beides geht durch dieselbe Queue.
///
/// **NIE WIEDER:**
/// - Frischer Player pro Track (HFP-Session-Bruch)
/// - setActive-Toggle pro Burst (Audio-Routing-Bruch)
/// - if (!_playing) _runQueue() (Race-Falle)
class SoundQueue {
  final KneipeClient _client;
  final String _baseUrl;

  /// Eigene Voice-Filter: Track wird in Queue stehen aber NICHT abgespielt.
  /// Bei manuellem Klick wird _silent entfernt → spielt dann normal.
  static const String silentFlag = '_silent';

  SoundQueue({required KneipeClient client, required String baseUrl})
      : _client = client,
        _baseUrl = baseUrl;

  final List<Map<String, dynamic>> _items = [];
  bool _workerActive = false;

  /// Reine UI-Anzeige: läuft gerade ein Track?
  bool get isPlaying => _playing;
  bool _playing = false;

  /// Anzahl Items in der Queue (inkl. dem aktuell laufenden, peek statt pop).
  int get length => _items.length;

  /// Player wird einmalig konfiguriert und für alle Tracks wiederverwendet.
  /// Lehre v0.7.x: dispose+neu pro Track bricht die HFP-Session.
  final AudioPlayer _player = AudioPlayer();

  /// Callback wenn Items in der Queue sich ändern (für UI-Refresh).
  void Function()? onChanged;

  /// Optionaler Hook: Mic-Restart nach Burst-Ende (Echo-Cancel-Reset).
  Future<void> Function()? onBurstEnd;

  /// UI-Hook für Banner-Logs.
  void Function(String)? onLog;
  void _log(String s) => onLog?.call(s);

  Future<void> setupPlayer() async {
    // v0.8.4: Player zurück auf usage: media (wie v0.7.25-Halleluja-Stand).
    // Die AudioSession-Config alleine reicht für voiceChat-Routing — Player
    // muss nicht selbst auf voiceCommunication. Versuch in v0.8.2/3 mit
    // Player auf voiceCommunication hat Sound während Mic-Aktiv blockiert.
    try {
      await _player.setAndroidAudioAttributes(const AndroidAudioAttributes(
        contentType: AndroidAudioContentType.music,
        flags: AndroidAudioFlags.none,
        usage: AndroidAudioUsage.media,
      ));
      await _player.setVolume(1.0);
    } catch (_) {}
  }

  /// Item in die Queue legen und Worker starten.
  void enqueue(Map<String, dynamic> msg) {
    _items.add(msg);
    _log('sound enqueue q=${_items.length} silent=${msg[silentFlag] == true}');
    onChanged?.call();
    _runWorker();
  }

  /// Worker — sequenziell, single-instance via _workerActive.
  Future<void> _runWorker() async {
    if (_workerActive) return;
    _workerActive = true;
    _playing = true;
    onChanged?.call();
    _log('sound burst-start q=${_items.length}');

    try {
      while (_items.isNotEmpty) {
        // peek — Item bleibt in Queue während Bearbeitung (UI-Banner-Wahrheit).
        final msg = _items.first;
        try {
          await _playOne(msg);
        } catch (e) {
          _log('sound playOne ERR $e');
        }
        if (_items.isNotEmpty) {
          _items.removeAt(0);
          onChanged?.call();
        }
      }
    } finally {
      _playing = false;
      _workerActive = false;
      onChanged?.call();
      _log('sound burst-end');
      // Burst-Ende-Hook: Mic-Restart-Pattern (Regel A+B-Hilfe)
      try {
        await onBurstEnd?.call();
      } catch (_) {}
    }
  }

  Future<void> _playOne(Map<String, dynamic> msg) async {
    // Tote Voice: Track ist sichtbar in Queue, wird aber stumm-übersprungen.
    if (msg[silentFlag] == true) {
      _log('sound playOne skip (silent)');
      return;
    }
    _log('sound playOne start');

    final url = (msg['file_url'] ?? msg['tts_url'])?.toString();
    if (url == null || url.isEmpty) return;

    final absUrl = Uri.parse(_baseUrl).resolve(url).toString();
    final bytes = await _client
        .fetchFile(absUrl)
        .timeout(const Duration(seconds: 8));

    final dir = await getTemporaryDirectory();
    final ext = url.endsWith('.wav')
        ? 'wav'
        : url.endsWith('.mp3')
            ? 'mp3'
            : url.endsWith('.m4a') || url.endsWith('.mp4')
                ? 'm4a'
                : 'webm';
    final path =
        '${dir.path}/play_${DateTime.now().microsecondsSinceEpoch}.$ext';
    await File(path).writeAsBytes(bytes);

    await _player.setFilePath(path).timeout(const Duration(seconds: 5));

    // Auf completed ODER idle warten (idle fängt Edge-Cases ab).
    final completer = Completer<void>();
    late StreamSubscription<PlayerState> sub;
    sub = _player.playerStateStream.listen((s) {
      if (s.processingState == ProcessingState.completed ||
          s.processingState == ProcessingState.idle) {
        if (!completer.isCompleted) completer.complete();
      }
    });
    try {
      await _player.play();
      await completer.future;
    } finally {
      await sub.cancel();
    }
  }

  Future<void> dispose() async {
    try {
      await _player.dispose();
    } catch (_) {}
  }
}
