import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:dio/dio.dart';
import '../../providers/auth_provider.dart';
import '../../providers/messages_provider.dart';
import '../../providers/presence_provider.dart';
import '../../providers/channels_provider.dart';
import '../../models/channel.dart';
import '../../models/message.dart';
import '../../widgets/message_bubble.dart';
import '../../widgets/message_input.dart';
import '../../widgets/typing_indicator.dart';
import '../../widgets/avatar.dart';
import '../../widgets/toast.dart';
import '../../api/calls_api.dart';
import '../../api/channels_api.dart';
import '../../widgets/forward_sheet.dart';
import '../../widgets/user_picker_sheet.dart';
import '../../websocket/ws_manager.dart';
import '../../websocket/ws_events.dart';
import '../calls/call_screen.dart';

class ChatScreen extends ConsumerStatefulWidget {
  final Channel channel;
  const ChatScreen({super.key, required this.channel});
  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen> {
  final _scrollController = ScrollController();
  final _callsApi = CallsApi();
  final _channelsApi = ChannelsApi();
  final _ws = WsManager();
  String? _replyToId;
  String? _replyToName;
  String? _editingId;
  final _editController = TextEditingController();
  int _pinnedIndex = 0; // Which pinned message to show / scroll to
  bool _shouldAutoScroll = true; // Track if user is near bottom
  int _lastMessageCount = 0; // Track message count for auto-scroll
  Map<String, dynamic>? _activeGroupCall; // Активный групповой звонок в этом канале
  List<Map<String, dynamic>> _members = []; // Участники для @-упоминаний

  @override
  void initState() {
    super.initState();
    Future.microtask(() async {
      await ref.read(messagesProvider.notifier).loadMessages(widget.channel.slug);
      // Auto-scroll to bottom after messages load
      Future.delayed(const Duration(milliseconds: 150), _scrollToBottom);
    });
    _loadMembersForMentions();
    _scrollController.addListener(_onScroll);

    // Групповые звонки: следим за активным звонком в канале
    if (!widget.channel.isDirect) {
      _checkActiveGroupCall();
      _ws.on(WsEvents.callStarted, _onGroupCallStarted);
      _ws.on(WsEvents.callEnded, _onGroupCallEnded);
    }
  }

  @override
  void dispose() {
    _scrollController.dispose();
    _editController.dispose();
    if (!widget.channel.isDirect) {
      _ws.off(WsEvents.callStarted, _onGroupCallStarted);
      _ws.off(WsEvents.callEnded, _onGroupCallEnded);
    }
    super.dispose();
  }

  /// Загружает участников канала для автокомплита @-упоминаний.
  Future<void> _loadMembersForMentions() async {
    try {
      final members = await _channelsApi.getMembers(widget.channel.slug);
      if (!mounted) return;
      setState(() {
        _members = members
            .whereType<Map>()
            .map((m) => Map<String, dynamic>.from(m))
            .toList();
      });
    } catch (_) {
      // Упоминания просто не будут доступны — не критично.
    }
  }

  // ── Групповой звонок ──────────────────────────────────────────────
  Future<void> _checkActiveGroupCall() async {
    try {
      final data = await _callsApi.getGroupCall(widget.channel.slug);
      if (mounted && data['active'] == true) {
        setState(() => _activeGroupCall = data);
      }
    } catch (_) {}
  }

  void _onGroupCallStarted(Map<String, dynamic> data) {
    if (!mounted) return;
    final slug = data['channel_slug']?.toString();
    if (slug != widget.channel.slug) return;
    setState(() => _activeGroupCall = Map<String, dynamic>.from(data));
  }

  void _onGroupCallEnded(Map<String, dynamic> data) {
    if (!mounted) return;
    final slug = data['channel_slug']?.toString();
    if (slug != null && slug != widget.channel.slug) return;
    setState(() => _activeGroupCall = null);
  }

  Future<void> _startGroupCall() async {
    try {
      final response = await _callsApi.startGroupCall(widget.channel.slug);
      final token = response['token'] as String?;
      final url = response['url'] as String?;
      final roomName = response['room_name'] as String?;
      if (token != null && url != null && roomName != null && mounted) {
        Navigator.push(context, MaterialPageRoute(
          builder: (_) => CallScreen(
            token: token,
            url: url,
            roomName: roomName,
            callsApi: _callsApi,
            groupChannelSlug: widget.channel.slug,
            channelName: widget.channel.displayName(ref.read(authProvider).user?.username ?? ''),
          ),
        ));
      }
    } catch (e) {
      if (mounted) Toast.show(context, 'Не удалось начать звонок', isError: true);
    }
  }

  Future<void> _joinGroupCall() async {
    final call = _activeGroupCall;
    if (call == null) return;
    final token = call['livekit_token'] as String?;
    final url = call['livekit_url'] as String?;
    final roomName = call['room_name'] as String?;
    if (token != null && url != null && roomName != null && mounted) {
      Navigator.push(context, MaterialPageRoute(
        builder: (_) => CallScreen(
          token: token,
          url: url,
          roomName: roomName,
          callsApi: _callsApi,
          groupChannelSlug: widget.channel.slug,
          channelName: widget.channel.displayName(ref.read(authProvider).user?.username ?? ''),
        ),
      ));
    }
  }

  void _onScroll() {
    if (_scrollController.position.pixels <= 50) {
      ref.read(messagesProvider.notifier).loadOlderMessages(widget.channel.slug);
    }
    // Track if user is near the bottom (within 150px)
    if (_scrollController.hasClients) {
      final maxScroll = _scrollController.position.maxScrollExtent;
      final currentScroll = _scrollController.position.pixels;
      _shouldAutoScroll = (maxScroll - currentScroll) < 150;
    }
  }

  void _scrollToBottom() {
    if (_scrollController.hasClients) {
      _scrollController.animateTo(
        _scrollController.position.maxScrollExtent,
        duration: const Duration(milliseconds: 300),
        curve: Curves.easeOut,
      );
    }
  }

  void _scrollToMessage(String messageId) {
    final messages = ref.read(messagesProvider).messages;
    final loading = ref.read(messagesProvider).loading;
    final idx = messages.indexWhere((m) => m.id == messageId);
    if (idx == -1 || !_scrollController.hasClients) return;

    // Estimate position: each message ~72px height, loading indicator adds 1 item
    final offset = loading ? idx + 1 : idx;
    // Use a rough estimate - scroll to approximate position
    final targetOffset = offset * 72.0;
    _scrollController.animateTo(
      targetOffset.clamp(0.0, _scrollController.position.maxScrollExtent),
      duration: const Duration(milliseconds: 400),
      curve: Curves.easeInOut,
    );
  }

  void _sendMessage(String text) {
    if (_editingId != null) {
      ref.read(messagesProvider.notifier).api.editMessage(
        widget.channel.slug,
        _editingId!,
        text,
      );
      setState(() => _editingId = null);
      return;
    }

    ref.read(messagesProvider.notifier).sendMessage(
      widget.channel.slug,
      text,
      replyTo: _replyToId,
    );
    setState(() {
      _replyToId = null;
      _replyToName = null;
    });
    // Force auto-scroll when user sends a message
    _shouldAutoScroll = true;
  }

  Future<void> _attachFile(String filePath, String fileName) async {
    try {
      final response = await ref.read(messagesProvider.notifier).api.uploadChatFile(filePath);
      final fileInfo = response['file'] as Map<String, dynamic>;
      await ref.read(messagesProvider.notifier).sendMessage(
        widget.channel.slug,
        fileName,
        file: fileInfo,
        type: 'file',
      );
    } catch (e) {
      if (mounted) Toast.show(context, 'Ошибка загрузки файла', isError: true);
    }
  }

  Future<void> _forwardMessage(Message message) async {
    final targetChannel = await showForwardSheet(context, ref);
    if (targetChannel == null || !mounted) return;
    try {
      if (message.isFile && message.file != null) {
        await ref.read(messagesProvider.notifier).sendMessage(
          targetChannel.slug,
          message.text,
          file: message.file,
          type: 'file',
        );
      } else {
        await ref.read(messagesProvider.notifier).sendMessage(
          targetChannel.slug,
          message.text,
        );
      }
      if (mounted) {
        final currentUser = ref.read(authProvider).user?.username ?? '';
        Toast.show(context, 'Переслано в ${targetChannel.displayName(currentUser)}');
      }
    } catch (e) {
      if (mounted) Toast.show(context, 'Ошибка пересылки', isError: true);
    }
  }

  void _showMessageMenu(Message message) {
    final authState = ref.read(authProvider);
    final isMine = message.sender == authState.user?.username;
    final isAdmin = authState.user?.role == 'admin';
    final t = ref.read(authProvider.notifier).t;

    showModalBottomSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Reply
            ListTile(
              leading: const Icon(Icons.reply),
              title: Text(t('msg.reply')),
              onTap: () {
                Navigator.pop(ctx);
                setState(() {
                  _replyToId = message.id;
                  _replyToName = message.senderName ?? message.sender;
                });
              },
            ),
            // Forward
            ListTile(
              leading: const Icon(Icons.forward),
              title: const Text('Переслать'),
              onTap: () {
                Navigator.pop(ctx);
                _forwardMessage(message);
              },
            ),
            // Reactions
            ListTile(
              leading: const Icon(Icons.emoji_emotions_outlined),
              title: Text(t('msg.reactions')),
              onTap: () {
                Navigator.pop(ctx);
                _showEmojiPicker(message);
              },
            ),
            // Pin / Unpin
            ListTile(
              leading: Icon(message.isPinned ? Icons.push_pin : Icons.push_pin_outlined),
              title: Text(message.isPinned ? 'Открепить' : 'Закрепить'),
              onTap: () {
                Navigator.pop(ctx);
                if (message.isPinned) {
                  ref.read(messagesProvider.notifier).unpinMessage(widget.channel.slug, message.id);
                } else {
                  ref.read(messagesProvider.notifier).pinMessage(widget.channel.slug, message.id);
                }
              },
            ),
            // Edit (own messages only)
            if (isMine && !message.isFile)
              ListTile(
                leading: const Icon(Icons.edit),
                title: const Text('Редактировать'),
                onTap: () {
                  Navigator.pop(ctx);
                  setState(() {
                    _editingId = message.id;
                    _editController.text = message.text;
                  });
                },
              ),
            // Delete
            if (isMine || isAdmin)
              ListTile(
                leading: Icon(Icons.delete, color: Theme.of(context).colorScheme.error),
                title: Text(t('ctx.delete'), style: TextStyle(color: Theme.of(context).colorScheme.error)),
                onTap: () {
                  Navigator.pop(ctx);
                  ref.read(messagesProvider.notifier).api.deleteMessage(widget.channel.slug, message.id);
                },
              ),
          ],
        ),
      ),
    );
  }

  void _showEmojiPicker(Message message) {
    final emojis = ['👍', '❤️', '😂', '😮', '😢', '🔥', '👏', '🎉'];
    showModalBottomSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Wrap(
            spacing: 12,
            runSpacing: 12,
            alignment: WrapAlignment.center,
            children: emojis.map((emoji) {
              return GestureDetector(
                onTap: () {
                  Navigator.pop(ctx);
                  ref.read(messagesProvider.notifier).api.addReaction(
                    widget.channel.slug,
                    message.id,
                    emoji,
                  );
                },
                child: Text(emoji, style: const TextStyle(fontSize: 32)),
              );
            }).toList(),
          ),
        ),
      ),
    );
  }

  void _handleTyping(bool isTyping) {
    ref.read(messagesProvider.notifier).sendTyping(widget.channel.id.toString(), isTyping);
  }

  Future<void> _startVideoCall() async {
    if (!widget.channel.isDirect) {
      if (mounted) {
        Toast.show(context, 'Адресный звонок доступен только в личном чате', isError: true);
      }
      return;
    }

    try {
      final currentUser = ref.read(authProvider).user?.username;
      final callee = widget.channel.members.where((m) => m != currentUser).firstOrNull;
      if (callee == null) {
        if (mounted) Toast.show(context, 'Нужен конкретный абонент для звонка', isError: true);
        return;
      }

      final response = await _callsApi.startCall(
        channelSlug: widget.channel.slug,
        calleeUsername: callee,
        mode: 'audio',
      );
      final callId = response['call_id'] as String?;
      final token = response['livekit_token'] as String?;
      final url = response['livekit_url'] as String?;
      final roomName = response['room_name'] as String?;

      if (token != null && url != null && roomName != null && callId != null && mounted) {
        Navigator.push(
          context,
          MaterialPageRoute(
            builder: (_) => CallScreen(
              token: token,
              url: url,
              roomName: roomName,
              callId: callId,
              callsApi: _callsApi,
              channelName: (widget.channel.name ?? '').isNotEmpty
                  ? widget.channel.name!
                  : widget.channel.displayName(
                      ref.read(authProvider).user?.username ?? ''),
            ),
          ),
        );
      }
    } on DioException catch (e) {
      String message = 'Ошибка начала звонка';
      final statusCode = e.response?.statusCode;
      final payload = e.response?.data;
      final detail = payload is Map ? payload['detail']?.toString() : null;
      if (detail != null && detail.isNotEmpty) {
        if (detail == 'Callee is busy') {
          message = 'Абонент сейчас занят';
        } else if (detail == 'Cannot call yourself') {
          message = 'Нельзя звонить самому себе';
        } else if (detail == 'Channel not found') {
          message = 'Канал не найден';
        } else {
          message = detail;
        }
      } else if (statusCode != null) {
        message = 'Ошибка начала звонка (HTTP $statusCode)';
      }
      if (mounted) Toast.show(context, message, isError: true);
    } catch (e) {
      if (mounted) Toast.show(context, 'Ошибка начала звонка: $e', isError: true);
    }
  }

  void _onPinnedBarTap(List<Message> pinnedMessages) {
    if (pinnedMessages.isEmpty) return;
    final msg = pinnedMessages[_pinnedIndex % pinnedMessages.length];
    _scrollToMessage(msg.id);
    setState(() {
      _pinnedIndex = (_pinnedIndex + 1) % pinnedMessages.length;
    });
  }

  @override
  Widget build(BuildContext context) {
    final messagesState = ref.watch(messagesProvider);
    final presenceState = ref.watch(presenceProvider);
    final authState = ref.watch(authProvider);
    final t = ref.read(authProvider.notifier).t;
    final currentUser = authState.user?.username ?? '';
    final displayName = widget.channel.displayName(currentUser);

    final messages = messagesState.messages;

    // Auto-scroll when new messages arrive and user is near bottom
    if (messages.length > _lastMessageCount && _lastMessageCount > 0 && _shouldAutoScroll) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _scrollToBottom());
    }
    _lastMessageCount = messages.length;
    final pinnedMessages = messagesState.pinnedMessages;
    final typingUsers = (presenceState.typingUsers[widget.channel.id] ?? [])
        .where((u) => u != currentUser)
        .toList();

    return Scaffold(
      appBar: AppBar(
        title: Row(
          children: [
            // Channel/DM avatar in AppBar
            if (!widget.channel.isDirect && widget.channel.avatarPath != null && widget.channel.avatarPath!.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(right: 10),
                child: UserAvatar(name: displayName, avatarPath: widget.channel.avatarPath, size: 36),
              ),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(displayName, style: const TextStyle(fontSize: 17)),
                  if (widget.channel.isDirect) ...[
                    Builder(builder: (_) {
                      final other = widget.channel.members.where((m) => m != currentUser).firstOrNull;
                      final online = other != null && presenceState.isOnline(other);
                      return Text(
                        online ? 'В сети' : '',
                        style: TextStyle(fontSize: 12, color: online ? Colors.green : Colors.transparent),
                      );
                    }),
                  ] else
                    GestureDetector(
                      onTap: _showMembers,
                      child: Text(
                        '${widget.channel.members.length} ${t('panel.members').toLowerCase()}',
                        style: TextStyle(fontSize: 12, color: Theme.of(context).colorScheme.onSurfaceVariant),
                      ),
                    ),
                ],
              ),
            ),
          ],
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.call),
            tooltip: 'Позвонить',
            onPressed: widget.channel.isDirect ? _startVideoCall : _startGroupCall,
          ),
          if (!widget.channel.isDirect)
            PopupMenuButton<String>(
              onSelected: (value) {
                if (value == 'add_members') _showAddMembers();
                if (value == 'members') _showMembers();
              },
              itemBuilder: (ctx) => [
                const PopupMenuItem(
                  value: 'members',
                  child: Row(children: [
                    Icon(Icons.groups),
                    SizedBox(width: 12),
                    Text('Участники'),
                  ]),
                ),
                const PopupMenuItem(
                  value: 'add_members',
                  child: Row(children: [
                    Icon(Icons.person_add),
                    SizedBox(width: 12),
                    Text('Добавить участников'),
                  ]),
                ),
              ],
            ),
        ],
      ),
      body: Column(
        children: [
          // Плашка активного группового звонка
          if (_activeGroupCall != null)
            _buildGroupCallBar(context),
          // Pinned message bar (Telegram-style)
          if (pinnedMessages.isNotEmpty)
            _buildPinnedBar(context, pinnedMessages),
          // Messages list
          Expanded(
            child: messages.isEmpty && !messagesState.loading
                ? Center(
                    child: Padding(
                      padding: const EdgeInsets.all(32),
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(Icons.chat_bubble_outline, size: 64, color: Theme.of(context).colorScheme.onSurfaceVariant),
                          const SizedBox(height: 16),
                          Text(
                            widget.channel.isDirect ? t('msg.welcomeDm') : t('msg.welcome'),
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                          const SizedBox(height: 8),
                          Text(
                            widget.channel.isDirect ? t('msg.welcomeDmHint') : t('msg.welcomeHint'),
                            style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                                ),
                            textAlign: TextAlign.center,
                          ),
                        ],
                      ),
                    ),
                  )
                : ListView.builder(
                    controller: _scrollController,
                    padding: const EdgeInsets.only(bottom: 8),
                    itemCount: messages.length + (messagesState.loading ? 1 : 0),
                    itemBuilder: (context, index) {
                      if (messagesState.loading && index == 0) {
                        return const Center(
                          child: Padding(
                            padding: EdgeInsets.all(16),
                            child: CircularProgressIndicator(strokeWidth: 2),
                          ),
                        );
                      }
                      final msgIndex = messagesState.loading ? index - 1 : index;
                      final msg = messages[msgIndex];
                      final isMine = msg.sender == currentUser;

                      // Show date separator
                      Widget? dateSeparator;
                      if (msgIndex == 0 || _shouldShowDate(messages[msgIndex - 1], msg)) {
                        dateSeparator = _buildDateSeparator(context, msg.timestamp, t);
                      }

                      // Show avatar/name for first in group
                      final showName = msgIndex == 0 ||
                          messages[msgIndex - 1].sender != msg.sender ||
                          _shouldShowDate(messages[msgIndex - 1], msg);

                      return Column(
                        children: [
                          if (dateSeparator != null) dateSeparator,
                          MessageBubble(
                            message: msg,
                            isMine: isMine,
                            showAvatar: showName,
                            showName: showName,
                            senderAvatar: msg.senderAvatar,
                            onLongPress: () => _showMessageMenu(msg),
                            onReaction: (emoji) {
                              ref.read(messagesProvider.notifier).api.addReaction(
                                widget.channel.slug,
                                msg.id,
                                emoji,
                              );
                            },
                          ),
                        ],
                      );
                    },
                  ),
          ),
          // Typing indicator
          TypingIndicator(typingUsers: typingUsers),
          // Message input
          MessageInput(
            onSend: _sendMessage,
            onAttach: _attachFile,
            onTyping: _handleTyping,
            replyToName: _replyToName,
            onCancelReply: () => setState(() {
              _replyToId = null;
              _replyToName = null;
            }),
            placeholder: t('msg.placeholder'),
            mentionCandidates: _members,
          ),
        ],
      ),
    );
  }

  Widget _buildGroupCallBar(BuildContext context) {
    final theme = Theme.of(context);
    final call = _activeGroupCall!;
    final startedByName = call['started_by_name']?.toString()
        ?? call['started_by']?.toString() ?? '';
    return Material(
      color: theme.colorScheme.primary,
      child: InkWell(
        onTap: _joinGroupCall,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
          child: Row(
            children: [
              const Icon(Icons.groups, color: Colors.white, size: 22),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Text(
                      'Идёт голосовой чат',
                      style: TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.w600),
                    ),
                    if (startedByName.isNotEmpty)
                      Text(
                        'Начал: $startedByName',
                        style: const TextStyle(color: Colors.white70, fontSize: 12),
                        overflow: TextOverflow.ellipsis,
                      ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(20),
                ),
                child: Text(
                  'Вступить',
                  style: TextStyle(color: theme.colorScheme.primary, fontWeight: FontWeight.bold, fontSize: 13),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _showAddMembers() async {
    final selected = await showUserPickerSheet(
      context,
      title: 'Добавить участников',
      confirmLabel: 'Добавить',
      excludeUsernames: widget.channel.members.toSet(),
    );
    if (selected == null || selected.isEmpty) return;
    try {
      await _channelsApi.addMembers(widget.channel.slug, selected.toList());
      ref.read(channelsProvider.notifier).loadChannels();
      if (mounted) Toast.show(context, 'Добавлено участников: ${selected.length}');
    } catch (e) {
      if (mounted) Toast.show(context, 'Не удалось добавить участников', isError: true);
    }
  }

  /// Показывает список участников группы с количеством и возможностью удаления.
  Future<void> _showMembers() async {
    List<dynamic> members;
    try {
      members = await _channelsApi.getMembers(widget.channel.slug);
    } catch (e) {
      if (mounted) Toast.show(context, 'Не удалось загрузить участников', isError: true);
      return;
    }
    if (!mounted) return;

    final currentUser = ref.read(authProvider).user?.username;
    final isSystemAdmin = ref.read(authProvider).user?.role == 'admin';
    // Роль текущего пользователя в канале
    final myEntry = members.where((m) => m['username'] == currentUser).firstOrNull;
    final myRole = myEntry?['role']?.toString() ?? 'member';
    final canManage = isSystemAdmin || myRole == 'owner' || myRole == 'admin';

    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSheet) {
          final theme = Theme.of(ctx);
          return DraggableScrollableSheet(
            expand: false,
            initialChildSize: 0.7,
            maxChildSize: 0.9,
            builder: (_, scrollController) => Column(
              children: [
                const SizedBox(height: 12),
                Text('Участники — ${members.length}',
                    style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.bold)),
                const SizedBox(height: 8),
                if (canManage)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 12),
                    child: SizedBox(
                      width: double.infinity,
                      child: OutlinedButton.icon(
                        icon: const Icon(Icons.person_add),
                        label: const Text('Добавить участников'),
                        onPressed: () {
                          Navigator.pop(ctx);
                          _showAddMembers();
                        },
                      ),
                    ),
                  ),
                const SizedBox(height: 4),
                Expanded(
                  child: ListView.builder(
                    controller: scrollController,
                    itemCount: members.length,
                    itemBuilder: (_, i) {
                      final m = members[i];
                      final uname = m['username'].toString();
                      final name = (m['display_name'] ?? uname).toString();
                      final role = m['role']?.toString() ?? 'member';
                      final isOwner = role == 'owner';
                      final isMe = uname == currentUser;
                      // Владельца и себя удалять нельзя
                      final canRemove = canManage && !isOwner && !isMe;
                      return ListTile(
                        leading: UserAvatar(name: name, avatarPath: m['avatar_path'], size: 40),
                        title: Text(name, maxLines: 1, overflow: TextOverflow.ellipsis),
                        subtitle: Text(
                          isOwner ? 'Владелец' : (role == 'admin' ? 'Администратор' : '@$uname'),
                          style: TextStyle(fontSize: 12, color: theme.colorScheme.onSurfaceVariant),
                        ),
                        trailing: canRemove
                            ? IconButton(
                                icon: Icon(Icons.person_remove, color: theme.colorScheme.error),
                                tooltip: 'Удалить',
                                onPressed: () async {
                                  final ok = await showDialog<bool>(
                                    context: ctx,
                                    builder: (dctx) => AlertDialog(
                                      title: const Text('Удалить участника?'),
                                      content: Text('Удалить $name из чата?'),
                                      actions: [
                                        TextButton(onPressed: () => Navigator.pop(dctx, false), child: const Text('Отмена')),
                                        TextButton(
                                          onPressed: () => Navigator.pop(dctx, true),
                                          child: Text('Удалить', style: TextStyle(color: theme.colorScheme.error)),
                                        ),
                                      ],
                                    ),
                                  );
                                  if (ok != true) return;
                                  try {
                                    await _channelsApi.removeMember(widget.channel.slug, uname);
                                    setSheet(() => members.removeAt(i));
                                    ref.read(channelsProvider.notifier).loadChannels();
                                  } catch (e) {
                                    if (ctx.mounted) {
                                      ScaffoldMessenger.of(ctx).showSnackBar(
                                        const SnackBar(content: Text('Не удалось удалить участника')),
                                      );
                                    }
                                  }
                                },
                              )
                            : null,
                      );
                    },
                  ),
                ),
                const SizedBox(height: 8),
              ],
            ),
          );
        },
      ),
    );
  }

  Widget _buildPinnedBar(BuildContext context, List<Message> pinnedMessages) {
    final theme = Theme.of(context);
    final currentPinned = pinnedMessages[_pinnedIndex % pinnedMessages.length];
    return GestureDetector(
      onTap: () => _onPinnedBarTap(pinnedMessages),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        decoration: BoxDecoration(
          color: theme.colorScheme.surfaceContainerHighest.withOpacity(0.7),
          border: Border(bottom: BorderSide(color: theme.dividerColor, width: 0.5)),
        ),
        child: Row(
          children: [
            Container(
              width: 3,
              height: 32,
              decoration: BoxDecoration(
                color: theme.colorScheme.primary,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Row(
                    children: [
                      Icon(Icons.push_pin, size: 14, color: theme.colorScheme.primary),
                      const SizedBox(width: 4),
                      Text(
                        pinnedMessages.length > 1
                            ? 'Закреплено (${_pinnedIndex % pinnedMessages.length + 1}/${pinnedMessages.length})'
                            : 'Закреплено',
                        style: TextStyle(fontSize: 12, color: theme.colorScheme.primary, fontWeight: FontWeight.w600),
                      ),
                    ],
                  ),
                  const SizedBox(height: 2),
                  Text(
                    currentPinned.text,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 13, color: theme.colorScheme.onSurface),
                  ),
                ],
              ),
            ),
            Icon(Icons.chevron_right, size: 20, color: theme.colorScheme.onSurfaceVariant),
          ],
        ),
      ),
    );
  }

  bool _shouldShowDate(Message prev, Message current) {
    try {
      final prevDate = DateTime.parse(prev.timestamp);
      final currDate = DateTime.parse(current.timestamp);
      return prevDate.day != currDate.day ||
          prevDate.month != currDate.month ||
          prevDate.year != currDate.year;
    } catch (_) {
      return false;
    }
  }

  Widget _buildDateSeparator(BuildContext context, String timestamp, String Function(String) t) {
    final theme = Theme.of(context);
    String label;
    try {
      final date = DateTime.parse(timestamp);
      final now = DateTime.now();
      if (date.year == now.year && date.month == now.month && date.day == now.day) {
        label = t('msg.today');
      } else if (date.year == now.year && date.month == now.month && date.day == now.day - 1) {
        label = t('msg.yesterday');
      } else {
        label = '${date.day.toString().padLeft(2, '0')}.${date.month.toString().padLeft(2, '0')}.${date.year}';
      }
    } catch (_) {
      label = '';
    }

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 16),
      child: Row(
        children: [
          Expanded(child: Divider(color: theme.dividerColor)),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Text(
              label,
              style: TextStyle(
                fontSize: 12,
                color: theme.colorScheme.onSurfaceVariant,
                fontWeight: FontWeight.w500,
              ),
            ),
          ),
          Expanded(child: Divider(color: theme.dividerColor)),
        ],
      ),
    );
  }
}
