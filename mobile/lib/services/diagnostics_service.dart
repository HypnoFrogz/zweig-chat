import 'dart:io';
import 'package:flutter/services.dart';
import 'package:permission_handler/permission_handler.dart';

enum DiagnosticStatus { ok, warning, error }

/// A single diagnostic check result.
class DiagnosticItem {
  final String id;
  final String title;
  final String description;
  final DiagnosticStatus status;

  /// Called when the user taps "Fix". Opens a system dialog or settings page.
  final Future<void> Function()? fix;

  /// Label for the fix button (default: "Исправить")
  final String fixLabel;

  const DiagnosticItem({
    required this.id,
    required this.title,
    required this.description,
    required this.status,
    this.fix,
    this.fixLabel = 'Исправить',
  });
}

class DiagnosticsService {
  static final DiagnosticsService _instance = DiagnosticsService._internal();
  factory DiagnosticsService() => _instance;
  DiagnosticsService._internal();

  static const _channel = MethodChannel('ru.nebenan/diagnostics');

  // ─── Run all checks ───────────────────────────────────────────────────────

  Future<List<DiagnosticItem>> runAll() async {
    final results = <DiagnosticItem>[];

    if (!Platform.isAndroid) return results; // iOS handles permissions differently

    final deviceInfo = await _getDeviceInfo();
    final manufacturer = (deviceInfo['manufacturer'] as String? ?? '').toLowerCase();
    final sdkInt = deviceInfo['sdkInt'] as int? ?? 0;

    // 1. Notifications permission (Android 13+)
    results.add(await _checkNotifications(sdkInt));

    // 2. Camera permission
    results.add(await _checkCamera());

    // 3. Microphone permission
    results.add(await _checkMicrophone());

    // 4. Battery optimization
    results.add(await _checkBatteryOptimization());

    // 5. Full-screen intent (Android 14+)
    if (sdkInt >= 34) {
      results.add(await _checkFullScreenIntent());
    }

    // 6. Google Play Services
    results.add(await _checkGms());

    // 7. Manufacturer-specific autostart (Huawei, Xiaomi, OPPO, Vivo)
    final needsAutostart = manufacturer.contains('huawei') ||
        manufacturer.contains('honor') ||
        manufacturer.contains('xiaomi') ||
        manufacturer.contains('redmi') ||
        manufacturer.contains('oppo') ||
        manufacturer.contains('vivo');
    if (needsAutostart) {
      results.add(_checkAutostart(manufacturer));
    }

    return results;
  }

  // ─── Individual checks ────────────────────────────────────────────────────

  Future<DiagnosticItem> _checkNotifications(int sdkInt) async {
    if (sdkInt < 33) {
      return const DiagnosticItem(
        id: 'notifications',
        title: 'Уведомления',
        description: 'Разрешены системой',
        status: DiagnosticStatus.ok,
      );
    }
    final status = await Permission.notification.status;
    if (status.isGranted) {
      return const DiagnosticItem(
        id: 'notifications',
        title: 'Уведомления',
        description: 'Разрешение выдано',
        status: DiagnosticStatus.ok,
      );
    }
    return DiagnosticItem(
      id: 'notifications',
      title: 'Уведомления',
      description: 'Без этого разрешения вы не получите ни одного уведомления',
      status: DiagnosticStatus.error,
      fixLabel: 'Разрешить',
      fix: () async {
        final result = await Permission.notification.request();
        if (result.isPermanentlyDenied) await openAppSettings();
      },
    );
  }

  Future<DiagnosticItem> _checkCamera() async {
    final status = await Permission.camera.status;
    if (status.isGranted) {
      return const DiagnosticItem(
        id: 'camera',
        title: 'Камера',
        description: 'Разрешение выдано',
        status: DiagnosticStatus.ok,
      );
    }
    return DiagnosticItem(
      id: 'camera',
      title: 'Камера',
      description: 'Нужна для видеозвонков',
      status: DiagnosticStatus.warning,
      fixLabel: 'Разрешить',
      fix: () async {
        final result = await Permission.camera.request();
        if (result.isPermanentlyDenied) await openAppSettings();
      },
    );
  }

  Future<DiagnosticItem> _checkMicrophone() async {
    final status = await Permission.microphone.status;
    if (status.isGranted) {
      return const DiagnosticItem(
        id: 'microphone',
        title: 'Микрофон',
        description: 'Разрешение выдано',
        status: DiagnosticStatus.ok,
      );
    }
    return DiagnosticItem(
      id: 'microphone',
      title: 'Микрофон',
      description: 'Нужен для аудио в видеозвонках',
      status: DiagnosticStatus.warning,
      fixLabel: 'Разрешить',
      fix: () async {
        final result = await Permission.microphone.request();
        if (result.isPermanentlyDenied) await openAppSettings();
      },
    );
  }

