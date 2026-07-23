import 'package:dio/dio.dart';
import 'api_client.dart';

class AuthApi {
  final _client = ApiClient();

  Future<Map<String, dynamic>> login(String username, String password) async {
    // No sendTimeout here on purpose (see BaseOptions): it made dio throw/stall
    // this POST even though the server returned 200. Keep only a receive cap.
    final res = await _client.dio.post(
      '/login',
      data: {'username': username, 'password': password},
      options: Options(
        extra: {'skipAuth': true},
        receiveTimeout: const Duration(seconds: 20),
      ),
    );
    return res.data;
  }
  Future<Map<String, dynamic>> refresh(String refreshToken) async {
    final res = await _client.dio.post('/auth/refresh', data: {'refresh_token': refreshToken}, options: Options(extra: {'skipAuth': true}));
    return res.data;
  }
  Future<Map<String, dynamic>> logout({String? refreshToken}) async {
    final payload = <String, dynamic>{};
    if (refreshToken != null && refreshToken.isNotEmpty) payload['refresh_token'] = refreshToken;
    final res = await _client.dio.post('/auth/logout', data: payload);
    return res.data;
  }
  Future<Map<String, dynamic>> getMe() async { final res = await _client.dio.get('/me'); return res.data; }
  Future<Map<String, dynamic>> updateMe(Map<String, dynamic> data) async { final res = await _client.dio.put('/me', data: data); return res.data; }
  Future<Map<String, dynamic>> uploadAvatar(String filePath) async {
    final formData = FormData.fromMap({'file': await MultipartFile.fromFile(filePath)});
    final res = await _client.dio.post('/me/avatar', data: formData);
    return res.data;
  }
  Future<List<dynamic>> getUsers() async { final res = await _client.dio.get('/users'); return res.data; }
  Future<Map<String, dynamic>> getPresence() async { final res = await _client.dio.get('/presence'); return res.data; }
}
