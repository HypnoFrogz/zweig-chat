class User {
  final String username;
  final String? displayName;
  final String? nickname;
  final String? avatarPath;
  final String? statusText;
  final String role;
  bool isOnline;

  User({required this.username, this.displayName, this.nickname, this.avatarPath, this.statusText, this.role = 'user', this.isOnline = false});

  factory User.fromJson(Map<String, dynamic> json) => User(
    username: json['username'] ?? '', displayName: json['display_name'], nickname: json['nickname'],
    avatarPath: json['avatar_path'], statusText: json['status_text'], role: json['role'] ?? 'user',
  );

  String get name => displayName ?? username;
  String get initials {
    final n = name;
    if (n.isEmpty) return '?';
    final parts = n.split(' ');
    if (parts.length >= 2) return '${parts[0][0]}${parts[1][0]}'.toUpperCase();
    return n[0].toUpperCase();
  }
}
