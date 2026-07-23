import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../api/messages_api.dart';
import '../models/message.dart';
import '../websocket/ws_manager.dart';
import '../websocket/ws_events.dart';

class MessagesState {
  final List<Message> messages; final bool loading; final bool hasMore; final String? channelSlug;
  final List<Message> pinnedMessages;
  const MessagesState({this.messages = const [], this.loading = false, this.hasMore = true, this.channelSlug, this.pinnedMessages = const []});
  MessagesState copyWith({List<Message>? messages, bool? loading, bool? hasMore, String? channelSlug, List<Message>? pinnedMessages}) =>
    MessagesState(messages: messages ?? this.messages, loading: loading ?? this.loading, hasMore: hasMore ?? this.hasMore, channelSlug: channelSlug ?? this.channelSlug, pinnedMessages: pinnedMessages ?? this.pinnedMessages);
}

class MessagesNotifier extends StateNotifier<MessagesState> {
  final api = MessagesApi(); final _ws = WsManager();
  MessagesNotifier() : super(const MessagesState()) {
    _ws.on(WsEvents.newMessage, (data) {
      // Only append to the state if this message belongs to the currently open channel
      final incomingSlug = data['channel_slug'] as String?;
      if (incomingSlug != null && incomingSlug != state.channelSlug) return;
      final msg = Message.fromJson(data['message']);
      // Deduplicate: skip if already present (e.g. sent by us via REST + WS echo)
      if (state.messages.any((m) => m.id == msg.id)) return;
      state = state.copyWith(messages: [...state.messages, msg]);
    });
    _ws.on(WsEvents.messageDeleted, (data) {
      final id = data['message_id']?.toString();
      if (id != null) {
        state = state.copyWith(
          messages: state.messages.where((m) => m.id != id).toList(),
          pinnedMessages: state.pinnedMessages.where((m) => m.id != id).toList(),
        );
      }
    });
    _ws.on(WsEvents.messageEdited, (data) {
      final id = data['message_id']?.toString(); final text = data['text'] as String?;
      if (id != null && text != null) {
        state = state.copyWith(messages: state.messages.map((m) => m.id == id ? m.copyWith(text: text, editedAt: data['edited_at']) : m).toList());
        state = state.copyWith(pinnedMessages: state.pinnedMessages.map((m) => m.id == id ? m.copyWith(text: text, editedAt: data['edited_at']) : m).toList());
      }
    });
    _ws.on(WsEvents.messagesRead, (data) {
      final who = data['username'] as String?;
      final upTo = data['up_to_message_id'] as String?;
      if (who == null) return;
      bool reached = upTo == null;
      state = state.copyWith(messages: state.messages.map((m) {
        if (!reached && m.id == upTo) reached = true;
        if (!m.readBy.contains(who)) {
          return m.copyWith(readBy: [...m.readBy, who]);
        }
        return m;
      }).toList());
    });
    _ws.on(WsEvents.reactionUpdated, (data) {
      final msgId = data['message_id']?.toString();
      final reactions = (data['reactions'] as List?)?.map((r) => Reaction.fromJson(r as Map<String, dynamic>)).toList() ?? [];
      if (msgId != null) {
        state = state.copyWith(messages: state.messages.map((m) => m.id == msgId ? m.copyWith(reactions: reactions) : m).toList());
      }
    });
    _ws.on(WsEvents.messagePinned, (data) {
      final msgId = data['message_id']?.toString();
      if (msgId == null) return;
      state = state.copyWith(messages: state.messages.map((m) => m.id == msgId ? m.copyWith(isPinned: true, pinnedBy: data['pinned_by'], pinnedAt: data['pinned_at']) : m).toList());
      final existing = state.messages.where((m) => m.id == msgId).firstOrNull;
      if (existing != null && !state.pinnedMessages.any((m) => m.id == msgId)) {
        final pinned = existing.copyWith(isPinned: true, pinnedBy: data['pinned_by'], pinnedAt: data['pinned_at']);
        state = state.copyWith(pinnedMessages: [...state.pinnedMessages, pinned]);
      }
    });
    _ws.on(WsEvents.messageUnpinned, (data) {
      final msgId = data['message_id']?.toString();
      if (msgId == null) return;
      state = state.copyWith(
        messages: state.messages.map((m) => m.id == msgId ? m.copyWith(isPinned: false) : m).toList(),
        pinnedMessages: state.pinnedMessages.where((m) => m.id != msgId).toList(),
      );
    });
  }

  Future<void> loadMessages(String slug) async {
    state = state.copyWith(loading: true, channelSlug: slug, messages: [], pinnedMessages: []);
    try {
      final data = await api.getMessages(slug);
      state = state.copyWith(messages: (data['messages'] as List).map((j) => Message.fromJson(j)).toList(), hasMore: data['has_more'] ?? false, loading: false);
      loadPinnedMessages(slug);
    } catch (e) { print('[MessagesProvider] loadMessages error: $e'); state = state.copyWith(loading: false); }
  }
  Future<void> loadOlderMessages(String slug) async {
    if (state.loading || !state.hasMore || state.messages.isEmpty) return;
    state = state.copyWith(loading: true);
    try { final data = await api.getMessages(slug, before: state.messages.first.id); state = state.copyWith(messages: [...(data['messages'] as List).map((j) => Message.fromJson(j)).toList(), ...state.messages], hasMore: data['has_more'] ?? false, loading: false); } catch (_) { state = state.copyWith(loading: false); }
  }
  Future<void> sendMessage(String slug, String text, {String? replyTo, Map<String, dynamic>? file, String type = 'text'}) async { await api.sendMessage(slug, text: text, type: type, replyTo: replyTo, file: file); }
  void sendTyping(String channelId, bool isTyping) { _ws.send({'event': 'typing', 'channel_id': channelId, 'is_typing': isTyping}); }

  Future<void> loadPinnedMessages(String slug) async {
    try {
      final data = await api.getPinnedMessages(slug);
      final pinned = (data as List).map((j) => Message.fromJson(j)).toList();
      state = state.copyWith(pinnedMessages: pinned);
    } catch (e) {
      print('[MessagesProvider] loadPinnedMessages error: $e');
    }
  }

  Future<void> pinMessage(String slug, String msgId) async {
    await api.pinMessage(slug, msgId);
  }

  Future<void> unpinMessage(String slug, String msgId) async {
    await api.unpinMessage(slug, msgId);
  }
}

final messagesProvider = StateNotifierProvider<MessagesNotifier, MessagesState>((ref) => MessagesNotifier());
