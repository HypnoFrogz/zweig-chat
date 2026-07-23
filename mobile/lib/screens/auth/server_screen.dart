import 'package:flutter/material.dart';
import '../../api/api_client.dart';
import '../../theme/colors.dart';
import 'login_screen.dart';

/// First-run screen: the user enters the server address (Mattermost-style).
/// Validated via /api/health, stored, then hands off to the login screen.
class ServerScreen extends StatefulWidget {
  const ServerScreen({super.key});

  @override
  State<ServerScreen> createState() => _ServerScreenState();
}

class _ServerScreenState extends State<ServerScreen> {
  final _controller = TextEditingController();
  bool _loading = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    // Pre-fill with the current base (minus the /api suffix), if any.
    final base = ApiClient.baseUrl;
    if (base.isNotEmpty) {
      _controller.text = base.replaceFirst(RegExp(r'/api$'), '');
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _connect() async {
    final raw = _controller.text.trim();
    if (raw.isEmpty) {
      setState(() => _error = 'Введите адрес сервера');
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    final ok = await ApiClient().pingServer(raw);
    if (!mounted) return;
    if (ok) {
      await ApiClient().setServer(raw);
      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const LoginScreen()),
      );
    } else {
      setState(() {
        _loading = false;
        _error = 'Сервер недоступен. Проверьте адрес';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = AppColors.of(context);
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(32),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Container(
                  width: 80,
                  height: 80,
                  decoration: BoxDecoration(
                    color: c.accent,
                    borderRadius: BorderRadius.circular(22),
                  ),
                  child: Icon(Icons.chat_bubble_rounded, color: c.onAccent, size: 40),
                ),
                const SizedBox(height: 16),
                Text('Nebenan',
                    style: TextStyle(fontSize: 28, fontWeight: FontWeight.w700, color: c.text)),
                const SizedBox(height: 4),
                Text('Мессенджер', style: TextStyle(fontSize: 14, color: c.text2)),
                const SizedBox(height: 40),
                Text('Подключение к серверу',
                    style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600, color: c.text)),
                const SizedBox(height: 6),
                Text(
                  'Введите адрес или IP сервера Nebenan',
                  textAlign: TextAlign.center,
                  style: TextStyle(fontSize: 13, color: c.text2),
                ),
                const SizedBox(height: 24),
                TextField(
                  controller: _controller,
                  autocorrect: false,
                  enableSuggestions: false,
                  keyboardType: TextInputType.url,
                  textInputAction: TextInputAction.go,
                  onSubmitted: (_) => _connect(),
                  decoration: InputDecoration(
                    hintText: 'напр., 192.168.1.10 или chat.example.com',
                    prefixIcon: const Icon(Icons.dns_outlined),
                  ),
                ),
                if (_error != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Text(_error!, style: TextStyle(color: c.danger, fontSize: 14)),
                  ),
                const SizedBox(height: 20),
                SizedBox(
                  width: double.infinity,
                  height: 50,
                  child: FilledButton(
                    onPressed: _loading ? null : _connect,
                    child: _loading
                        ? SizedBox(
                            width: 22, height: 22,
                            child: CircularProgressIndicator(strokeWidth: 2, color: c.onAccent),
                          )
                        : const Text('Продолжить', style: TextStyle(fontSize: 16)),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
