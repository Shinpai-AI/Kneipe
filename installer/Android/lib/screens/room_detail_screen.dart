import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../session.dart';

final _roomDetailProvider =
    FutureProvider.autoDispose.family<Map<String, dynamic>, String>(
  (ref, raumId) async {
    final session = ref.watch(activeSessionProvider);
    if (session == null) throw StateError('Keine Verbindung');
    return session.client.getBar(raumId);
  },
);

class RoomDetailScreen extends ConsumerWidget {
  final String raumId;
  const RoomDetailScreen({super.key, required this.raumId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final detail = ref.watch(_roomDetailProvider(raumId));

    return Scaffold(
      appBar: AppBar(
        title: detail.when(
          loading: () => const Text('…'),
          error: (e, _) => const Text('Fehler'),
          data: (d) => Text(d['raum_name']?.toString() ?? raumId),
        ),
        actions: [
          IconButton(
            tooltip: 'Aktualisieren',
            icon: const Icon(Icons.refresh),
            onPressed: () => ref.invalidate(_roomDetailProvider(raumId)),
          ),
        ],
      ),
      body: detail.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Text('Fehler: $e', textAlign: TextAlign.center),
          ),
        ),
        data: (d) {
          final tische = (d['tische'] as List?) ?? const [];
          final eigenschaften =
              (d['eigenschaften'] as List?)?.cast<String>() ?? const [];

          return ListView(
            padding: const EdgeInsets.symmetric(vertical: 8),
            children: [
              if (eigenschaften.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
                  child: Wrap(
                    spacing: 6,
                    runSpacing: 4,
                    children: eigenschaften
                        .map(
                          (e) => Chip(
                            label: Text(
                              e,
                              style: const TextStyle(fontSize: 12),
                            ),
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
                ),
              if (tische.isEmpty)
                const Padding(
                  padding: EdgeInsets.all(24),
                  child: Center(child: Text('Keine Tische.')),
                ),
              ...tische.map((t) => _TableCard(table: t as Map<String, dynamic>)),
            ],
          );
        },
      ),
    );
  }
}

class _TableCard extends StatelessWidget {
  final Map<String, dynamic> table;
  const _TableCard({required this.table});

  Color _bgColor(String valenz, String intensitaet) {
    if (valenz == 'positiv') {
      return intensitaet == 'aggressiv'
          ? Colors.amber.shade50
          : Colors.green.shade50;
    }
    if (valenz == 'negativ') {
      return intensitaet == 'aggressiv'
          ? Colors.red.shade50
          : Colors.indigo.shade50;
    }
    return Colors.grey.shade100;
  }

  Color _borderColor(String valenz, String intensitaet) {
    if (valenz == 'positiv') {
      return intensitaet == 'aggressiv'
          ? Colors.amber.shade300
          : Colors.green.shade300;
    }
    if (valenz == 'negativ') {
      return intensitaet == 'aggressiv'
          ? Colors.red.shade300
          : Colors.indigo.shade300;
    }
    return Colors.grey.shade300;
  }

  @override
  Widget build(BuildContext context) {
    final thema = table['thema']?.toString() ?? '?';
    final energieEmoji = table['energie_emoji']?.toString() ?? '';
    final energieLabel = table['energie_label']?.toString() ?? '';
    final valenz = table['valenz']?.toString() ?? '';
    final intensitaet = table['intensität']?.toString() ?? '';
    final members = (table['members'] as num?)?.toInt() ?? 0;
    final max = (table['max'] as num?)?.toInt() ?? 0;
    final names = (table['names'] as List?)?.cast<String>() ?? const [];
    final voll = table['voll'] == true;
    final hasPassword = table['has_password'] == true;
    final adultOnly = table['adult_only'] == true;

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      elevation: 2,
      color: _bgColor(valenz, intensitaet),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(color: _borderColor(valenz, intensitaet), width: 1),
      ),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: voll
            ? null
            : () => context.push(
                  '/chat?tisch=${Uri.encodeComponent(table['id'].toString())}',
                ),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(thema, style: const TextStyle(fontSize: 44)),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Text(
                          energieEmoji,
                          style: const TextStyle(fontSize: 18),
                        ),
                        const SizedBox(width: 4),
                        Expanded(
                          child: Text(
                            energieLabel.isEmpty ? 'neutral' : energieLabel,
                            style: const TextStyle(
                              fontWeight: FontWeight.w600,
                              fontSize: 15,
                            ),
                          ),
                        ),
                        if (hasPassword)
                          const Padding(
                            padding: EdgeInsets.only(left: 4),
                            child: Icon(Icons.lock, size: 16),
                          ),
                        if (adultOnly)
                          const Padding(
                            padding: EdgeInsets.only(left: 4),
                            child: Text(
                              '18+',
                              style: TextStyle(
                                fontSize: 11,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                          ),
                      ],
                    ),
                    const SizedBox(height: 6),
                    Row(
                      children: [
                        Icon(
                          voll
                              ? Icons.person_off_outlined
                              : Icons.people_outline,
                          size: 16,
                          color: Colors.grey.shade700,
                        ),
                        const SizedBox(width: 4),
                        Text(
                          voll ? 'voll — $members / $max' : '$members / $max',
                          style: TextStyle(
                            fontSize: 13,
                            color: Colors.grey.shade800,
                          ),
                        ),
                      ],
                    ),
                    if (names.isNotEmpty) ...[
                      const SizedBox(height: 8),
                      Wrap(
                        spacing: 4,
                        runSpacing: 4,
                        children: names
                            .take(6)
                            .map(
                              (n) => Container(
                                padding: const EdgeInsets.symmetric(
                                  horizontal: 8,
                                  vertical: 3,
                                ),
                                decoration: BoxDecoration(
                                  color: Colors.white,
                                  borderRadius: BorderRadius.circular(12),
                                  border: Border.all(
                                    color: Colors.grey.shade300,
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
                    ],
                  ],
                ),
              ),
              Icon(
                voll ? Icons.block : Icons.chevron_right,
                color: voll ? Colors.grey : Colors.grey.shade700,
              ),
            ],
          ),
        ),
      ),
    );
  }
}
