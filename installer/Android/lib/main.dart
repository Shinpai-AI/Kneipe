import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'screens/chat_screen.dart';
import 'screens/favorites_screen.dart';
import 'screens/login_screen.dart';
import 'screens/room_detail_screen.dart';
import 'screens/rooms_screen.dart';

final _router = GoRouter(
  routes: [
    GoRoute(
      path: '/',
      builder: (_, _) => const FavoritesScreen(),
    ),
    GoRoute(
      path: '/login',
      builder: (_, _) => const LoginScreen(),
    ),
    GoRoute(
      path: '/rooms',
      builder: (_, _) => const RoomsScreen(),
    ),
    GoRoute(
      path: '/room/:raumId',
      builder: (_, state) =>
          RoomDetailScreen(raumId: state.pathParameters['raumId']!),
    ),
    GoRoute(
      path: '/chat',
      builder: (_, state) => ChatScreen(
        tischId: state.uri.queryParameters['tisch'] ?? '',
      ),
    ),
  ],
);

void main() {
  runApp(const ProviderScope(child: KneipeApp()));
}

class KneipeApp extends StatelessWidget {
  const KneipeApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: 'Kneipen-Schlägerei',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.amber),
        useMaterial3: true,
      ),
      routerConfig: _router,
    );
  }
}
