import 'dart:async';
import 'dart:convert';
import 'package:web_socket_channel/web_socket_channel.dart';
import '../api/api_client.dart';

typedef WsEventHandler = void Function(Map<String, dynamic> data);

class WsManager {
  static final WsManager _instance = WsManager._internal();
  factory WsManager() => _instance;
  WsManager._internal();

  WebSocketChannel? _channel;
  Timer? _pingTimer;
  Timer? _reconnectTimer;
  String? _token;
  bool _disposed = false;
  bool _wasConnected = false; // true after first successful connect
  final Map<String, List<WsEventHandler>> _handlers = {};
  final ApiClient _apiClient = ApiClient();

  /// Called every time the WS successfully (re)connects.
  /// Use this to restore state that may have been missed while offline.
  void Function()? onReconnect;

  void connect(String token) { _token = token; _disposed = false; _wasConnected = false; _doConnect(); }

  Future<void> _doConnect() async {
    if (_disposed || _token == null) return;
    try {
      // Access token may expire while app is backgrounded; refresh before WS connect/reconnect.
      await _apiClient.loadToken();
      final authorized = await _apiClient.ensureAuthorized();
      if (!authorized || _apiClient.token == null) {
        _scheduleReconnect();
        return;
      }
      _token = _apiClient.token;
      final encodedToken = Uri.encodeQueryComponent(_token!);
      // Derive the WS endpoint from the configured API base (http->ws, https->wss)
      // so it follows the same server as the REST API.
      final wsBase = ApiClient.baseUrl.replaceFirst('http', 'ws');
      final uri = Uri.parse('$wsBase/ws/messaging?token=$encodedToken');
      _channel = WebSocketChannel.connect(uri);
      _channel!.stream.listen(
        _onMessage,
        onError: (_) => _scheduleReconnect(),
        onDone: () => _scheduleReconnect(),
      );
      _startPing();
      // Notify listeners of (re)connection
      Future.microtask(() {
        if (!_disposed) onReconnect?.call();
      });
      _wasConnected = true;
    } catch (_) { _scheduleReconnect(); }
  }

  void _onMessage(dynamic raw) {
    try {
      final data = jsonDecode(raw as String) as Map<String, dynamic>;
      final event = data['event'] as String?;
      if (event == null || event == 'pong') return;
      for (final h in List.of(_handlers[event] ?? [])) { h(data); }
    } catch (_) {}
  }

  void _startPing() { _pingTimer?.cancel(); _pingTimer = Timer.periodic(const Duration(seconds: 25), (_) => send({'event': 'ping'})); }
  void _scheduleReconnect() { if (_disposed) return; _pingTimer?.cancel(); _channel = null; _reconnectTimer?.cancel(); _reconnectTimer = Timer(const Duration(seconds: 3), _doConnect); }
  void send(Map<String, dynamic> data) { try { _channel?.sink.add(jsonEncode(data)); } catch (_) {} }
  void on(String event, WsEventHandler handler) { _handlers.putIfAbsent(event, () => []).add(handler); }
  void off(String event, [WsEventHandler? handler]) { if (handler != null) _handlers[event]?.remove(handler); else _handlers.remove(event); }
  void disconnect() { _disposed = true; _pingTimer?.cancel(); _reconnectTimer?.cancel(); _channel?.sink.close(); _channel = null; _handlers.clear(); onReconnect = null; }
}
