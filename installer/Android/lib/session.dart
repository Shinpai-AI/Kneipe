import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'api/kneipe_client.dart';

class ActiveSession {
  final KneipeClient client;
  final String baseUrl;
  final String label;
  const ActiveSession({
    required this.client,
    required this.baseUrl,
    required this.label,
  });
}

class ActiveSessionNotifier extends Notifier<ActiveSession?> {
  @override
  ActiveSession? build() => null;

  void connect({required String baseUrl, required String label}) {
    state?.client.close();
    state = ActiveSession(
      client: KneipeClient.create(baseUrl: baseUrl),
      baseUrl: baseUrl,
      label: label,
    );
  }

  void disconnect() {
    state?.client.close();
    state = null;
  }
}

final activeSessionProvider =
    NotifierProvider<ActiveSessionNotifier, ActiveSession?>(
  ActiveSessionNotifier.new,
);