  Future<DiagnosticItem> _checkBatteryOptimization() async {
    try {
      final ignored = await _channel.invokeMethod<bool>('isBatteryOptimizationIgnored') ?? false;
      if (ignored) {
        return const DiagnosticItem(
          id: 'battery',
          title: 'Оптимизация батареи',
          description: 'Приложение в белом списке',
          status: DiagnosticStatus.ok,
        );
      }
      return DiagnosticItem(
        id: 'battery',
        title: 'Оптимизация батареи',
        description:
            'Система может убить приложение в фоне — уведомления и звонки перестанут приходить',
        status: DiagnosticStatus.error,
        fixLabel: 'Отключить ограничения',
        fix: () async {
          await _channel.invokeMethod('requestIgnoreBatteryOptimization');
        },
      );
    } catch (_) {
      return const DiagnosticItem(
        id: 'battery',
        title: 'Оптимизация батареи',
        description: 'Не удалось проверить',
        status: DiagnosticStatus.warning,
      );
    }
  }

  Future<DiagnosticItem> _checkFullScreenIntent() async {
    try {
      final allowed = await _channel.invokeMethod<bool>('canUseFullScreenIntent') ?? true;
      if (allowed) {
        return const DiagnosticItem(
          id: 'fullscreen',
          title: 'Экран блокировки (звонки)',
          description: 'Уведомления о звонке показываются на экране блокировки',
          status: DiagnosticStatus.ok,
        );
      }
      return DiagnosticItem(
        id: 'fullscreen',
        title: 'Экран блокировки (звонки)',
        description:
            'На Android 14+ нужно отдельное разрешение, чтобы звонок будил экран',
        status: DiagnosticStatus.warning,
        fixLabel: 'Разрешить',
        fix: () async {
          await _channel.invokeMethod('openFullScreenIntentSettings');
        },
      );
    } catch (_) {
      return const DiagnosticItem(
        id: 'fullscreen',
        title: 'Экран блокировки (звонки)',
        description: 'Не удалось проверить',
        status: DiagnosticStatus.warning,
      );
    }
  }

  Future<DiagnosticItem> _checkGms() async {
    try {
      final available = await _channel.invokeMethod<bool>('isGmsAvailable') ?? false;
      if (available) {
        return const DiagnosticItem(
          id: 'gms',
          title: 'Google Play Services',
          description: 'Установлены — push-уведомления работают',
          status: DiagnosticStatus.ok,
        );
      }
      return const DiagnosticItem(
        id: 'gms',
        title: 'Google Play Services',
        description:
            'Не найдены (Huawei без GMS). Push-уведомления через FCM не будут работать. '
            'Используйте приложение в активном режиме или обратитесь к администратору.',
        status: DiagnosticStatus.warning,
      );
    } catch (_) {
      return const DiagnosticItem(
        id: 'gms',
        title: 'Google Play Services',
        description: 'Не удалось проверить',
        status: DiagnosticStatus.warning,
      );
    }
  }

  DiagnosticItem _checkAutostart(String manufacturer) {
    String brand = 'производителя';
    if (manufacturer.contains('huawei') || manufacturer.contains('honor')) brand = 'Huawei';
    if (manufacturer.contains('xiaomi') || manufacturer.contains('redmi')) brand = 'Xiaomi/MIUI';
    if (manufacturer.contains('oppo')) brand = 'OPPO';
    if (manufacturer.contains('vivo')) brand = 'Vivo';

    return DiagnosticItem(
      id: 'autostart',
      title: 'Автозапуск ($brand)',
      description:
          '$brand блокирует фоновые приложения. Разрешите автозапуск Nebenan, '
          'чтобы получать уведомления и звонки в фоне.',
      status: DiagnosticStatus.warning,
      fixLabel: 'Открыть настройки',
      fix: () async {
        await _channel.invokeMethod('openAutostartSettings');
      },
    );
  }

  // ─── Helpers ──────────────────────────────────────────────────────────────

  Future<Map<String, dynamic>> _getDeviceInfo() async {
    try {
      final result = await _channel.invokeMapMethod<String, dynamic>('getDeviceInfo');
      return result ?? {};
    } catch (_) {
      return {};
    }
  }

  /// Returns true if all critical checks pass (no errors)
  Future<bool> allCriticalOk() async {
    final items = await runAll();
    return !items.any((i) => i.status == DiagnosticStatus.error);
  }
}
