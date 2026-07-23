import 'package:flutter/material.dart';
import 'package:flutter/widgets.dart' show WidgetsBinding, AppLifecycleState;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'dart:async';
import 'providers/auth_provider.dart';
import 'providers/theme_provider.dart';
import 'providers/channels_provider.dart';
import 'theme/app_theme.dart';
import 'screens/auth/login_screen.dart';
import 'screens/auth/server_screen.dart';
import 'screens/chat/channel_list_screen.dart';
import 'screens/chat/chat_screen.dart';
import 'screens/files/file_manager_screen.dart';
import 'screens/profile/profile_screen.dart';
import 'screens/admin/admin_screen.dart';
import 'screens/calls/call_screen.dart';
import 'dart:io';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:flutter_callkit_incoming/flutter_callkit_incoming.dart';
import 'package:flutter_callkit_incoming/entities/entities.dart';
import 'websocket/ws_manager.dart';
import 'websocket/ws_events.dart';
import 'services/notification_service.dart';
import 'services/diagnostics_service.dart';
import 'screens/settings/permissions_screen.dart';
import 'package:dio/dio.dart';
import 'api/api_client.dart';
import 'api/calls_api.dart';
import 'api/channels_api.dart';
import 'models/channel.dart';

/// Global navigator key for navigating from notification taps
final GlobalKey<NavigatorState> navigatorKey = GlobalKey<NavigatorState>();

class NebenanApp extends ConsumerWidget {
  const NebenanApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final authState = ref.watch(authProvider);
    final isDark = ref.watch(themeProvider);

    return MaterialApp(
      navigatorKey: navigatorKey,
      title: 'Nebenan',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light(),
      darkTheme: AppTheme.dark(),
      themeMode: isDark ? ThemeMode.dark : ThemeMode.light,
      home: authState.token != null
          ? const MainScreen()
          : (ApiClient().hasServer ? const LoginScreen() : const ServerScreen()),
    );
  }
}

class MainScreen extends ConsumerStatefulWidget {
  const MainScreen({super.key});

  @override
  ConsumerState<MainScreen> createState() => _MainScreenState();
}

