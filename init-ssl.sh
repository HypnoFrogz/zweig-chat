#!/bin/bash
# ==============================================
# Let's Encrypt SSL для мессенджера Zweig.
# Запускать ПОСЛЕ: домен куплен, A-запись указывает сюда,
# порты 80/443 доступны снаружи.
# ==============================================

# Домен, на котором работает мессенджер (замени на свой):
DOMAIN="chat.example.com"
# Твоя почта для Let's Encrypt:
EMAIL="admin@example.com"

# Команда compose (переопределяемая через env при необходимости).
DC="${DC:-docker compose}"

echo "=== Шаг 1: Запускаем контейнеры ==="
$DC up -d nginx backend

echo ""
echo "=== Шаг 2: Получаем сертификат ==="
$DC run --rm --entrypoint certbot certbot certonly \
  --webroot \
  --webroot-path=/var/www/certbot \
  --email "$EMAIL" \
  --agree-tos \
  --no-eff-email \
  -d "$DOMAIN"

if [ $? -ne 0 ]; then
  echo ""
  echo "ОШИБКА: Не удалось получить сертификат. Проверь:"
  echo "  1. $DOMAIN резолвится на этот сервер (nslookup $DOMAIN)"
  echo "  2. Порт 80 открыт снаружи (ufw allow 80/tcp)"
  echo "  3. Контейнер nginx запущен (docker ps)"
  exit 1
fi

echo ""
echo "=== Шаг 3: Переключаем nginx на HTTPS ==="
# Bootstrap-конфиг (HTTP) заменяется прод-версией с TLS и LiveKit.
# ВАЖНО: nginx-ssl.conf ссылается на live/$DOMAIN/ — если твой домен другой,
# поправь server_name и ssl_certificate* в nginx/nginx-ssl.conf.
cp nginx/nginx-ssl.conf nginx/nginx.conf

echo ""
echo "=== Шаг 4: Перезапускаем контейнеры ==="
$DC up -d --force-recreate

echo ""
echo "================================================"
echo "  ГОТОВО!  Мессенджер: https://$DOMAIN"
echo "  Сертификат будет продлеваться автоматически."
echo "================================================"
