import 'package:shared_preferences/shared_preferences.dart';

/// Cross-platform key-value storage backed by shared_preferences.
///
/// We previously used flutter_secure_storage (Keystore/Keychain) on mobile, but
/// the Android Keystore proved unreliable on some devices — reads/writes would
/// stall or crash the app natively right after login. shared_preferences is
/// rock-solid across platforms; token confidentiality on a self-hosted client
/// can be revisited later without blocking sign-in.
class AppStorage {
  static final AppStorage _instance = AppStorage._internal();
  factory AppStorage() => _instance;
  AppStorage._internal();

  Future<void> write({required String key, required String? value}) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      if (value == null) {
        await prefs.remove(key);
      } else {
        await prefs.setString(key, value);
      }
    } catch (_) {}
  }

  Future<String?> read({required String key}) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      return prefs.getString(key);
    } catch (_) {
      return null;
    }
  }

  Future<void> delete({required String key}) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(key);
    } catch (_) {}
  }
}
