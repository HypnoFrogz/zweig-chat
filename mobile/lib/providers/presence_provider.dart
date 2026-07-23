import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../api/auth_api.dart';
import '../websocket/ws_manager.dart';
import '../websocket/ws_events.dart';

class PresenceState {
  final Map<String, bool> onlineUsers; final Map<int, List<String>> typingUsers;
  const PresenceState({this.onlineUsers = const {}, this.typingUsers = const {}});
  PresenceState copyWith({Map<String, bool>? onlineUsers, Map<int, List<String>>? typingUsers}) =>
    PresenceState(onlineUsers: onlineUsers ?? this.onlineUsers, typingUsers: typingUsers ?? this.typingUsers);
  bool isOnline(String username) => onlineUsers[username] ?? false;
}

class PresenceNotifier extends StateNotifier<PresenceState> {
  final _ws = WsManager();
  PresenceNotifier() : super(const PresenceState()) {
    _ws.on(WsEvents.presence, (data) { final u = data['username'] as String?; final o = data['online'] as bool? ?? false; if (u != null) { final m = Map<String, bool>.from(state.onlineUsers); m[u] = o; state = state.copyWith(onlineUsers: m); } });
    _ws.on(WsEvents.userTyping, (data) {
      final ch = data['channel_id'] as int?; final u = data['username'] as String?; final t = data['is_typing'] as bool? ?? false;
      if (ch != null && u != null) { final m = Map<int, List<String>>.from(state.typingUsers.map((k, v) => MapEntry(k, List<String>.from(v)))); m.putIfAbsent(ch, () => []); if (t && !m[ch]!.contains(u)) m[ch]!.add(u); else if (!t) m[ch]!.remove(u); state = state.copyWith(typingUsers: m); }
    });
  }
  Future<void> loadPresence() async {
    try { final data = await AuthApi().getPresence(); final o = <String, bool>{}; data.forEach((k, v) { if (v is Map) o[k] = v['online'] ?? false; }); state = state.copyWith(onlineUsers: o); } catch (_) {}
  }
}

final presenceProvider = StateNotifierProvider<PresenceNotifier, PresenceState>((ref) => PresenceNotifier());
