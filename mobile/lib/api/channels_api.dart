import 'package:dio/dio.dart';
import 'api_client.dart';

class ChannelsApi {
  final _client = ApiClient();

  Future<List<dynamic>> getChannels() async { final res = await _client.dio.get('/channels'); return res.data; }
  Future<List<dynamic>> getPublicChannels() async { final res = await _client.dio.get('/channels/public'); return res.data; }
  Future<Map<String, dynamic>> createChannel(Map<String, dynamic> data) async { final res = await _client.dio.post('/channels', data: data); return res.data; }
  Future<Map<String, dynamic>> getChannel(String slug) async { final res = await _client.dio.get('/channels/$slug'); return res.data; }
  Future<Map<String, dynamic>> updateChannel(String slug, Map<String, dynamic> data) async { final res = await _client.dio.put('/channels/$slug', data: data); return res.data; }
  Future<void> deleteChannel(String slug) async { await _client.dio.delete('/channels/$slug'); }
  Future<void> joinChannel(String slug) async { await _client.dio.post('/channels/$slug/join'); }
  Future<void> leaveChannel(String slug) async { await _client.dio.post('/channels/$slug/leave'); }
  Future<void> markRead(String slug) async { await _client.dio.post('/channels/$slug/read'); }
  Future<List<dynamic>> getMembers(String slug) async { final res = await _client.dio.get('/channels/$slug/members'); return res.data; }
  Future<Map<String, dynamic>> addMembers(String slug, List<String> usernames) async {
    final res = await _client.dio.put('/channels/$slug', data: {'add_members': usernames});
    return res.data;
  }
  Future<Map<String, dynamic>> removeMember(String slug, String username) async {
    final res = await _client.dio.put('/channels/$slug', data: {'remove_members': [username]});
    return res.data;
  }

  Future<Map<String, dynamic>> uploadChannelAvatar(String slug, String filePath) async {
    final formData = FormData.fromMap({
      'file': await MultipartFile.fromFile(filePath),
    });
    final res = await _client.dio.post('/channels/$slug/avatar', data: formData);
    return res.data;
  }
}
