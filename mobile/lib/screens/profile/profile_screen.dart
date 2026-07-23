import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';
import '../../providers/auth_provider.dart';
import '../../providers/theme_provider.dart';
import '../../api/auth_api.dart';
import '../../widgets/avatar.dart';
import '../../widgets/toast.dart';
import '../settings/permissions_screen.dart';

class ProfileScreen extends ConsumerStatefulWidget {
  const ProfileScreen({super.key});
  @override
  ConsumerState<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends ConsumerState<ProfileScreen> {
  final _displayNameController = TextEditingController();
  final _nicknameController = TextEditingController();
  final _statusController = TextEditingController();
  bool _saving = false;
  bool _inited = false;

  @override
  void dispose() {
    _displayNameController.dispose();
    _nicknameController.dispose();
    _statusController.dispose();
    super.dispose();
  }

  void _initFields() {
    if (_inited) return;
    final user = ref.read(authProvider).user;
    if (user == null) return;
    _displayNameController.text = user.displayName ?? '';
    _nicknameController.text = user.nickname ?? '';
    _statusController.text = user.statusText ?? '';
    _inited = true;
  }

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      await AuthApi().updateMe({
        'display_name': _displayNameController.text.trim(),
        'nickname': _nicknameController.text.trim(),
        'status_text': _statusController.text.trim(),
      });
      if (mounted) {
        Toast.success(context, ref.read(authProvider.notifier).t('toast.profileSaved'));
      }
    } catch (e) {
      if (mounted) Toast.show(context, 'Ошибка', isError: true);
    }
    setState(() => _saving = false);
  }

  Future<void> _changeAvatar() async {
    final picker = ImagePicker();
    final image = await picker.pickImage(source: ImageSource.gallery, imageQuality: 80, maxWidth: 512);
    if (image == null) return;

    setState(() => _saving = true);
    try {
      await AuthApi().uploadAvatar(image.path);
      if (mounted) Toast.success(context, 'Аватар обновлён');
    } catch (e) {
      if (mounted) Toast.show(context, 'Ошибка загрузки аватара', isError: true);
    }
    setState(() => _saving = false);
  }

  void _confirmLogout() {
    final t = ref.read(authProvider.notifier).t;
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(t('settings.logout')),
        content: const Text('Вы уверены, что хотите выйти?'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Отмена')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Theme.of(context).colorScheme.error),
            onPressed: () {
              Navigator.pop(ctx);
              ref.read(authProvider.notifier).logout();
            },
            child: Text(t('settings.logout')),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final authState = ref.watch(authProvider);
    final isDark = ref.watch(themeProvider);
    final t = ref.read(authProvider.notifier).t;
    final theme = Theme.of(context);
    final user = authState.user;

    _initFields();

    return Scaffold(
      appBar: AppBar(
        title: Text(t('profile.title')),
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          children: [
            // Avatar
            GestureDetector(
              onTap: _changeAvatar,
              child: Stack(
                children: [
                  UserAvatar(
                    name: user?.name ?? '?',
                    avatarPath: user?.avatarPath,
                    size: 100,
                  ),
                  Positioned(
                    right: 0,
                    bottom: 0,
                    child: Container(
                      padding: const EdgeInsets.all(6),
                      decoration: BoxDecoration(
                        color: theme.colorScheme.primary,
                        shape: BoxShape.circle,
                      ),
                      child: const Icon(Icons.camera_alt, size: 18, color: Colors.white),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 8),
            Text(
              '@${user?.username ?? ''}',
              style: theme.textTheme.bodyMedium?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
            const SizedBox(height: 24),
            // Display Name
            TextField(
              controller: _displayNameController,
              decoration: InputDecoration(
                labelText: t('profile.displayName'),
                prefixIcon: const Icon(Icons.person),
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
              ),
            ),
            const SizedBox(height: 16),
            // Nickname
            TextField(
              controller: _nicknameController,
              decoration: InputDecoration(
                labelText: 'Никнейм',
                prefixIcon: const Icon(Icons.alternate_email),
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
              ),
            ),
            const SizedBox(height: 16),
            // Status
            TextField(
              controller: _statusController,
              decoration: InputDecoration(
                labelText: 'Статус',
                prefixIcon: const Icon(Icons.mood),
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
              ),
            ),
            const SizedBox(height: 24),
            // Save button
            SizedBox(
              width: double.infinity,
              height: 48,
              child: FilledButton(
                onPressed: _saving ? null : _save,
                child: _saving
                    ? const SizedBox(width: 24, height: 24, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                    : Text(t('profile.save')),
              ),
            ),
            const SizedBox(height: 32),
            const Divider(),
            const SizedBox(height: 16),
            // Settings section
            Text(
              t('settings.title'),
              style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 16),
            // Permissions health check
            ListTile(
              leading: const Icon(Icons.verified_user_outlined),
              title: const Text('Разрешения приложения'),
              subtitle: const Text('Проверить настройки уведомлений и звонков'),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => Navigator.push(
                context,
                MaterialPageRoute(
                    builder: (_) => const PermissionsScreen()),
              ),
            ),
            const Divider(height: 1),
            const SizedBox(height: 8),
            // Theme toggle
            ListTile(
              leading: Icon(isDark ? Icons.dark_mode : Icons.light_mode),
              title: Text(t('settings.theme')),
              subtitle: Text(isDark ? 'Тёмная' : 'Светлая'),
              trailing: Switch(
                value: isDark,
                onChanged: (_) => ref.read(themeProvider.notifier).toggle(),
              ),
            ),
            // Language
            ListTile(
              leading: const Icon(Icons.language),
              title: Text(t('settings.language')),
              subtitle: Text(authState.language == 'ru' ? 'Русский' : 'English'),
              trailing: SegmentedButton<String>(
                segments: const [
                  ButtonSegment(value: 'ru', label: Text('RU')),
                  ButtonSegment(value: 'en', label: Text('EN')),
                ],
                selected: {authState.language},
                onSelectionChanged: (v) => ref.read(authProvider.notifier).setLanguage(v.first),
              ),
            ),
            const SizedBox(height: 16),
            // Logout
            SizedBox(
              width: double.infinity,
              height: 48,
              child: OutlinedButton.icon(
                onPressed: _confirmLogout,
                icon: const Icon(Icons.logout),
                label: Text(t('settings.logout')),
                style: OutlinedButton.styleFrom(
                  foregroundColor: theme.colorScheme.error,
                  side: BorderSide(color: theme.colorScheme.error),
                ),
              ),
            ),
            const SizedBox(height: 32),
          ],
        ),
      ),
    );
  }
}
