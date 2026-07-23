import 'dart:convert';
import 'dart:io';
import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_callkit_incoming/flutter_callkit_incoming.dart';
import 'package:flutter_callkit_incoming/entities/entities.dart';
import 'package:audioplayers/audioplayers.dart';
import 'package:app_badge_plus/app_badge_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:uuid/uuid.dart';
import '../api/api_client.dart';

/// Top-level background message handler (must be top-level function)
@pragma('vm:entry-point')
Future<void> firebaseMessagingBackgroundHandler(RemoteMessage message) async {
  await Firebase.initializeApp();
  final type = message.data['type'];
  if (type == 'call' || type == 'call_invite') {
    await NotificationService.savePendingCallInvite(message.data);
    await NotificationService._showCallNotificationStatic(message.data);
  } else if (type == 'message') {
    await NotificationService._showMessageNotificationStatic(message.data, message.notification);
  }
}

class NotificationService {
  static const String _messagesChannelId = 'messages';
  static const String _pendingCallInviteKey = 'pending_call_invite';
  static final NotificationService _instance = NotificationService._internal();
  factory NotificationService() => _instance;
  NotificationService._internal();

  FirebaseMessaging? _messaging;
  final FlutterLocalNotificationsPlugin _localNotifications =
      FlutterLocalNotificationsPlugin();
  final AudioPlayer _messageAudioPlayer = AudioPlayer();
  final AudioPlayer _ringtoneAudioPlayer = AudioPlayer();

  String? _fcmToken;
  String? get fcmToken => _fcmToken;
  bool _initialized = false;
  bool _localNotificationsInitialized = false;
  bool _isCallRinging = false;
  String? _activeCallkitId;

  void Function(String channelSlug)? onMessageTap;
  void Function(Map<String, dynamic> callData)? onCallAccept;
  void Function(Map<String, dynamic> callData)? onCallReject;

  Future<void> initLocalNotifications() async {
    if (_localNotificationsInitialized) return;
    try {
      const androidInit = AndroidInitializationSettings('@mipmap/ic_launcher');
      const iosInit = DarwinInitializationSettings(
        requestAlertPermission: true,
        requestBadgePermission: true,
        requestSoundPermission: true,
      );

      await _localNotifications.initialize(
        const InitializationSettings(android: androidInit, iOS: iosInit),
        onDidReceiveNotificationResponse: _onNotificationTap,
      );

      await _createNotificationChannels();

      if (Platform.isAndroid) {
        final androidPlugin = _localNotifications
            .resolvePlatformSpecificImplementation<
                AndroidFlutterLocalNotificationsPlugin>();
        if (androidPlugin != null) {
          final granted = await androidPlugin.requestNotificationsPermission();
          print('[NotificationService] POST_NOTIFICATIONS granted: $granted');
          try {
            await androidPlugin.requestFullScreenIntentPermission();
          } catch (_) {}
        }
      }

      _localNotificationsInitialized = true;
      print('[NotificationService] Local notifications initialized');
    } catch (e) {
      print('[NotificationService] Local notifications init error: $e');
    }
  }

  Future<void> initialize() async {
    if (_initialized) return;

    await initLocalNotifications();

    try {
      await Firebase.initializeApp();
    } catch (_) {}

    try {
      _messaging = FirebaseMessaging.instance;

      FirebaseMessaging.onBackgroundMessage(firebaseMessagingBackgroundHandler);

      final settings = await _messaging!.requestPermission(
        alert: true,
        badge: true,
        sound: true,
      );

      if (settings.authorizationStatus == AuthorizationStatus.denied) {
        _initialized = true;
        return;
      }

      _fcmToken = await _messaging!.getToken();
      print('[NotificationService] FCM token: $_fcmToken');

      await _messaging!.setForegroundNotificationPresentationOptions(
        alert: false,
        badge: false,
        sound: false,
      );

      _messaging!.onTokenRefresh.listen((newToken) {
        _fcmToken = newToken;
        _registerTokenOnServer(newToken);
      });

      FirebaseMessaging.onMessage.listen(_onForegroundMessage);
      FirebaseMessaging.onMessageOpenedApp.listen(_onMessageOpenedApp);

      final initialMessage = await _messaging!.getInitialMessage();
      if (initialMessage != null) {
        _onMessageOpenedApp(initialMessage);
      }

      _initialized = true;
    } catch (e) {
      print('[NotificationService] Firebase init error: $e');
      _initialized = true;
    }
  }

