import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../utils/storage.dart';

class ThemeNotifier extends StateNotifier<bool> {
  final _storage = AppStorage();
  ThemeNotifier() : super(true) { _load(); }
  Future<void> _load() async { final theme = await _storage.read(key: 'theme'); state = theme != 'light'; }
  Future<void> toggle() async { state = !state; await _storage.write(key: 'theme', value: state ? 'dark' : 'light'); }
}

final themeProvider = StateNotifierProvider<ThemeNotifier, bool>((ref) => ThemeNotifier());
