class Message {
  final String id;
  final String channelId;
  final String sender;
  final String? senderName;
  final String? senderAvatar;
  final String type;
  final String text;
  final String timestamp;
  final String? editedAt;
  final List<String> readBy;
  final List<Reaction> reactions;
  final String? replyTo;
  final Map<String, dynamic>? replyToPreview;
  final int replyCount;
  final Map<String, dynamic>? file;
  final bool isPinned;
  final String? pinnedBy;
  final String? pinnedAt;

  Message({required this.id, required this.channelId, required this.sender, this.senderName, this.senderAvatar, this.type = 'text', required this.text, required this.timestamp, this.editedAt, this.readBy = const [], this.reactions = const [], this.replyTo, this.replyToPreview, this.replyCount = 0, this.file, this.isPinned = false, this.pinnedBy, this.pinnedAt});

  factory Message.fromJson(Map<String, dynamic> json) => Message(
    id: json['id']?.toString() ?? '', channelId: json['channel_id']?.toString() ?? '', sender: json['sender'] ?? '',
    senderName: json['sender_name'], senderAvatar: json['sender_avatar'], type: json['type'] ?? 'text', text: json['text'] ?? '',
    timestamp: json['timestamp'] ?? '', editedAt: json['edited_at'],
    readBy: (json['read_by'] as List?)?.map((e) => e.toString()).toList() ?? [],
    reactions: (json['reactions'] as List?)?.map((r) => Reaction.fromJson(r)).toList() ?? [],
    replyTo: json['reply_to']?.toString(), replyToPreview: json['reply_to_preview'],
    replyCount: json['reply_count'] ?? 0, file: json['file'],
    isPinned: json['is_pinned'] == true || json['is_pinned'] == 1,
    pinnedBy: json['pinned_by'], pinnedAt: json['pinned_at'],
  );

  bool get isEdited => editedAt != null;
  bool get isFile => type == 'file' && file != null;
  bool get isSystem => type == 'system';

  Message copyWith({
    String? id, String? channelId, String? sender, String? senderName, String? senderAvatar,
    String? type, String? text, String? timestamp, String? editedAt, List<String>? readBy,
    List<Reaction>? reactions, String? replyTo, Map<String, dynamic>? replyToPreview,
    int? replyCount, Map<String, dynamic>? file, bool? isPinned, String? pinnedBy, String? pinnedAt,
  }) => Message(
    id: id ?? this.id, channelId: channelId ?? this.channelId, sender: sender ?? this.sender,
    senderName: senderName ?? this.senderName, senderAvatar: senderAvatar ?? this.senderAvatar,
    type: type ?? this.type, text: text ?? this.text, timestamp: timestamp ?? this.timestamp,
    editedAt: editedAt ?? this.editedAt, readBy: readBy ?? this.readBy, reactions: reactions ?? this.reactions,
    replyTo: replyTo ?? this.replyTo, replyToPreview: replyToPreview ?? this.replyToPreview,
    replyCount: replyCount ?? this.replyCount, file: file ?? this.file,
    isPinned: isPinned ?? this.isPinned, pinnedBy: pinnedBy ?? this.pinnedBy, pinnedAt: pinnedAt ?? this.pinnedAt,
  );
}

class Reaction {
  final String emoji;
  final int count;
  final List<String> users;
  Reaction({required this.emoji, required this.count, required this.users});
  factory Reaction.fromJson(Map<String, dynamic> json) => Reaction(
    emoji: json['emoji'] ?? '', count: json['count'] ?? 0,
    users: (json['users'] as List?)?.map((e) => e.toString()).toList() ?? [],
  );
}
