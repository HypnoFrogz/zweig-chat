#!/usr/bin/env bash
# Готовит сервер к обновлению по кнопке из админки. Запускать под root на самой
# машине, один раз.
#
#   ./install-deploy-access.sh "ssh-ed25519 AAAA... adm@zweig"
#
# Что делает: заводит пользователя zweig-deploy, кладёт ему публичный ключ,
# ставит zweig-update.sh в /usr/local/bin и разрешает через sudo запускать
# ровно этот файл — и ничего больше. Украденный ключ после этого даёт
# возможность обновить сервер, а не хозяйничать на нём.
set -euo pipefail

PUBKEY="${1:-}"
USER_NAME="${DEPLOY_USER:-zweig-deploy}"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

[ "$(id -u)" = "0" ] || { echo "нужен root"; exit 1; }
[ -n "$PUBKEY" ] || { echo "передайте публичный ключ первым аргументом"; exit 1; }

id "$USER_NAME" >/dev/null 2>&1 || {
  echo "создаю пользователя $USER_NAME"
  useradd --create-home --shell /bin/bash "$USER_NAME"
}

# Docker нужен ему без sudo — обновление поднимает контейнеры.
getent group docker >/dev/null && usermod -aG docker "$USER_NAME"

HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "$HOME_DIR/.ssh"
touch "$HOME_DIR/.ssh/authorized_keys"
grep -qxF "$PUBKEY" "$HOME_DIR/.ssh/authorized_keys" || echo "$PUBKEY" >> "$HOME_DIR/.ssh/authorized_keys"
chmod 600 "$HOME_DIR/.ssh/authorized_keys"
chown "$USER_NAME:$USER_NAME" "$HOME_DIR/.ssh/authorized_keys"

install -m 755 "$SRC_DIR/zweig-update.sh" /usr/local/bin/zweig-update.sh
[ -f /etc/zweig-update.conf ] || {
  install -m 644 "$SRC_DIR/zweig-update.conf.example" /etc/zweig-update.conf
  echo "положил /etc/zweig-update.conf — ЗАПОЛНИТЕ его под этот сервер"
}

# Право ровно на один файл. Без NOPASSWD запуск по ключу повиснет на пароле.
cat > /etc/sudoers.d/zweig-deploy <<SUDO
$USER_NAME ALL=(root) NOPASSWD: /usr/local/bin/zweig-update.sh, /usr/local/bin/zweig-update.sh --check
SUDO
chmod 440 /etc/sudoers.d/zweig-deploy
visudo -cf /etc/sudoers.d/zweig-deploy >/dev/null

echo
echo "готово. Проверка с центральной машины:"
echo "  ssh $USER_NAME@<адрес> sudo /usr/local/bin/zweig-update.sh --check"
