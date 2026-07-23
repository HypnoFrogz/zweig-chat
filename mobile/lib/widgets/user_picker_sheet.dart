import 'package:flutter/material.dart';
import '../api/auth_api.dart';
import 'avatar.dart';

/// Показывает нижний лист выбора пользователей (мультивыбор с поиском).
/// [excludeUsernames] — те, кого не показывать (уже в чате / уже в звонке).
/// Возвращает выбранные username, либо null если отменили.
Future<Set<String>?> showUserPickerSheet(
  BuildContext context, {
  required String title,
  String confirmLabel = 'Добавить',
  Set<String> excludeUsernames = const {},
}) async {
  List<dynamic> allUsers;
  try {
    allUsers = await AuthApi().getUsers();
  } catch (_) {
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Не удалось загрузить пользователей')),
      );
    }
    return null;
  }

  final candidates =
      allUsers.where((u) => !excludeUsernames.contains(u['username'])).toList();
  if (!context.mounted) return null;
  if (candidates.isEmpty) {
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Нет доступных пользователей')),
    );
    return null;
  }

  final selected = <String>{};
  String query = '';

  final confirmed = await showModalBottomSheet<bool>(
    context: context,
    isScrollControlled: true,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
    ),
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, setSheet) {
        final theme = Theme.of(ctx);
        final filtered = query.isEmpty
            ? candidates
            : candidates.where((u) {
                final name = (u['display_name'] ?? u['username'] ?? '')
                    .toString()
                    .toLowerCase();
                final uname = (u['username'] ?? '').toString().toLowerCase();
                return name.contains(query.toLowerCase()) ||
                    uname.contains(query.toLowerCase());
              }).toList();
        return DraggableScrollableSheet(
          expand: false,
          initialChildSize: 0.7,
          maxChildSize: 0.9,
          builder: (_, scrollController) => Column(
            children: [
              const SizedBox(height: 12),
              Text(title,
                  style: theme.textTheme.titleMedium
                      ?.copyWith(fontWeight: FontWeight.bold)),
              Padding(
                padding: const EdgeInsets.all(12),
                child: TextField(
                  decoration: InputDecoration(
                    hintText: 'Поиск...',
                    prefixIcon: const Icon(Icons.search, size: 20),
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(24),
                      borderSide: BorderSide.none,
                    ),
                    filled: true,
                    fillColor: theme.colorScheme.surfaceContainerHighest,
                    contentPadding:
                        const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                    isDense: true,
                  ),
                  onChanged: (v) => setSheet(() => query = v),
                ),
              ),
              Expanded(
                child: ListView.builder(
                  controller: scrollController,
                  itemCount: filtered.length,
                  itemBuilder: (_, i) {
                    final u = filtered[i];
                    final uname = u['username'].toString();
                    final name = (u['display_name'] ?? uname).toString();
                    final checked = selected.contains(uname);
                    return CheckboxListTile(
                      value: checked,
                      onChanged: (v) => setSheet(() {
                        if (v == true) {
                          selected.add(uname);
                        } else {
                          selected.remove(uname);
                        }
                      }),
                      secondary: UserAvatar(
                          name: name, avatarPath: u['avatar_path'], size: 40),
                      title:
                          Text(name, maxLines: 1, overflow: TextOverflow.ellipsis),
                      subtitle: Text('@$uname',
                          style: TextStyle(
                              fontSize: 12,
                              color: theme.colorScheme.onSurfaceVariant)),
                    );
                  },
                ),
              ),
              SafeArea(
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: SizedBox(
                    width: double.infinity,
                    child: FilledButton(
                      onPressed: selected.isEmpty
                          ? null
                          : () => Navigator.pop(ctx, true),
                      child: Text(selected.isEmpty
                          ? 'Выберите людей'
                          : '$confirmLabel (${selected.length})'),
                    ),
                  ),
                ),
              ),
            ],
          ),
        );
      },
    ),
  );

  if (confirmed == true && selected.isNotEmpty) return selected;
  return null;
}
