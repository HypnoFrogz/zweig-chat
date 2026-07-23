import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:dio/dio.dart';
import '../api/api_client.dart';
import '../api/auth_api.dart';
import '../models/user.dart';
import '../websocket/ws_manager.dart';
import '../i18n/translations.dart';
import '../services/notification_service.dart';

class AuthState {
  final String? token; final User? user; final String language; final bool loading; final String? error;
  const AuthState({this.token, this.user, this.language = 'ru', this.loading = false, this.error});
  AuthState copyWith({String? token, User? user, String? language, bool? loading, String? error}) =>
    AuthState(token: token ?? this.token, user: user ?? this.user, language: language ?? this.language, loading: loading ?? this.loading, error: error);
}

class AuthNotifier extends StateNotifier<AuthState> {
  final _api = AuthApi(); final _client = ApiClient(); final _ws = WsManager();
  AuthNotifier() : super(const AuthState()) { _tryAutoLogin(); }

  String t(String key) => Translations.t(key, state.language);

  Future<void> _tryAutoLogin() async {
    await _client.loadToken();
    final canAuthorize = await _client.ensureAuthorized();
    if (!canAuthorize) return;

    state = state.copyWith(token: _client.token, loading: true, error: null);
    try {
      final data = await _api.getMe();
      state = state.copyWith(token: _client.token, user: User.fromJson(data), loading: false);
      NotificationService().registerToken();
    } on DioException catch (e) {
      final statusCode = e.response?.statusCode ?? 0;
      // Keep current session on transient network/server issues.
      if (statusCode == 401 || statusCode == 403) {
        await _client.clearToken();
        state = const AuthState();
      } else {
        state = state.copyWith(token: _client.token, loading: false, error: 'Network error');
      }
    } catch (_) {
      state = state.copyWith(token: _client.token, loading: false, error: 'Network error');
    }
  }

  Future<void> login(String username, String password) async {
    state = state.copyWith(loading: true, error: null);
    try {
      final data = await _api.login(username, password);
      final accessToken = (data['access_token'] ?? data['token'])?.toString();
      if (accessToken == null) {
        throw Exception('Invalid auth payload');
      }
      final refreshToken = data['refresh_token']?.toString() ?? '';
      await _client.setTokens(accessToken, refreshToken);
      state = state.copyWith(
        token: _client.token,
        user: User(username: data['username'], displayName: data['display_name'], role: data['role'] ?? 'user', avatarPath: data['avatar_path']),
        loading: false,
        error: null,
      );
      NotificationService().registerToken();
    } on DioException catch (e) {
      final data = e.response?.data;
      final detail = data is Map ? data['detail'] as String? : null;
      final fallback = e.response != null
          ? 'Ошибка входа (${e.response?.statusCode})'
          : 'Сервер недоступен: ${e.type.name}';
      state = state.copyWith(loading: false, error: detail ?? fallback);
    } catch (e) {
      state = state.copyWith(loading: false, error: 'Ошибка входа');
    }
  }

  void connectWebSocket() { if (state.token != null) _ws.connect(state.token!); }
  Future<void> logout() async { await NotificationService().unregisterToken(); _ws.disconnect(); await _client.clearToken(); state = const AuthState(); }
  void setLanguage(String lang) => state = state.copyWith(language: lang);
}

final authProvider = StateNotifierProvider<AuthNotifier, AuthState>((ref) => AuthNotifier());
