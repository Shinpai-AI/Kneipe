import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../audio/audio_session_manager.dart';
import '../audio/sound_queue.dart';
import '../audio/whisper_queue.dart';
import '../session.dart';

/// ChatScreen — UI-Schicht. Audio-Logik liegt in lib/audio/.
///
/// **Goldene Regeln (siehe Doku Kneipe-Flutter-App-Goldene-Regeln.md):**
/// - Regel A (Speaker-Mode): Sound spielt → Mic stumm. Stille → Mic aktiv.
/// - Regel B (Speaker-Mode): Mic-Input läuft → Sound wartet.
/// - Regel C (alle Modi): SoundQueue + WhisperQueue → ChatQueue (FiFo stumpf).
/// - Regel D: Kopfhörer nimmt immer auf, Handy pausiert bei Ton.
class ChatScreen extends ConsumerStatefulWidget {
  final String tischId;
  const ChatScreen({super.key, required this.tischId});

  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen> {
  // === Audio ===
  late final AudioSessionManager _audioSession;
  late final SoundQueue _soundQueue;
  late final WhisperQueue _whisperQueue;

  // === UI-State ===
  final _textCtrl = TextEditingController();
  final _scrollCtrl = ScrollController();
  final List<Map<String, dynamic>> _messages = [];
  final Set<String> _playedIds = {};
  List<String> _tableMembers = const [];
  double _lastTs = 0;
  bool _sending = false;
  bool _joined = false;
  bool _initialHistoryLoaded = false;
  String? _error;

  // Modus: speakerMode true = Handy-Modus, false = Kopfhörer-Modus.
  bool _speakerMode = true;

  // Banner-Logs für Debug-Sichtbarkeit (max 8 letzte Einträge)
  final List<String> _micLog = [];
  void _appendLog(String s) {
    if (!mounted) return;
    setState(() {
      _micLog.add('${DateTime.now().toIso8601String().substring(11, 19)} $s');
      if (_micLog.length > 8) _micLog.removeAt(0);
    });
  }

  Timer? _pollTimer;
  StreamSubscription<void>? _routingSub;

  @override
  void initState() {
    super.initState();
    final session = ref.read(activeSessionProvider);
    if (session == null) {
      _error = 'Keine aktive Session';
      return;
    }

    _audioSession = AudioSessionManager();
    _soundQueue = SoundQueue(client: session.client, baseUrl: session.baseUrl);
    _whisperQueue = WhisperQueue(client: session.client, tischId: widget.tischId);

    // Cross-wiring: Sound-Queue weckt Mic-Reset am Burst-Ende.
    _soundQueue.onBurstEnd = _whisperQueue.resetAfterBurst;

    // UI-Refresh wenn sich was in den Queues ändert.
    _soundQueue.onChanged = _markDirty;
    _whisperQueue.onChanged = _markDirty;
    _whisperQueue.onError = (m) {
      if (mounted) setState(() => _error = m);
    };
    // Debug-Logs ins Banner.
    _audioSession.onLog = _appendLog;
    _soundQueue.onLog = _appendLog;
    _whisperQueue.onLog = _appendLog;

    WidgetsBinding.instance.addPostFrameCallback((_) {
      _bootstrap();
    });
  }

  Future<void> _bootstrap() async {
    // v0.8.5: Reihenfolge KRITISCH. Player-AudioAttributes MUST vor
    // AudioSession-Config gesetzt werden — sonst ignoriert Android die
    // Player-Attributes und greift nur auf Session-Routing zurück.
    // Lehre aus v0.7.25: in setupAudio() wurde _player.setAndroidAudioAttributes
    // VOR session.configure aufgerufen.
    await _soundQueue.setupPlayer();
    await _audioSession.setup();
    await _whisperQueue.initWhisper();

    // Profil holen → MyUserId für Tote-Voice-Filter.
    try {
      final session = ref.read(activeSessionProvider);
      if (session != null) {
        final profile = await session.client.getMyProfile();
        _whisperQueue.myUserId = profile['id']?.toString();
      }
    } catch (_) {}

    // Routing-Listener.
    _routingSub = _audioSession.devicesChangedStream.listen((_) async {
      final isHp = await _audioSession.isHeadphoneActive();
      final newSpeaker = !isHp;
      if (newSpeaker != _speakerMode) {
        if (mounted) setState(() => _speakerMode = newSpeaker);
        _whisperQueue.speakerMode = newSpeaker;
      }
    });
    final initialHp = await _audioSession.isHeadphoneActive();
    if (mounted) setState(() => _speakerMode = !initialHp);
    _whisperQueue.speakerMode = !initialHp;

    // Tisch beitreten + Polling starten.
    await _joinAndStart();
  }

  void _markDirty() {
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    _routingSub?.cancel();
    _soundQueue.dispose();
    _whisperQueue.dispose();
    _leaveIfJoined();
    _textCtrl.dispose();
    _scrollCtrl.dispose();
    super.dispose();
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
      if (mounted) setState(() => _error = 'Tisch beitreten: $e');
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
      if (mounted) setState(() => _tableMembers = names);
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
      if (!mounted || data.isEmpty) return;

      final newMessages = data.cast<Map<String, dynamic>>();
      final wasAtBottom = !_scrollCtrl.hasClients ||
          _scrollCtrl.position.pixels >=
              _scrollCtrl.position.maxScrollExtent - 60;

      setState(() {
        _messages.addAll(newMessages);
        _lastTs = (newMessages.last['time'] as num?)?.toDouble() ?? _lastTs;
        _error = null;
      });
      if (wasAtBottom) _scrollToBottomForce();

      if (newMessages.any((m) => m['system'] == true)) {
        _fetchMembers();
      }

      if (!_initialHistoryLoaded) {
        // Beim ersten Poll: alle vorhandenen Messages als "schon gespielt"
        // markieren — kein Autoplay des kompletten Verlaufs.
        for (final m in newMessages) {
          final url = (m['file_url'] ?? m['tts_url'])?.toString();
          if (url == null || url.isEmpty) continue;
          _playedIds.add('${m['time']}_$url');
        }
        _initialHistoryLoaded = true;
      } else {
        for (final m in newMessages) {
          final url = (m['file_url'] ?? m['tts_url'])?.toString();
          if (url == null || url.isEmpty) continue;
          final id = '${m['time']}_$url';
          if (_playedIds.contains(id)) continue;
          _playedIds.add(id);

          // Tote-Voice-Filter: eigene Voice → silent (in Queue, aber nicht spielen).
          final senderId = m['user_id']?.toString();
          final isOwn = _whisperQueue.myUserId != null &&
              senderId == _whisperQueue.myUserId;
          if (isOwn) {
            m[SoundQueue.silentFlag] = true;
          }
          _soundQueue.enqueue(m);
        }
      }
    } catch (e) {
      if (mounted) setState(() => _error = 'Poll: $e');
    }
  }

