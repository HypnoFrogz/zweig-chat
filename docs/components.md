# Компоненты

## Mobile Android build
- `build.gradle.kts` (app): compile/target settings, release signing.
- `gradle.properties`: параметры стабильности сборки.
- `google-services.json`: Firebase/Google Services для Android.
- `key.properties` (локально): параметры подписи release.
- `upload-keystore.jks` (локально): ключ подписи релиза.

## Компоненты авторизации
- `backend/helpers.py`: генерация/валидация access и refresh JWT, хэширование refresh-token.
- `backend/routes/auth.py`: endpoint'ы `login`, `auth/refresh`, `auth/logout`, ротация refresh-сессий.
- `backend/routes/admin.py`: отзыв refresh-сессий при блокировке и сбросе пароля.
- `backend/database.py`: таблица `user_sessions` + индексы.
- `mobile/lib/api/api_client.dart`: secure storage двух токенов, авто-refresh и retry после `401`, конфигурируемый `API_BASE_URL` через `--dart-define` с fallback на production.
- `mobile/lib/providers/auth_provider.dart`: корректный автологин без сброса сессии при сетевых ошибках.

## Компоненты звонков (audio-first)
- `backend/database.py`: таблица `calls` (TTL, статус, адресные участники, media-state).
- `backend/routes/videocall.py`: endpoint'ы `calls/*`, совместимость со старыми `videocall/*`, очистка протухших `ringing` и устаревших `active` перед проверкой busy, фоновый cleanup-цикл и retention удаление старых finished-звонков.
- `backend/fcm_sender.py`: адресный data-only push `call_invite` для Android cold-call wake.
- `mobile/lib/api/calls_api.dart`: клиентские методы работы с `calls/*`.
- `mobile/lib/app.dart`: обработка входящего `call_invite` по `call_id`.
- `mobile/lib/app.dart`: accept-сценарий использует ответ `calls/answer` как источник `livekit_token/livekit_url/room_name`.
- `mobile/lib/screens/chat/chat_screen.dart`: direct-only проверка перед стартом адресного звонка и отображение причины backend-ошибки.
- `mobile/lib/websocket/ws_manager.dart`: refresh access-token перед websocket connect/reconnect для корректного presence.
- `frontend/app.js`: отображение причины ошибки `calls/start` (серверный `detail`/HTTP status).
- `frontend/app.js`: incoming-call state machine для web (`setIncomingCallData/clearIncomingCallUI`), корреляция `call_answered/call_ended` по `call_id`, TTL fallback по `expires_at`.
- `mobile/lib/screens/calls/call_screen.dart`: audio-first старт, ручные toggle для speaker и video.
- `nginx/nginx.conf`: прямой websocket proxy `/livekit/` на `livekit:7880` без rewrite/dynamic upstream.
- `backend/ws_manager.py`: удаление невалидных websocket-соединений при ошибке отправки + немедленное обновление presence.
- `mobile/lib/services/notification_service.dart`: background/foreground обработка FCM `call_invite` с incoming call notification/ringtone и сохранением pending invite.
- `mobile/lib/services/notification_service.dart`: call-уведомления показываются без звука push (`playSound/presentSound=false`), рингтон воспроизводится только ringtone-подсистемой.
- `mobile/lib/services/notification_service.dart`: отдельные аудио-плееры для ringtone/message и подавление message sound во время входящего звонка.
- `mobile/lib/services/notification_service.dart`: сохранение pending `call_invite` в `SharedPreferences` и очистка после обработки.
- `backend/routes/videocall.py`: для `calls/start` офлайн-callee поддерживается через push-wake fallback (`CALL_PUSH_ALWAYS`) с сохранением WS-доставки для online сценариев.
- `backend/routes/videocall.py`: для `calls/start` включён push-fallback (`CALL_PUSH_ALWAYS`) и web push wake (`send_push_to_user`) для фоновых сценариев cold-call.
- `frontend/service-worker.js`: обработка web push `type=call_invite` в фоне (incoming-call notification + open conversation on click).
- `frontend/service-worker.js`: для `call_invite` включён `silent: true`, чтобы push не генерировал самостоятельный звук.
- `frontend/app.js`: дедупликация входящих событий звонка по `call_id` для исключения повторного старта рингтона.
- `mobile/android/app/proguard-rules.pro`: keep-правила для `flutter_local_notifications`/Gson `TypeToken` в release.
- `mobile/lib/app.dart`: анти-дубль принятия звонка (`_isAcceptingCall`, `_acceptingCallId`).
- `mobile/lib/app.dart`: восстановление pending invite при старте `MainScreen` и гарантированный показ in-app кнопок принятия/отклонения.
