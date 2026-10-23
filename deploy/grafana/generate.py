#!/usr/bin/env python3
"""Собирает дашборды Grafana для Zweig из запросов ниже.

Править запросы и раскладку — здесь, затем:  python3 deploy/grafana/generate.py
Результат — dashboards/*.json (их и подхватывает Grafana при старте).

Источник данных — SQLite мессенджера через плагин frser-sqlite-datasource.
Все отметки времени в базе — UTC; переменная $tz сдвигает их в местное время
(по умолчанию +3, Москва).
"""
import json
from pathlib import Path

OUT = Path(__file__).parent / "dashboards"
DS = {"type": "frser-sqlite-datasource", "uid": "zweig-sqlite"}

# Границы выбранного периода для разных форматов времени в базе.
TS_RANGE = ("{c} >= strftime('%Y-%m-%dT%H:%M:%S', ${{__from:date:seconds}}, 'unixepoch') "
            "AND {c} < strftime('%Y-%m-%dT%H:%M:%S', ${{__to:date:seconds}}, 'unixepoch')")
HOUR_RANGE = ("{c} >= strftime('%Y-%m-%d %H:00', ${{__from:date:seconds}}, 'unixepoch') "
              "AND {c} <= strftime('%Y-%m-%d %H:00', ${{__to:date:seconds}}, 'unixepoch')")


def ts(c):
    return TS_RANGE.format(c=c)


def hr(c):
    return HOUR_RANGE.format(c=c)


LOCAL_USERS = "COALESCE(home_server, '') = ''"
CLIENT_NAME = ("CASE {c} WHEN 'ios' THEN 'iPhone' WHEN 'android' THEN 'Android' WHEN 'mobile' THEN 'Телефон' "
               "WHEN 'desktop' THEN 'Компьютер (приложение)' WHEN 'web' THEN 'Браузер' ELSE {c} END")
CALL_STATUS = ("CASE {c} WHEN 'ended' THEN 'состоялся' WHEN 'missed' THEN 'пропущен' WHEN 'rejected' THEN 'отклонён' "
               "WHEN 'cancelled' THEN 'отменён' WHEN 'active' THEN 'идёт' ELSE {c} END")
WEEKDAY = ("CASE strftime('%w', {c}) WHEN '1' THEN 'Пн' WHEN '2' THEN 'Вт' WHEN '3' THEN 'Ср' WHEN '4' THEN 'Чт' "
           "WHEN '5' THEN 'Пт' WHEN '6' THEN 'Сб' ELSE 'Вс' END")

_id = [0]


def nid():
    _id[0] += 1
    return _id[0]


def target(sql, time_series=False):
    t = {"refId": "A", "datasource": DS, "queryText": sql, "rawQueryText": sql,
         "queryType": "time series" if time_series else "table"}
    if time_series:
        t["timeColumns"] = ["time"]
    return t


def stat(title, sql, x, y, w=4, h=4, unit="none", desc="", color="green"):
    return {"id": nid(), "type": "stat", "title": title, "description": desc, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": [target(sql)],
            "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "fixed", "fixedColor": color}}, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "value", "graphMode": "none", "textMode": "value", "justifyMode": "center"}}


def series(title, sql, x, y, w=12, h=8, bars=True, desc="", stack=False):
    return {"id": nid(), "type": "timeseries", "title": title, "description": desc, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": [target(sql, True)],
            "fieldConfig": {"defaults": {"custom": {
                "drawStyle": "bars" if bars else "line", "fillOpacity": 70 if bars else 15, "lineWidth": 1,
                "barAlignment": 0, "showPoints": "never" if bars else "auto", "spanNulls": False,
                "stacking": {"mode": "normal" if stack else "none", "group": "A"}},
                "color": {"mode": "palette-classic"}, "min": 0}, "overrides": []},
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                        "tooltip": {"mode": "multi", "sort": "none"}}}


