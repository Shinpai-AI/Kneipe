import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../session.dart';

final _roomsProvider = FutureProvider.autoDispose<List<dynamic>>((ref) async {
  final session = ref.watch(activeSessionProvider);
  if (session == null) return const [];
  return session.client.getRooms();
});

class RoomsScreen extends ConsumerWidget {
  const RoomsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final session = ref.watch(activeSessionProvider);
    final rooms = ref.watch(_roomsProvider);

    if (session == null) {
      return const Scaffold(
        body: Center(child: Text('Keine Kneipe verbunden.')),
      );
    }

    return Scaffold(
      appBar: AppBar(
        title: Text(session.label),
        actions: [
          IconButton(
            tooltip: 'Aktualisieren',
            icon: const Icon(Icons.refresh),
            onPressed: () => ref.invalidate(_roomsProvider),
          ),
          IconButton(
            tooltip: 'Abmelden',
            icon: const Icon(Icons.logout),
            onPressed: () async {
              await session.client.logout();
              ref.read(activeSessionProvider.notifier).disconnect();
              if (context.mounted) context.go('/');
            },
          ),
        ],
      ),
      body: rooms.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Text('Fehler: $e', textAlign: TextAlign.center),
          ),
        ),
        data: (list) {
          if (list.isEmpty) {
            return const Center(child: Text('Keine Räume.'));
          }
          return ListView.builder(
            padding: const EdgeInsets.symmetric(vertical: 8),
            itemCount: list.length,
            itemBuilder: (_, i) {
              final r = list[i] as Map<String, dynamic>;
              return _RoomCard(room: r);
            },
          );
        },
      ),
    );
  }
}

class _RoomCard extends StatelessWidget {
  final Map<String, dynamic> room;
  const _RoomCard({required this.room});

  @override
  Widget build(BuildContext context) {
    final eigenschaften =
        (room['eigenschaften'] as List?)?.cast<String>() ?? const [];
    final phaseLabel = room['phase_label']?.toString();
    final members = room['total_members'] ?? 0;
    final tische = room['tische_count'] ?? 0;

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      elevation: 2,
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () => context.push(
          '/room/${Uri.encodeComponent(room['id'].toString())}',
        ),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  const Icon(Icons.storefront, size: 28),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      room['name']?.toString() ?? 'unbenannt',
                      style: const TextStyle(
                        fontSize: 20,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                  if (phaseLabel != null && phaseLabel.isNotEmpty)
                    Chip(
                      label: Text(
                        phaseLabel,
                        style: const TextStyle(fontSize: 11),
                      ),
                      visualDensity: VisualDensity.compact,
                      padding: EdgeInsets.zero,
                      materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                    ),
                ],
              ),
              if (eigenschaften.isNotEmpty) ...[
                const SizedBox(height: 10),
                Wrap(
                  spacing: 6,
                  runSpacing: 4,
                  children: eigenschaften
                      .map(
                        (e) => Chip(
                          label: Text(e, style: const TextStyle(fontSize: 12)),
                          visualDensity: VisualDensity.compact,
                          padding: EdgeInsets.zero,
                          materialTapTargetSize:
                              MaterialTapTargetSize.shrinkWrap,
                          backgroundColor: Colors.amber.shade50,
                          side: BorderSide(color: Colors.amber.shade200),
                        ),
                      )
                      .toList(),
                ),
              ],
              const SizedBox(height: 12),
              Row(
                children: [
                  Icon(
                    Icons.people_outline,
                    size: 18,
                    color: Colors.grey.shade600,
                  ),
                  const SizedBox(width: 4),
                  Text(
                    '$members am Start',
                    style: TextStyle(color: Colors.grey.shade800),
                  ),
                  const SizedBox(width: 16),
                  Icon(
                    Icons.table_bar,
                    size: 18,
                    color: Colors.grey.shade600,
                  ),
                  const SizedBox(width: 4),
                  Text(
                    '$tische Tische',
                    style: TextStyle(color: Colors.grey.shade800),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