class _MainScreenState extends ConsumerState<MainScreen>
    with WidgetsBindingObserver {
  int _currentIndex = 0;
  final _ws = WsManager();
  final _callsApi = CallsApi();
  Map<String, dynamic>? _incomingCall;
  bool _inCall = false;
  bool _isAcceptingCall = false;
  String? _acceptingCallId;
  // Call IDs we've already begun answering. Android delivers "accept" via
  // several handlers (callkit event + launch check + overlay); this set makes
  // duplicate accepts a no-op so we never fire a second /calls/answer.
  final Set<String> _handledAnswerCallIds = {};
  Timer? _incomingCallTimeoutTimer;

  // Overlay entry for the global incoming call banner
  OverlayEntry? _callOverlayEntry;

  @override
  void initState() {
    super.initState();
    _ws.on(WsEvents.callInvite, _onCallStarted);
    _ws.on(WsEvents.callStarted, _onCallStarted); // backward compatibility
    _ws.on(WsEvents.callEnded, _onCallEnded);
    _ws.on(WsEvents.callAnswered, _onCallAnswered);

    // On every WS reconnect check if a call started while we were offline
    _ws.onReconnect = _checkActiveCallOnReconnect;

    // Check for active call when app resumes from background/lock screen
    WidgetsBinding.instance.addObserver(this);

    _setupNotificationCallbacks();

    // Bring up background services one at a time, each guarded, so a failing
    // plugin can't take down the chat screen.
    _bootstrapServices();
  }

  Future<void> _bootstrapServices() async {
    try { ref.read(authProvider.notifier).connectWebSocket(); } catch (_) {}
    try { await NotificationService().initLocalNotifications(); } catch (_) {}
    try { await NotificationService().initialize(); } catch (_) {}
    try { await NotificationService().clearBadge(); } catch (_) {}
    if (Platform.isAndroid) {
      try { FlutterCallkitIncoming.onEvent.listen(_handleCallkitEvent); } catch (_) {}
      try { await _checkCallkitOnStart(); } catch (_) {}
    }
    try { await _maybeShowPermissions(); } catch (_) {}
  }

  /// Called once after first frame: handles the case where the app was launched
  /// by accepting a callkit notification while the app was killed.
  Future<void> _checkCallkitOnStart() async {
    if (!mounted || _inCall || _incomingCall != null) return;
    try {
      final activeCalls = await FlutterCallkitIncoming.activeCalls();
      if (activeCalls is! List || activeCalls.isEmpty) return;
      // App was opened via callkit accept — retrieve the saved FCM invite data
      final pending = await NotificationService().consumePendingCallInvite();
      if (pending == null || pending.isEmpty) return;
      if (!mounted || _inCall || _incomingCall != null) return;
      final normalized = Map<String, dynamic>.from(pending);
      normalized['started_by_name'] ??= normalized['caller_name'] ?? 'Call';
      normalized['started_by'] ??= normalized['caller_name'] ?? 'Call';
      setState(() => _incomingCall = normalized);
      _acceptCall();
    } catch (e) {
      print('[App] _checkCallkitOnStart error: $e');
    }
  }

  /// Called every time WS (re)connects. Asks the server whether there is an
  /// active incoming call we might have missed while the connection was down.
  Future<void> _checkActiveCallOnReconnect() async {
    if (!mounted || _inCall) return;
    try {
      final response = await ApiClient().dio.get('/videocall/active');
      if (response.data['active'] == true) {
        final data = Map<String, dynamic>.from(response.data as Map);
        // Treat this exactly like a fresh call_started event
        _onCallStarted(data);
      }
    } catch (_) {
      // Ignore errors — the endpoint may not be reachable yet right after reconnect
    }
  }

  void _setupNotificationCallbacks() {
    final ns = NotificationService();

    // When user taps a message notification → open that chat
    ns.onMessageTap = (channelSlug) {
      _navigateToChat(channelSlug);
    };

    // When user taps call notification or "Accept" button → accept the call
    ns.onCallAccept = (callData) {
      if (_incomingCall != null) {
        // Overlay is already visible — accept through it
        _acceptCall();
      } else {
        // Coming from background/killed state.
        // FCM payload already contains livekit_token + livekit_url — use directly.
        // Normalize field names (FCM uses caller_name, WS uses started_by_name).
        final normalized = Map<String, dynamic>.from(callData);
        if (normalized['started_by_name'] == null) {
          normalized['started_by_name'] = normalized['caller_name'] ?? 'Call';
        }
        if (normalized['started_by'] == null) {
          normalized['started_by'] = normalized['caller_name'] ?? 'Call';
        }
        setState(() => _incomingCall = normalized);
        _acceptCall();
      }
    };

    // When user taps "Reject" button on call notification
    ns.onCallReject = (callData) {
      if (_incomingCall != null) {
        _declineCall();
      } else {
        final channelSlug = callData['channel_slug'] as String?;
        final callId = callData['call_id'] as String?;
        if (callId != null && callId.isNotEmpty) {
          _callsApi.rejectCall(callId).catchError((_) {});
        } else if (channelSlug != null) {
          ApiClient().dio.post('/videocall/reject', data: {'channel_slug': channelSlug}).catchError((_) {});
        }
        NotificationService().cancelCallNotification();
        NotificationService().stopRingtone();
      }
    };
  }

  /// Navigate to a specific chat by channel slug (from notification tap)
  Future<void> _navigateToChat(String channelSlug) async {
    try {
      final channelsApi = ChannelsApi();
      final data = await channelsApi.getChannel(channelSlug);
      final channel = Channel.fromJson(data);

      // Select the channel in provider
      ref.read(channelsProvider.notifier).selectChannel(channel);

      // Navigate using the global navigator key
      final nav = navigatorKey.currentState;
      if (nav != null) {
        nav.push(
          MaterialPageRoute(builder: (_) => ChatScreen(channel: channel)),
        );
      }
    } catch (e) {
      print('[App] _navigateToChat error: $e');
    }
  }

  void _showIncomingCallFromNotification(Map<String, dynamic> callData) {
    if (!mounted || _inCall) return;
    final callId = callData['call_id'] as String?;
    if (callId != null &&
        _incomingCall != null &&
        _incomingCall!['call_id']?.toString() == callId) {
      return;
    }
    _setIncomingCall(Map<String, dynamic>.from(callData));
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _ws.off(WsEvents.callStarted, _onCallStarted);
    _ws.off(WsEvents.callEnded, _onCallEnded);
    _ws.off(WsEvents.callAnswered, _onCallAnswered);
    _ws.onReconnect = null;
    _removeCallOverlay();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // When the app comes to foreground (unlocked screen, alt-tab, etc.)
    // check if there's an incoming call we missed while backgrounded
    if (state == AppLifecycleState.resumed && _incomingCall == null && !_inCall) {
      _checkActiveCallOnReconnect();
    }
  }

  /// Shows PermissionsScreen on first launch or when critical checks fail.
  Future<void> _maybeShowPermissions() async {
    if (!mounted) return;
    try {
      final prefs = await SharedPreferences.getInstance();
      final lastCheck = prefs.getInt('permissions_last_check') ?? 0;
      final now = DateTime.now().millisecondsSinceEpoch;
      // Re-check at most once per day (86400000 ms)
      final shouldCheck = (now - lastCheck) > 86400000;
      if (!shouldCheck) return;

      final allOk = await DiagnosticsService().allCriticalOk();
      await prefs.setInt('permissions_last_check', now);

      if (!mounted) return;
      if (!allOk) {
        Navigator.of(context).push(
          MaterialPageRoute(
            builder: (_) => const PermissionsScreen(isOnboarding: true),
          ),
        );
      }
    } catch (_) {}
  }

  /// Handles events from flutter_callkit_incoming (Android only).
  void _handleCallkitEvent(CallEvent? event) {
    if (event == null || !mounted) return;
    final extra = event.body['extra'] as Map?;

    // callkit id is the call_id we passed to showCallkitIncoming
    final callkitId = event.body['id']?.toString();

    switch (event.event) {
      case Event.actionCallAccept:
        if (_incomingCall != null) {
          _acceptCall();
        } else {
          // App was in background — build call data from extra
          final normalized = <String, dynamic>{};
          if (extra != null) normalized.addAll(Map<String, dynamic>.from(extra));
          // callkit id == call_id
          if (callkitId != null) normalized['call_id'] ??= callkitId;
          normalized['started_by_name'] ??= normalized['caller_name'] ?? 'Call';
          normalized['started_by']      ??= normalized['caller_name'] ?? 'Call';
          setState(() => _incomingCall = normalized);
          _acceptCall();
        }
        break;
      case Event.actionCallDecline:
        if (_incomingCall != null) {
          _declineCall();
        } else if (extra != null) {
          final callId = extra['call_id']?.toString();
          final channelSlug = extra['channel_slug'] as String?;
          if (callId != null && callId.isNotEmpty) {
            _callsApi.rejectCall(callId).catchError((_) {});
          } else if (channelSlug != null) {
            ApiClient().dio
                .post('/videocall/reject', data: {'channel_slug': channelSlug})
                .catchError((_) {});
          }
          NotificationService().cancelCallNotification();
        }
        break;
      case Event.actionCallEnded:
        _clearIncomingCall();
        break;
      default:
        break;
    }
  }

  void _onCallStarted(Map<String, dynamic> data) {
    if (!mounted) return;
    final currentUser = ref.read(authProvider).user?.username;
    // Ignore if we started the call or already in a call
    if (data['started_by'] == currentUser || _inCall) return;
    // Deduplicate: if we already show this exact call, don't show it again
    final incomingSlug = data['channel_slug'] as String?;
    if (_incomingCall != null && _incomingCall!['channel_slug'] == incomingSlug) return;

    setState(() => _incomingCall = data);

    if (Platform.isAndroid) {
      // Foreground (WS path): show only the in-app overlay.
      // Callkit is shown by the FCM background handler when app is not running.
      _showCallOverlay(data);
      NotificationService().playRingtone();
    } else {
      // iOS: overlay + local notification + ringtone
      _showCallOverlay(data);
      NotificationService().showCallNotification(data);
      NotificationService().playRingtone();
    }
  }

  void _onCallEnded(Map<String, dynamic> data) {
    if (!mounted) return;
    final endedCallId = data['call_id']?.toString();
    final endedChannel = data['channel_slug'] ?? data['channel_id'];

    // Allow a future call with this id to be answered again.
    if (endedCallId != null && endedCallId.isNotEmpty) {
      _handledAnswerCallIds.remove(endedCallId);
    }

    // On Android always end any active callkit notification for this call
    if (Platform.isAndroid && endedCallId != null && endedCallId.isNotEmpty) {
      FlutterCallkitIncoming.endCall(endedCallId).catchError((_) {});
    }

    // Dismiss incoming call UI if we have one showing.
    // Match by call_id first (надёжнее: событие call_ended от /calls/end
    // содержит call_id, но не всегда channel_slug), затем по каналу.
    if (_incomingCall != null) {
      final callChannel = _incomingCall!['channel_slug'] ?? _incomingCall!['channel_id'];
      final myCallId = _incomingCall!['call_id']?.toString();
      final matchesById = endedCallId != null && endedCallId.isNotEmpty && myCallId == endedCallId;
      final matchesByChannel = endedChannel != null && callChannel == endedChannel;
      if (matchesById || matchesByChannel) {
        _incomingCallTimeoutTimer?.cancel();
        _incomingCallTimeoutTimer = null;
        setState(() => _incomingCall = null);
        NotificationService().stopRingtone();
        NotificationService().cancelCallNotification();
        _removeCallOverlay();
      }
    }
  }

  void _onCallAnswered(Map<String, dynamic> data) {
    if (!mounted) return;
    // Someone answered the call on another device — dismiss incoming call UI
    if (_incomingCall != null && !_inCall) {
      if (Platform.isAndroid) {
        final ckId = _incomingCall!['call_id']?.toString();
        if (ckId != null && ckId.isNotEmpty) {
          FlutterCallkitIncoming.endCall(ckId).catchError((_) {});
        }
      }
      setState(() => _incomingCall = null);
      NotificationService().stopRingtone();
      NotificationService().cancelCallNotification();
      _removeCallOverlay();
    }
  }

  // ---- Global overlay management ----

  void _showCallOverlay(Map<String, dynamic> data) {
    _removeCallOverlay(); // Remove existing overlay if any
    final overlayState = navigatorKey.currentState?.overlay;
    if (overlayState == null) return;

    _callOverlayEntry = OverlayEntry(
      builder: (ctx) => _IncomingCallBanner(
        callData: data,
        onAccept: _acceptCall,
        onDecline: _declineCall,
      ),
    );
    overlayState.insert(_callOverlayEntry!);
  }

  void _removeCallOverlay() {
    _callOverlayEntry?.remove();
    _callOverlayEntry = null;
  }

  // ---- Call actions ----

  void _acceptCall() {
    _acceptCallInternal();
  }

  Future<void> _acceptCallInternal() async {
    if (_incomingCall == null) return;
    final data = _incomingCall!;
    final callId = data['call_id']?.toString();
    final channelSlug = data['channel_slug'] as String?;

    // Hard dedup: once we begin answering a given call_id, ignore every other
    // accept path for it. Without this a second /calls/answer hits an already
    // active call, the server returns 409, and the UI showed a false
    // "Вызов завершён" while tearing down a call that was actually connected.
    if (callId != null && callId.isNotEmpty) {
      if (_handledAnswerCallIds.contains(callId)) {
        if (_incomingCall != null && mounted) setState(() => _incomingCall = null);
        return;
      }
      _handledAnswerCallIds.add(callId);
    }

    // Guard against duplicate accepts for the same call.
    if (_isAcceptingCall &&
        callId != null &&
        callId.isNotEmpty &&
        _acceptingCallId == callId) {
      return;
    }
    _isAcceptingCall = true;
    _acceptingCallId = callId;

    setState(() => _inCall = true);
    // Clear state without calling FlutterCallkitIncoming.endCall —
    // ending callkit here interferes with navigation to CallScreen on Android.
    _incomingCallTimeoutTimer?.cancel();
    _incomingCallTimeoutTimer = null;
    setState(() => _incomingCall = null);
    NotificationService().stopRingtone();
    NotificationService().clearPendingCallInvite();
    _removeCallOverlay();

    try {
      String? token;
      String? url;
      String? roomName;

      if (callId != null && callId.isNotEmpty) {
        try {
          // Use authoritative room/token from answer response.
          final response = await _callsApi.answerCall(callId);
          token = response['livekit_token'] as String?;
          url = response['livekit_url'] as String?;
          roomName = response['room_name'] as String?;
        } on DioException catch (e) {
          if (e.response?.statusCode == 409) {
            // Call already ended by the caller — treat as missed call.
            print('[App] _acceptCall 409: call already ended');
            if (mounted) {
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(content: Text('Вызов завершён'), duration: Duration(seconds: 2)),
              );
            }
            setState(() { _inCall = false; _isAcceptingCall = false; _acceptingCallId = null; });
            return;
          }
          print('[App] _acceptCall answer error: $e');
        } catch (e) {
          print('[App] _acceptCall answer error: $e');
        }
      }

      // Legacy fallback for old invite payloads.
      if (token == null || url == null || roomName == null) {
        if (channelSlug != null && (callId == null || callId.isEmpty)) {
          ApiClient()
              .dio
              .post('/videocall/answer', data: {'channel_slug': channelSlug}).catchError((_) {});
        }
        token = data['livekit_token'] as String?;
        url = data['livekit_url'] as String?;
        roomName = data['room_name'] as String?;
      }

      final callerName = data['started_by_name'] ?? data['caller_name'] ?? data['started_by'] ?? data['caller_username'] ?? 'Call';

      if (token != null &&
          token.isNotEmpty &&
          url != null &&
          url.isNotEmpty &&
          roomName != null &&
          roomName.isNotEmpty) {
        final nav = navigatorKey.currentState;
        if (nav == null) {
          setState(() => _inCall = false);
          return;
        }
        nav.push(
          MaterialPageRoute(
            builder: (_) => CallScreen(
              token: token!,
              url: url!,
              roomName: roomName!,
              callId: callId,
              callsApi: _callsApi,
              channelName: callerName.toString(),
            ),
          ),
        ).then((_) {
          if (mounted) setState(() => _inCall = false);
        });
      } else {
        setState(() => _inCall = false);
      }
    } finally {
      _isAcceptingCall = false;
      _acceptingCallId = null;
    }
  }

  void _declineCall() {
    final callId = _incomingCall?['call_id']?.toString();
    final channelSlug = _incomingCall?['channel_slug'] as String?;
    setState(() => _incomingCall = null);
    NotificationService().stopRingtone();
    NotificationService().cancelCallNotification();
    _removeCallOverlay();

    // Notify server that call was rejected (ends call for 1:1 channels)
    if (callId != null && callId.isNotEmpty) {
      _callsApi.rejectCall(callId).catchError((_) {});
    } else if (channelSlug != null) {
      ApiClient().dio.post('/videocall/reject', data: {'channel_slug': channelSlug}).catchError((_) {});
    }
  }

  void _setIncomingCall(Map<String, dynamic> data) {
    final incomingId = _incomingCall?['call_id']?.toString();
    final nextId = data['call_id']?.toString();
    if (incomingId != null &&
        incomingId.isNotEmpty &&
        nextId != null &&
        incomingId == nextId) {
      _scheduleIncomingCallTimeout(data);
      return;
    }
    _incomingCallTimeoutTimer?.cancel();
    setState(() => _incomingCall = data);
    NotificationService().playRingtone();
    _scheduleIncomingCallTimeout(data);
  }

  void _clearIncomingCall() {
    _incomingCallTimeoutTimer?.cancel();
    _incomingCallTimeoutTimer = null;
    if (_incomingCall != null) {
      setState(() => _incomingCall = null);
    }
    NotificationService().stopRingtone();
    NotificationService().cancelCallNotification();
    NotificationService().clearPendingCallInvite();
  }

  void _scheduleIncomingCallTimeout(Map<String, dynamic> data) {
    final expiresAtRaw = data['expires_at']?.toString();
    if (expiresAtRaw == null || expiresAtRaw.isEmpty) return;
    final expiresAt = DateTime.tryParse(expiresAtRaw);
    if (expiresAt == null) return;
    final now = DateTime.now().toUtc();
    final expiresUtc = expiresAt.toUtc();
    final delay = expiresUtc.difference(now);
    if (delay <= Duration.zero) {
      _clearIncomingCall();
      return;
    }
    _incomingCallTimeoutTimer = Timer(delay, () {
      if (!mounted || _incomingCall == null) return;
      final currentId = _incomingCall!['call_id']?.toString();
      final targetId = data['call_id']?.toString();
      if (targetId == null || targetId.isEmpty || currentId == targetId) {
        _clearIncomingCall();
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final authState = ref.watch(authProvider);
    final isAdmin = authState.user?.role == 'admin';
    final t = ref.read(authProvider.notifier).t;

    final screens = [
      const ChannelListScreen(),
      const FileManagerScreen(),
      const ProfileScreen(),
      if (isAdmin) const AdminScreen(),
    ];

    return Scaffold(
      body: IndexedStack(index: _currentIndex, children: screens),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _currentIndex,
        onDestinationSelected: (i) => setState(() => _currentIndex = i),
        destinations: [
          NavigationDestination(
            icon: const Icon(Icons.chat_bubble_outline),
            selectedIcon: const Icon(Icons.chat_bubble),
            label: t('sidebar.chats'),
          ),
          NavigationDestination(
            icon: const Icon(Icons.folder_outlined),
            selectedIcon: const Icon(Icons.folder),
            label: t('settings.files'),
          ),
          NavigationDestination(
            icon: const Icon(Icons.person_outline),
            selectedIcon: const Icon(Icons.person),
            label: t('profile.title'),
          ),
          if (isAdmin)
            NavigationDestination(
              icon: const Icon(Icons.admin_panel_settings_outlined),
              selectedIcon: const Icon(Icons.admin_panel_settings),
              label: 'Admin',
            ),
        ],
      ),
    );
  }
}

/// Global incoming call banner widget shown via Overlay
class _IncomingCallBanner extends StatelessWidget {
  final Map<String, dynamic> callData;
  final VoidCallback onAccept;
  final VoidCallback onDecline;

  const _IncomingCallBanner({
    required this.callData,
    required this.onAccept,
    required this.onDecline,
  });

  @override
  Widget build(BuildContext context) {
    final callerName = callData['started_by_name'] ?? callData['caller_name'] ?? callData['started_by'] ?? '???';
    final topPad = MediaQuery.of(context).padding.top;

    return Positioned(
      top: 0,
      left: 0,
      right: 0,
      child: Material(
        color: Colors.transparent,
        child: Container(
          padding: EdgeInsets.only(top: topPad + 12, bottom: 16, left: 20, right: 20),
          decoration: BoxDecoration(
            color: const Color(0xFF1a1a2e),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withOpacity(0.4),
                blurRadius: 16,
                offset: const Offset(0, 4),
              )
            ],
          ),
          child: Row(
            children: [
              // Caller avatar
              CircleAvatar(
                radius: 24,
                backgroundColor: const Color(0xFF4CAF50),
                child: Text(
                  callerName.toString().isNotEmpty
                      ? callerName.toString()[0].toUpperCase()
                      : '?',
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 20,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
              const SizedBox(width: 14),
              // Caller info
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      callerName.toString(),
                      style: const TextStyle(
                        color: Colors.white,
                        fontSize: 16,
                        fontWeight: FontWeight.w600,
                      ),
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 2),
                    const Text(
                      'Входящий звонок...',
                      style: TextStyle(color: Colors.white60, fontSize: 13),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              // Decline button
              GestureDetector(
                onTap: onDecline,
                child: Container(
                  width: 48,
                  height: 48,
                  decoration: const BoxDecoration(
                    shape: BoxShape.circle,
                    color: Colors.redAccent,
                  ),
                  child: const Icon(Icons.call_end, color: Colors.white, size: 24),
                ),
              ),
              const SizedBox(width: 12),
              // Accept button
              GestureDetector(
                onTap: onAccept,
                child: Container(
                  width: 48,
                  height: 48,
                  decoration: const BoxDecoration(
                    shape: BoxShape.circle,
                    color: Colors.green,
                  ),
                  child: const Icon(Icons.call, color: Colors.white, size: 24),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