def bars(title, sql, x, y, w=12, h=8, desc=""):
    return {"id": nid(), "type": "barchart", "title": title, "description": desc, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": [target(sql)],
            "fieldConfig": {"defaults": {"color": {"mode": "palette-classic"}, "min": 0,
                                         "custom": {"fillOpacity": 80, "lineWidth": 0}}, "overrides": []},
            "options": {"orientation": "vertical", "showValue": "never", "xTickLabelRotation": 0,
                        "legend": {"showLegend": False, "displayMode": "list", "placement": "bottom"},
                        "tooltip": {"mode": "single"}, "barWidth": 0.8, "groupWidth": 0.7}}


def table(title, sql, x, y, w=24, h=10, desc=""):
    return {"id": nid(), "type": "table", "title": title, "description": desc, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": [target(sql)],
            "fieldConfig": {"defaults": {"custom": {"align": "auto", "filterable": True}}, "overrides": []},
            "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}}}


def pie(title, sql, x, y, w=8, h=8, desc=""):
    return {"id": nid(), "type": "piechart", "title": title, "description": desc, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": [target(sql)],
            "fieldConfig": {"defaults": {"color": {"mode": "palette-classic"}}, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True},
                        "pieType": "donut", "legend": {"displayMode": "table", "placement": "right",
                                                        "values": ["value", "percent"], "showLegend": True},
                        "displayLabels": []}}


