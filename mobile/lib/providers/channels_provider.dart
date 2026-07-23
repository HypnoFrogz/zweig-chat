import 'package:flutter/widgets.dart' show WidgetsBinding, AppLifecycleState;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../api/channels_api.dart';
import '../models/channel.dart';
import '../websocket/ws_manager.dart';
import '../websocket/ws_events.dart';
import '../services/notification_service.dart';

class ChannelsState {
  final List<Channel> channels; final Channel? currentChannel; final bool loading;
  const ChannelsState({this.channels = const [], this.currentChannel, this.loading = false});
  ChannelsState copyWith({List<Channel>? channels, Channel? currentChannel, bool? loading, bool clearCurrent = false}) =>
    ChannelsState(channels: channels ?? this.channels, currentChannel: clearCurrent ? null : (currentChannel ?? this.currentChannel), loading: loading ?? this.loading);
}

class ChannelsNotifier extends StateNotifier<ChannelsState> {
  final _api = ChannelsApi(); final _ws = WsManager();
  String? currentUsername;
  void setCurrentUser(String username) => currentUsername = username;
  ChannelsNotifier() : super(const ChannelsState()) {
    _ws.on(WsEvents.newMessage, (data) {
      // Extract channel slug from top-level field or from message payload
      final msg = data['message'];
      final msgChannel = data['channel_slug'] as String?
          ?? msg?['channel_slug'] as String?;

      // Skip notification if this is our own message
      final sender = msg?['sender']?.toString();
      final isOwnMessage = sender != null && sender == currentUsername;

      if (!isOwnMessage && msgChannel != null) {
        final isInSameChat = state.currentChannel?.slug == msgChannel;
        // Only show local notification when app is NOT in foreground.
        // In foreground the message appears directly in the chat, no notification needed.
        // FCM handles the notification when app is in background/killed.
        final lifecycle = WidgetsBinding.instance.lifecycleState;
        final isBackground = lifecycle != AppLifecycleState.resumed;

        if (!isInSameChat) {
          // Только звук — уведомление показывает FCM (notification_service),
          // чтобы не дублировать попап
          NotificationService().playMessageSound();
        }
      }
      loadChannels();
    });
    _ws.on(WsEvents.channelCreated, (_) => loadChannels());
    _ws.on(WsEvents.channelDeleted, (data) { if (state.currentChannel?.id == data['channel_id']) state = state.copyWith(clearCurrent: true); loadChannels(); });
    _ws.on(WsEvents.memberLeft, (_) => loadChannels());
  }

  Future<void> loadChannels() async {
    if (state.channels.isEmpty) {
      state = state.copyWith(loading: true);
    }
    try {
      final data = await _api.getChannels();
      final channels = data.map((j) => Channel.fromJson(j)).toList();
      state = state.copyWith(channels: channels, loading: false);
      // Update app badge with total unread count
      try {
        final totalUnread = channels.fold<int>(0, (sum, ch) => sum + ch.unreadCount);
        NotificationService().updateBadge(totalUnread);
      } catch (_) {}
    } catch (e) {
      state = state.copyWith(loading: false);
      print('[ChannelsProvider] loadChannels error: $e');
    }
  }
  Future<void> selectChannel(Channel ch) async { state = state.copyWith(currentChannel: ch); try { await _api.markRead(ch.slug); await loadChannels(); } catch (_) {} }
  Future<void> deleteChannel(String slug) async { await _api.deleteChannel(slug); if (state.currentChannel?.slug == slug) state = state.copyWith(clearCurrent: true); await loadChannels(); }
  Future<void> leaveChannel(String slug) async { await _api.leaveChannel(slug); if (state.currentChannel?.slug == slug) state = state.copyWith(clearCurrent: true); await loadChannels(); }
}

final channelsProvider = StateNotifierProvider<ChannelsNotifier, ChannelsState>((ref) => ChannelsNotifier());