  Future<void> _createNotificationChannels() async {
    final androidPlugin = _localNotifications
        .resolvePlatformSpecificImplementation<
            AndroidFlutterLocalNotificationsPlugin>();

    if (androidPlugin != null) {
      try {
        await androidPlugin.deleteNotificationChannel('calls');
        await androidPlugin.deleteNotificationChannel('calls_silent_v2');
      } catch (_) {}

      await androidPlugin.createNotificationChannel(
        const AndroidNotificationChannel(
          _messagesChannelId,
          'Messages',
          description: 'New message notifications',
          importance: Importance.high,
          enableVibration: true,
        ),
      );
    }
  }

  void _onForegroundMessage(RemoteMessage message) {
    final type = message.data['type'];
    if (type == 'message') {
      showMessageNotification(
        senderName: message.data['sender_name'] ?? '',
        text: message.notification?.body ?? '',
        channelSlug: message.data['channel_slug'] ?? '',
        channelName: message.data['channel_name'],
      );
    } else if (type == 'call' || type == 'call_invite') {
      savePendingCallInvite(message.data);
      showCallNotification(Map<String, dynamic>.from(message.data));
    }
  }

  void _onMessageOpenedApp(RemoteMessage message) {
    final type = message.data['type'];
    if (type == 'message') {
      final channelSlug = message.data['channel_slug'];
      if (channelSlug != null) onMessageTap?.call(channelSlug);
    } else if (type == 'call' || type == 'call_invite') {
      final data = Map<String, dynamic>.from(message.data);
      savePendingCallInvite(data);
      onCallAccept?.call(data);
    }
  }

  void _onNotificationTap(NotificationResponse response) {
    if (response.payload == null) return;
    try {
      final data = jsonDecode(response.payload!);
      final actionId = response.actionId;

      if (data['type'] == 'call' || data['type'] == 'call_invite') {
        savePendingCallInvite(Map<String, dynamic>.from(data));
        if (actionId == 'reject_call') {
          onCallReject?.call(Map<String, dynamic>.from(data));
        } else {
          onCallAccept?.call(Map<String, dynamic>.from(data));
        }
      } else if (data['type'] == 'message' && data['channel_slug'] != null) {
        onMessageTap?.call(data['channel_slug']);
      }
    } catch (_) {}
  }

  static Future<void> _showMessageNotificationStatic(
      Map<String, dynamic> data, RemoteNotification? notification) async {
    final plugin = FlutterLocalNotificationsPlugin();
    const androidInit = AndroidInitializationSettings('@mipmap/ic_launcher');
    const iosInit = DarwinInitializationSettings();
    await plugin.initialize(
        const InitializationSettings(android: androidInit, iOS: iosInit));

    final senderName = data['sender_name'] ?? '';
    final text = notification?.body ?? data['text'] ?? '';
    final channelSlug = data['channel_slug'] ?? '';
    final channelName = data['channel_name'];
    final preview = text.length > 100 ? '${text.substring(0, 100)}...' : text;
    final id = channelSlug.hashCode.abs() % 100000;

    await plugin.show(
      id,
      channelName ?? senderName,
      '$senderName: $preview',
      const NotificationDetails(
        android: AndroidNotificationDetails(
          _messagesChannelId,
          'Messages',
          importance: Importance.high,
          priority: Priority.high,
          playSound: true,
        ),
        iOS: DarwinNotificationDetails(
          presentAlert: true,
          presentBadge: true,
          presentSound: true,
        ),
      ),
      payload: jsonEncode({
        'type': 'message',
        'channel_slug': channelSlug,
      }),
    );
  }

