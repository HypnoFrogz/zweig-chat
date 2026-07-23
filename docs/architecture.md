# Архитектура

## Android release pipeline
- Flutter mobile app (`mobile/`).
- Android build config: `mobile/android/app/build.gradle.kts`.
- Release signing: `mobile/android/key.properties` + keystore в `mobile/android/keystore/`.
- Output artifacts:
  - `mobile/build/app/outputs/flutter-apk/app-release.apk`
  - `mobile/build/app/outputs/bundle/release/app-release.aab`

## Архитектура авторизации
- `POST /api/login` выдаёт пару токенов: `access_token` (короткий) и `refresh_token` (долгий).
- `POST /api/auth/refresh` ротирует refresh-сессию и возвращает новую пару токенов.
- Сессии refresh хранятся в SQLite таблице `user_sessions` (id, hash токена, срок действия, revoke-метки).
- Мобильный `ApiClient` перехватывает `401`, выполняет refresh и повторяет исходный запрос автоматически.
- Mobile base URL задаётся **в рантайме**: на экране ввода адреса сервера (`ServerScreen`) адрес проверяется через `GET /api/health`, сохраняется в защищённом хранилище и применяется к `ApiClient`/WebSocket. Экран показывается, пока адрес не задан; сменить его можно с экрана входа.
- Опционально адрес можно «зашить» на этапе сборки через `--dart-define=API_BASE_URL=...` — он используется как дефолт, если пользователь ещё ничего не вводил.
- Адрес нормализуется на клиенте (добавляется схема при её отсутствии, убираются хвостовые `/`, при необходимости добавляется суффикс `/api`).

## Архитектура звонков (упрощённая)
- Адресный вызов: `caller -> callee` (без широковещательного cold-call по всем участникам).
- Серверная сущность `calls` хранит `status`, `expires_at`, `mode`, `video_enabled`, `speaker_enabled`.
- API `calls/*` управляет жизненным циклом звонка: `start`, `answer`, `reject`, `end`, `update-media`.
- В `calls/start` перед busy-check выполняется очистка протухших `ringing` записей (`expires_at < now`) во избежание ложного статуса "busy".
- В `calls/start` также закрываются устаревшие `active` звонки по порогу `CALL_ACTIVE_STALE_SEC`.
- В backend работает фоновый цикл обслуживания call-state: периодическая очистка + cleanup при старте приложения.
- Retention звонков без архивации: finished-статусы удаляются старше окна `CALL_RETENTION_DAYS`.
- Mobile запускает звонок в режиме `audio-first`; видео и speaker включаются вручную во время вызова.
- Mobile websocket перед connect/reconnect выполняет refresh access-token (`ensureAuthorized`) для устойчивого presence.
- Mobile inbound accept получает livekit-параметры из ответа `calls/answer` (server-authoritative), чтобы исключить рассинхрон invite payload после рестартов/deploy.
- Nginx proxy `/livekit/` использует прямой upstream `http://livekit:7880/` для корректного websocket upgrade.
- `ConnectionManager` при send-error очищает сломанный websocket и публикует offline presence для корректного `is_online()`.
- В `calls/start` включён FCM fallback для адресного потока (`CALL_PUSH_ALWAYS`): вызов допускается и для offline callee с фоновым wake сигналом.
- Mobile `NotificationService` обрабатывает FCM `call_invite` в фоне и foreground: сохраняет pending invite и показывает incoming call notification/ringtone как wake UX.
- Для `call_invite` локальные уведомления работают в silent-режиме; слышимый сигнал генерируется только ringtone-слоем приложения (без push-sound).
- В mobile разнесены аудиоканалы: отдельный плеер для message-sound и отдельный для ringtone, что исключает взаимное прерывание.
- Web push для адресного звонка публикуется через `routes.push.send_push_to_user` (`type=call_invite`, `call_id`, `expires_at`) как background wake путь для PWA.
- Web service worker публикует `call_invite` с `silent: true`, чтобы исключить системный звук push и оставить контроль рингтона на клиентской call-логике.
- Release-конфигурация Android использует явный proguard-файл `mobile/android/app/proguard-rules.pro` для сохранения generic-signatures (`TypeToken`).
- Mobile `MainScreen` сериализует accept-flow по `call_id`, исключая повторные `calls/answer` в одном жизненном цикле входящего вызова.
- Mobile хранит pending `call_invite` в локальном persistent storage и на старте экрана восстанавливает in-app incoming overlay как fail-safe к системным ограничениям уведомлений Android.
- Mobile коррелирует `call_ended`/`call_answered` для incoming overlay по `call_id` (приоритетно), чтобы избежать ложного закрытия по совпавшему `channel_slug`.
- Web `frontend/app.js` использует тот же принцип: incoming UI закрывается по `call_id`, а локальный TTL fallback по `expires_at` завершает рингтон и входящий overlay при задержке серверного события.
- Для устранения гонок WS/push в mobile и web добавлена дедупликация входящего `call_invite` по `call_id`.
