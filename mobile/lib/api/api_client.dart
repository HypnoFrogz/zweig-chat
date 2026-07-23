import 'package:dio/dio.dart';
import 'dart:async';
import '../utils/storage.dart';

class ApiClient {
  // Server base URL, e.g. "https://host/api". Empty = not configured yet.
  // Set at runtime via the server-address screen (persisted); an optional
  // build-time default can be baked with --dart-define=API_BASE_URL=...
  static const String _envBase =
      String.fromEnvironment('API_BASE_URL', defaultValue: '');
  static String baseUrl = _normalizeApiBaseUrl(_envBase);

  static const String _serverKey = 'server_url';
  static const String _legacyTokenKey = 'token';
  static const String _accessTokenKey = 'access_token';
  static const String _refreshTokenKey = 'refresh_token';
  static final ApiClient _instance = ApiClient._internal();
  factory ApiClient() => _instance;

  late final Dio dio;
  final _storage = AppStorage();
  String? _accessToken;
  String? _refreshToken;
  String? get token => _accessToken;
  Future<void>? _refreshFuture;

  static String _normalizeApiBaseUrl(String rawValue) {
    var value = rawValue.trim();
    if (value.isEmpty) return '';
    // Default to https when no scheme is given. Falling back to http meant the
    // health check silently followed the 80->443 redirect (looked fine) while
    // the login POST hit the 301 and failed, since POST doesn't follow it.
    // Local/LAN servers without TLS can still be reached by typing http:// .
    if (!RegExp(r'^https?://', caseSensitive: false).hasMatch(value)) {
      value = 'https://$value';
    }
    while (value.endsWith('/')) {
      value = value.substring(0, value.length - 1);
    }
    if (!value.endsWith('/api')) {
      value = '$value/api';
    }
    return value;
  }

  bool get hasServer => baseUrl.isNotEmpty;

  /// Load the stored server URL. Call once at startup, before runApp().
  Future<void> loadServer() async {
    final stored = await _storage.read(key: _serverKey);
    if (stored != null && stored.isNotEmpty) {
      baseUrl = stored; // already normalized when stored
    }
    dio.options.baseUrl = baseUrl;
  }

  /// Persist and apply a new server URL.
  Future<void> setServer(String raw) async {
    baseUrl = _normalizeApiBaseUrl(raw);
    dio.options.baseUrl = baseUrl;
    await _storage.write(key: _serverKey, value: baseUrl);
  }

  /// Forget the configured server (used by "change server").
  Future<void> clearServer() async {
    baseUrl = '';
    dio.options.baseUrl = '';
    await _storage.delete(key: _serverKey);
  }

  /// Validate a server address by hitting its /api/health endpoint.
  Future<bool> pingServer(String raw) async {
    final base = _normalizeApiBaseUrl(raw);
    if (base.isEmpty) return false;
    try {
      final resp = await dio.get(
        '$base/health',
        options: Options(
          extra: {'skipAuth': true, 'didRetry': true},
          receiveTimeout: const Duration(seconds: 8),
          sendTimeout: const Duration(seconds: 8),
        ),
      );
      return resp.statusCode == 200 &&
          resp.data is Map &&
          (resp.data as Map)['status'] == 'ok';
    } catch (_) {
      return false;
    }
  }

  ApiClient._internal() {
    dio = Dio(BaseOptions(
      baseUrl: baseUrl,
      connectTimeout: const Duration(seconds: 15),
      receiveTimeout: const Duration(seconds: 30),
      // No global sendTimeout: dio can spuriously throw/stall a POST with a
      // buffered (non-stream) JSON body even after the server answered 200.
      // Streamed uploads set their own sendTimeout where it's actually safe.
    ));
    dio.interceptors.add(InterceptorsWrapper(
      onRequest: (options, handler) {
        if (_accessToken != null && options.extra['skipAuth'] != true) {
          options.headers['Authorization'] = 'Bearer $_accessToken';
        }
        return handler.next(options);
      },
      onError: (error, handler) async {
        final request = error.requestOptions;
        final statusCode = error.response?.statusCode;
        final canRetry = request.extra['didRetry'] != true;
        final isRefreshRequest = request.path.endsWith('/auth/refresh');

        if (statusCode == 401 && !isRefreshRequest && canRetry && _refreshToken != null) {
          final refreshed = await _refreshAccessToken();
          if (refreshed && _accessToken != null) {
            final headers = Map<String, dynamic>.from(request.headers);
            headers['Authorization'] = 'Bearer $_accessToken';
            headers.remove('content-length');
            final retryRequest = request.copyWith(
              headers: headers,
              extra: <String, dynamic>{...request.extra, 'didRetry': true},
            );
            final response = await dio.fetch(retryRequest);
            return handler.resolve(response);
          }
        }
        return handler.next(error);
      },
    ));
  }

  Future<void> setTokens(String accessToken, String refreshToken) async {
    _accessToken = accessToken;
    _refreshToken = refreshToken;
    await _storage.write(key: _accessTokenKey, value: accessToken);
    await _storage.write(key: _refreshTokenKey, value: refreshToken);
    await _storage.delete(key: _legacyTokenKey);
  }

  Future<String?> loadToken() async {
    _accessToken = await _storage.read(key: _accessTokenKey);
    _refreshToken = await _storage.read(key: _refreshTokenKey);

    // Legacy compatibility: old clients stored only `token`.
    if (_accessToken == null) {
      _accessToken = await _storage.read(key: _legacyTokenKey);
      if (_accessToken != null) {
        await _storage.write(key: _accessTokenKey, value: _accessToken);
        await _storage.delete(key: _legacyTokenKey);
      }
    }
    return _accessToken;
  }

  Future<bool> ensureAuthorized() async {
    if (_accessToken != null) return true;
    if (_refreshToken == null) await loadToken();
    if (_accessToken != null) return true;
    if (_refreshToken == null) return false;
    return _refreshAccessToken();
  }

  Future<void> clearToken() async {
    _accessToken = null;
    _refreshToken = null;
    await _storage.delete(key: _legacyTokenKey);
    await _storage.delete(key: _accessTokenKey);
    await _storage.delete(key: _refreshTokenKey);
  }

  Future<bool> _refreshAccessToken() async {
    if (_refreshToken == null) return false;
    if (_refreshFuture != null) {
      await _refreshFuture;
      return _accessToken != null;
    }

    final completer = Completer<void>();
    _refreshFuture = completer.future;
    try {
      final response = await dio.post(
        '/auth/refresh',
        data: {'refresh_token': _refreshToken},
        options: Options(extra: {'skipAuth': true, 'didRetry': true}),
      );
      final data = response.data as Map<String, dynamic>;
      final access = data['access_token'] as String?;
      final refresh = data['refresh_token'] as String?;
      if (access == null || refresh == null) {
        await clearToken();
        return false;
      }
      await setTokens(access, refresh);
      return true;
    } catch (_) {
      await clearToken();
      return false;
    } finally {
      completer.complete();
      _refreshFuture = null;
    }
  }
}
