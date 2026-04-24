import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../session.dart';

class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key});

  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final _nameCtrl = TextEditingController();
  final _pwCtrl = TextEditingController();
  final _totpCtrl = TextEditingController();
  bool _loading = false;
  String? _error;
  bool _needs2fa = false;

  @override
  void dispose() {
    _nameCtrl.dispose();
    _pwCtrl.dispose();
    _totpCtrl.dispose();
    super.dispose();
  }

  Future<void> _doLogin() async {
    final session = ref.read(activeSessionProvider);
    if (session == null) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final result = await session.client.login(
        name: _nameCtrl.text.trim(),
        password: _pwCtrl.text,
        totp: _totpCtrl.text.trim().isEmpty ? null : _totpCtrl.text.trim(),
      );
      if (result['ok'] == true) {
        if (mounted) context.go('/rooms');
      } else if (result['requires_2fa'] == true) {
        setState(() {
          _needs2fa = true;
          _error = result['error']?.toString();
        });
      } else {
        setState(() {
          _error = result['error']?.toString() ?? 'Unbekannter Fehler';
        });
      }
    } catch (e) {
      setState(() {
        _error = 'Verbindungsfehler: $e';
      });
    } finally {
      if (mounted) {
        setState(() {
          _loading = false;
        });
      }
    }
  }

  Future<void> _doGuest() async {
    final session = ref.read(activeSessionProvider);
    if (session == null) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final result = await session.client.loginGuest();
      if (result['ok'] == true) {
        if (mounted) context.go('/rooms');
      } else {
        setState(() {
          _error =
              result['error']?.toString() ?? 'Gast-Login fehlgeschlagen';
        });
      }
    } catch (e) {
      setState(() {
        _error = 'Verbindungsfehler: $e';
      });
    } finally {
      if (mounted) {
        setState(() {
          _loading = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = ref.watch(activeSessionProvider);
    if (session == null) {
      return const Scaffold(
        body: Center(child: Text('Keine Kneipe verbunden.')),
      );
    }

    return Scaffold(
      appBar: AppBar(title: Text(session.label)),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const SizedBox(height: 16),
            Text(
              '🍺 ${session.label}',
              style: Theme.of(context).textTheme.headlineSmall,
              textAlign: TextAlign.center,
            ),
            Text(
              session.baseUrl,
              style: const TextStyle(fontSize: 12, color: Colors.grey),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 32),
            TextField(
              controller: _nameCtrl,
              decoration: const InputDecoration(
                labelText: 'Name',
                border: OutlineInputBorder(),
                prefixIcon: Icon(Icons.person),
              ),
              autofillHints: const [AutofillHints.username],
              textInputAction: TextInputAction.next,
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _pwCtrl,
              decoration: const InputDecoration(
                labelText: 'Passwort',
                border: OutlineInputBorder(),
                prefixIcon: Icon(Icons.lock),
              ),
              obscureText: true,
              autofillHints: const [AutofillHints.password],
              onSubmitted: (_) => _loading ? null : _doLogin(),
            ),
            if (_needs2fa) ...[
              const SizedBox(height: 12),
              TextField(
                controller: _totpCtrl,
                decoration: const InputDecoration(
                  labelText: '2FA Code',
                  border: OutlineInputBorder(),
                  prefixIcon: Icon(Icons.shield),
                ),
                keyboardType: TextInputType.number,
                autofocus: true,
              ),
            ],
            if (_error != null) ...[
              const SizedBox(height: 12),
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: Colors.red.shade50,
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Text(
                  _error!,
                  style: TextStyle(color: Colors.red.shade700),
                ),
              ),
            ],
            const SizedBox(height: 24),
            FilledButton.icon(
              icon: _loading
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.login),
              label: const Text('Einloggen'),
              onPressed: _loading ? null : _doLogin,
            ),
            const SizedBox(height: 8),
            const Row(
              children: [
                Expanded(child: Divider()),
                Padding(
                  padding: EdgeInsets.symmetric(horizontal: 8),
                  child: Text('oder'),
                ),
                Expanded(child: Divider()),
              ],
            ),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              icon: const Icon(Icons.person_outline),
              label: const Text('Als Gast beitreten'),
              onPressed: _loading ? null : _doGuest,
            ),
          ],
        ),
      ),
    );
  }
}
