import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import '../api/api_client.dart';
import '../theme/colors.dart';

class UserAvatar extends StatelessWidget {
  final String? avatarPath;
  final String name;
  final double size;
  final bool isOnline;

  /// Rounded-square shape for channels; circle (default) for people.
  final bool squircle;

  /// Optional icon shown instead of initials (e.g. # or lock for channels).
  final IconData? icon;

  /// Override the fallback background (used for per-channel colors).
  final Color? bgColor;

  const UserAvatar({
    super.key,
    this.avatarPath,
    required this.name,
    this.size = 40,
    this.isOnline = false,
    this.squircle = false,
    this.icon,
    this.bgColor,
  });

  String get _initials {
    if (name.isEmpty) return '?';
    final parts = name.trim().split(' ');
    if (parts.length >= 2) return '${parts[0][0]}${parts[1][0]}'.toUpperCase();
    return name[0].toUpperCase();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final c = theme.brightness == Brightness.dark ? AppColors.dark : AppColors.light;
    final hasAvatar = avatarPath != null && avatarPath!.isNotEmpty;
    final radius = squircle ? size * 0.3 : size / 2;

    Widget avatar;
    if (hasAvatar) {
      final url = '${ApiClient.baseUrl.replaceAll('/api', '')}$avatarPath';
      avatar = ClipRRect(
        borderRadius: BorderRadius.circular(radius),
        child: CachedNetworkImage(
          imageUrl: url,
          width: size,
          height: size,
          fit: BoxFit.cover,
          placeholder: (context, url) => _fallback(c),
          errorWidget: (context, url, error) => _fallback(c),
        ),
      );
    } else {
      avatar = _fallback(c);
    }

    if (!isOnline) return avatar;

    return Stack(
      clipBehavior: Clip.none,
      children: [
        avatar,
        Positioned(
          right: -1,
          bottom: -1,
          child: Container(
            width: size * 0.3,
            height: size * 0.3,
            decoration: BoxDecoration(
              color: c.online,
              shape: BoxShape.circle,
              border: Border.all(color: theme.scaffoldBackgroundColor, width: 2),
            ),
          ),
        ),
      ],
    );
  }

  Widget _fallback(dynamic c) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: bgColor ?? c.accent,
        borderRadius: BorderRadius.circular(squircle ? size * 0.3 : size / 2),
      ),
      alignment: Alignment.center,
      child: icon != null
          ? Icon(icon, size: size * 0.5, color: c.onAccent)
          : Text(
              _initials,
              style: TextStyle(
                fontSize: size * 0.38,
                fontWeight: FontWeight.w600,
                color: c.onAccent,
              ),
            ),
    );
  }
}
