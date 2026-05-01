import 'package:audio_session/audio_session.dart';
import 'package:flutter_tts/flutter_tts.dart';

/// AudioSessionManager — voiceChat-Mode + permanent setActive(true).
///
/// Lehre aus v0.7.x-Marathon: BT-Headsets ohne LE Audio haben nur
/// HFP/A2DP. Mic-Aktiv erzwingt HFP-Switch → Sound stumm. voiceChat-Mode
/// signalisiert Android: "Telefon-Modus, BT bleibt im HFP, Sound + Mic
/// parallel". Sound wird Schmalband (mono ~16kHz), aber funktioniert.
///
/// **NIE WIEDER:**
/// - setActive(true)/false togglen (Audio-Routing-Bruch)
/// - voiceRecognition als AudioSource (Hardware-Echo-Cancel-Falle)
class AudioSessionManager {
  bool _initialized = false;
  final FlutterTts _systemTts = FlutterTts();

  /// UI-Hook für Banner-Logs.
  void Function(String)? onLog;
  void _log(String s) => onLog?.call(s);

  Future<void> setup() async {
    if (_initialized) return;

    // System-TTS als Fallback (selten genutzt, aber da)
    try {
      await _systemTts.setLanguage('de-DE');
      await _systemTts.setSpeechRate(0.5);
    } catch (_) {}

    final session = await AudioSession.instance;
    await session.configure(AudioSessionConfiguration(
      avAudioSessionCategory: AVAudioSessionCategory.playAndRecord,
      avAudioSessionCategoryOptions:
          AVAudioSessionCategoryOptions.allowBluetooth |
              AVAudioSessionCategoryOptions.allowBluetoothA2dp |
              AVAudioSessionCategoryOptions.defaultToSpeaker |
              AVAudioSessionCategoryOptions.mixWithOthers,
      avAudioSessionMode: AVAudioSessionMode.voiceChat,
      avAudioSessionRouteSharingPolicy:
          AVAudioSessionRouteSharingPolicy.defaultPolicy,
      avAudioSessionSetActiveOptions: AVAudioSessionSetActiveOptions.none,
      androidAudioAttributes: const AndroidAudioAttributes(
        contentType: AndroidAudioContentType.speech,
        flags: AndroidAudioFlags.none,
        usage: AndroidAudioUsage.voiceCommunication,
      ),
      androidAudioFocusGainType:
          AndroidAudioFocusGainType.gainTransientMayDuck,
      androidWillPauseWhenDucked: false,
    ));

    // PERMANENT setActive(true) — niemals togglen.
    try {
      await session.setActive(true);
      _log('audioSession setActive(true) ok');
    } catch (e) {
      _log('audioSession setActive ERR $e');
    }

    _initialized = true;
  }

  /// Listener für Hardware-Routing-Wechsel (Speaker ↔ Kopfhörer)
  Future<bool> isHeadphoneActive() async {
    try {
      final session = await AudioSession.instance;
      final devices = await session.getDevices(
        includeInputs: false,
        includeOutputs: true,
      );
      return devices.any(
        (d) =>
            d.isOutput &&
            (d.type == AudioDeviceType.wiredHeadset ||
                d.type == AudioDeviceType.wiredHeadphones ||
                d.type == AudioDeviceType.bluetoothA2dp ||
                d.type == AudioDeviceType.bluetoothLe ||
                d.type == AudioDeviceType.bluetoothSco),
      );
    } catch (_) {
      return false;
    }
  }

  Stream<void> get devicesChangedStream async* {
    final session = await AudioSession.instance;
    await for (final _ in session.devicesChangedEventStream) {
      yield null;
    }
  }
}
