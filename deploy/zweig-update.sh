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
KEEP_LOCAL="${KEEP_LOCAL:-}"
AFTER_PULL="${AFTER_PULL:-}"
HEALTH_RESOLVE="${HEALTH_RESOLVE:-}"

[ -n "$REPO_DIR" ] || die "в $CONF не задан REPO_DIR"
[ -d "$REPO_DIR/.git" ] || die "$REPO_DIR не похож на git-каталог"

cd "$REPO_DIR" || die "не открывается $REPO_DIR"

# Скрипт идёт от root (через sudo), а каталог обычно принадлежит обычному
# пользователю — тому, кто когда-то сделал git clone. С 2.35.2 git в таком
# каталоге отказывается работать («detected dubious ownership»). Доверяем ровно
# этому каталогу и только на время вызова: общий конфиг root не трогаем.
git() { command git -c safe.directory="$REPO_DIR" "$@"; }

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
  local i code resolve=()
  # Многие серверы за NAT не достают сами до себя по внешнему адресу
  # (hairpin NAT): curl на свой же домен висит и проверка «падает», хотя всё
  # работает. HEALTH_RESOLVE=127.0.0.1 — идти на этот адрес, сохранив домен
  # (SNI и Host), чтобы nginx отдал тот же сайт.
  if [ -n "$HEALTH_RESOLVE" ]; then
    local host port
    host="$(printf '%s' "$HEALTH_URL" | sed -E 's#^[a-z]+://([^/:]+).*#\1#')"
    port="$(printf '%s' "$HEALTH_URL" | sed -nE 's#^[a-z]+://[^/:]+:([0-9]+).*#\1#p')"
    if [ -z "$port" ]; then case "$HEALTH_URL" in https://*) port=443 ;; *) port=80 ;; esac; fi
    resolve=(--resolve "$host:$port:$HEALTH_RESOLVE")
  fi
  for i in $(seq 1 "$HEALTH_TRIES"); do
    code="$(curl -sk ${resolve[@]+"${resolve[@]}"} -o /dev/null -w '%{http_code}' --max-time 5 "$HEALTH_URL" 2>/dev/null)" || true
    code="${code:-000}"
    if [ "$code" = "200" ]; then log "проверка здоровья: 200 (попытка $i)"; return 0; fi
    sleep "$HEALTH_DELAY"
  done
  log "проверка здоровья: последний ответ $code после $HEALTH_TRIES попыток"
  return 1
}

# Локальные правки, без которых этот сервер не работает (обычно nginx с
# настоящим доменом вместо chat.example.com). git pull --ff-only на них
# спотыкается, поэтому: перед pull откладываем их в патч и возвращаем файлы к
# версии из репозитория, после pull — накатываем обратно. Если файл в
# репозитории за это время поменялся так, что патч не ложится, выполняется
# AFTER_PULL (например, sed с доменом) — он и так нужен, когда правки
# проще пересоздать, чем перенести.
KEEP_PATCH=""
stash_local() {
  [ -n "$KEEP_LOCAL" ] || return 0
  KEEP_PATCH="$(mktemp /tmp/zweig-keep.XXXXXX.patch)"
  # shellcheck disable=SC2086
  git diff --binary -- $KEEP_LOCAL >"$KEEP_PATCH" 2>/dev/null
  if [ -s "$KEEP_PATCH" ]; then
    log "откладываю локальные правки: $(git diff --name-only -- $KEEP_LOCAL | tr '\n' ' ')"
    # shellcheck disable=SC2086
    git checkout -- $KEEP_LOCAL || return 1
  fi
  return 0
}

restore_local() {
  # AFTER_PULL задан — правки пересоздаются им, патч не нужен: так надёжнее,
  # чем переносить diff на файл, который в репозитории мог поменяться.
  if [ -n "$AFTER_PULL" ]; then
    log "после pull: $AFTER_PULL"
    bash -c "$AFTER_PULL" 2>&1 || { log "AFTER_PULL завершился с ошибкой"; return 1; }
    return 0
  fi
  [ -n "$KEEP_PATCH" ] && [ -s "$KEEP_PATCH" ] || return 0
  if git apply --check "$KEEP_PATCH" >/dev/null 2>&1 && git apply "$KEEP_PATCH" >/dev/null 2>&1; then
    log "локальные правки возвращены"
    return 0
  fi
  log "локальные правки не ложатся на новую версию"
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

stash_local || die "не удалось отложить локальные правки ($KEEP_LOCAL)"

log "git pull --ff-only $GIT_REMOTE $GIT_BRANCH"
if ! git pull --ff-only "$GIT_REMOTE" "$GIT_BRANCH" 2>&1; then
  restore_local >/dev/null 2>&1
  dirty="$(git status --porcelain --untracked-files=no 2>/dev/null | head -5)"
  [ -n "$dirty" ] && log "в каталоге есть локальные правки — добавьте эти файлы в KEEP_LOCAL в $CONF:" && printf '%s\n' "$dirty"
  die "git pull не прошёл — причина в выводе git выше"
fi

restore_local || {
  log "откат на $(git rev-parse --short "$BEFORE"), чтобы не остаться без своих настроек"
  git reset -q --hard "$BEFORE"; restore_local >/dev/null 2>&1
  die "локальные правки не переносятся на новую версию — задайте AFTER_PULL в $CONF"
}

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
  restore_local >/dev/null 2>&1
  rebuild >/dev/null 2>&1
  die "сборка новой версии не удалась, вернули предыдущую"
fi

if ! health_ok; then
  log "сервер не отвечает после обновления — откат на $(git rev-parse --short "$BEFORE")"
  git reset --hard "$BEFORE" >/dev/null 2>&1
  restore_local >/dev/null 2>&1
  if rebuild && health_ok; then
    die "новая версия не поднялась, вернули предыдущую — она отвечает"
  fi
  die "новая версия не поднялась, и откат тоже не отвечает — нужны руки"
fi

[ -n "$KEEP_PATCH" ] && rm -f "$KEEP_PATCH"
log "ГОТОВО: $(git rev-parse --short "$BEFORE") → $(current_commit)"
