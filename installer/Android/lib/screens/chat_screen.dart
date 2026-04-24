import 'dart:async';
import 'dart:io';
import 'package:audio_session/audio_session.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:just_audio/just_audio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:record/record.dart';
import '../session.dart';

class ChatScreen extends ConsumerStatefulWidget {
  final String tischId;
  const ChatScreen({super.key, required this.tischId});

  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen>
    with WidgetsBindingObserver {
  // Hartkodiert auf Preset "moderat ALLES" (entspricht Web-Kneipe Preset 2).
  // UI-Regler kommt später mit dem Profil-Ausbau — jetzt fest eingestellt.
  static const _silenceThresholdDb = -20.0;
  static const _silenceCutMs = 800; // match Web-Kneipe VAD

  final _textCtrl = TextEditingController();
  final _scrollCtrl = ScrollController();
  // Recorder wird LAZY erst beim ersten Mic-Klick angelegt — sonst greift
  // Android auf die Mic-Hardware zu obwohl wir nicht recorden.
  AudioRecorder? _recorder;
  final _player = AudioPlayer();
  final List<Map<String, dynamic>> _messages = [];
  final Set<String> _playedIds = {};
  final List<Map<String, dynamic>> _playQueue = [];
  List<String> _tableMembers = const [];
  double _lastTs = 0;
  Timer? _pollTimer;
  bool _sending = false;
  bool _joined = false;
  bool _micArmed = false;
  bool _recording = false;
  bool _uploading = false;
  bool _playing = false;
  bool _speakerMode = true; // true = Lautsprecher, false = Kopfhörer
  bool _initialHistoryLoaded = false;
  bool _chunkHadSpeech = false;
  bool _queuePausedBySpeech = false; // Player pausiert weil User spricht (nur Speaker-Mode)
  DateTime? _lastLoudAt;
  String? _recordingPath;
  String? _error;
  StreamSubscription<Amplitude>? _ampSub;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _setupAudio();
      _joinAndStart();
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _pollTimer?.cancel();
    _ampSub?.cancel();
    _player.dispose();
    _recorder?.dispose();
    _recorder = null;
    _leaveIfJoined();
    _textCtrl.dispose();
    _scrollCtrl.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      if (_joined && _pollTimer == null) {
        _startPolling();
      } else if (!_joined) {
        _joinAndStart();
      }
    }
  }

  Future<void> _setupAudio() async {
    try {
      final session = await AudioSession.instance;
      await session.configure(const AudioSessionConfiguration.music());
    } catch (_) {}
  }

  Future<void> _leaveIfJoined() async {
    if (!_joined) return;
    final session = ref.read(activeSessionProvider);
    if (session == null) return;
    try {
      await session.client.leaveTable(widget.tischId);
    } catch (_) {}
  }

  Future<void> _joinAndStart() async {
    final session = ref.read(activeSessionProvider);
    if (session == null) return;
    try {
      await session.client.joinTable(widget.tischId);
      if (!mounted) return;
      setState(() {
        _joined = true;
        _error = null;
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = 'Tisch beitreten fehlgeschlagen: $e';
        });
      }
      return;
    }
    _fetchMembers();
    _startPolling();
  }

  String get _raumId {
    final parts = widget.tischId.split('_');
    return parts.isNotEmpty ? parts.first : '';
  }

  Future<void> _fetchMembers() async {
    final session = ref.read(activeSessionProvider);
    if (session == null) return;
    try {
      final bar = await session.client.getBar(_raumId);
      final tische = (bar['tische'] as List?) ?? const [];
      final myTable = tische.firstWhere(
        (t) =>
            (t as Map<String, dynamic>)['id']?.toString() == widget.tischId,
        orElse: () => const <String, dynamic>{},
      ) as Map<String, dynamic>;
      final names =
          (myTable['names'] as List?)?.cast<String>() ?? const <String>[];
      if (!mounted) return;
      setState(() {
        _tableMembers = names;
      });
    } catch (_) {}
  }

  void _startPolling() {
    _pollOnce();
    _pollTimer = Timer.periodic(const Duration(seconds: 3), (_) => _pollOnce());
  }

  Future<void> _pollOnce() async {
    final session = ref.read(activeSessionProvider);
    if (session == null) return;
    try {
      final data = await session.client.pollChat(
        widget.tischId,
        since: _lastTs > 0 ? _lastTs : null,
      );
      if (!mounted) return;
      if (data.isNotEmpty) {
        final newMessages = data.cast<Map<String, dynamic>>();
        setState(() {
          _messages.addAll(newMessages);
          _lastTs =
              (newMessages.last['time'] as num?)?.toDouble() ?? _lastTs;
          _error = null;
        });
        _scrollToBottom();
        if (newMessages.any((m) => m['system'] == true)) {
          _fetchMembers();
        }
        if (!_initialHistoryLoaded) {
          for (final m in newMessages) {
            final url = (m['file_url'] ?? m['tts_url'])?.toString();
            if (url == null || url.isEmpty) continue;
            _playedIds.add('${m['time']}_$url');
          }
        } else {
          for (final m in newMessages) {
            final url = (m['file_url'] ?? m['tts_url'])?.toString();
            if (url == null || url.isEmpty) continue;
            final id = '${m['time']}_$url';
            if (_playedIds.contains(id)) continue;
            _playedIds.add(id);
            _enqueueAutoplay(m);
          }
        }
      }
      if (!_initialHistoryLoaded) {
        _initialHistoryLoaded = true;
        // Multiple scroll attempts after layout settles — ListView sometimes
        // has no maxScrollExtent on first layout pass.
        for (final delay in [100, 300, 600, 1000]) {
          Future.delayed(Duration(milliseconds: delay), () {
            if (mounted && _scrollCtrl.hasClients) {
              _scrollCtrl.jumpTo(_scrollCtrl.position.maxScrollExtent);
            }
          });
        }
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = e.toString();
        });
      }
    }
  }

  void _enqueueAutoplay(Map<String, dynamic> msg) {
    _playQueue.add(msg);
    if (!_playing) {
      _runQueue();
    }
  }

  Future<void> _runQueue() async {
    if (_playing) return;
    if (mounted) setState(() => _playing = true);
    // NEU v0.4.2: Mic läuft immer parallel. Nur der Player pausiert (nicht
    // das Mic) wenn im Speaker-Mode Sprache detected wird — Echo-Schutz.
    // Im Kopfhörer-Mode läuft alles komplett parallel ohne Pause.
    try {
      while (_playQueue.isNotEmpty && mounted) {
        final msg = _playQueue.removeAt(0);
        try {
          await _playOne(msg);
        } catch (e) {
          if (mounted) setState(() => _error = 'Voice übersprungen: $e');
        }
      }
    } finally {
      if (mounted) setState(() => _playing = false);
    }
  }

  Future<void> _playOne(Map<String, dynamic> msg) async {
    final session = ref.read(activeSessionProvider);
    if (session == null) return;
    final url = (msg['file_url'] ?? msg['tts_url'])?.toString();
    if (url == null || url.isEmpty) return;

    final absUrl = Uri.parse(session.baseUrl).resolve(url).toString();
    final bytes = await session.client
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
    // v0.2.2 Form: Listener auf completed ODER idle (idle fängt Edge-Cases
    // wo Player nach Fehler in idle rutscht statt completed zu emitten).
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

  Future<void> _pauseRecording() async {
    final r = _recorder;
    if (r == null) return;
    try {
      if (await r.isRecording()) {
        await r.pause();
      }
    } catch (_) {}
  }

  Future<void> _resumeRecording() async {
    final r = _recorder;
    if (r == null) return;
    try {
      if (await r.isPaused()) {
        await r.resume();
      }
    } catch (_) {}
  }

  // ============================================================
  //  Mikrofon-Toggle (VAD-Chunking)
  // ============================================================

  Future<void> _toggleMic() async {
    if (_micArmed) {
      await _disarmMic();
    } else {
      await _armMic();
    }
  }

  Future<void> _armMic() async {
    final perm = await Permission.microphone.request();
    if (!perm.isGranted) {
      if (mounted) {
        setState(() => _error = 'Mikrofon-Berechtigung fehlt');
      }
      return;
    }
    // LAZY: Recorder wird erst jetzt instanziiert, nicht schon im initState.
    // Verhindert passiven Mic-Zugriff auf Android wenn User Mic nie benutzt.
    _recorder ??= AudioRecorder();
    if (mounted) {
      setState(() {
        _micArmed = true;
        _error = null;
      });
    }
    await _startChunk();
  }

  Future<void> _disarmMic() async {
    if (mounted) setState(() => _micArmed = false);
    await _ampSub?.cancel();
    _ampSub = null;
    await _stopChunkAndMaybeSend();
    // Recorder komplett disposen damit die Mic-Hardware freigegeben wird.
    try {
      await _recorder?.dispose();
    } catch (_) {}
    _recorder = null;
  }

  Future<void> _startChunk() async {
    final r = _recorder;
    if (r == null) return;
    try {
      final dir = await getTemporaryDirectory();
      final path =
          '${dir.path}/rec_${DateTime.now().microsecondsSinceEpoch}.m4a';
      _chunkHadSpeech = false;
      _lastLoudAt = null;
      await r.start(
        const RecordConfig(
          encoder: AudioEncoder.aacLc,
          bitRate: 48000,
          sampleRate: 22050,
          numChannels: 1,
        ),
        path: path,
      );
      if (!mounted) return;
      setState(() {
        _recording = true;
        _recordingPath = path;
      });
      await _ampSub?.cancel();
      _ampSub = r
          .onAmplitudeChanged(const Duration(milliseconds: 150))
          .listen(_onAmplitude);
    } catch (e) {
      if (mounted) setState(() => _error = 'Aufnahme: $e');
    }
  }

  void _onAmplitude(Amplitude amp) {
    if (_uploading) return;
    final now = DateTime.now();
    final isLoud = amp.current > _silenceThresholdDb;
    if (isLoud) {
      _chunkHadSpeech = true;
      _lastLoudAt = now;
      // NEU v0.4.2: In Speaker-Mode bei Sprache → Player PAUSIEREN (nicht
      // abbrechen!) damit kein Echo aufgenommen wird. Kopfhörer-Mode: nichts.
      if (_speakerMode && _playing && !_queuePausedBySpeech) {
        _queuePausedBySpeech = true;
        _player.pause().catchError((_) {});
      }
    } else if (_chunkHadSpeech && _lastLoudAt != null) {
      final silenceMs = now.difference(_lastLoudAt!).inMilliseconds;
      if (silenceMs > _silenceCutMs) {
        _chunkBreakAndContinue();
      }
    }
  }

  Future<void> _chunkBreakAndContinue() async {
    final wasArmed = _micArmed;
    await _ampSub?.cancel();
    _ampSub = null;
    await _stopChunkAndMaybeSend();
    // NEU v0.4.2: Player war wegen Sprache pausiert → jetzt Resume!
    if (_queuePausedBySpeech) {
      _queuePausedBySpeech = false;
      try {
        await _player.play();
      } catch (_) {}
    }
    if (wasArmed && _micArmed) {
      await _startChunk();
    }
  }

  Future<void> _stopChunkAndMaybeSend() async {
    if (!_recording) return;
    final rawPath = _recordingPath;
    String? stoppedPath;
    try {
      stoppedPath = await _recorder?.stop();
    } catch (_) {}
    if (mounted) {
      setState(() {
        _recording = false;
      });
    }
    final actualPath = stoppedPath ?? rawPath ?? '';
    if (actualPath.isEmpty) return;
    final file = File(actualPath);
    if (!await file.exists()) return;
    final shouldSend = _chunkHadSpeech;
    if (mounted && shouldSend) {
      setState(() => _uploading = true);
    }
    try {
      await Future<void>.delayed(const Duration(milliseconds: 150));
      final bytes = await file.readAsBytes();
      await file.delete();
      if (!shouldSend || bytes.isEmpty) return;
      final session = ref.read(activeSessionProvider);
      if (session == null) return;
      await session.client.sendVoice(
        widget.tischId,
        bytes,
        filename: 'Voice.m4a',
        filetype: 'audio/mp4',
      );
      await _pollOnce();
    } catch (e) {
      if (mounted) setState(() => _error = 'Voice senden: $e');
    } finally {
      if (mounted) {
        setState(() {
          _uploading = false;
          _recordingPath = null;
        });
      }
    }
  }

  // ============================================================
  //  Text-Send + Playback-Manual
  // ============================================================

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollCtrl.hasClients) {
        _scrollCtrl.animateTo(
          _scrollCtrl.position.maxScrollExtent,
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
        );
      }
    });
  }

  Future<void> _send() async {
    final text = _textCtrl.text.trim();
    if (text.isEmpty) return;
    final session = ref.read(activeSessionProvider);
    if (session == null) return;
    setState(() {
      _sending = true;
    });
    try {
      await session.client.sendChat(widget.tischId, text);
      _textCtrl.clear();
      await _pollOnce();
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = 'Senden fehlgeschlagen: $e';
        });
      }
    } finally {
      if (mounted) {
        setState(() {
          _sending = false;
        });
      }
    }
  }

  Future<void> _playMessage(Map<String, dynamic> msg) async {
    final url = (msg['file_url'] ?? msg['tts_url'])?.toString();
    if (url == null || url.isEmpty) return;
    final session = ref.read(activeSessionProvider);
    if (session == null) return;
    try {
      final absUrl = Uri.parse(session.baseUrl).resolve(url).toString();
      final bytes = await session.client.fetchFile(absUrl);
      final dir = await getTemporaryDirectory();
      final ext = url.endsWith('.wav')
          ? 'wav'
          : url.endsWith('.mp3')
              ? 'mp3'
              : url.endsWith('.m4a') || url.endsWith('.mp4')
                  ? 'm4a'
                  : 'webm';
      final path =
          '${dir.path}/manual_${DateTime.now().microsecondsSinceEpoch}.$ext';
      await File(path).writeAsBytes(bytes);
      try {
        await _player.stop();
        await _player.seek(Duration.zero);
      } catch (_) {}
      await _player.setFilePath(path);
      await _player.play();
    } catch (e) {
      if (mounted) setState(() => _error = 'Abspielen: $e');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('Tisch ${widget.tischId}'),
        actions: [
          if (_playing)
            const Padding(
              padding: EdgeInsets.only(right: 12),
              child: Center(
                child: SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
              ),
            ),
        ],
      ),
      body: Column(
        children: [
          if (_tableMembers.isNotEmpty)
            Container(
              width: double.infinity,
              color: Colors.amber.shade50,
              padding: const EdgeInsets.symmetric(
                horizontal: 12,
                vertical: 8,
              ),
              child: Row(
                children: [
                  Icon(
                    Icons.people_outline,
                    size: 16,
                    color: Colors.grey.shade700,
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Wrap(
                      spacing: 4,
                      runSpacing: 4,
                      children: _tableMembers
                          .map(
                            (n) => Container(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 8,
                                vertical: 2,
                              ),
                              decoration: BoxDecoration(
                                color: Colors.white,
                                borderRadius: BorderRadius.circular(10),
                                border: Border.all(
                                  color: Colors.amber.shade200,
                                ),
                              ),
                              child: Text(
                                n,
                                style: const TextStyle(fontSize: 11),
                              ),
                            ),
                          )
                          .toList(),
                    ),
                  ),
                ],
              ),
            ),
          Expanded(
            child: ListView.builder(
              controller: _scrollCtrl,
              padding: const EdgeInsets.all(8),
              itemCount: _messages.length,
              itemBuilder: (_, i) => _MessageTile(
                message: _messages[i],
                onPlay: () => _playMessage(_messages[i]),
              ),
            ),
          ),
          if (_error != null)
            Container(
              color: Colors.red.shade50,
              padding: const EdgeInsets.all(8),
              width: double.infinity,
              child: Text(
                _error!,
                style: TextStyle(color: Colors.red.shade700),
              ),
            ),
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(8),
              child: Row(
                children: [
                  IconButton(
                    tooltip: _speakerMode
                        ? 'Lautsprecher-Modus (Mic pausiert bei Playback)'
                        : 'Kopfhörer-Modus (Mic läuft parallel)',
                    icon: Icon(
                      _speakerMode
                          ? Icons.volume_up
                          : Icons.headphones,
                    ),
                    onPressed: () {
                      setState(() => _speakerMode = !_speakerMode);
                    },
                  ),
                  IconButton.filled(
                    tooltip: _micArmed
                        ? 'Mikrofon stoppen'
                        : 'Mikrofon aktivieren',
                    icon: _uploading
                        ? const SizedBox(
                            width: 20,
                            height: 20,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: Colors.white,
                            ),
                          )
                        : Icon(_micArmed ? Icons.stop : Icons.mic),
                    style: IconButton.styleFrom(
                      backgroundColor: _micArmed
                          ? Colors.red
                          : Theme.of(context).colorScheme.primary,
                    ),
                    onPressed: _toggleMic,
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: TextField(
                      controller: _textCtrl,
                      decoration: const InputDecoration(
                        hintText: 'Nachricht…',
                        border: OutlineInputBorder(),
                        isDense: true,
                      ),
                      textInputAction: TextInputAction.send,
                      onSubmitted: (_) => _sending ? null : _send(),
                      maxLines: 3,
                      minLines: 1,
                    ),
                  ),
                  const SizedBox(width: 8),
                  FilledButton(
                    onPressed: _sending ? null : _send,
                    child: _sending
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.send),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _MessageTile extends StatelessWidget {
  final Map<String, dynamic> message;
  final VoidCallback onPlay;
  const _MessageTile({required this.message, required this.onPlay});

  @override
  Widget build(BuildContext context) {
    final isSystem = message['system'] == true;
    final user = message['user']?.toString();
    final text = message['text']?.toString() ?? '';
    final hasVoice = (message['file_url'] ?? message['tts_url']) != null;

    if (isSystem) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 4, horizontal: 8),
        child: Text(
          text,
          style: TextStyle(
            fontStyle: FontStyle.italic,
            color: Colors.grey.shade600,
            fontSize: 12,
          ),
          textAlign: TextAlign.center,
        ),
      );
    }

    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: Colors.amber.shade50,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  user ?? 'Anon',
                  style: const TextStyle(
                    fontWeight: FontWeight.bold,
                    fontSize: 12,
                  ),
                ),
                if (hasVoice) ...[
                  const SizedBox(width: 6),
                  InkWell(
                    onTap: onPlay,
                    child: const Icon(Icons.play_circle_outline, size: 18),
                  ),
                ],
              ],
            ),
            if (text.isNotEmpty) Text(text),
          ],
        ),
      ),
    );
  }
}
