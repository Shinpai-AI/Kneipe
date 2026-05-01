import 'dart:convert';
import 'dart:typed_data';
import 'package:dio/dio.dart';

class KneipeClient {
  final Dio _dio;
  final String _baseUrl;
  String? _token;

  KneipeClient._(this._dio, this._baseUrl);

  static KneipeClient create({
    String baseUrl = 'https://bar.shinpai.de',
    String? token,
  }) {
    final dio = Dio(BaseOptions(
      baseUrl: baseUrl,
      connectTimeout: const Duration(seconds: 10),
      receiveTimeout: const Duration(seconds: 20),
      headers: {'Accept': 'application/json'},
      validateStatus: (s) => s != null && s < 500,
    ));
    final client = KneipeClient._(dio, baseUrl);
    client._token = token;
    dio.interceptors.add(InterceptorsWrapper(
      onRequest: (options, handler) {
        final t = client._token;
        if (t != null && t.isNotEmpty) {
          options.headers['Authorization'] = 'Bearer $t';
        }
        handler.next(options);
      },
    ));
    return client;
  }

  String get baseUrl => _baseUrl;
  String? get token => _token;
  bool get isAuthenticated => _token != null && _token!.isNotEmpty;

  void setToken(String? token) {
    _token = token;
  }

  Future<Map<String, dynamic>> login({
    required String name,
    required String password,
    String? totp,
  }) async {
    final r = await _dio.post('/api/login', data: {
      'name': name,
      'password': password,
      'totp': ?totp,
    });
    final body = Map<String, dynamic>.from(r.data);
    if (body['ok'] == true && body['token'] is String) {
      _token = body['token'] as String;
    }
    return body;
  }

  Future<Map<String, dynamic>> loginGuest() async {
    final r = await _dio.post('/api/guest/join');
    final body = Map<String, dynamic>.from(r.data);
    if (body['ok'] == true && body['token'] is String) {
      _token = body['token'] as String;
    }
    return body;
  }

  Future<Map<String, dynamic>> register({
    required String name,
    required String email,
    required String password,
    String? registerCode,
  }) async {
    final r = await _dio.post('/api/register', data: {
      'name': name,
      'email': email,
      'password': password,
      'register_code': ?registerCode,
    });
    return Map<String, dynamic>.from(r.data);
  }

  Future<void> logout() async {
    try {
      await _dio.post('/api/logout');
    } catch (_) {}
    _token = null;
  }

  Future<List<dynamic>> getRooms() async {
    final r = await _dio.get('/api/raeume');
    return List<dynamic>.from(r.data);
  }

  Future<Map<String, dynamic>> getBar(String raumId) async {
    final r = await _dio.get('/api/bar', queryParameters: {'raum_id': raumId});
    return Map<String, dynamic>.from(r.data);
  }

  Future<void> joinTable(String tischId) async {
    final r = await _dio.post('/api/tisch/join', data: {'tisch_id': tischId});
    _throwIfError(r.data);
  }

  Future<void> leaveTable(String tischId) async {
    final r = await _dio.post('/api/tisch/leave', data: {'tisch_id': tischId});
    _throwIfError(r.data);
  }

  Future<List<dynamic>> pollChat(String tischId, {double? since}) async {
    final params = <String, dynamic>{};
    if (since != null) params['since'] = since.toString();
    final r = await _dio.get(
      '/api/chat/poll/$tischId',
      queryParameters: params,
    );
    final data = r.data;
    if (data is Map && data['error'] != null) {
      throw KneipeApiException(data['error'].toString());
    }
    if (data is! List) {
      throw KneipeApiException('Unerwartete Antwort vom Server');
    }
    return List<dynamic>.from(data);
  }

  Future<void> sendChat(String tischId, String text) async {
    final r = await _dio.post('/api/chat/send', data: {
      'tisch_id': tischId,
      'text': text,
    });
    _throwIfError(r.data);
  }

  Future<Map<String, dynamic>?> sendVoice(
    String tischId,
    Uint8List audioBytes, {
    String filename = 'Voice',
    String filetype = 'audio/webm',
    String text = '',
  }) async {
    final dataUrl = 'data:$filetype;base64,${base64Encode(audioBytes)}';
    // voice_input: true unterdrückt server-seitige TTS-Generierung — kein Echo.
    // text: optional vom client-seitigen Whisper transkribiert.
    final r = await _dio.post(
      '/api/chat/send',
      data: {
        'tisch_id': tischId,
        'text': text,
        'voice': dataUrl,
        'voice_input': true,
      },
    );
    _throwIfError(r.data);
    final data = r.data;
    if (data is Map && data['msg'] is Map) {
      return Map<String, dynamic>.from(data['msg'] as Map);
    }
    return null;
  }

  Future<Uint8List> fetchFile(String url) async {
    final r = await _dio.get<List<int>>(
      url,
      options: Options(
        responseType: ResponseType.bytes,
        headers: {'Accept': '*/*'},
        receiveTimeout: const Duration(seconds: 8),
        sendTimeout: const Duration(seconds: 5),
      ),
    );
    return Uint8List.fromList(r.data ?? const []);
  }

  void _throwIfError(dynamic data) {
    if (data is Map && data['error'] != null) {
      throw KneipeApiException(data['error'].toString());
    }
  }

  Future<Map<String, dynamic>> getMyProfile() async {
    final r = await _dio.get('/api/profile');
    return Map<String, dynamic>.from(r.data);
  }

  void close() => _dio.close(force: true);
}

class KneipeApiException implements Exception {
  final String message;
  KneipeApiException(this.message);
  @override
  String toString() => message;
}
