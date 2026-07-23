class Channel {
  final String id;
  final String slug;
  final String? name;
  final String? description;
  final String type;
  final String? createdBy;
  final String? lastMsgTimestamp;
  final int unreadCount;
  final List<String> members;
  final List<Map<String, dynamic>>? memberDetails;
  final String? avatarPath;

  Channel({required this.id, required this.slug, this.name, this.description, this.type = 'public', this.createdBy, this.lastMsgTimestamp, this.unreadCount = 0, this.members = const [], this.memberDetails, this.avatarPath});

  factory Channel.fromJson(Map<String, dynamic> json) {
    // Extract last message timestamp from nested object or direct field
    String? lastMsgTs = json['last_msg_timestamp'];
    if (lastMsgTs == null && json['last_message'] is Map) {
      lastMsgTs = json['last_message']['timestamp'];
    }
    return Channel(
      id: json['id']?.toString() ?? '', slug: json['slug'] ?? '', name: json['name'], description: json['description'],
      type: json['type'] ?? 'public', createdBy: json['created_by'], lastMsgTimestamp: lastMsgTs,
      unreadCount: json['unread_count'] ?? 0,
      members: (json['members'] as List?)?.map((e) => e.toString()).toList() ?? [],
      memberDetails: (json['member_details'] as List?)?.cast<Map<String, dynamic>>(),
      avatarPath: json['avatar_path'],
    );
  }

  bool get isDirect => type == 'direct';
  bool get isPrivate => type == 'private';

  String displayName(String currentUser) {
    if (isDirect) {
      final other = members.where((m) => m != currentUser).firstOrNull;
      if (other != null && memberDetails != null) {
        final detail = memberDetails!.where((d) => d['username'] == other).firstOrNull;
        if (detail != null) return detail['display_name'] ?? other;
      }
      return other ?? name ?? slug;
    }
    return name ?? slug;
  }
}
