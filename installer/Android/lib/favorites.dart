import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

class Favorite {
  final String label;
  final String url;
  const Favorite({required this.label, required this.url});

  Map<String, dynamic> toJson() => {'label': label, 'url': url};
  factory Favorite.fromJson(Map<String, dynamic> j) =>
      Favorite(label: j['label'] as String, url: j['url'] as String);
}

class FavoritesNotifier extends AsyncNotifier<List<Favorite>> {
  static const _key = 'favorites_v1';
  static const _defaults = [
    Favorite(label: 'Shinpai-Kneipe', url: 'https://bar.shinpai.de'),
  ];

  @override
  Future<List<Favorite>> build() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_key);
    if (raw == null) {
      await _write(prefs, _defaults);
      return _defaults;
    }
    final list = (jsonDecode(raw) as List).cast<Map<String, dynamic>>();
    return list.map(Favorite.fromJson).toList();
  }

  Future<void> _write(SharedPreferences prefs, List<Favorite> list) async {
    await prefs.setString(
      _key,
      jsonEncode(list.map((f) => f.toJson()).toList()),
    );
  }

  Future<void> add(Favorite f) async {
    final current = await future;
    final newList = [...current, f];
    state = AsyncValue.data(newList);
    final prefs = await SharedPreferences.getInstance();
    await _write(prefs, newList);
  }

  Future<void> remove(Favorite f) async {
    final current = await future;
    final newList = current.where((x) => x.url != f.url).toList();
    state = AsyncValue.data(newList);
    final prefs = await SharedPreferences.getInstance();
    await _write(prefs, newList);
  }
}

final favoritesProvider =
    AsyncNotifierProvider<FavoritesNotifier, List<Favorite>>(
  FavoritesNotifier.new,
);
