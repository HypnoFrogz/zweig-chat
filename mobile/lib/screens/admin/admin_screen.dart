import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../providers/auth_provider.dart';
import '../../api/admin_api.dart';
import '../../widgets/avatar.dart';

class AdminScreen extends ConsumerStatefulWidget {
  const AdminScreen({super.key});
  @override
  ConsumerState<AdminScreen> createState() => _AdminScreenState();
}

class _AdminScreenState extends ConsumerState<AdminScreen> {
  final _api = AdminApi();
  List<Map<String, dynamic>> _users = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _loadUsers();
  }

  Future<void> _loadUsers() async {
    try {
      final data = await _api.getUsers();
      if (mounted) {
        setState(() {
          _users = data.cast<Map<String, dynamic>>();
          _loading = false;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _loading = false);
      _showError('Ошибка загрузки: $e');
    }
  }

  void _showError(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(msg), backgroundColor: Colors.red),
    );
  }

  void _showSuccess(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(msg), backgroundColor: Colors.green),
    );
  }

  // ── Create User Dialog ──

  void _showCreateUserDialog() {
    final usernameCtrl = TextEditingController();
    final passwordCtrl = TextEditingController();
    final displayNameCtrl = TextEditingController();
    String role = 'user';

    showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) => AlertDialog(
          title: const Text('Создать пользователя'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextField(
                  controller: usernameCtrl,
                  decoration: const InputDecoration(labelText: 'Логин', border: OutlineInputBorder()),
                  textInputAction: TextInputAction.next,
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: passwordCtrl,
                  decoration: const InputDecoration(labelText: 'Пароль', border: OutlineInputBorder()),
                  obscureText: true,
                  textInputAction: TextInputAction.next,
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: displayNameCtrl,
                  decoration: const InputDecoration(labelText: 'Имя (необязательно)', border: OutlineInputBorder()),
                  textInputAction: TextInputAction.done,
                ),
                const SizedBox(height: 12),
                DropdownButtonFormField<String>(
                  value: role,
                  decoration: const InputDecoration(labelText: 'Роль', border: OutlineInputBorder()),
                  items: const [
                    DropdownMenuItem(value: 'user', child: Text('Пользователь')),
                    DropdownMenuItem(value: 'admin', child: Text('Администратор')),
                  ],
                  onChanged: (v) => setDialogState(() => role = v ?? 'user'),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Отмена')),
            FilledButton(
              onPressed: () async {
                final username = usernameCtrl.text.trim();
                final password = passwordCtrl.text;
                if (username.isEmpty || password.isEmpty) {
                  _showError('Заполните логин и пароль');
                  return;
                }
                Navigator.pop(ctx);
                try {
                  await _api.createUser(
                    username: username,
                    password: password,
                    displayName: displayNameCtrl.text.trim(),
                    role: role,
                  );
                  _showSuccess('Пользователь $username создан');
                  _loadUsers();
                } catch (e) {
                  _showError('Ошибка: $e');
                }
              },
              child: const Text('Создать'),
            ),
          ],
        ),
      ),
    );
  }

  // ── User Actions Menu ──

  void _showUserActions(Map<String, dynamic> user) {
    final username = user['username'] as String;
    final currentUser = ref.read(authProvider).user?.username;
    final isBlocked = user['blocked'] == 1 || user['blocked'] == true;
    final isAdmin = user['role'] == 'admin';
    final isSelf = username == currentUser;

    showModalBottomSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.all(16),
              child: Text(
                user['display_name'] ?? username,
                style: Theme.of(context).textTheme.titleMedium,
              ),
            ),
            if (!isSelf) ...[
              ListTile(
                leading: Icon(isBlocked ? Icons.lock_open : Icons.block, color: isBlocked ? Colors.green : Colors.orange),
                title: Text(isBlocked ? 'Разблокировать' : 'Заблокировать'),
                onTap: () {
                  Navigator.pop(ctx);
                  _toggleBlock(username, !isBlocked);
                },
              ),
              ListTile(
                leading: const Icon(Icons.password),
                title: const Text('Сбросить пароль'),
                onTap: () {
                  Navigator.pop(ctx);
                  _showResetPasswordDialog(username);
                },
              ),
              ListTile(
                leading: Icon(isAdmin ? Icons.person : Icons.admin_panel_settings),
                title: Text(isAdmin ? 'Сделать пользователем' : 'Сделать администратором'),
                onTap: () {
                  Navigator.pop(ctx);
                  _changeRole(username, isAdmin ? 'user' : 'admin');
                },
              ),
            ],
            if (isSelf)
              const ListTile(
                leading: Icon(Icons.info_outline, color: Colors.grey),
                title: Text('Это вы', style: TextStyle(color: Colors.grey)),
              ),
          ],
        ),
      ),
    );
  }

  Future<void> _toggleBlock(String username, bool block) async {
    try {
      await _api.toggleBlock(username, block);
      _showSuccess(block ? '$username заблокирован' : '$username разблокирован');
      _loadUsers();
    } catch (e) {
      _showError('Ошибка: $e');
    }
  }

  void _showResetPasswordDialog(String username) {
    final ctrl = TextEditingController();
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Сброс пароля: $username'),
        content: TextField(
          controller: ctrl,
          decoration: const InputDecoration(labelText: 'Новый пароль', border: OutlineInputBorder()),
          obscureText: true,
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Отмена')),
          FilledButton(
            onPressed: () async {
              final pw = ctrl.text;
              if (pw.length < 3) {
                _showError('Минимум 3 символа');
                return;
              }
              Navigator.pop(ctx);
              try {
                await _api.resetPassword(username, pw);
                _showSuccess('Пароль $username сброшен');
              } catch (e) {
                _showError('Ошибка: $e');
              }
            },
            child: const Text('Сбросить'),
          ),
        ],
      ),
    );
  }

  Future<void> _changeRole(String username, String newRole) async {
    try {
      await _api.changeRole(username, newRole);
      _showSuccess('Роль $username изменена на $newRole');
      _loadUsers();
    } catch (e) {
      _showError('Ошибка: $e');
    }
  }

  // ── Build ──

  @override
  Widget build(BuildContext context) {
    final t = ref.read(authProvider.notifier).t;

    return Scaffold(
      appBar: AppBar(title: Text(t('admin.title'))),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _loadUsers,
              child: _users.isEmpty
                  ? ListView(children: const [
                      SizedBox(height: 200),
                      Center(child: Text('Нет пользователей')),
                    ])
                  : ListView.builder(
                      itemCount: _users.length,
                      itemBuilder: (context, index) => _buildUserTile(_users[index]),
                    ),
            ),
      floatingActionButton: FloatingActionButton(
        onPressed: _showCreateUserDialog,
        child: const Icon(Icons.person_add),
      ),
    );
  }

  Widget _buildUserTile(Map<String, dynamic> user) {
    final username = user['username'] as String? ?? '';
    final displayName = user['display_name'] as String? ?? username;
    final role = user['role'] as String? ?? 'user';
    final isBlocked = user['blocked'] == 1 || user['blocked'] == true;
    final createdAt = user['created_at'] as String? ?? '';
    final avatarPath = user['avatar_path'] as String?;

    String dateStr = '';
    if (createdAt.isNotEmpty) {
      try {
        final dt = DateTime.parse(createdAt);
        dateStr = '${dt.day.toString().padLeft(2, '0')}.${dt.month.toString().padLeft(2, '0')}.${dt.year}';
      } catch (_) {}
    }

    return ListTile(
      leading: UserAvatar(
        name: displayName,
        avatarPath: avatarPath,
        size: 40,
      ),
      title: Row(
        children: [
          Flexible(child: Text(displayName, overflow: TextOverflow.ellipsis)),
          if (role == 'admin') ...[
            const SizedBox(width: 6),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
              decoration: BoxDecoration(
                color: Colors.blue.shade50,
                borderRadius: BorderRadius.circular(4),
              ),
              child: Text('admin', style: TextStyle(fontSize: 10, color: Colors.blue.shade700)),
            ),
          ],
          if (isBlocked) ...[
            const SizedBox(width: 6),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
              decoration: BoxDecoration(
                color: Colors.red.shade50,
                borderRadius: BorderRadius.circular(4),
              ),
              child: Text('blocked', style: TextStyle(fontSize: 10, color: Colors.red.shade700)),
            ),
          ],
        ],
      ),
      subtitle: Text('@$username${dateStr.isNotEmpty ? ' • $dateStr' : ''}',
          style: const TextStyle(fontSize: 12)),
      onTap: () => _showUserActions(user),
    );
  }
}
