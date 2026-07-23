import 'package:dio/dio.dart';
import 'api_client.dart';

class MessagesApi {
  final _client = ApiClient();

  Future<Map<String, dynamic>> getMessages(String slug, {int limit = 50, String? before}) async {
    final params = <String, dynamic>{'limit': limit};
    if (before != null) params['before'] = before;
    final res = await _client.dio.get('/channels/$slug/messages', queryParameters: params);
    return res.data;
  }
  Future<Map<String, dynamic>> sendMessage(String slug, {required String text, String type = 'text', String? replyTo, Map<String, dynamic>? file}) async {
    final data = <String, dynamic>{'text': text, 'type': type};
    if (replyTo != null) data['reply_to'] = replyTo;
    if (file != null) data['file'] = file;
    final res = await _client.dio.post('/channels/$slug/messages', data: data);
    return res.data;
  }
  Future<void> editMessage(String slug, String msgId, String text) async { await _client.dio.put('/channels/$slug/messages/$msgId', data: {'text': text}); }
  Future<void> deleteMessage(String slug, String msgId) async { await _client.dio.delete('/channels/$slug/messages/$msgId'); }
  Future<void> addReaction(String slug, String msgId, String emoji) async { await _client.dio.post('/channels/$slug/messages/$msgId/reactions', data: {'emoji': emoji}); }
  Future<void> removeReaction(String slug, String msgId, String emoji) async { await _client.dio.delete('/channels/$slug/messages/$msgId/reactions', data: {'emoji': emoji}); }
  Future<List<dynamic>> searchMessages(String query) async { final res = await _client.dio.get('/messages/search', queryParameters: {'q': query}); return res.data; }
  Future<Map<String, dynamic>> uploadChatFile(String filePath) async {
    final formData = FormData.fromMap({'file': await MultipartFile.fromFile(filePath)});
    final res = await _client.dio.post('/files/upload-to-chat', data: formData);
    return res.data;
  }

  Future<List<dynamic>> getPinnedMessages(String slug) async {
    final res = await _client.dio.get('/channels/$slug/pinned');
    return res.data;
  }
  Future<void> pinMessage(String slug, String msgId) async { await _client.dio.post('/channels/$slug/messages/$msgId/pin'); }
  Future<void> unpinMessage(String slug, String msgId) async { await _client.dio.delete('/channels/$slug/messages/$msgId/pin'); }
}