def row(title, y):
    return {"id": nid(), "type": "row", "title": title, "collapsed": False,
            "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}


TZ_VAR = {"name": "tz", "label": "Часовой пояс, ч от UTC", "type": "custom", "query": "+3,+2,+4,+5,+6,+7,+8,+9,+10,+11,+12,0",
          "current": {"text": "+3", "value": "+3"}, "hide": 0, "includeAll": False, "multi": False,
          "options": []}


def dashboard(uid, title, panels, variables, desc, time_from="now-30d"):
    return {"uid": uid, "title": title, "description": desc, "tags": ["zweig"], "timezone": "browser",
            "schemaVersion": 39, "version": 1, "editable": False, "graphTooltip": 1,
            "time": {"from": time_from, "to": "now"}, "refresh": "5m",
            "timepicker": {"refresh_intervals": ["1m", "5m", "15m", "1h"]},
            "templating": {"list": variables}, "annotations": {"list": []}, "panels": panels,
            "links": [{"title": "Дашборды Zweig", "type": "dashboards", "tags": ["zweig"], "asDropdown": True}]}


ACTIVE_DAYS = (
    "SELECT date(datetime(hour, '$tz hours')) AS d, username FROM activity_hourly WHERE " + hr("hour") +
    " UNION SELECT date, username FROM user_activity_log WHERE date >= date(${__from:date:seconds}, 'unixepoch')"
    " AND date <= date(${__to:date:seconds}, 'unixepoch')"
)


def usage():
    p = []
    y = 0
    p.append(row("Сейчас и за период", y)); y += 1
    p.append(stat("В сети сейчас",
                  "SELECT COUNT(*) AS value FROM online_sessions WHERE online = 1 "
                  "AND updated_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-3 minutes')",
                  0, y, desc="Подключены прямо сейчас (обновление раз в 5 минут)."))
    for i, (title, days, d) in enumerate([("Активны сегодня (DAU)", 0, "Заходили сегодня, по UTC."),
                                          ("За 7 дней (WAU)", 6, "Заходили хотя бы раз за последние 7 дней."),
                                          ("За 30 дней (MAU)", 29, "Заходили хотя бы раз за последние 30 дней.")]):
        p.append(stat(title,
                      "SELECT COUNT(DISTINCT username) AS value FROM ("
                      f"SELECT username FROM activity_hourly WHERE hour >= strftime('%Y-%m-%d 00:00', 'now', '-{days} days') "
                      f"UNION SELECT username FROM user_activity_log WHERE date >= date('now', '-{days} days'))",
                      4 * (i + 1), y, desc=d, color="blue"))
    p.append(stat("Всего сотрудников", f"SELECT COUNT(*) AS value FROM users WHERE {LOCAL_USERS} AND blocked = 0",
                  16, y, desc="Учётные записи этого сервера, без заблокированных и гостей с других серверов.",
                  color="purple"))
    p.append(stat("Новых за период", f"SELECT COUNT(*) AS value FROM users WHERE {LOCAL_USERS} AND " + ts("created_at"),
                  20, y, color="orange"))
    y += 4

    p.append(row("Активность", y)); y += 1
    p.append(series("Активные пользователи по дням",
                    "SELECT CAST(strftime('%s', d) AS INTEGER) AS time, COUNT(DISTINCT username) AS \"Активные\" "
                    f"FROM ({ACTIVE_DAYS}) GROUP BY d ORDER BY d", 0, y))
    p.append(series("Сообщения по дням",
                    "SELECT CAST(strftime('%s', date(datetime(timestamp, '$tz hours'))) AS INTEGER) AS time, "
                    "SUM(type != 'file') AS \"Текст\", SUM(type = 'file') AS \"Файлы\" "
                    "FROM messages WHERE sender != 'system' AND type != 'call' AND " + ts("timestamp") +
                    " GROUP BY 1 ORDER BY 1", 12, y, stack=True))
    y += 8
    p.append(bars("По часам суток",
                  "SELECT strftime('%H', datetime(hour, '$tz hours')) AS \"Час\", COUNT(*) AS \"Человеко-часов\" "
                  "FROM activity_hourly WHERE " + hr("hour") + " GROUP BY 1 ORDER BY 1", 0, y,
                  desc="Сколько людей было в сети в каждый час суток, суммарно за период."))
    p.append(bars("По дням недели",
                  "SELECT " + WEEKDAY.format(c="datetime(hour, '$tz hours')") + " AS \"День\", "
                  "COUNT(DISTINCT username || date(datetime(hour, '$tz hours'))) AS \"Человеко-дней\" "
                  "FROM activity_hourly WHERE " + hr("hour") +
                  " GROUP BY 1 ORDER BY (CAST(strftime('%w', datetime(hour, '$tz hours')) AS INTEGER) + 6) % 7", 12, y))
    y += 8

    p.append(row("Сотрудники", y)); y += 1
    def in_period(alias, col):
        return ts(f"{alias}.{col}")
    p.append(table("Использование по сотрудникам",
                   "SELECT u.username AS \"Логин\", u.display_name AS \"Имя\", "
                   "replace(substr(datetime(NULLIF(u.last_seen, ''), '$tz hours'), 1, 16), 'T', ' ') AS \"Последний раз в сети\", "
                   "(SELECT COUNT(DISTINCT d) FROM (" + ACTIVE_DAYS + ") ad WHERE ad.username = u.username) AS \"Дней активности\", "
                   "(SELECT COUNT(*) FROM activity_hourly a WHERE a.username = u.username AND " + hr("a.hour") + ") AS \"Часов в сети\", "
                   "(SELECT COUNT(*) FROM messages m WHERE m.sender = u.username AND m.type NOT IN ('file', 'call') AND " + in_period("m", "timestamp") + ") AS \"Сообщений\", "
                   "(SELECT COUNT(*) FROM messages m WHERE m.sender = u.username AND m.type = 'file' AND " + in_period("m", "timestamp") + ") AS \"Файлов\", "
                   "(SELECT COUNT(*) FROM call_log c WHERE (c.started_by = u.username OR c.callee = u.username) AND " + in_period("c", "started_at") + ") AS \"Звонков\", "
                   "(SELECT ROUND(COALESCE(SUM(c.duration_sec), 0) / 60.0) FROM call_log c WHERE (c.started_by = u.username OR c.callee = u.username) AND c.status = 'ended' AND " + in_period("c", "started_at") + ") AS \"Минут в звонках\", "
                   "(SELECT group_concat(name, ', ') FROM (SELECT DISTINCT " + CLIENT_NAME.format(c="a.client") + " AS name FROM activity_hourly a WHERE a.username = u.username AND " + hr("a.hour") + ")) AS \"Устройства\" "
                   f"FROM users u WHERE {LOCAL_USERS} AND u.blocked = 0 ORDER BY \"Часов в сети\" DESC, \"Сообщений\" DESC",
                   0, y, h=12,
                   desc="За выбранный период. Звонки — один на один и начатые человеком общие; участие в чужом "
                        "общем звонке пока не учитывается."))
    y += 12
    p.append(table("Давно не заходили (больше 14 дней)",
                   "SELECT username AS \"Логин\", display_name AS \"Имя\", "
                   "replace(substr(datetime(NULLIF(last_seen, ''), '$tz hours'), 1, 16), 'T', ' ') AS \"Последний раз в сети\", "
                   "CAST(julianday('now') - julianday(NULLIF(last_seen, '')) AS INTEGER) AS \"Дней назад\" "
                   f"FROM users WHERE {LOCAL_USERS} AND blocked = 0 AND (last_seen = '' OR last_seen < strftime('%Y-%m-%dT%H:%M:%S', 'now', '-14 days')) "
                   "ORDER BY last_seen", 0, y, w=12, h=9))
    p.append(pie("Устройства (людей за период)",
                 "SELECT " + CLIENT_NAME.format(c="client") + " AS \"Клиент\", COUNT(DISTINCT username) AS \"Людей\" "
                 "FROM activity_hourly WHERE " + hr("hour") + " GROUP BY client ORDER BY 2 DESC", 12, y, w=6, h=9))
    p.append(table("Версии приложений",
                   "SELECT " + CLIENT_NAME.format(c="client") + " AS \"Клиент\", app_version AS \"Версия\", "
                   "COUNT(DISTINCT username) AS \"Людей\" FROM session_log WHERE app_version != '' AND " + ts("started_at") +
                   " GROUP BY 1, 2 ORDER BY 3 DESC", 18, y, w=6, h=9,
                   desc="Приложения, которые сообщают свою версию при подключении."))
    y += 9

    p.append(row("Звонки", y)); y += 1
    p.append(series("Звонки по дням",
                    "SELECT CAST(strftime('%s', date(datetime(started_at, '$tz hours'))) AS INTEGER) AS time, "
                    "SUM(status = 'ended') AS \"Состоялись\", SUM(status IN ('missed', 'rejected', 'cancelled')) AS \"Не состоялись\" "
                    "FROM call_log WHERE " + ts("started_at") + " GROUP BY 1 ORDER BY 1", 0, y, w=12, stack=True))
    p.append(stat("Звонков за период", "SELECT COUNT(*) AS value FROM call_log WHERE " + ts("started_at"), 12, y, w=4, color="blue"))
    p.append(stat("Минут в звонках", "SELECT ROUND(COALESCE(SUM(duration_sec), 0) / 60.0) AS value FROM call_log "
                  "WHERE status = 'ended' AND " + ts("started_at"), 16, y, w=4, color="green"))
    p.append(stat("Средний звонок, мин", "SELECT ROUND(AVG(duration_sec) / 60.0, 1) AS value FROM call_log "
                  "WHERE status = 'ended' AND duration_sec > 0 AND " + ts("started_at"), 20, y, w=4, color="purple"))
    p.append(stat("Не дозвонились", "SELECT COUNT(*) AS value FROM call_log WHERE status IN ('missed', 'rejected', 'cancelled') AND "
                  + ts("started_at"), 12, y + 4, w=4, color="red"))
    p.append(stat("Видеозвонков", "SELECT COUNT(*) AS value FROM call_log WHERE (mode = 'video' OR callee IS NULL) AND "
                  + ts("started_at"), 16, y + 4, w=4, color="orange", desc="Общие звонки в каналах — всегда видео."))
    p.append(stat("Самый длинный, мин", "SELECT ROUND(MAX(duration_sec) / 60.0) AS value FROM call_log WHERE status = 'ended' AND "
                  + ts("started_at"), 20, y + 4, w=4, color="yellow"))
    y += 8
    p.append(table("Последние звонки",
                   "SELECT replace(substr(datetime(started_at, '$tz hours'), 1, 16), 'T', ' ') AS \"Начало\", "
                   "started_by AS \"Кто звонил\", COALESCE(callee, 'общий звонок') AS \"Кому\", "
                   "CASE COALESCE(mode, 'video') WHEN 'audio' THEN 'голос' ELSE 'видео' END AS \"Тип\", "
                   + CALL_STATUS.format(c="status") + " AS \"Итог\", "
                   "ROUND(COALESCE(duration_sec, 0) / 60.0, 1) AS \"Минут\" "
                   "FROM call_log WHERE " + ts("started_at") + " ORDER BY started_at DESC LIMIT 300", 0, y, h=10))
    y += 10

    p.append(row("Каналы", y)); y += 1
    p.append(table("Самые активные каналы",
                   "SELECT name AS \"Канал\", kind AS \"Тип\", msgs AS \"Сообщений\", authors AS \"Авторов\" FROM ("
                   "SELECT COALESCE(NULLIF(c.name, ''), c.slug) AS name, CASE c.type WHEN 'private' THEN 'закрытый' ELSE 'открытый' END AS kind, "
                   "COUNT(*) AS msgs, COUNT(DISTINCT m.sender) AS authors FROM messages m JOIN channels c ON c.id = m.channel_id "
                   "WHERE c.type != 'direct' AND m.sender != 'system' AND " + ts("m.timestamp") + " GROUP BY m.channel_id "
                   "UNION ALL SELECT 'Личные переписки (все)', 'личные', COUNT(*), COUNT(DISTINCT m.sender) FROM messages m "
                   "JOIN channels c ON c.id = m.channel_id WHERE c.type = 'direct' AND m.sender != 'system' AND " + ts("m.timestamp") +
                   ") WHERE msgs > 0 ORDER BY msgs DESC LIMIT 25", 0, y, h=10,
                   desc="Личные переписки показаны одной строкой: кто с кем переписывается, дашборд не раскрывает."))
    return dashboard("zweig-usage", "Zweig — использование", p, [TZ_VAR],
                     "Кто и когда пользуется мессенджером: активность, сотрудники, устройства, звонки, каналы.")


def person():
    user_var = {"name": "user", "label": "Сотрудник", "type": "query", "datasource": DS,
                "query": f"SELECT username FROM users WHERE {LOCAL_USERS} AND blocked = 0 ORDER BY username",
                "definition": "", "refresh": 1, "hide": 0, "includeAll": False, "multi": False, "sort": 1,
                "current": {}, "options": []}
    # Плагин SQLite понимает переменную запроса в поле queryText.
    user_var["query"] = {"queryText": user_var["query"], "rawQueryText": user_var["query"], "queryType": "table"}
    p = []
    y = 0
    p.append(stat("Последний раз в сети",
                  "SELECT replace(substr(datetime(NULLIF(last_seen, ''), '$tz hours'), 1, 16), 'T', ' ') AS value FROM users WHERE username = '$user'",
                  0, y, w=6, color="blue"))
    p.append(stat("Дней активности", "SELECT COUNT(DISTINCT d) AS value FROM (" + ACTIVE_DAYS + ") WHERE username = '$user'", 6, y, w=4))
    p.append(stat("Часов в сети", "SELECT COUNT(*) AS value FROM activity_hourly WHERE username = '$user' AND " + hr("hour"), 10, y, w=4))
    p.append(stat("Сообщений", "SELECT COUNT(*) AS value FROM messages WHERE sender = '$user' AND type NOT IN ('call') AND " + ts("timestamp"), 14, y, w=3, color="purple"))
    p.append(stat("Звонков", "SELECT COUNT(*) AS value FROM call_log WHERE (started_by = '$user' OR callee = '$user') AND " + ts("started_at"), 17, y, w=3, color="orange"))
    p.append(stat("Минут в звонках", "SELECT ROUND(COALESCE(SUM(duration_sec), 0) / 60.0) AS value FROM call_log WHERE (started_by = '$user' OR callee = '$user') AND status = 'ended' AND " + ts("started_at"), 20, y, w=4, color="yellow"))
    y += 4
    p.append(series("Часов в сети по дням",
                    "SELECT CAST(strftime('%s', date(datetime(hour, '$tz hours'))) AS INTEGER) AS time, COUNT(*) AS \"Часов\" "
                    "FROM activity_hourly WHERE username = '$user' AND " + hr("hour") + " GROUP BY 1 ORDER BY 1", 0, y))
    p.append(series("Сообщений по дням",
                    "SELECT CAST(strftime('%s', date(datetime(timestamp, '$tz hours'))) AS INTEGER) AS time, COUNT(*) AS \"Сообщений\" "
                    "FROM messages WHERE sender = '$user' AND type != 'call' AND " + ts("timestamp") + " GROUP BY 1 ORDER BY 1", 12, y))
    y += 8
    p.append(bars("По часам суток",
                  "SELECT strftime('%H', datetime(hour, '$tz hours')) AS \"Час\", COUNT(*) AS \"Дней\" "
                  "FROM activity_hourly WHERE username = '$user' AND " + hr("hour") + " GROUP BY 1 ORDER BY 1", 0, y))
    p.append(pie("Устройства, часов",
                 "SELECT " + CLIENT_NAME.format(c="client") + " AS \"Клиент\", COUNT(*) AS \"Часов\" FROM activity_hourly "
                 "WHERE username = '$user' AND " + hr("hour") + " GROUP BY client", 12, y, w=12))
    y += 8
    p.append(table("Сеансы",
                   "SELECT replace(substr(datetime(started_at, '$tz hours'), 1, 16), 'T', ' ') AS \"Начало\", "
                   "replace(substr(datetime(ended_at, '$tz hours'), 1, 16), 'T', ' ') AS \"Конец\", "
                   "ROUND(COALESCE(duration_sec, 0) / 60.0) AS \"Минут\", " + CLIENT_NAME.format(c="client") + " AS \"Клиент\", "
                   "app_version AS \"Версия\" FROM session_log WHERE username = '$user' AND " + ts("started_at") +
                   " ORDER BY started_at DESC LIMIT 300", 0, y, w=12, h=10,
                   desc="Каждое подключение приложения или вкладки. Сеансы, оборванные перезапуском сервера, — 0 минут."))
    p.append(table("Звонки",
                   "SELECT replace(substr(datetime(started_at, '$tz hours'), 1, 16), 'T', ' ') AS \"Начало\", "
                   "CASE WHEN started_by = '$user' THEN 'исходящий' ELSE 'входящий' END AS \"Направление\", "
                   "CASE WHEN started_by = '$user' THEN COALESCE(callee, 'общий звонок') ELSE started_by END AS \"С кем\", "
                   + CALL_STATUS.format(c="status") + " AS \"Итог\", ROUND(COALESCE(duration_sec, 0) / 60.0, 1) AS \"Минут\" "
                   "FROM call_log WHERE (started_by = '$user' OR callee = '$user') AND " + ts("started_at") +
                   " ORDER BY started_at DESC LIMIT 300", 12, y, w=12, h=10))
    return dashboard("zweig-person", "Zweig — сотрудник", p, [user_var, TZ_VAR],
                     "Активность одного сотрудника: часы в сети, сеансы, устройства, звонки.")


def main():
    OUT.mkdir(exist_ok=True)
    for name, d in (("zweig-usage.json", usage()), ("zweig-person.json", person())):
        _id[0] = 0
        (OUT / name).write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("written", OUT / name, len(d["panels"]), "panels")


if __name__ == "__main__":
    main()
