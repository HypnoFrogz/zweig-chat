import '../api/api_client.dart';

/// A chat folder (like Telegram's chat folders/tabs).
/// Synced with server via API.
class ChatFolder {
  final String id;
  String name;
  int position;
  List<String> channelSlugs;

  ChatFolder({required this.id, required this.name, this.position = 0, this.channelSlugs = const []});

  factory ChatFolder.fromJson(Map<String, dynamic> json) => ChatFolder(
    id: json['id'] ?? '',
    name: json['name'] ?? '',
    position: json['position'] ?? 0,
    channelSlugs: (json['channel_slugs'] as List?)?.cast<String>() ?? [],
  );
}

class ChatFoldersService {
  static final ChatFoldersService _instance = ChatFoldersService._internal();
  factory ChatFoldersService() => _instance;
  ChatFoldersService._internal();

  final _client = ApiClient();
  List<ChatFolder> _folders = [];
  List<ChatFolder> get folders => List.unmodifiable(_folders);

  Future<void> load() async {
    try {
      final res = await _client.dio.get('/chat-folders');
      final list = (res.data as List?) ?? [];
      _folders = list.map((e) => ChatFolder.fromJson(e as Map<String, dynamic>)).toList();
    } catch (e) {
      print('[ChatFoldersService] load error: $e');
    }
  }

  Future<ChatFolder?> createFolder(String name) async {
    try {
      final res = await _client.dio.post('/chat-folders', data: {'name': name});
      final folder = ChatFolder.fromJson(res.data);
      _folders.add(folder);
      return folder;
    } catch (e) {
      print('[ChatFoldersService] createFolder error: $e');
      return null;
    }
  }

  Future<void> renameFolder(String folderId, String newName) async {
    try {
      await _client.dio.put('/chat-folders/$folderId', data: {'name': newName});
      final folder = _folders.where((f) => f.id == folderId).firstOrNull;
      if (folder != null) folder.name = newName;
    } catch (e) {
      print('[ChatFoldersService] renameFolder error: $e');
    }
  }

  Future<void> deleteFolder(String folderId) async {
    try {
      await _client.dio.delete('/chat-folders/$folderId');
      _folders.removeWhere((f) => f.id == folderId);
    } catch (e) {
      print('[ChatFoldersService] deleteFolder error: $e');
    }
  }

  Future<void> addChannelToFolder(String folderId, String channelSlug) async {
    try {
      await _client.dio.post('/chat-folders/$folderId/channels', data: {'channel_slug': channelSlug});
      // Update local state: remove from other folders, add to this one
      for (final folder in _folders) {
        folder.channelSlugs = folder.channelSlugs.where((s) => s != channelSlug).toList();
      }
      final target = _folders.where((f) => f.id == folderId).firstOrNull;
      if (target != null) {
        target.channelSlugs = [...target.channelSlugs, channelSlug];
      }
    } catch (e) {
      print('[ChatFoldersService] addChannelToFolder error: $e');
    }
  }

  Future<void> removeChannelFromFolder(String folderId, String channelSlug) async {
    try {
      await _client.dio.delete('/chat-folders/$folderId/channels/$channelSlug');
      final folder = _folders.where((f) => f.id == folderId).firstOrNull;
      if (folder != null) {
        folder.channelSlugs = folder.channelSlugs.where((s) => s != channelSlug).toList();
      }
    } catch (e) {
      print('[ChatFoldersService] removeChannelFromFolder error: $e');
    }
  }

  /// Remove channel from all folders
  Future<void> removeChannelFromAllFolders(String channelSlug) async {
    for (final folder in List.from(_folders)) {
      if (folder.channelSlugs.contains(channelSlug)) {
        await removeChannelFromFolder(folder.id, channelSlug);
      }
    }
  }

  /// Get which folder a channel belongs to (null if none)
  String? getFolderForChannel(String channelSlug) {
    for (final folder in _folders) {
      if (folder.channelSlugs.contains(channelSlug)) return folder.id;
    }
    return null;
  }
}
