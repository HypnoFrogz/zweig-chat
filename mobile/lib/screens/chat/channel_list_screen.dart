import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../providers/auth_provider.dart';
import '../../providers/channels_provider.dart';
import '../../providers/presence_provider.dart';
import '../../models/channel.dart';
import '../../widgets/channel_list_item.dart';
import '../../widgets/avatar.dart';
import '../../services/chat_folders_service.dart';
import '../../api/auth_api.dart';
import '../../api/channels_api.dart';
import '../../widgets/toast.dart';
import 'chat_screen.dart';
import 'create_channel_screen.dart';

class ChannelListScreen extends ConsumerStatefulWidget {
  const ChannelListScreen({super.key});
  @override
  ConsumerState<ChannelListScreen> createState() => _ChannelListScreenState();
}

class _ChannelListScreenState extends ConsumerState<ChannelListScreen> {
  final _searchController = TextEditingController();
  final _foldersService = ChatFoldersService();
  String _searchQuery = '';
  String? _selectedFolderId; // null = "All chats"

  // User search state
  List<Map<String, dynamic>> _foundUsers = [];
  bool _loadingUsers = false;
  bool _creatingDm = false;

  @override
  void initState() {
    super.initState();
    Future.microtask(() {
      final username = ref.read(authProvider).user?.username;
      if (username != null) {
        ref.read(channelsProvider.notifier).setCurrentUser(username);
      }
      ref.read(channelsProvider.notifier).loadChannels();
      ref.read(presenceProvider.notifier).loadPresence();
    });
    _foldersService.load().then((_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    await ref.read(channelsProvider.notifier).loadChannels();
    await ref.read(presenceProvider.notifier).loadPresence();
  }

  void _openChat(Channel channel) {
    ref.read(channelsProvider.notifier).selectChannel(channel);
    Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => ChatScreen(channel: channel)),
    );
  }

