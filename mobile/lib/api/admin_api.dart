import 'api_client.dart';

class AdminApi {
  final _client = ApiClient();

  Future<List<dynamic>> getUsers() async {
    final res = await _client.dio.get('/admin/users');
    return res.data;
  }

  Future<Map<String, dynamic>> createUser({
    required String username,
    required String password,
    String? displayName,
    String role = 'user',
  }) async {
    final res = await _client.dio.post('/admin/users', data: {
      'username': username,
      'password': password,
      if (displayName != null && displayName.isNotEmpty) 'display_name': displayName,
      'role': role,
    });
    return res.data;
  }

  Future<Map<String, dynamic>> toggleBlock(String username, bool blocked) async {
    final res = await _client.dio.put('/admin/users/$username/block', data: {'blocked': blocked});
    return res.data;
  }

  Future<void> resetPassword(String username, String newPassword) async {
    await _client.dio.put('/admin/users/$username/reset-password', data: {'password': newPassword});
  }

  Future<Map<String, dynamic>> changeRole(String username, String role) async {
    final res = await _client.dio.put('/admin/users/$username/role', data: {'role': role});
    return res.data;
  }
}
