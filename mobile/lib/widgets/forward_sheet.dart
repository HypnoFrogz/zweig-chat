import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/channel.dart';
import '../providers/channels_provider.dart';
import '../providers/auth_provider.dart';
import 'avatar.dart';

/// Shows a bottom sheet with channel list for forwarding a message.
/// Returns the selected [Channel] or null if dismissed.
Future<Channel?> showForwardSheet(BuildContext context, WidgetRef ref) {
  return showModalBottomSheet<Channel>(
    context: context,
    isScrollControlled: true,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
    ),
    builder: (ctx) => _ForwardSheet(ref: ref),
  );
}

class _ForwardSheet extends ConsumerStatefulWidget {
  final WidgetRef ref;
  const _ForwardSheet({required this.ref});

  @override
  ConsumerState<_ForwardSheet> createState() => _ForwardSheetState();
}

class _ForwardSheetState extends ConsumerState<_ForwardSheet> {
  final _searchController = TextEditingController();
  String _query = '';

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final channels = ref.watch(channelsProvider).channels;
    final currentUser = ref.watch(authProvider).user?.username ?? '';

    final filtered = _query.isEmpty
        ? channels
        : channels.where((ch) {
            final name = ch.displayName(currentUser).toLowerCase();
            return name.contains(_query.toLowerCase());
          }).toList();

    return DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.6,
      minChildSize: 0.4,
      maxChildSize: 0.9,
      builder: (_, scrollController) => Column(
        children: [
          // Handle
          Padding(
            padding: const EdgeInsets.only(top: 8, bottom: 4),
            child: Container(
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: theme.dividerColor,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
          ),
          // Title
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: Text(
              'Переслать в...',
              style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.bold),
            ),
          ),
          // Search
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
            child: TextField(
              controller: _searchController,
              autofocus: false,
              decoration: InputDecoration(
                hintText: 'Поиск чата...',
                prefixIcon: const Icon(Icons.search, size: 20),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(24),
                  borderSide: BorderSide.none,
                ),
                filled: true,
                fillColor: theme.colorScheme.surfaceContainerHighest,
                contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                isDense: true,
              ),
              onChanged: (v) => setState(() => _query = v),
            ),
          ),
          const SizedBox(height: 4),
          // Channels list
          Expanded(
            child: filtered.isEmpty
                ? Center(
                    child: Text(
                      'Нет чатов',
                      style: TextStyle(color: theme.colorScheme.onSurfaceVariant),
                    ),
                  )
                : ListView.builder(
                    controller: scrollController,
                    itemCount: filtered.length,
                    itemBuilder: (_, i) {
                      final ch = filtered[i];
                      final name = ch.displayName(currentUser);
                      final subtitle = ch.isDirect
                          ? 'Личный чат'
                          : ch.isPrivate
                              ? 'Приватный канал'
                              : 'Канал';
                      return ListTile(
                        leading: UserAvatar(
                          name: name,
                          avatarPath: ch.avatarPath,
                          size: 40,
                        ),
                        title: Text(name, maxLines: 1, overflow: TextOverflow.ellipsis),
                        subtitle: Text(subtitle,
                            style: TextStyle(fontSize: 12, color: theme.colorScheme.onSurfaceVariant)),
                        onTap: () => Navigator.pop(context, ch),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }
}