  Future<void> _onSearchChanged(String val) async {
    setState(() {
      _searchQuery = val;
      _foundUsers = [];
    });

    if (val.trim().isEmpty) return;

    // Search users in parallel with channel filter
    setState(() => _loadingUsers = true);
    try {
      final currentUser = ref.read(authProvider).user?.username;
      final allUsers = await AuthApi().getUsers();
      final query = val.trim().toLowerCase();
      final filtered = allUsers
          .map((u) => u as Map<String, dynamic>)
          .where((u) => u['username'] != currentUser)
          .where((u) {
            final username = (u['username'] ?? '').toString().toLowerCase();
            final displayName = (u['display_name'] ?? '').toString().toLowerCase();
            return username.contains(query) || displayName.contains(query);
          })
          .toList();
      if (mounted) {
        setState(() {
          _foundUsers = filtered;
          _loadingUsers = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _loadingUsers = false);
    }
  }

  Future<void> _openDmWith(Map<String, dynamic> user) async {
    final username = user['username'] as String?;
    if (username == null) return;

    // Check if DM already exists in the channels list
    final channelsState = ref.read(channelsProvider);
    final currentUser = ref.read(authProvider).user?.username ?? '';
    final existing = channelsState.channels.where((ch) {
      if (!ch.isDirect) return false;
      return ch.members.contains(username);
    }).firstOrNull;

    if (existing != null) {
      _openChat(existing);
      return;
    }

    // Create new DM
    setState(() => _creatingDm = true);
    try {
      final data = await ChannelsApi().createChannel({
        'type': 'direct',
        'participant': username,
      });
      await ref.read(channelsProvider.notifier).loadChannels();
      if (mounted) {
        final channel = Channel.fromJson(data);
        // Clear search
        _searchController.clear();
        setState(() {
          _searchQuery = '';
          _foundUsers = [];
          _creatingDm = false;
        });
        _openChat(channel);
      }
    } catch (e) {
      if (mounted) {
        Toast.show(context, 'Error creating DM', isError: true);
        setState(() => _creatingDm = false);
      }
    }
  }

  void _showChannelMenu(Channel channel) {
    final t = ref.read(authProvider.notifier).t;
    final authState = ref.read(authProvider);
    final isOwner = channel.createdBy == authState.user?.username;
    final isAdmin = authState.user?.role == 'admin';
    final folders = _foldersService.folders;
    final currentFolder = _foldersService.getFolderForChannel(channel.slug);

    showModalBottomSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Move to folder
            if (folders.isNotEmpty)
              ListTile(
                leading: const Icon(Icons.folder_open),
                title: const Text('Move to folder'),
                onTap: () {
                  Navigator.pop(ctx);
                  _showMoveFolderDialog(channel);
                },
              ),
            // Remove from folder (if in a folder)
            if (currentFolder != null)
              ListTile(
                leading: const Icon(Icons.folder_off),
                title: const Text('Remove from folder'),
                onTap: () async {
                  Navigator.pop(ctx);
                  await _foldersService.removeChannelFromAllFolders(channel.slug);
                  setState(() {});
                },
              ),
            if (!channel.isDirect)
              ListTile(
                leading: const Icon(Icons.exit_to_app),
                title: Text(t('channel.leave')),
                onTap: () {
                  Navigator.pop(ctx);
                  _confirmLeave(channel);
                },
              ),
            if (isOwner || isAdmin)
              ListTile(
                leading: Icon(Icons.delete, color: Theme.of(context).colorScheme.error),
                title: Text(t('channel.delete'), style: TextStyle(color: Theme.of(context).colorScheme.error)),
                onTap: () {
                  Navigator.pop(ctx);
                  _confirmDelete(channel);
                },
              ),
          ],
        ),
      ),
    );
  }

  void _showMoveFolderDialog(Channel channel) {
    final folders = _foldersService.folders;
    showModalBottomSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.all(16),
              child: Text('Move to folder', style: Theme.of(context).textTheme.titleMedium),
            ),
            ...folders.map((folder) => ListTile(
              leading: const Icon(Icons.folder),
              title: Text(folder.name),
              trailing: folder.channelSlugs.contains(channel.slug) ? const Icon(Icons.check, color: Colors.green) : null,
              onTap: () async {
                Navigator.pop(ctx);
                await _foldersService.addChannelToFolder(folder.id, channel.slug);
                setState(() {});
              },
            )),
            const Divider(),
            ListTile(
              leading: const Icon(Icons.close),
              title: const Text('Remove from all folders'),
              onTap: () async {
                Navigator.pop(ctx);
                await _foldersService.removeChannelFromAllFolders(channel.slug);
                setState(() {});
              },
            ),
          ],
        ),
      ),
    );
  }

  void _confirmLeave(Channel channel) {
    final t = ref.read(authProvider.notifier).t;
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(t('channel.leave')),
        content: Text(t('channel.leaveConfirm')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(
            onPressed: () {
              Navigator.pop(ctx);
              ref.read(channelsProvider.notifier).leaveChannel(channel.slug);
            },
            child: Text(t('channel.leave')),
          ),
        ],
      ),
    );
  }

  void _confirmDelete(Channel channel) {
    final t = ref.read(authProvider.notifier).t;
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(t('channel.delete')),
        content: const Text('Are you sure? This action is irreversible.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Theme.of(context).colorScheme.error),
            onPressed: () {
              Navigator.pop(ctx);
              ref.read(channelsProvider.notifier).deleteChannel(channel.slug);
            },
            child: Text(t('channel.delete')),
          ),
        ],
      ),
    );
  }

  // --- Folder management dialogs ---

  void _showCreateFolderDialog() {
    final controller = TextEditingController();
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Create folder'),
        content: TextField(
          controller: controller,
          autofocus: true,
          decoration: const InputDecoration(
            hintText: 'Folder name',
            border: OutlineInputBorder(),
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(
            onPressed: () async {
              final name = controller.text.trim();
              if (name.isNotEmpty) {
                await _foldersService.createFolder(name);
                if (mounted) {
                  Navigator.pop(ctx);
                  setState(() {});
                }
              }
            },
            child: const Text('Create'),
          ),
        ],
      ),
    );
  }

  void _showEditFolderDialog(ChatFolder folder) {
    final controller = TextEditingController(text: folder.name);
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Edit folder'),
        content: TextField(
          controller: controller,
          autofocus: true,
          decoration: const InputDecoration(
            hintText: 'Folder name',
            border: OutlineInputBorder(),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () async {
              Navigator.pop(ctx);
              await _foldersService.deleteFolder(folder.id);
              if (_selectedFolderId == folder.id) _selectedFolderId = null;
              if (mounted) setState(() {});
            },
            child: Text('Delete', style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ),
          const Spacer(),
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(
            onPressed: () async {
              final name = controller.text.trim();
              if (name.isNotEmpty) {
                await _foldersService.renameFolder(folder.id, name);
                if (mounted) {
                  Navigator.pop(ctx);
                  setState(() {});
                }
              }
            },
            child: const Text('Save'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final channelsState = ref.watch(channelsProvider);
    final presenceState = ref.watch(presenceProvider);
    final authState = ref.watch(authProvider);
    final t = ref.read(authProvider.notifier).t;
    final currentUser = authState.user?.username ?? '';
    final theme = Theme.of(context);

    // Sort channels: most recent first
    var channels = List<Channel>.from(channelsState.channels)
      ..sort((a, b) {
        final aTime = a.lastMsgTimestamp ?? '';
        final bTime = b.lastMsgTimestamp ?? '';
        return bTime.compareTo(aTime);
      });

    // Filter by search query
    if (_searchQuery.isNotEmpty) {
      channels = channels.where((ch) {
        final name = ch.displayName(currentUser).toLowerCase();
        return name.contains(_searchQuery.toLowerCase());
      }).toList();
    }

    // Filter by selected folder
    if (_selectedFolderId != null) {
      final folder = _foldersService.folders.where((f) => f.id == _selectedFolderId).firstOrNull;
      if (folder != null) {
        channels = channels.where((ch) => folder.channelSlugs.contains(ch.slug)).toList();
      }
    }

    final folders = _foldersService.folders;

    // Determine if we should show user search results
    final showUserResults = _searchQuery.isNotEmpty;

    // Filter out users that already have a DM channel in the list
    final displayedUsers = _foundUsers.where((u) {
      final uname = u['username'] as String? ?? '';
      return !channels.any((ch) => ch.isDirect && ch.members.contains(uname));
    }).toList();

    return Scaffold(
      appBar: AppBar(
        title: Text(t('sidebar.chats')),
        actions: [
          IconButton(
            icon: const Icon(Icons.create_new_folder_outlined),
            tooltip: 'Create folder',
            onPressed: _showCreateFolderDialog,
          ),
        ],
      ),
      body: Column(
        children: [
          // Search input
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: TextField(
              controller: _searchController,
              onChanged: _onSearchChanged,
              decoration: InputDecoration(
                hintText: 'Search chats or people...',
                prefixIcon: const Icon(Icons.search, size: 20),
                suffixIcon: _searchQuery.isNotEmpty
                    ? IconButton(
                        icon: const Icon(Icons.close, size: 18),
                        onPressed: () {
                          _searchController.clear();
                          setState(() {
                            _searchQuery = '';
                            _foundUsers = [];
                          });
                        },
                      )
                    : null,
                filled: true,
                fillColor: theme.colorScheme.surfaceContainerHighest.withOpacity(0.5),
                contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(24),
                  borderSide: BorderSide.none,
                ),
              ),
            ),
          ),

          // Folder tabs (Telegram style) — only show when not searching
          if (folders.isNotEmpty && !showUserResults)
            SizedBox(
              height: 40,
              child: ListView(
                scrollDirection: Axis.horizontal,
                padding: const EdgeInsets.symmetric(horizontal: 12),
                children: [
                  // "All" tab
                  Padding(
                    padding: const EdgeInsets.only(right: 6),
                    child: ChoiceChip(
                      label: const Text('All'),
                      selected: _selectedFolderId == null,
                      onSelected: (_) => setState(() => _selectedFolderId = null),
                      materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                      visualDensity: VisualDensity.compact,
                    ),
                  ),
                  // Folder tabs
                  ...folders.map((folder) {
                    final unread = channelsState.channels
                        .where((ch) => folder.channelSlugs.contains(ch.slug))
                        .fold<int>(0, (sum, ch) => sum + ch.unreadCount);
                    return Padding(
                      padding: const EdgeInsets.only(right: 6),
                      child: GestureDetector(
                        onLongPress: () => _showEditFolderDialog(folder),
                        child: ChoiceChip(
                          label: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Text(folder.name),
                              if (unread > 0) ...[
                                const SizedBox(width: 6),
                                Container(
                                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                                  decoration: BoxDecoration(
                                    color: theme.colorScheme.primary,
                                    borderRadius: BorderRadius.circular(10),
                                  ),
                                  child: Text(
                                    unread > 99 ? '99+' : unread.toString(),
                                    style: TextStyle(
                                      color: theme.colorScheme.onPrimary,
                                      fontSize: 11,
                                      fontWeight: FontWeight.bold,
                                    ),
                                  ),
                                ),
                              ],
                            ],
                          ),
                          selected: _selectedFolderId == folder.id,
                          onSelected: (_) => setState(() {
                            _selectedFolderId = _selectedFolderId == folder.id ? null : folder.id;
                          }),
                          materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                          visualDensity: VisualDensity.compact,
                        ),
                      ),
                    );
                  }),
                ],
              ),
            ),

          if (folders.isNotEmpty && !showUserResults) const SizedBox(height: 4),

          // Channels list + user results
          Expanded(
            child: RefreshIndicator(
              onRefresh: _refresh,
              child: channelsState.loading
                  ? const Center(child: CircularProgressIndicator())
                  : (channels.isEmpty && !showUserResults)
                  ? ListView(
                      children: [
                        SizedBox(
                          height: MediaQuery.of(context).size.height * 0.4,
                          child: Center(
                            child: Text(
                              _selectedFolderId != null
                                  ? 'Folder is empty'
                                  : t('msg.empty'),
                              style: theme.textTheme.bodyLarge?.copyWith(
                                color: theme.colorScheme.onSurfaceVariant,
                              ),
                            ),
                          ),
                        ),
                      ],
                    )
                  : ListView(
                      children: [
                        // Matching channels
                        if (channels.isNotEmpty) ...[
                          if (showUserResults && channels.isNotEmpty)
                            Padding(
                              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
                              child: Text('Chats', style: theme.textTheme.labelMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
                            ),
                          ...channels.map((channel) {
                            bool isOnline = false;
                            if (channel.isDirect) {
                              final other = channel.members.where((m) => m != currentUser).firstOrNull;
                              if (other != null) isOnline = presenceState.isOnline(other);
                            }
                            return ChannelListItem(
                              channel: channel,
                              currentUser: currentUser,
                              isOnline: isOnline,
                              onTap: () => _openChat(channel),
                              onLongPress: () => _showChannelMenu(channel),
                            );
                          }),
                        ],
                        // User search results (people not in existing DMs)
                        if (showUserResults) ...[
                          if (_loadingUsers)
                            const Padding(
                              padding: EdgeInsets.symmetric(vertical: 16),
                              child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
                            )
                          else if (displayedUsers.isNotEmpty) ...[
                            Padding(
                              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
                              child: Text('People', style: theme.textTheme.labelMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
                            ),
                            ...displayedUsers.map((user) => ListTile(
                              leading: UserAvatar(
                                name: user['display_name'] ?? user['username'] ?? '',
                                avatarPath: user['avatar_path'] as String?,
                                size: 44,
                              ),
                              title: Text(user['display_name'] ?? user['username'] ?? ''),
                              subtitle: user['username'] != null ? Text('@${user['username']}') : null,
                              trailing: _creatingDm
                                  ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                                  : const Icon(Icons.arrow_forward_ios, size: 14),
                              onTap: _creatingDm ? null : () => _openDmWith(user),
                            )),
                          ] else if (channels.isEmpty)
                            Padding(
                              padding: const EdgeInsets.symmetric(vertical: 32),
                              child: Center(
                                child: Text(
                                  'Nothing found',
                                  style: theme.textTheme.bodyLarge?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                                ),
                              ),
                            ),
                        ] else if (channels.isEmpty)
                          Padding(
                            padding: const EdgeInsets.symmetric(vertical: 32),
                            child: Center(
                              child: Text(
                                'Nothing found',
                                style: theme.textTheme.bodyLarge?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                              ),
                            ),
                          ),
                      ],
                    ),
            ),
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: () {
          Navigator.push(context, MaterialPageRoute(builder: (_) => const CreateChannelScreen()));
        },
        child: const Icon(Icons.add),
      ),
    );
  }
}
