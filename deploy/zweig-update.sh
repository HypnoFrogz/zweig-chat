#!/usr/bin/env bash
# Обновление одной установки Zweig. Запускается на самом сервере — руками или
# по SSH с управляющей машины.
#
#   zweig-update.sh            # обновить: git pull, пересборка, проверка, откат при неудаче
#   zweig-update.sh --check    # ничего не менять, показать текущее состояние
#
# Что и как обновлять, знает сервер, а не тот, кто нажал кнопку: у каждой
# установки свой каталог, свой набор compose-файлов и свои службы, которые надо
# поднять следом. Всё это лежит в /etc/zweig-update.conf, рядом с машиной.
#
# Скрипт не должен ронять сервер молча, поэтому неудачная проверка здоровья
# возвращает код на предыдущий коммит и пересобирает его обратно.
set -uo pipefail

CONF="${ZWEIG_UPDATE_CONF:-/etc/zweig-update.conf}"
LOCK="${ZWEIG_UPDATE_LOCK:-/var/lock/zweig-update.lock}"

log() { printf '%s  %s\n' "$(date '+%H:%M:%S')" "$*"; }
die() { log "ОШИБКА: $*"; exit 1; }

[ -r "$CONF" ] || die "нет файла настроек $CONF (см. zweig-update.conf.example)"
# shellcheck disable=SC1090
. "$CONF"

REPO_DIR="${REPO_DIR:-}"
COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.yml}"
SERVICES="${SERVICES:-backend}"
HEALTH_URL="${HEALTH_URL:-}"
HEALTH_TRIES="${HEALTH_TRIES:-30}"
HEALTH_DELAY="${HEALTH_DELAY:-3}"
POST_RESTART="${POST_RESTART:-}"
GIT_REMOTE="${GIT_REMOTE:-origin}"
GIT_BRANCH="${GIT_BRANCH:-main}"

[ -n "$REPO_DIR" ] || die "в $CONF не задан REPO_DIR"
[ -d "$REPO_DIR/.git" ] || die "$REPO_DIR не похож на git-каталог"

cd "$REPO_DIR" || die "не открывается $REPO_DIR"

# Компоновщик: старая docker-compose и новая docker compose называются по-разному.
# Ищется лениво — осмотр (--check) должен работать и на машине, где с docker
# что-то не так: именно тогда за осмотром и приходят.
COMPOSE=""
find_compose() {
  if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
  elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
  fi
  [ -n "$COMPOSE" ]
}

current_commit() { git rev-parse --short HEAD 2>/dev/null || echo "?"; }
current_subject() { git log -1 --format='%s' 2>/dev/null || echo "?"; }

show_state() {
  log "каталог:  $REPO_DIR"
  log "ветка:    $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
  log "коммит:   $(current_commit)  $(current_subject)"
  local dirty
  dirty="$(git status --porcelain 2>/dev/null | head -5)"
  [ -n "$dirty" ] && log "локальные изменения:" && printf '%s\n' "$dirty"
  git fetch --quiet "$GIT_REMOTE" "$GIT_BRANCH" 2>/dev/null && {
    local behind
    behind="$(git rev-list --count "HEAD..$GIT_REMOTE/$GIT_BRANCH" 2>/dev/null || echo '?')"
    log "отстаёт на $behind коммит(ов) от $GIT_REMOTE/$GIT_BRANCH"
  }
  if find_compose; then
    # shellcheck disable=SC2086
    $COMPOSE $COMPOSE_FILES ps 2>&1 | tail -n +1
  else
    log "docker compose не найден — контейнеры показать нечем"
  fi
}

health_ok() {
  [ -n "$HEALTH_URL" ] || { log "HEALTH_URL не задан — проверка пропущена"; return 0; }
  local i code
  for i in $(seq 1 "$HEALTH_TRIES"); do
    code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 "$HEALTH_URL" || echo 000)"
    if [ "$code" = "200" ]; then log "проверка здоровья: 200 (попытка $i)"; return 0; fi
    sleep "$HEALTH_DELAY"
  done
  log "проверка здоровья: последний ответ $code после $HEALTH_TRIES попыток"
  return 1
}

rebuild() {
  find_compose || { log "не найден ни docker compose, ни docker-compose"; return 1; }
  log "сборка и запуск: $SERVICES"
  # shellcheck disable=SC2086
  $COMPOSE $COMPOSE_FILES up -d --build $SERVICES 2>&1 || return 1
  for svc in $POST_RESTART; do
    log "перезапуск следом: $svc"
    docker restart "$svc" >/dev/null 2>&1 || log "  не удалось перезапустить $svc"
  done
  return 0
}

if [ "${1:-}" = "--check" ]; then
  show_state
  exit 0
fi

# Один запуск за раз: два обновления одновременно оставят каталог в состоянии,
# которое потом никто не разберёт. flock есть не везде (на macOS, например,
# нет), поэтому запасной вариант — каталог: mkdir атомарен на любой файловой
# системе.
[ -d "$(dirname "$LOCK")" ] || LOCK="/tmp/$(basename "$LOCK")"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK" || die "не открывается $LOCK"
  flock -n 9 || die "обновление уже идёт на этом сервере"
else
  mkdir "$LOCK.d" 2>/dev/null || die "обновление уже идёт на этом сервере"
  trap 'rmdir "$LOCK.d" 2>/dev/null' EXIT
fi

BEFORE="$(git rev-parse HEAD)"
log "было: $(current_commit)  $(current_subject)"

log "git pull --ff-only $GIT_REMOTE $GIT_BRANCH"
if ! git pull --ff-only "$GIT_REMOTE" "$GIT_BRANCH" 2>&1; then
  die "git pull не прошёл — вероятно, локальные изменения в рабочем каталоге"
fi

AFTER="$(git rev-parse HEAD)"
if [ "$BEFORE" = "$AFTER" ]; then
  log "новых коммитов нет — сервер уже на $(current_commit)"
  # Пересобирать нечего, но пусть проверка здоровья всё равно скажет, живо ли.
  health_ok || die "сервер не отвечает, хотя код не менялся"
  log "ГОТОВО (без изменений)"
  exit 0
fi

log "стало: $(current_commit)  $(current_subject)"

if ! rebuild; then
  log "сборка не удалась — откат на $(git rev-parse --short "$BEFORE")"
  git reset --hard "$BEFORE" >/dev/null 2>&1
  rebuild >/dev/null 2>&1
  die "сборка новой версии не удалась, вернули предыдущую"
fi

if ! health_ok; then
  log "сервер не отвечает после обновления — откат на $(git rev-parse --short "$BEFORE")"
  git reset --hard "$BEFORE" >/dev/null 2>&1
  if rebuild && health_ok; then
    die "новая версия не поднялась, вернули предыдущую — она отвечает"
  fi
  die "новая версия не поднялась, и откат тоже не отвечает — нужны руки"
fi

log "ГОТОВО: $(git rev-parse --short "$BEFORE") → $(current_commit)"
