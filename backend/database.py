"""
ChaosHelper Messenger — Database layer.
SQLite + aiosqlite, WAL mode, singleton async connection.
"""

import os
import json
import aiosqlite
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DB_PATH = DATA_DIR / "chaoshelper.db"
_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _db = await aiosqlite.connect(str(DB_PATH))
        _db.row_factory = aiosqlite.Row
        try:
            await _db.execute("PRAGMA journal_mode=WAL")
        except Exception as e:
            print(f"[db] WAL failed: {e}, trying DELETE journal mode...")
            try:
                await _db.close()
            except Exception:
                pass
            _db = None
            # Try to remove corrupted files
            for suffix in ("", "-wal", "-shm"):
                p = Path(str(DB_PATH) + suffix)
                try:
                    if p.exists():
                        p.unlink()
                except PermissionError:
                    print(f"[db] Cannot delete {p.name}, skipping...")
            _db = await aiosqlite.connect(str(DB_PATH))
            _db.row_factory = aiosqlite.Row
            # Fall back to DELETE journal mode (works on NTFS/Docker bind mounts)
            try:
                await _db.execute("PRAGMA journal_mode=DELETE")
            except Exception as e2:
                print(f"[db] DELETE journal also failed: {e2}, continuing without journal mode")
        await _db.execute("PRAGMA foreign_keys=ON")
    return _db


async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None