  void _scrollToBottomForce() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollCtrl.hasClients) return;
      _scrollCtrl.animateTo(
        _scrollCtrl.position.maxScrollExtent,
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOut,
      );
    });
  }

  Future<void> _send() async {
    final text = _textCtrl.text.trim();
    if (text.isEmpty) return;
    final session = ref.read(activeSessionProvider);
    if (session == null) return;
    setState(() => _sending = true);
    try {
      await session.client.sendChat(widget.tischId, text);
      _textCtrl.clear();
      await _pollOnce();
    } catch (e) {
      if (mounted) setState(() => _error = 'Senden: $e');
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  void _playMessageManually(Map<String, dynamic> msg) {
    // Manueller Klick: Tote-Voice-Marker abnehmen damit eigene Voice trotzdem spielt.
    final cleaned = Map<String, dynamic>.from(msg)..remove(SoundQueue.silentFlag);
    _soundQueue.enqueue(cleaned);
  }

  Future<void> _toggleMic() async {
    if (_whisperQueue.armed) {
      await _whisperQueue.disarm();
    } else {
      final ok = await _whisperQueue.arm();
      if (!ok && mounted) setState(() => _error = 'Mikrofon-Berechtigung fehlt');
    }
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final isPlaying = _soundQueue.isPlaying;
    final whisperLen = _whisperQueue.queueLength;
    final whisperOverloaded = _whisperQueue.overloaded;

    return Scaffold(
      appBar: AppBar(
        title: Text('Tisch ${widget.tischId}'),
        actions: [
          if (isPlaying)
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
              padding:
                  const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              child: Row(
                children: [
                  Icon(Icons.people_outline,
                      size: 16, color: Colors.grey.shade700),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Wrap(
                      spacing: 4,
                      runSpacing: 4,
                      children: _tableMembers
                          .map((n) => Container(
                                padding: const EdgeInsets.symmetric(
                                    horizontal: 8, vertical: 2),
                                decoration: BoxDecoration(
                                  color: Colors.white,
                                  borderRadius: BorderRadius.circular(10),
                                  border: Border.all(
                                      color: Colors.amber.shade200),
                                ),
                                child: Text(n,
                                    style: const TextStyle(fontSize: 11)),
                              ))
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
                onPlay: () => _playMessageManually(_messages[i]),
              ),
            ),
          ),
          if (whisperOverloaded)
            Container(
              color: Colors.orange.shade100,
              padding: const EdgeInsets.all(8),
              width: double.infinity,
              child: Text(
                '⚠ Whisper überlastet ($whisperLen Aufnahmen offen) — bitte kurz Pause',
                style: TextStyle(
                  color: Colors.orange.shade900,
                  fontWeight: FontWeight.bold,
                ),
              ),
            )
          else if (whisperLen > 0)
            Container(
              color: Colors.green.shade50,
              padding:
                  const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              width: double.infinity,
              child: Text(
                'Whisper bereit · $whisperLen im Hintergrund',
                style: TextStyle(
                    color: Colors.green.shade800, fontSize: 11),
              ),
            ),
          if (_error != null)
            Container(
              color: Colors.red.shade50,
              padding: const EdgeInsets.all(8),
              width: double.infinity,
              child: Text(_error!,
                  style: TextStyle(color: Colors.red.shade700)),
            ),
          // v0.8.6: Debug-Banner — letzte 8 Audio-Events live sichtbar.
          if (_micLog.isNotEmpty)
            Container(
              color: Colors.grey.shade100,
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              width: double.infinity,
              constraints: const BoxConstraints(maxHeight: 110),
              child: SingleChildScrollView(
                reverse: true,
                child: Text(
                  _micLog.join('\n'),
                  style: const TextStyle(
                    fontSize: 10,
                    fontFamily: 'monospace',
                    color: Colors.black87,
                  ),
                ),
              ),
            ),
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(8),
              child: Row(
                children: [
                  IconButton(
                    tooltip: _speakerMode
                        ? 'Lautsprecher-Modus'
                        : 'Kopfhörer-Modus',
                    icon: Icon(_speakerMode
                        ? Icons.volume_up
                        : Icons.headphones),
                    onPressed: () {
                      setState(() => _speakerMode = !_speakerMode);
                      _whisperQueue.speakerMode = _speakerMode;
                    },
                  ),
                  IconButton.filled(
                    tooltip: _whisperQueue.armed
                        ? 'Mikrofon stoppen'
                        : 'Mikrofon aktivieren',
                    icon: Icon(_whisperQueue.armed ? Icons.stop : Icons.mic),
                    style: IconButton.styleFrom(
                      backgroundColor: _whisperQueue.armed
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
                            child:
                                CircularProgressIndicator(strokeWidth: 2),
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