  /// Background handler: show callkit on Android, local notification on iOS
  static Future<void> _showCallNotificationStatic(
      Map<String, dynamic> data) async {
    if (Platform.isAndroid) {
      final callerName = data['caller_name'] ?? 'Incoming Call';
      final callId = data['call_id'] ?? const Uuid().v4();

      final params = CallKitParams(
        id: callId,
        nameCaller: callerName,
        appName: 'Nebenan',
        type: 1,
        duration: 45000,
        textAccept: 'Ответить',
        textDecline: 'Отклонить',
        android: const AndroidParams(
          isCustomNotification: false,
          isShowFullLockedScreen: true,
          ringtonePath: 'system_ringtone_default',
          backgroundColor: '#1a1a2e',
          backgroundUrl: '',
          actionColor: '#4CAF50',
        ),
        extra: {
          'call_id': callId.toString(),
          'channel_slug': data['channel_slug'] ?? '',
          'livekit_token': data['livekit_token'] ?? '',
          'livekit_url': data['livekit_url'] ?? '',
          'room_name': data['room_name'] ?? '',
        },
      );
      await FlutterCallkitIncoming.showCallkitIncoming(params);
    } else {
      // iOS — local notification fallback (iOS not touched per requirements)
      final plugin = FlutterLocalNotificationsPlugin();
      const androidInit = AndroidInitializationSettings('@mipmap/ic_launcher');
      const iosInit = DarwinInitializationSettings();
      await plugin.initialize(
          const InitializationSettings(android: androidInit, iOS: iosInit));

      final callerName = data['caller_name'] ?? 'Incoming Call';
      await plugin.show(
        0,
        callerName,
        'Incoming call',
        const NotificationDetails(
          iOS: DarwinNotificationDetails(
            presentAlert: true,
            presentBadge: true,
            presentSound: false,
          ),
        ),
        payload: jsonEncode({
          ...data,
          'type': data['type'] ?? 'call_invite',
        }),
      );
    }
  }

  /// Show incoming call UI
  Future<void> showCallNotification(Map<String, dynamic> data) async {
    if (Platform.isAndroid) {
      final callerName = data['started_by_name'] ?? data['caller_name'] ?? 'Incoming Call';
      final callId = data['call_id'] ?? const Uuid().v4();
      _activeCallkitId = callId.toString();

      final params = CallKitParams(
        id: _activeCallkitId,
        nameCaller: callerName,
        appName: 'Nebenan',
        type: 1,
        duration: 45000,
        textAccept: 'Ответить',
        textDecline: 'Отклонить',
        android: const AndroidParams(
          isCustomNotification: false,
          isShowFullLockedScreen: true,
          ringtonePath: 'system_ringtone_default',
          backgroundColor: '#1a1a2e',
          backgroundUrl: '',
          actionColor: '#4CAF50',
        ),
        extra: {
          'call_id': callId.toString(),
          'channel_slug': data['channel_slug'] ?? '',
          'livekit_token': data['livekit_token'] ?? '',
          'livekit_url': data['livekit_url'] ?? '',
          'room_name': data['room_name'] ?? '',
        },
      );
      _isCallRinging = true;
      await FlutterCallkitIncoming.showCallkitIncoming(params);
      return;
    }

    // iOS — local notification + ringtone
    if (!_localNotificationsInitialized) await initLocalNotifications();
    final callerName = data['started_by_name'] ?? data['caller_name'] ?? 'Incoming Call';
    await _localNotifications.show(
      0,
      callerName,
      'Incoming call',
      const NotificationDetails(
        iOS: DarwinNotificationDetails(
          presentAlert: true,
          presentBadge: true,
          presentSound: false,
        ),
      ),
      payload: jsonEncode({
        ...data,
        'type': data['type'] ?? 'call_invite',
      }),
    );
    playRingtone();
  }

