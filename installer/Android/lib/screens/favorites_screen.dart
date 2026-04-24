import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../favorites.dart';
import '../session.dart';

class FavoritesScreen extends ConsumerWidget {
  const FavoritesScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final favs = ref.watch(favoritesProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('🍺 Meine Kneipen'),
      ),
      body: favs.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Text('Fehler: $e', textAlign: TextAlign.center),
          ),
        ),
        data: (list) {
          if (list.isEmpty) {
            return const Center(
              child: Padding(
                padding: EdgeInsets.all(24),
                child: Text(
                  'Noch keine Kneipe gespeichert. Tipp auf +',
                  textAlign: TextAlign.center,
                ),
              ),
            );
          }
          return ListView.builder(
            padding: const EdgeInsets.all(8),
            itemCount: list.length,
            itemBuilder: (_, i) {
              final f = list[i];
              return Card(
                child: ListTile(
                  leading: const Text('🍺', style: TextStyle(fontSize: 32)),
                  title: Text(f.label),
                  subtitle: Text(
                    f.url,
                    style: const TextStyle(fontSize: 12),
                  ),
                  trailing: IconButton(
                    icon: const Icon(Icons.delete_outline),
                    onPressed: () =>
                        ref.read(favoritesProvider.notifier).remove(f),
                  ),
                  onTap: () {
                    ref
                        .read(activeSessionProvider.notifier)
                        .connect(baseUrl: f.url, label: f.label);
                    context.push('/login');
                  },
                ),
              );
            },
          );
        },
      ),
      floatingActionButton: FloatingActionButton.extended(
        icon: const Icon(Icons.add),
        label: const Text('Kneipe hinzufügen'),
        onPressed: () => _showAddSheet(context, ref),
      ),
    );
  }

  Future<void> _showAddSheet(BuildContext context, WidgetRef ref) async {
    final labelCtrl = TextEditingController();
    final urlCtrl = TextEditingController(text: 'https://');

    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => Padding(
        padding: EdgeInsets.only(
          left: 16,
          right: 16,
          top: 16,
          bottom: 16 + MediaQuery.of(ctx).viewInsets.bottom,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              'Neue Kneipe',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 16),
            TextField(
              controller: labelCtrl,
              decoration: const InputDecoration(
                labelText: 'Name (z.B. Shinpai-Kneipe)',
                border: OutlineInputBorder(),
              ),
              autofocus: true,
            ),
            const SizedBox(height: 12),
            TextField(
              controller: urlCtrl,
              decoration: const InputDecoration(
                labelText: 'URL (z.B. https://bar.shinpai.de)',
                border: OutlineInputBorder(),
              ),
              keyboardType: TextInputType.url,
            ),
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: () => Navigator.pop(ctx),
                    child: const Text('Abbrechen'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: FilledButton(
                    onPressed: () {
                      final label = labelCtrl.text.trim();
                      final url = urlCtrl.text.trim();
                      if (label.isEmpty || url.isEmpty) return;
                      ref
                          .read(favoritesProvider.notifier)
                          .add(Favorite(label: label, url: url));
                      Navigator.pop(ctx);
                    },
                    child: const Text('Speichern'),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
