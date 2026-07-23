import 'package:flutter/material.dart';
import '../models/channel.dart';
import '../theme/colors.dart';
import 'avatar.dart';

class ChannelListItem extends StatelessWidget {
  final Channel channel;
  final String currentUser;
  final bool isOnline;
  final VoidCallback onTap;
  final VoidCallback? onLongPress;

  const ChannelListItem({
    super.key,
    required this.channel,
    required this.currentUser,
    this.isOnline = false,
    required this.onTap,
    this.onLongPress,
  });

  // Warm Nebenan-family tones for channel squircles, picked deterministically.
  static const _channelColors = [
    Color(0xFF3A5A48), // forest
    Color(0xFFD97B4F), // terracotta
    Color(0xFFB07C3A), // amber-deep
    Color(0xFF8C5E3C), // clay
    Color(0xFF5F7355), // olive
  ];

  Color _colorFor(String key) =>
      _channelColors[key.hashCode.abs() % _channelColors.length];

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final c = theme.brightness == Brightness.dark ? AppColors.dark : AppColors.light;
    final displayName = channel.displayName(currentUser);
    final hasUnread = channel.unreadCount > 0;

    String? otherAvatar;
    if (channel.isDirect && channel.memberDetails != null) {
      final other = channel.memberDetails!.where((d) => d['username'] != currentUser).firstOrNull;
      if (other != null) otherAvatar = other['avatar_path'] as String?;
    }

    final Widget leading = channel.isDirect
        ? UserAvatar(name: displayName, avatarPath: otherAvatar, size: 48, isOnline: isOnline)
        : UserAvatar(
            name: displayName,
            avatarPath: channel.avatarPath,
            size: 48,
            squircle: true,
            icon: channel.isPrivate ? Icons.lock_rounded : Icons.tag_rounded,
            bgColor: _colorFor(channel.slug),
          );

    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 2),
      leading: leading,
      title: Text(
        displayName,
        style: TextStyle(
          fontSize: 15,
          fontWeight: hasUnread ? FontWeight.w600 : FontWeight.w500,
          color: c.text,
        ),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      subtitle: (channel.description != null && channel.description!.isNotEmpty)
          ? Text(
              channel.description!,
              style: TextStyle(fontSize: 12.5, color: c.text2),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            )
          : null,
      trailing: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          if (channel.lastMsgTimestamp != null)
            Text(_formatTime(channel.lastMsgTimestamp!),
                style: TextStyle(fontSize: 11, color: c.text3)),
          if (hasUnread) ...[
            const SizedBox(height: 5),
            Container(
              constraints: const BoxConstraints(minWidth: 20),
              height: 20,
              alignment: Alignment.center,
              padding: const EdgeInsets.symmetric(horizontal: 6),
              decoration: BoxDecoration(
                color: c.notification,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Text(
                channel.unreadCount > 99 ? '99+' : channel.unreadCount.toString(),
                style: const TextStyle(
                  color: Color(0xFF33302C),
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
          ],
        ],
      ),
      onTap: onTap,
      onLongPress: onLongPress,
    );
  }

  String _formatTime(String timestamp) {
    try {
      final dt = DateTime.parse(timestamp);
      final now = DateTime.now();
      final diff = now.difference(dt);
      if (diff.inDays == 0) {
        return '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
      } else if (diff.inDays == 1) {
        return 'Вчера';
      } else if (diff.inDays < 7) {
        const days = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
        return days[dt.weekday - 1];
      } else {
        return '${dt.day.toString().padLeft(2, '0')}.${dt.month.toString().padLeft(2, '0')}';
      }
    } catch (_) {
      return '';
    }
  }
}