  Future<void> _registerTokenOnServer(String token) async {
    try {
      final client = ApiClient();
      await client.dio.post('/devices/register', data: {
        'fcm_token': token,
        'platform': Platform.isIOS ? 'ios' : 'android',
      });
    } catch (e) {
      print('[NotificationService] Token registration error: $e');
    }
  }

  Future<void> registerToken() async {
    if (_fcmToken != null) {
      await _registerTokenOnServer(_fcmToken!);
    }
  }

  Future<void> unregisterToken() async {
    if (_fcmToken != null) {
      try {
        final client = ApiClient();
        await client.dio
            .post('/devices/unregister', data: {'fcm_token': _fcmToken});
      } catch (_) {}
    }
  }

  Future<void> showMessageNotification({
    required String senderName,
    required String text,
    required String channelSlug,
    String? channelName,
  }) async {
    if (!_localNotificationsInitialized) await initLocalNotifications();
    if (_isCallRinging) return;
    try {
      final preview = text.length > 100 ? '${text.substring(0, 100)}...' : text;
      final id = channelSlug.hashCode.abs() % 100000;
      await _localNotifications.show(
        id,
        channelName ?? senderName,
        '$senderName: $preview',
        const NotificationDetails(
          android: AndroidNotificationDetails(
            _messagesChannelId,
            'Messages',
            importance: Importance.high,
            priority: Priority.high,
            playSound: true,
          ),
          iOS: DarwinNotificationDetails(
            presentAlert: true,
            presentBadge: true,
            presentSound: true,
          ),
        ),
        payload: jsonEncode({
          'type': 'message',
          'channel_slug': channelSlug,
        }),
      );
    } catch (e) {
      print('[NotificationService] showMessageNotification error: $e');
    }
  }

  Future<void> playMessageSound() async {
    if (_isCallRinging) return;
    try {
      await _messageAudioPlayer.play(AssetSource('sounds/message_received.wav'));
    } catch (e) {
      print('[NotificationService] playMessageSound error: $e');
    }
  }

  Future<void> playRingtone() async {
    _isCallRinging = true;
    try {
      await _ringtoneAudioPlayer.setReleaseMode(ReleaseMode.loop);
      await _ringtoneAudioPlayer.play(AssetSource('sounds/ringtone.wav'));
    } catch (e) {
      print('[NotificationService] playRingtone error: $e');
    }
  }

  Future<void> stopRingtone() async {
    _isCallRinging = false;
    try {
      await _ringtoneAudioPlayer.stop();
      await _ringtoneAudioPlayer.setReleaseMode(ReleaseMode.release);
    } catch (_) {}
  }

  Future<void> updateBadge(int count) async {
    try {
      AppBadgePlus.updateBadge(count);
    } catch (_) {}
  }

  Future<void> clearBadge() async {
    try {
      AppBadgePlus.updateBadge(0);
    } catch (_) {}
  }

  void cancelCallNotification() {
    if (Platform.isAndroid && _activeCallkitId != null) {
      FlutterCallkitIncoming.endCall(_activeCallkitId!).catchError((_) {});
      _activeCallkitId = null;
    }
    try {
      _localNotifications.cancel(0);
    } catch (_) {}
    _isCallRinging = false;
  }

  static Future<void> savePendingCallInvite(Map<String, dynamic> data) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_pendingCallInviteKey, jsonEncode(data));
    } catch (_) {}
  }

  Future<Map<String, dynamic>?> consumePendingCallInvite() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_pendingCallInviteKey);
      if (raw == null || raw.isEmpty) return null;
      await prefs.remove(_pendingCallInviteKey);
      final decoded = jsonDecode(raw);
      if (decoded is Map<String, dynamic>) return decoded;
      if (decoded is Map) return Map<String, dynamic>.from(decoded);
    } catch (_) {}
    return null;
  }

  Future<void> clearPendingCallInvite() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(_pendingCallInviteKey);
    } catch (_) {}
  }
}
