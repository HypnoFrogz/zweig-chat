import 'api_client.dart';

class CallsApi {
  final _client = ApiClient();

  Future<Map<String, dynamic>> startCall({
    required String channelSlug,
    required String calleeUsername,
    String mode = 'audio',
  }) async {
    final res = await _client.dio.post('/calls/start', data: {
      'channel_slug': channelSlug,
      'callee_username': calleeUsername,
      'mode': mode,
    });
    return Map<String, dynamic>.from(res.data as Map);
  }

  Future<Map<String, dynamic>> answerCall(String callId) async {
    final res = await _client.dio.post('/calls/answer', data: {'call_id': callId});
    return Map<String, dynamic>.from(res.data as Map);
  }

  Future<Map<String, dynamic>> rejectCall(String callId) async {
    final res = await _client.dio.post('/calls/reject', data: {'call_id': callId});
    return Map<String, dynamic>.from(res.data as Map);
  }

  Future<Map<String, dynamic>> endCall(String callId) async {
    final res = await _client.dio.post('/calls/end', data: {'call_id': callId});
    return Map<String, dynamic>.from(res.data as Map);
  }

  Future<Map<String, dynamic>> updateMedia({
    required String callId,
    required bool videoEnabled,
    required bool speakerEnabled,
  }) async {
    final res = await _client.dio.post('/calls/update-media', data: {
      'call_id': callId,
      'video_enabled': videoEnabled,
      'speaker_enabled': speakerEnabled,
    });
    return Map<String, dynamic>.from(res.data as Map);
  }

  Future<Map<String, dynamic>> getCall(String callId) async {
    final res = await _client.dio.get('/calls/$callId');
    return Map<String, dynamic>.from(res.data as Map);
  }

  // ── Групповые звонки (channel-based) ──────────────────────────────
  /// Начать групповой звонок в канале.
  Future<Map<String, dynamic>> startGroupCall(String channelSlug) async {
    final res = await _client.dio.post('/videocall/start', data: {'channel_slug': channelSlug});
    return Map<String, dynamic>.from(res.data as Map);
  }

  /// Проверить, идёт ли групповой звонок в канале (возвращает токен для подключения).
  Future<Map<String, dynamic>> getGroupCall(String channelSlug) async {
    final res = await _client.dio.get('/channels/$channelSlug/call');
    return Map<String, dynamic>.from(res.data as Map);
  }

  /// Завершить групповой звонок в канале.
  Future<void> endGroupCall(String channelSlug, {String? roomName}) async {
    await _client.dio.post('/videocall/end', data: {
      'channel_slug': channelSlug,
      if (roomName != null) 'room_name': roomName,
    });
  }

  /// Пригласить пользователей в идущий групповой звонок.
  Future<Map<String, dynamic>> inviteToCall(String channelSlug, List<String> usernames) async {
    final res = await _client.dio.post('/videocall/invite', data: {
      'channel_slug': channelSlug,
      'usernames': usernames,
    });
    return Map<String, dynamic>.from(res.data as Map);
  }
}