TABLES = [
    # Users with profiles
    """CREATE TABLE IF NOT EXISTS users (
        username     TEXT PRIMARY KEY,
        password     TEXT NOT NULL,
        display_name TEXT NOT NULL DEFAULT '',
        nickname     TEXT NOT NULL DEFAULT '',
        avatar_path  TEXT NOT NULL DEFAULT '',
        status_text  TEXT NOT NULL DEFAULT '',
        role         TEXT NOT NULL DEFAULT 'user',
        blocked      INTEGER NOT NULL DEFAULT 0,
        created_at   TEXT NOT NULL DEFAULT '',
        last_seen    TEXT NOT NULL DEFAULT ''
    )""",
    # Channels (public, private, direct)
    """CREATE TABLE IF NOT EXISTS channels (
        id              TEXT PRIMARY KEY,
        name            TEXT NOT NULL DEFAULT '',
        slug            TEXT UNIQUE NOT NULL,
        type            TEXT NOT NULL DEFAULT 'public',
        description     TEXT NOT NULL DEFAULT '',
        created_by      TEXT NOT NULL DEFAULT '',
        created_at      TEXT NOT NULL DEFAULT '',
        last_msg_text        TEXT,
        last_msg_sender      TEXT,
        last_msg_sender_name TEXT,
        last_msg_timestamp   TEXT
    )""",
    # Channel membership
    """CREATE TABLE IF NOT EXISTS channel_members (
        channel_id  TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
        username    TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
        role        TEXT NOT NULL DEFAULT 'member',
        joined_at   TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (channel_id, username)
    )""",
    # Messages
    """CREATE TABLE IF NOT EXISTS messages (
        id          TEXT PRIMARY KEY,
        channel_id  TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
        sender      TEXT NOT NULL,
        sender_name TEXT NOT NULL DEFAULT '',
        type        TEXT NOT NULL DEFAULT 'text',
        text        TEXT NOT NULL DEFAULT '',
        file_data   TEXT,
        call_data   TEXT,
        reply_to    TEXT,
        edited_at   TEXT,
        timestamp   TEXT NOT NULL DEFAULT ''
    )""",
    # Read receipts
    """CREATE TABLE IF NOT EXISTS message_reads (
        message_id  TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        username    TEXT NOT NULL,
        PRIMARY KEY (message_id, username)
    )""",
    # Active video calls
    """CREATE TABLE IF NOT EXISTS active_calls (
        channel_id  TEXT PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE,
        room_name   TEXT NOT NULL,
        started_by  TEXT NOT NULL,
        started_at  TEXT NOT NULL DEFAULT ''
    )""",
    # Addressed 1:1 call sessions (audio-first flow)
    """CREATE TABLE IF NOT EXISTS calls (
        id              TEXT PRIMARY KEY,
        channel_id      TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
        channel_slug    TEXT NOT NULL DEFAULT '',
        room_name       TEXT NOT NULL,
        caller_username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
        callee_username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
        status          TEXT NOT NULL DEFAULT 'ringing',
        mode            TEXT NOT NULL DEFAULT 'audio',
        video_enabled   INTEGER NOT NULL DEFAULT 0,
        speaker_enabled INTEGER NOT NULL DEFAULT 0,
        expires_at      TEXT NOT NULL DEFAULT '',
        created_at      TEXT NOT NULL DEFAULT '',
        answered_at     TEXT,
        ended_at        TEXT,
        end_reason      TEXT NOT NULL DEFAULT ''
    )""",
    # User preferences
    """CREATE TABLE IF NOT EXISTS preferences (
        username  TEXT PRIMARY KEY,
        theme     TEXT NOT NULL DEFAULT 'dark',
        language  TEXT NOT NULL DEFAULT 'ru'
    )""",
    # Feedback from users
    """CREATE TABLE IF NOT EXISTS feedback (
        id           TEXT PRIMARY KEY,
        username     TEXT NOT NULL,
        display_name TEXT NOT NULL DEFAULT '',
        text         TEXT NOT NULL,
        created_at   TEXT NOT NULL DEFAULT ''
    )""",
    # Device tokens for push notifications
    """CREATE TABLE IF NOT EXISTS user_devices (
        id          TEXT PRIMARY KEY,
        username    TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
        fcm_token   TEXT NOT NULL UNIQUE,
        platform    TEXT NOT NULL DEFAULT 'android',
        created_at  TEXT NOT NULL DEFAULT '',
        updated_at  TEXT NOT NULL DEFAULT ''
    )""",
    # User-to-user blocks. Distinct from users.blocked, which is an admin
    # disabling an account outright; this is one user muting another for
    # themselves only (App Store guideline 1.2).
    """CREATE TABLE IF NOT EXISTS user_blocks (
        id          TEXT PRIMARY KEY,
        blocker     TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
        blocked     TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
        created_at  TEXT NOT NULL DEFAULT '',
        UNIQUE(blocker, blocked)
    )""",
    # Reports of objectionable content, for admins to act on (guideline 1.2).
    """CREATE TABLE IF NOT EXISTS content_reports (
        id            TEXT PRIMARY KEY,
        reporter      TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
        reported_user TEXT NOT NULL DEFAULT '',
        message_id    TEXT NOT NULL DEFAULT '',
        channel_slug  TEXT NOT NULL DEFAULT '',
        reason        TEXT NOT NULL DEFAULT '',
        details       TEXT NOT NULL DEFAULT '',
        -- Snapshot of the reported text: the author can delete the message,
        -- and a report with no evidence left is useless to a moderator.
        message_text  TEXT NOT NULL DEFAULT '',
        status        TEXT NOT NULL DEFAULT 'open',
        created_at    TEXT NOT NULL DEFAULT '',
        resolved_at   TEXT NOT NULL DEFAULT '',
        resolved_by   TEXT NOT NULL DEFAULT ''
    )""",
    # PushKit VoIP tokens (iOS calls). Kept apart from user_devices because a
    # VoIP token is issued by PushKit independently of the FCM token, arrives in
    # its own callback, and user_devices.fcm_token is NOT NULL UNIQUE — so an
    # iOS device cannot be represented there before its FCM token is known.
    """CREATE TABLE IF NOT EXISTS user_voip_devices (
        id          TEXT PRIMARY KEY,
        username    TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
        voip_token  TEXT NOT NULL UNIQUE,
        created_at  TEXT NOT NULL DEFAULT '',
        updated_at  TEXT NOT NULL DEFAULT ''
    )""",
    # Long-lived auth sessions (refresh tokens)
    """CREATE TABLE IF NOT EXISTS user_sessions (
        id            TEXT PRIMARY KEY,
        username      TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
        refresh_hash  TEXT NOT NULL,
        created_at    TEXT NOT NULL DEFAULT '',
        expires_at    TEXT NOT NULL DEFAULT '',
        last_used_at  TEXT NOT NULL DEFAULT '',
        revoked_at    TEXT,
        revoked_reason TEXT NOT NULL DEFAULT '',
        device_info   TEXT NOT NULL DEFAULT ''
    )""",
    # Emoji reactions on messages
    """CREATE TABLE IF NOT EXISTS message_reactions (
        message_id  TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        username    TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
        emoji       TEXT NOT NULL,
        created_at  TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (message_id, username, emoji)
    )""",
    # Analytics: daily user activity (one row per user per day)
    """CREATE TABLE IF NOT EXISTS online_sessions (
        username    TEXT PRIMARY KEY,
        online      INTEGER NOT NULL DEFAULT 0,
        updated_at  TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS user_activity_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        username    TEXT NOT NULL,
        date        TEXT NOT NULL,
        UNIQUE(username, date)
    )""",
    # Analytics: login events with full timestamp
    """CREATE TABLE IF NOT EXISTS login_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        username    TEXT NOT NULL,
        timestamp   TEXT NOT NULL,
        ip          TEXT NOT NULL DEFAULT ''
    )""",
    # Analytics: call history with duration
    """CREATE TABLE IF NOT EXISTS call_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id      TEXT NOT NULL,
        started_by      TEXT NOT NULL,
        started_at      TEXT NOT NULL,
        ended_at        TEXT,
        duration_sec    INTEGER,
        status          TEXT NOT NULL DEFAULT 'active'
    )""",
    # ── ChaosTracker ──────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS task_projects (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        slug        TEXT UNIQUE NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        parent_id   TEXT REFERENCES task_projects(id) ON DELETE CASCADE,
        created_by  TEXT NOT NULL,
        created_at  TEXT NOT NULL DEFAULT '',
        updated_at  TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS task_project_members (
        project_id  TEXT NOT NULL REFERENCES task_projects(id) ON DELETE CASCADE,
        username    TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
        role        TEXT NOT NULL DEFAULT 'member',
        joined_at   TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (project_id, username)
    )""",
    """CREATE TABLE IF NOT EXISTS tasks (
        id            TEXT PRIMARY KEY,
        project_id    TEXT NOT NULL REFERENCES task_projects(id) ON DELETE CASCADE,
        title         TEXT NOT NULL,
        description   TEXT NOT NULL DEFAULT '',
        status        TEXT NOT NULL DEFAULT 'todo',
        priority      TEXT NOT NULL DEFAULT 'medium',
        assignee      TEXT,
        created_by    TEXT NOT NULL,
        due_date      TEXT,
        issue_type_id TEXT REFERENCES task_issue_types(id) ON DELETE SET NULL,
        position      INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL DEFAULT '',
        updated_at    TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS task_labels (
        id          TEXT PRIMARY KEY,
        project_id  TEXT NOT NULL REFERENCES task_projects(id) ON DELETE CASCADE,
        name        TEXT NOT NULL,
        color       TEXT NOT NULL DEFAULT '#4a90d9'
    )""",
    """CREATE TABLE IF NOT EXISTS task_label_assignments (
        task_id     TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        label_id    TEXT NOT NULL REFERENCES task_labels(id) ON DELETE CASCADE,
        PRIMARY KEY (task_id, label_id)
    )""",
    """CREATE TABLE IF NOT EXISTS task_subtasks (
        id          TEXT PRIMARY KEY,
        task_id     TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        title       TEXT NOT NULL,
        completed   INTEGER NOT NULL DEFAULT 0,
        position    INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS task_comments (
        id           TEXT PRIMARY KEY,
        task_id      TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        username     TEXT NOT NULL,
        display_name TEXT NOT NULL DEFAULT '',
        text         TEXT NOT NULL,
        created_at   TEXT NOT NULL DEFAULT '',
        edited_at    TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS task_attachments (
        id          TEXT PRIMARY KEY,
        task_id     TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        filename    TEXT NOT NULL,
        filepath    TEXT NOT NULL,
        filesize    INTEGER NOT NULL DEFAULT 0,
        uploaded_by TEXT NOT NULL,
        created_at  TEXT NOT NULL DEFAULT ''
    )""",
    # Issue types per project
    """CREATE TABLE IF NOT EXISTS task_issue_types (
        id          TEXT PRIMARY KEY,
        project_id  TEXT NOT NULL REFERENCES task_projects(id) ON DELETE CASCADE,
        name        TEXT NOT NULL,
        icon        TEXT NOT NULL DEFAULT '📋',
        color       TEXT NOT NULL DEFAULT '#4a90d9',
        position    INTEGER NOT NULL DEFAULT 0
    )""",
    # Custom fields per project
    """CREATE TABLE IF NOT EXISTS task_custom_fields (
        id           TEXT PRIMARY KEY,
        project_id   TEXT NOT NULL REFERENCES task_projects(id) ON DELETE CASCADE,
        name         TEXT NOT NULL,
        field_type   TEXT NOT NULL DEFAULT 'text',
        options      TEXT NOT NULL DEFAULT '[]',
        required     INTEGER NOT NULL DEFAULT 0,
        position     INTEGER NOT NULL DEFAULT 0
    )""",
    # Binding fields to issue types
    """CREATE TABLE IF NOT EXISTS task_issue_type_fields (
        issue_type_id TEXT NOT NULL REFERENCES task_issue_types(id) ON DELETE CASCADE,
        field_id      TEXT NOT NULL REFERENCES task_custom_fields(id) ON DELETE CASCADE,
        required      INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (issue_type_id, field_id)
    )""",
    # Custom field values per task
    """CREATE TABLE IF NOT EXISTS task_custom_values (
        id        TEXT PRIMARY KEY,
        task_id   TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        field_id  TEXT NOT NULL REFERENCES task_custom_fields(id) ON DELETE CASCADE,
        value     TEXT NOT NULL DEFAULT '',
        UNIQUE(task_id, field_id)
    )""",

    # ── Federation ────────────────────────────────────────────────
    # One row per peer server we are linked (or trying to link) with.
    #
    # status:    pending_out  — we asked them, waiting for their admin
    #            pending_in   — they asked us, waiting for our admin
    #            active       — linked, shared_secret usable for S2S calls
    #            declined     — one side refused
    #            revoked      — link was removed after being active
    # direction: who initiated the link ('outgoing' | 'incoming')
    #
    # request_token proves domain ownership during the handshake: the initiator
    # serves it from /api/federation/verify, so the receiver can confirm the
    # request really came from the domain it claims to be.
    """CREATE TABLE IF NOT EXISTS federated_servers (
        domain        TEXT PRIMARY KEY,
        status        TEXT NOT NULL DEFAULT 'pending_out',
        direction     TEXT NOT NULL DEFAULT 'outgoing',
        request_token TEXT NOT NULL DEFAULT '',
        shared_secret TEXT NOT NULL DEFAULT '',
        requested_by  TEXT NOT NULL DEFAULT '',
        decided_by    TEXT NOT NULL DEFAULT '',
        last_error    TEXT NOT NULL DEFAULT '',
        created_at    TEXT NOT NULL DEFAULT '',
        updated_at    TEXT NOT NULL DEFAULT ''
    )""",
    # Invite links — how someone on a federated server gets permission to
    # start a conversation with a local user. There is deliberately no
    # cross-server user directory: the owner generates a token, passes the
    # link on out of band, and only a holder of that token can reach them.
    """CREATE TABLE IF NOT EXISTS federation_invites (
        token        TEXT PRIMARY KEY,
        owner        TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
        created_at   TEXT NOT NULL DEFAULT '',
        expires_at   TEXT NOT NULL DEFAULT '',
        max_uses     INTEGER NOT NULL DEFAULT 1,
        used_count   INTEGER NOT NULL DEFAULT 0,
        revoked      INTEGER NOT NULL DEFAULT 0,
        note         TEXT NOT NULL DEFAULT ''
    )""",
    # Outgoing queue for server-to-server message delivery. A peer can be down,
    # so delivery is retried in the background instead of failing the send.
    #
    # id is "<message_id>:<domain>" — one row per message per destination, so
    # re-queueing the same message is a no-op and retries cannot fan out.
    # Сообщение, удалённое одним человеком у себя. Строка вместо колонки:
    # у одного сообщения таких людей может быть сколько угодно, а у большинства
    # сообщений — ни одного.
    """CREATE TABLE IF NOT EXISTS message_hidden (
        message_id TEXT NOT NULL,
        username   TEXT NOT NULL,
        hidden_at  TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (message_id, username)
    )""",
    """CREATE TABLE IF NOT EXISTS federation_outbox (
        id           TEXT PRIMARY KEY,
        domain       TEXT NOT NULL,
        path         TEXT NOT NULL DEFAULT '',
        channel_id   TEXT NOT NULL DEFAULT '',
        payload      TEXT NOT NULL,
        attempts     INTEGER NOT NULL DEFAULT 0,
        next_attempt TEXT NOT NULL DEFAULT '',
        last_error   TEXT NOT NULL DEFAULT '',
        created_at   TEXT NOT NULL DEFAULT '',
        delivered_at TEXT NOT NULL DEFAULT ''
    )""",
]

INDEXES = [
    # Blocks are checked on every message fetch and broadcast, so keep the
    # blocker lookup indexed.
    "CREATE INDEX IF NOT EXISTS idx_user_blocks_blocker ON user_blocks(blocker)",
    "CREATE INDEX IF NOT EXISTS idx_content_reports_status ON content_reports(status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_reactions_message ON message_reactions(message_id)",
    "CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_user_devices_username ON user_devices(username)",
    "CREATE INDEX IF NOT EXISTS idx_calls_callee_status ON calls(callee_username, status)",
    "CREATE INDEX IF NOT EXISTS idx_calls_expires ON calls(expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_calls_channel_status ON calls(channel_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_user_sessions_username ON user_sessions(username)",
    "CREATE INDEX IF NOT EXISTS idx_user_sessions_expires ON user_sessions(expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_user_sessions_revoked ON user_sessions(revoked_at)",
    "CREATE INDEX IF NOT EXISTS idx_federation_invites_owner ON federation_invites(owner)",
    "CREATE INDEX IF NOT EXISTS idx_federation_outbox_pending ON federation_outbox(delivered_at, next_attempt)",
    "CREATE INDEX IF NOT EXISTS idx_message_hidden_user ON message_hidden(username)",
    # ChaosTracker indexes
    "CREATE INDEX IF NOT EXISTS idx_task_project_members ON task_project_members(username)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee)",
    "CREATE INDEX IF NOT EXISTS idx_task_subtasks ON task_subtasks(task_id, position)",
    "CREATE INDEX IF NOT EXISTS idx_task_comments ON task_comments(task_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_task_attachments ON task_attachments(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_task_labels_project ON task_labels(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_task_issue_types_project ON task_issue_types(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_task_custom_fields_project ON task_custom_fields(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_task_custom_values_task ON task_custom_values(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_task_projects_parent ON task_projects(parent_id)",
    "CREATE INDEX IF NOT EXISTS idx_user_activity_log_date ON user_activity_log(date)",
    "CREATE INDEX IF NOT EXISTS idx_login_log_timestamp ON login_log(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_login_log_username ON login_log(username)",
    "CREATE INDEX IF NOT EXISTS idx_call_log_started_by ON call_log(started_by)",
    "CREATE INDEX IF NOT EXISTS idx_call_log_started_at ON call_log(started_at)",
]


async def init_db():
    """Create tables, run migrations, seed default admin."""
    db = await get_db()

    for ddl in TABLES:
        await db.execute(ddl)
    await db.commit()

    # ---------- Migration: add reply_to column ----------
    pragma = await db.execute("PRAGMA table_info(messages)")
    cols = [r[1] for r in await pragma.fetchall()]
    if "reply_to" not in cols:
        await db.execute("ALTER TABLE messages ADD COLUMN reply_to TEXT")
        await db.commit()

    # ---------- Migration: add pin columns to messages ----------
    pragma2 = await db.execute("PRAGMA table_info(messages)")
    msg_cols = [r[1] for r in await pragma2.fetchall()]
    if "is_pinned" not in msg_cols:
        await db.execute("ALTER TABLE messages ADD COLUMN is_pinned INTEGER NOT NULL DEFAULT 0")
        await db.execute("ALTER TABLE messages ADD COLUMN pinned_by TEXT")
        await db.execute("ALTER TABLE messages ADD COLUMN pinned_at TEXT")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_pinned ON messages(channel_id, is_pinned)")
        await db.commit()

    # ---------- Migration: add avatar_path to channels ----------
    pragma3 = await db.execute("PRAGMA table_info(channels)")
    ch_cols = [r[1] for r in await pragma3.fetchall()]
    if "avatar_path" not in ch_cols:
        await db.execute("ALTER TABLE channels ADD COLUMN avatar_path TEXT NOT NULL DEFAULT ''")
        await db.commit()

    # ---------- Migration: add home_server to users (federation) ----------
    # Empty = local account. Otherwise the peer domain the user actually lives
    # on; such rows are stubs for remote members and never hold a password.
    pragma_fed = await db.execute("PRAGMA table_info(users)")
    user_cols = [r[1] for r in await pragma_fed.fetchall()]
    if "home_server" not in user_cols:
        await db.execute("ALTER TABLE users ADD COLUMN home_server TEXT NOT NULL DEFAULT ''")
        await db.commit()

    # ---------- Migration: home_server on channels (federated group channels) ----------
    # Empty = the channel lives here. Otherwise the domain that owns it, and
    # this row is our mirror: the same channel id on every participating
    # server, so a message can name the conversation it belongs to instead of
    # being inferred from the pair of people involved.
    pragma_ch_fed = await db.execute("PRAGMA table_info(channels)")
    ch_fed_cols = [r[1] for r in await pragma_ch_fed.fetchall()]
    if "home_server" not in ch_fed_cols:
        await db.execute("ALTER TABLE channels ADD COLUMN home_server TEXT NOT NULL DEFAULT ''")
        await db.commit()

    # ---------- Migration: per-user "cleared" boundary on a conversation ----------
    # Deleting a direct chat is one-sided, like in Telegram: the chat disappears
    # for whoever deleted it while the other side keeps everything. There is no
    # per-user copy of the messages, so instead we remember when this member
    # cleared the conversation and hide everything older from them. A new
    # message after that moment brings the chat back — showing only what arrived
    # since.
    pragma_cleared = await db.execute("PRAGMA table_info(channel_members)")
    member_cols = [r[1] for r in await pragma_cleared.fetchall()]
    if "cleared_at" not in member_cols:
        await db.execute("ALTER TABLE channel_members ADD COLUMN cleared_at TEXT NOT NULL DEFAULT ''")
        await db.commit()

    # ---------- Migration: с какого момента участнику видна переписка ----------
    # Пусто — вся история. Иначе момент, раньше которого сообщения ему не
    # показываются: так добавленный в канал человек не получает доступ к тому,
    # что обсуждали без него, если добавлявший этого не захотел.
    pragma_hist = await db.execute("PRAGMA table_info(channel_members)")
    hist_cols = [r[1] for r in await pragma_hist.fetchall()]
    if "history_from" not in hist_cols:
        await db.execute("ALTER TABLE channel_members ADD COLUMN history_from TEXT NOT NULL DEFAULT ''")
        await db.commit()

    # ---------- Migration: беззвучные беседы ----------
    # Пусто — звук включён. Иначе момент, до которого беседа молчит; «навсегда»
    # это просто очень далёкая дата, чтобы не заводить отдельного признака и не
    # разбирать его во всех местах, где проверяется тишина.
    pragma_mute = await db.execute("PRAGMA table_info(channel_members)")
    mute_cols = [r[1] for r in await pragma_mute.fetchall()]
    if "muted_until" not in mute_cols:
        await db.execute("ALTER TABLE channel_members ADD COLUMN muted_until TEXT NOT NULL DEFAULT ''")
        await db.commit()

    # ---------- Migration: federated call columns ----------
    # A call room lives on exactly one server, because its LiveKit token is
    # signed with that server's secret. The other side cannot mint a token, so
    # it stores the one the room's server sent, together with the URL to dial.
    # remote_domain is set on both sides and marks the call as crossing servers.
    pragma_calls = await db.execute("PRAGMA table_info(calls)")
    call_cols = [r[1] for r in await pragma_calls.fetchall()]
    for col in ("remote_domain", "remote_livekit_url", "remote_livekit_token"):
        if col not in call_cols:
            await db.execute(f"ALTER TABLE calls ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    await db.commit()

    # ---------- Migration: path on the federation outbox ----------
    # Очередь начиналась как «только сообщения», и путь был вшит в доставку.
    # Теперь через неё же повторяется состав канала, и строка должна знать, куда
    # её везти. Пустое значение — ряд из прежних времён, он всегда про сообщение.
    pragma_outbox = await db.execute("PRAGMA table_info(federation_outbox)")
    outbox_cols = [r[1] for r in await pragma_outbox.fetchall()]
    if "path" not in outbox_cols:
        await db.execute("ALTER TABLE federation_outbox ADD COLUMN path TEXT NOT NULL DEFAULT ''")
        await db.commit()
    if "channel_id" not in outbox_cols:
        # Разговор, к которому относится строка. Нужен, чтобы после удачной
        # синхронизации состава вернуть в работу сообщения, которые сосед
        # отверг, пока не знал об этом канале.
        await db.execute("ALTER TABLE federation_outbox ADD COLUMN channel_id TEXT NOT NULL DEFAULT ''")
        await db.commit()
        # Заполняем задним числом из тела запроса: строки, застрявшие до этой
        # миграции, — как раз те, ради которых колонка и заводится. json_extract
        # есть не в каждой сборке SQLite, и падать из-за него на старте нельзя.
        try:
            await db.execute(
                "UPDATE federation_outbox SET channel_id = "
                "COALESCE(json_extract(payload, '$.channel_id'), '') WHERE channel_id = ''"
            )
            await db.commit()
        except Exception as e:
            print(f"[db] outbox channel_id backfill skipped: {e}")

    # ---------- Migration: add remote_username to users (federation) ----------
    # username is the primary key and the namespace is shared with local
    # accounts, so a remote user is stored qualified as "bob@peer.example".
    # remote_username keeps the bare name they use on their own server.
    if "remote_username" not in user_cols:
        await db.execute("ALTER TABLE users ADD COLUMN remote_username TEXT NOT NULL DEFAULT ''")
        await db.commit()

    # ---------- Migration: add parent_id to task_projects ----------
    pragma4 = await db.execute("PRAGMA table_info(task_projects)")
    tp_cols = [r[1] for r in await pragma4.fetchall()]
    if "parent_id" not in tp_cols:
        await db.execute("ALTER TABLE task_projects ADD COLUMN parent_id TEXT REFERENCES task_projects(id) ON DELETE CASCADE")
        await db.commit()

    # ---------- Migration: add issue_type_id to tasks ----------
    pragma5 = await db.execute("PRAGMA table_info(tasks)")
    task_cols = [r[1] for r in await pragma5.fetchall()]
    if "issue_type_id" not in task_cols:
        await db.execute("ALTER TABLE tasks ADD COLUMN issue_type_id TEXT REFERENCES task_issue_types(id) ON DELETE SET NULL")
        await db.commit()

    # ---------- Migration: add visibility to task_projects ----------
    pragma_tp_vis = await db.execute("PRAGMA table_info(task_projects)")
    tp_vis_cols = [r[1] for r in await pragma_tp_vis.fetchall()]
    if "visibility" not in tp_vis_cols:
        await db.execute("ALTER TABLE task_projects ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'")
        await db.commit()

    # ---------- Migration: add default_assignee to task_projects ----------
    pragma_tp_da = await db.execute("PRAGMA table_info(task_projects)")
    tp_da_cols = [r[1] for r in await pragma_tp_da.fetchall()]
    if "default_assignee" not in tp_da_cols:
        await db.execute("ALTER TABLE task_projects ADD COLUMN default_assignee TEXT")
        await db.commit()

    # ---------- Migration: add prefix & task_number_seq to task_projects ----------
    pragma_tp_pf = await db.execute("PRAGMA table_info(task_projects)")
    tp_pf_cols = [r[1] for r in await pragma_tp_pf.fetchall()]
    if "prefix" not in tp_pf_cols:
        await db.execute("ALTER TABLE task_projects ADD COLUMN prefix TEXT DEFAULT ''")
        await db.commit()
    if "task_number_seq" not in tp_pf_cols:
        await db.execute("ALTER TABLE task_projects ADD COLUMN task_number_seq INTEGER DEFAULT 0")
        await db.commit()

    # ---------- Migration: add task_number & task_key to tasks ----------
    pragma_t_tn = await db.execute("PRAGMA table_info(tasks)")
    t_tn_cols = [r[1] for r in await pragma_t_tn.fetchall()]
    if "task_number" not in t_tn_cols:
        await db.execute("ALTER TABLE tasks ADD COLUMN task_number INTEGER DEFAULT 0")
        await db.commit()
    if "task_key" not in t_tn_cols:
        await db.execute("ALTER TABLE tasks ADD COLUMN task_key TEXT DEFAULT ''")
        await db.commit()

    # Create indexes AFTER migrations (columns must exist first)
    for ddl in INDEXES:
        await db.execute(ddl)
    await db.commit()

    # ---------- Migration from old schema ----------
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'"
    )
    if await cursor.fetchone():
        await _migrate_conversations(db)

    # ---------- Cleanup stale active_calls (older than 2 hours) ----------
    await db.execute(
        "DELETE FROM active_calls WHERE started_at < datetime('now', '-2 hours')"
    )
    await db.commit()

    # ---------- Seed admin user ----------
    admin_user = os.environ.get("ADMIN_USERNAME", "admin")
    admin_pass = os.environ.get("ADMIN_PASSWORD", "admin")

    c = await db.execute("SELECT 1 FROM users WHERE username = ?", (admin_user,))
    if not await c.fetchone():
        from passlib.hash import bcrypt as _bcrypt
        hashed = _bcrypt.hash(admin_pass)
        now = _now_iso()
        await db.execute(
            "INSERT INTO users (username, password, display_name, role, created_at) VALUES (?, ?, ?, 'admin', ?)",
            (admin_user, hashed, admin_user.capitalize(), now),
        )

    await db.commit()


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def _migrate_conversations(db: aiosqlite.Connection):
    """Migrate old conversations/messages tables to new channels schema."""
    import uuid

    try:
        now = _now_iso()

        # Migrate conversations → channels
        cursor = await db.execute("SELECT * FROM conversations")
        old_convs = await cursor.fetchall()

        for conv in old_convs:
            conv = dict(conv)
            ch_id = conv["id"]
            conv_type = conv.get("type", "direct")

            if conv_type == "direct":
                slug = f"dm-{ch_id[:8]}"
                ch_type = "direct"
                name = conv.get("name") or ""
            else:
                name = conv.get("name") or "Group"
                slug = _simple_slugify(name) or f"group-{ch_id[:8]}"
                ch_type = "private"

            # Ensure unique slug
            c_check = await db.execute("SELECT 1 FROM channels WHERE slug = ?", (slug,))
            if await c_check.fetchone():
                slug = f"{slug}-{ch_id[:6]}"

            await db.execute(
                """INSERT OR IGNORE INTO channels
                   (id, name, slug, type, created_by, created_at,
                    last_msg_text, last_msg_sender, last_msg_sender_name, last_msg_timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ch_id, name, slug, ch_type,
                    conv.get("created_by", ""),
                    conv.get("created_at") or now,
                    conv.get("last_msg_text"),
                    conv.get("last_msg_sender"),
                    conv.get("last_msg_sender_name"),
                    conv.get("last_msg_timestamp"),
                ),
            )

        # Migrate participants → channel_members
        cursor2 = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='conversation_participants'"
        )
        if await cursor2.fetchone():
            cursor3 = await db.execute("SELECT * FROM conversation_participants")
            for row in await cursor3.fetchall():
                row = dict(row)
                await db.execute(
                    "INSERT OR IGNORE INTO channel_members (channel_id, username, role, joined_at) VALUES (?, ?, 'member', ?)",
                    (row["conversation_id"], row["username"], now),
                )

        # Migrate messages: conversation_id → channel_id
        old_msgs = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        )
        if await old_msgs.fetchone():
            pragma = await db.execute("PRAGMA table_info(messages)")
            cols = [r[1] for r in await pragma.fetchall()]
            if "conversation_id" in cols and "channel_id" not in cols:
                await db.execute("ALTER TABLE messages RENAME TO _old_messages")
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
                        id TEXT PRIMARY KEY,
                        channel_id TEXT NOT NULL,
                        sender TEXT NOT NULL,
                        sender_name TEXT NOT NULL DEFAULT '',
                        type TEXT NOT NULL DEFAULT 'text',
                        text TEXT NOT NULL DEFAULT '',
                        file_data TEXT,
                        call_data TEXT,
                        edited_at TEXT,
                        timestamp TEXT NOT NULL DEFAULT ''
                    )""")
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel_id, timestamp)"
                )
                await db.execute(
                    """INSERT OR IGNORE INTO messages
                       (id, channel_id, sender, sender_name, type, text, file_data, call_data, timestamp)
                       SELECT id, conversation_id, sender, sender_name, type, text, file_data, call_data, timestamp
                       FROM _old_messages"""
                )
                await db.execute("DROP TABLE _old_messages")

        # Drop old tables
        await db.execute("DROP TABLE IF EXISTS conversation_participants")
        await db.execute("DROP TABLE IF EXISTS conversations")

        # Drop unused old tables
        for old_table in [
            "calendar_events", "shopping_items", "notes",
            "polls", "poll_options", "poll_votes",
            "shares", "share_recipients", "passwords", "cache"
        ]:
            await db.execute(f"DROP TABLE IF EXISTS {old_table}")

        await db.commit()
    except Exception as e:
        print(f"[migration] Warning: {e}")


def _simple_slugify(text: str) -> str:
    """Basic slugify for migration (full version in helpers.py)."""
    import re
    text = text.lower().strip()
    tr = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
        'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
    }
    result = ""
    for ch in text:
        result += tr.get(ch, ch)
    result = re.sub(r'[^a-z0-9]+', '-', result).strip('-')
    return result[:50] if result else ""
