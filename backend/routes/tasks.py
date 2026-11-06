"""ChaosHelper Messenger — ChaosTracker (projects, tasks, Kanban board)."""

import json
import os
import uuid
import shutil
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from helpers import get_current_user, get_display_name, slugify, now_iso, UPLOAD_DIR
from database import get_db
from ws_manager import manager

# Трекер — модуль: выключен в adm.zweig.services — все его ручки отвечают
# 403 module_disabled. Данные при этом не трогаются.
from routes.inbox import require_tracker
router = APIRouter(prefix="/api/tasks", tags=["tasks"], dependencies=[Depends(require_tracker)])

TASK_UPLOAD_DIR = UPLOAD_DIR / "task_attachments"
TASK_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)



# ── Helpers ──────────────────────────────────────────────────────

async def _get_project_role(db, project_id: str, username: str) -> str | None:
    cursor = await db.execute(
        "SELECT role FROM task_project_members WHERE project_id = ? AND username = ?",
        (project_id, username),
    )
    row = await cursor.fetchone()
    return row["role"] if row else None


async def _require_role(db, project_id: str, username: str, min_roles: list[str]) -> str:
    role = await _get_project_role(db, project_id, username)
    if role not in min_roles:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return role


async def _get_project_by_slug(db, slug: str, username: str):
    cursor = await db.execute(
        """SELECT p.* FROM task_projects p
           LEFT JOIN task_project_members pm ON p.id = pm.project_id AND pm.username = ?
           WHERE p.slug = ? AND (pm.username IS NOT NULL OR p.visibility = 'public')""",
        (username, slug),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    return dict(row)


# ── Статусы (колонки доски) ─────────────────────────────────────
# Ключи todo/in_progress/review/done совпадают со старыми жёстко заданными
# статусами, поэтому задачи, созданные до настраиваемых колонок, встают на
# свои места без миграции данных.
STATUS_TEMPLATES = {
    "simple": [
        ("todo", "Нужно сделать", "#E0A458", "new"),
        ("in_progress", "В работе", "#8FB8E8", "work"),
        ("done", "Готово", "#7FE0B0", "done"),
    ],
    "object": [
        ("todo", "Нужно сделать", "#E0A458", "new"),
        ("in_progress", "В работе", "#8FB8E8", "work"),
        ("review", "На проверке", "#C8A2E0", "review"),
        ("done", "Готово", "#7FE0B0", "done"),
    ],
    "requests": [
        ("todo", "Новая", "#F07A6A", "new"),
        ("in_progress", "В работе", "#8FB8E8", "work"),
        ("waiting", "Ждёт ответа", "#E0A458", "work"),
        ("done", "Решена", "#7FE0B0", "done"),
    ],
}
# review — «на согласовании»: задача сдана и ждёт решения руководителя или
# автора. Из этого статуса её принимают (→ первый «Завершена») или
# возвращают с комментарием (→ первый «В работе»), в том числе с телефона.
STATUS_KINDS = ("new", "work", "review", "done")
COLOR_RE = __import__("re").compile(r"^#[0-9A-Fa-f]{6}$")


async def _seed_statuses(db, project_id: str, template: str = "object") -> None:
    for pos, (key, name, color, kind) in enumerate(STATUS_TEMPLATES.get(template) or STATUS_TEMPLATES["object"]):
        await db.execute(
            "INSERT OR IGNORE INTO task_statuses (id, project_id, key, name, color, kind, on_board, position) VALUES (?,?,?,?,?,?,1,?)",
            (str(uuid.uuid4()), project_id, key, name, color, kind, pos),
        )


async def _get_statuses(db, project_id: str) -> list[dict]:
    """Статусы проекта по порядку. Проекты, созданные до настраиваемых
    колонок, получают стандартный набор при первом обращении."""
    cur = await db.execute(
        "SELECT key, name, color, kind, on_board, position FROM task_statuses WHERE project_id = ? ORDER BY position",
        (project_id,),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    if not rows:
        await _seed_statuses(db, project_id)
        await db.commit()
        return await _get_statuses(db, project_id)
    for r in rows:
        r["on_board"] = bool(r["on_board"])
    return rows


async def _done_keys(db, project_id: str) -> set[str]:
    return {s["key"] for s in await _get_statuses(db, project_id) if s["kind"] == "done"}


async def _check_status(db, project_id: str, status: str) -> str:
    keys = [s["key"] for s in await _get_statuses(db, project_id)]
    if status not in keys:
        raise HTTPException(status_code=400, detail="Invalid status")
    return status


def _make_prefix(name: str) -> str:
    """«Объект на Лесной» → «ЛЕС»: первые буквы последнего значимого слова."""
    import re as _re
    words = [w for w in _re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", name) if len(w) > 2]
    base = words[-1] if words else (name.strip() or "ZW")
    return base[:3].upper()


async def _unique_prefix(db, prefix: str, exclude_project: str = "") -> str:
    """Номер задачи — ключ ссылки в чате, поэтому префиксы не должны
    повторяться: к занятому дописываем цифру."""
    base, n = prefix, 1
    while True:
        c = await db.execute(
            "SELECT 1 FROM task_projects WHERE UPPER(prefix) = ? AND id != ?", (prefix.upper(), exclude_project)
        )
        if not await c.fetchone():
            return prefix
        n += 1
        prefix = f"{base}{n}"


async def _get_project_members(db, project_id: str) -> list[str]:
    cursor = await db.execute(
        "SELECT username FROM task_project_members WHERE project_id = ?", (project_id,)
    )
    return [r["username"] for r in await cursor.fetchall()]


async def _build_task(db, row) -> dict:
    t = dict(row)
    # Labels
    cursor = await db.execute(
        """SELECT tl.id, tl.name, tl.color FROM task_labels tl
           JOIN task_label_assignments tla ON tla.label_id = tl.id
           WHERE tla.task_id = ?""",
        (t["id"],),
    )
    t["labels"] = [dict(r) for r in await cursor.fetchall()]
    # Subtask counts
    cursor2 = await db.execute(
        "SELECT COUNT(*) as total, SUM(completed) as done FROM task_subtasks WHERE task_id = ?",
        (t["id"],),
    )
    row2 = await cursor2.fetchone()
    t["subtask_total"] = row2["total"] or 0
    t["subtask_done"] = int(row2["done"] or 0)
    # Comment count
    cursor3 = await db.execute("SELECT COUNT(*) FROM task_comments WHERE task_id = ?", (t["id"],))
    t["comment_count"] = (await cursor3.fetchone())[0]
    # Attachment count
    cursor4 = await db.execute("SELECT COUNT(*) FROM task_attachments WHERE task_id = ?", (t["id"],))
    t["attachment_count"] = (await cursor4.fetchone())[0]
    # Assignee info
    if t.get("assignee"):
        dn = await db.execute(
            "SELECT display_name, avatar_path FROM users WHERE username = ?", (t["assignee"],)
        )
        dn_row = await dn.fetchone()
        if dn_row:
            t["assignee_name"] = dn_row["display_name"]
            t["assignee_avatar"] = dn_row["avatar_path"] or ""
        else:
            t["assignee_name"] = t["assignee"]
            t["assignee_avatar"] = ""
    else:
        t["assignee_name"] = ""
        t["assignee_avatar"] = ""
    # Issue type info
    if t.get("issue_type_id"):
        it_cursor = await db.execute(
            "SELECT id, name, icon, color FROM task_issue_types WHERE id = ?",
            (t["issue_type_id"],),
        )
        it_row = await it_cursor.fetchone()
        t["issue_type"] = dict(it_row) if it_row else None
    else:
        t["issue_type"] = None
    # Custom field values
    cv_cursor = await db.execute(
        """SELECT cv.field_id, cv.value, cf.name, cf.field_type, cf.options
           FROM task_custom_values cv
           JOIN task_custom_fields cf ON cf.id = cv.field_id
           WHERE cv.task_id = ?""",
        (t["id"],),
    )
    t["custom_values"] = [dict(r) for r in await cv_cursor.fetchall()]
    return t


# ── Projects CRUD ────────────────────────────────────────────────

@router.get("/projects")
async def list_projects(
    username: str = Depends(get_current_user),
    parent_id: str | None = Query(None),
):
    db = await get_db()
    _proj_sql = """SELECT p.*, COALESCE(pm.role, 'viewer') as my_role,
               CASE WHEN pm.username IS NOT NULL THEN 1 ELSE 0 END as is_member,
               (SELECT COUNT(*) FROM task_project_members WHERE project_id = p.id) as member_count,
               (SELECT COUNT(*) FROM tasks WHERE project_id = p.id) as task_count,
               (SELECT COUNT(*) FROM task_projects WHERE parent_id = p.id) as child_count,
               (SELECT COUNT(*) FROM tasks WHERE project_id = p.id AND status = 'todo') as todo_count,
               (SELECT COUNT(*) FROM tasks WHERE project_id = p.id AND status = 'in_progress') as in_progress_count,
               (SELECT COUNT(*) FROM tasks WHERE project_id = p.id AND status = 'review') as review_count,
               (SELECT COUNT(*) FROM tasks WHERE project_id = p.id AND status = 'done') as done_count,
               (SELECT display_name FROM users WHERE username = p.created_by) as creator_name
               FROM task_projects p
               LEFT JOIN task_project_members pm ON p.id = pm.project_id AND pm.username = ?"""
    _where_member = " AND (pm.username IS NOT NULL OR p.visibility = 'public')"
    if parent_id:
        cursor = await db.execute(
            _proj_sql + " WHERE p.parent_id = ?" + _where_member + " ORDER BY p.updated_at DESC",
            (username, parent_id),
        )
    else:
        cursor = await db.execute(
            _proj_sql + " WHERE p.parent_id IS NULL" + _where_member + " ORDER BY p.updated_at DESC",
            (username,),
        )
    rows = [dict(r) for r in await cursor.fetchall()]
    for r in rows:
        r["is_member"] = bool(r.get("is_member"))
        done = await _done_keys(db, r["id"])
        marks = ",".join("?" * len(done)) or "''"
        c = await db.execute(
            f"""SELECT SUM(CASE WHEN status IN ({marks}) THEN 0 ELSE 1 END) AS open,
                       SUM(CASE WHEN status NOT IN ({marks}) AND due_date IS NOT NULL AND due_date < date('now') THEN 1 ELSE 0 END) AS overdue
                FROM tasks WHERE project_id = ?""",
            (*done, *done, r["id"]),
        )
        cr = await c.fetchone()
        r["open_count"] = cr["open"] or 0
        r["overdue_count"] = cr["overdue"] or 0
    return rows


@router.get("/dashboard")
async def dashboard_stats(username: str = Depends(get_current_user)):
    db = await get_db()
    c1 = await db.execute(
        """SELECT COUNT(*) as total,
           SUM(CASE WHEN t.status='todo' THEN 1 ELSE 0 END) as todo,
           SUM(CASE WHEN t.status='in_progress' THEN 1 ELSE 0 END) as in_progress,
           SUM(CASE WHEN t.status='review' THEN 1 ELSE 0 END) as review,
           SUM(CASE WHEN t.status='done' THEN 1 ELSE 0 END) as done
        FROM tasks t
        JOIN task_project_members pm ON pm.project_id = t.project_id AND pm.username = ?""",
        (username,),
    )
    totals = dict(await c1.fetchone())
    c2 = await db.execute(
        """SELECT COUNT(*) as total,
           SUM(CASE WHEN t.status='todo' THEN 1 ELSE 0 END) as todo,
           SUM(CASE WHEN t.status='in_progress' THEN 1 ELSE 0 END) as in_progress,
           SUM(CASE WHEN t.status='review' THEN 1 ELSE 0 END) as review,
           SUM(CASE WHEN t.status='done' THEN 1 ELSE 0 END) as done
        FROM tasks t
        JOIN task_project_members pm ON pm.project_id = t.project_id AND pm.username = ?
        WHERE t.assignee = ?""",
        (username, username),
    )
    my_tasks = dict(await c2.fetchone())
    c3 = await db.execute(
        """SELECT COUNT(*) as count FROM tasks t
        JOIN task_project_members pm ON pm.project_id = t.project_id AND pm.username = ?
        WHERE t.due_date IS NOT NULL AND t.due_date < date('now')
          AND t.status NOT IN (SELECT key FROM task_statuses s WHERE s.project_id = t.project_id AND s.kind = 'done')
          AND t.status != 'done'""",
        (username,),
    )
    overdue = (await c3.fetchone())[0]
    return {"totals": totals, "my_tasks": my_tasks, "overdue_count": overdue}




@router.post("/projects")
async def create_project(data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Project name required")

    parent_id = data.get("parent_id") or None
    # Verify parent exists if provided
    if parent_id:
        c_p = await db.execute("SELECT id FROM task_projects WHERE id = ?", (parent_id,))
        if not await c_p.fetchone():
            raise HTTPException(status_code=404, detail="Parent project not found")

    visibility = data.get("visibility", "private")
    if visibility not in ("public", "private"):
        visibility = "private"

    prefix = (data.get("prefix") or "").strip().upper()[:6] or _make_prefix(name)
    prefix = await _unique_prefix(db, prefix)
    color = data.get("color") or ""
    if color and not COLOR_RE.match(color):
        color = ""
    discussion_default = 1 if data.get("discussion_default", True) else 0

    slug = slugify(name) or "project"
    # Ensure unique slug
    c = await db.execute("SELECT 1 FROM task_projects WHERE slug = ?", (slug,))
    if await c.fetchone():
        slug = f"{slug}-{uuid.uuid4().hex[:6]}"

    now = now_iso()
    project_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO task_projects (id, name, slug, description, parent_id, created_by, created_at, updated_at, visibility, prefix, task_number_seq, color, discussion_default) VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?)",
        (project_id, name, slug, data.get("description", ""), parent_id, username, now, now, visibility, prefix, color, discussion_default),
    )
    await _seed_statuses(db, project_id, data.get("template") or "object")
    await db.execute(
        "INSERT INTO task_project_members (project_id, username, role, joined_at) VALUES (?,?,?,?)",
        (project_id, username, "owner", now),
    )

    # Add initial members if provided
    members = data.get("members") or []
    for m in members:
        member_username = m.get("username", "")
        member_role = m.get("role", "member")
        if member_username == username:
            continue
        if member_role not in ("lead", "member", "viewer"):
            member_role = "member"
        # home_server = '' — task projects are local-only; federated stubs
        # cannot be project members.
        c_u = await db.execute(
            "SELECT 1 FROM users WHERE username = ? AND blocked = 0 AND home_server = ''",
            (member_username,),
        )
        if await c_u.fetchone():
            await db.execute(
                "INSERT OR IGNORE INTO task_project_members (project_id, username, role, joined_at) VALUES (?,?,?,?)",
                (project_id, member_username, member_role, now),
            )

    # Inherit members from parent project for subprojects
    if parent_id:
        cursor_pm = await db.execute(
            "SELECT username, role FROM task_project_members WHERE project_id = ? AND username != ?",
            (parent_id, username),
        )
        parent_members = await cursor_pm.fetchall()
        for pm in parent_members:
            await db.execute(
                "INSERT OR IGNORE INTO task_project_members (project_id, username, role, joined_at) VALUES (?,?,?,?)",
                (project_id, pm["username"], pm["role"], now),
            )

    await db.commit()

    # Notify all project members about new project
    c_members = await db.execute(
        "SELECT username FROM task_project_members WHERE project_id = ?", (project_id,)
    )
    members = [r["username"] for r in await c_members.fetchall()]
    await manager.send_to_channel(members, {
        "event": "project_created",
        "project_id": project_id,
        "project": {"id": project_id, "name": name, "slug": slug},
    })

    return {"id": project_id, "name": name, "slug": slug, "prefix": prefix}


@router.get("/projects/{slug}")
async def get_project(slug: str, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    # Members
    cursor = await db.execute(
        """SELECT pm.username, pm.role, pm.joined_at, u.display_name, u.avatar_path,
                  -- Нагрузка: сколько открытых задач на человеке во всех проектах.
                  -- Видна в «Передать», чтобы не отдать задачу самому занятому.
                  (SELECT COUNT(*) FROM tasks t2
                     LEFT JOIN task_statuses s2 ON s2.project_id = t2.project_id AND s2.key = t2.status
                    WHERE t2.assignee = pm.username AND COALESCE(s2.kind, 'work') != 'done') AS open_tasks
           FROM task_project_members pm
           JOIN users u ON u.username = pm.username
           WHERE pm.project_id = ?""",
        (project["id"],),
    )
    project["members"] = [dict(r) for r in await cursor.fetchall()]
    # My role (viewer for public projects where user is not a member)
    role = await _get_project_role(db, project["id"], username)
    project["my_role"] = role or "viewer"
    project["is_member"] = role is not None
    # Creator display name
    cr_cursor = await db.execute(
        "SELECT display_name FROM users WHERE username = ?", (project.get("created_by", ""),)
    )
    cr_row = await cr_cursor.fetchone()
    project["creator_name"] = cr_row["display_name"] if cr_row else project.get("created_by", "")
    # Children (sub-projects)
    ch_cursor = await db.execute(
        """SELECT id, name, slug, description, prefix,
           (SELECT COUNT(*) FROM tasks WHERE project_id = p.id) as task_count
           FROM task_projects p WHERE p.parent_id = ?
           ORDER BY name""",
        (project["id"],),
    )
    project["children"] = [dict(r) for r in await ch_cursor.fetchall()]
    # Breadcrumb: walk up parent chain
    breadcrumb = []
    pid = project.get("parent_id")
    while pid:
        pc = await db.execute("SELECT id, name, slug, parent_id FROM task_projects WHERE id = ?", (pid,))
        pr = await pc.fetchone()
        if not pr:
            break
        breadcrumb.insert(0, {"id": pr["id"], "name": pr["name"], "slug": pr["slug"]})
        pid = pr["parent_id"]
    project["breadcrumb"] = breadcrumb
    project["statuses"] = await _get_statuses(db, project["id"])
    return project


@router.put("/projects/{slug}")
async def update_project(slug: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner"])

    name = (data.get("name") or "").strip() or project["name"]
    desc = data.get("description", project["description"])
    visibility = data.get("visibility", project.get("visibility", "private"))
    if visibility not in ("public", "private"):
        visibility = "private"
    default_assignee = data.get("default_assignee") or None
    prefix = (data.get("prefix") or "").strip().upper()[:6] if "prefix" in data else (project.get("prefix") or "")
    if prefix and prefix.upper() != (project.get("prefix") or "").upper():
        prefix = await _unique_prefix(db, prefix, project["id"])
    color = data.get("color", project.get("color") or "")
    if color and not COLOR_RE.match(color):
        color = project.get("color") or ""
    discussion_default = (1 if data["discussion_default"] else 0) if "discussion_default" in data else project.get("discussion_default", 1)
    now = now_iso()
    await db.execute(
        "UPDATE task_projects SET name = ?, description = ?, visibility = ?, default_assignee = ?, prefix = ?, color = ?, discussion_default = ?, updated_at = ? WHERE id = ?",
        (name, desc, visibility, default_assignee, prefix, color, discussion_default, now, project["id"]),
    )
    await db.commit()

    members = await _get_project_members(db, project["id"])
    await manager.send_to_channel(members, {
        "event": "project_updated", "project_id": project["id"],
        "project": {"id": project["id"], "name": name, "description": desc, "visibility": visibility,
                    "prefix": prefix, "color": color, "discussion_default": discussion_default},
    })
    return {"ok": True, "prefix": prefix}


@router.delete("/projects/{slug}")
async def delete_project(slug: str, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner"])

    members = await _get_project_members(db, project["id"])
    await db.execute("DELETE FROM task_projects WHERE id = ?", (project["id"],))
    await db.commit()

    await manager.send_to_channel(members, {
        "event": "project_deleted", "project_id": project["id"],
    })
    return {"ok": True}


# ── Project Members ──────────────────────────────────────────────

@router.get("/projects/{slug}/members")
async def list_members(slug: str, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    cursor = await db.execute(
        """SELECT pm.username, pm.role, pm.joined_at, u.display_name, u.avatar_path,
                  -- Нагрузка: сколько открытых задач на человеке во всех проектах.
                  -- Видна в «Передать», чтобы не отдать задачу самому занятому.
                  (SELECT COUNT(*) FROM tasks t2
                     LEFT JOIN task_statuses s2 ON s2.project_id = t2.project_id AND s2.key = t2.status
                    WHERE t2.assignee = pm.username AND COALESCE(s2.kind, 'work') != 'done') AS open_tasks
           FROM task_project_members pm
           JOIN users u ON u.username = pm.username
           WHERE pm.project_id = ?""",
        (project["id"],),
    )
    return [dict(r) for r in await cursor.fetchall()]


@router.post("/projects/{slug}/members")
async def add_member(slug: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner", "lead"])

    new_user = data.get("username", "").strip()
    role = data.get("role", "member")
    if role not in ("lead", "member", "viewer"):
        raise HTTPException(status_code=400, detail="Invalid role")
    if not new_user:
        raise HTTPException(status_code=400, detail="Username required")

    # Check user exists and is local — task projects do not span servers.
    c = await db.execute(
        "SELECT 1 FROM users WHERE username = ? AND home_server = ''", (new_user,)
    )
    if not await c.fetchone():
        raise HTTPException(status_code=404, detail="User not found")

    now = now_iso()
    await db.execute(
        "INSERT OR IGNORE INTO task_project_members (project_id, username, role, joined_at) VALUES (?,?,?,?)",
        (project["id"], new_user, role, now),
    )
    await db.commit()

    members = await _get_project_members(db, project["id"])
    await manager.send_to_channel(members, {
        "event": "project_member_added", "project_id": project["id"],
        "username": new_user, "role": role,
    })
    return {"ok": True}


@router.put("/projects/{slug}/members/{member_username}")
async def update_member_role(slug: str, member_username: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner"])

    role = data.get("role", "member")
    if role not in ("lead", "member", "viewer"):
        raise HTTPException(status_code=400, detail="Invalid role")
    if await _get_project_role(db, project["id"], member_username) == "owner":
        raise HTTPException(status_code=400, detail="Owner role cannot be changed")

    await db.execute(
        "UPDATE task_project_members SET role = ? WHERE project_id = ? AND username = ?",
        (role, project["id"], member_username),
    )
    await db.commit()
    return {"ok": True}


@router.delete("/projects/{slug}/members/{member_username}")
async def remove_member(slug: str, member_username: str, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner"])

    if member_username == username:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")
    if await _get_project_role(db, project["id"], member_username) == "owner":
        raise HTTPException(status_code=400, detail="Owner cannot be removed")

    await db.execute(
        "DELETE FROM task_project_members WHERE project_id = ? AND username = ?",
        (project["id"], member_username),
    )
    await db.commit()

    members = await _get_project_members(db, project["id"])
    await manager.send_to_channel(members, {
        "event": "project_member_removed", "project_id": project["id"],
        "username": member_username,
    })
    return {"ok": True}


# ── Tasks CRUD ───────────────────────────────────────────────────

@router.get("/projects/{slug}/tasks")
async def list_tasks(
    slug: str,
    username: str = Depends(get_current_user),
    status: str | None = Query(None),
    assignee: str | None = Query(None),
    priority: str | None = Query(None),
    q: str | None = Query(None),
):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    # «Вся компания»: открытый проект видят все сотрудники, менять — участники.
    # _get_project_by_slug уже отсеял закрытые проекты, где человек не участник.

    query = "SELECT * FROM tasks WHERE project_id = ?"
    params: list = [project["id"]]

    if status:
        query += " AND status = ?"
        params.append(status)
    if assignee:
        query += " AND assignee = ?"
        params.append(assignee)
    if priority:
        query += " AND priority = ?"
        params.append(priority)
    if q:
        query += " AND (LOWER(title) LIKE LOWER(?) OR LOWER(description) LIKE LOWER(?))"
        params.extend([f"%{q}%", f"%{q}%"])

    query += " ORDER BY position ASC, created_at DESC"
    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    return [await _build_task(db, r) for r in rows]


@router.post("/projects/{slug}/tasks")
async def create_task(slug: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner", "lead", "member"])

    title = (data.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title required")

    now = now_iso()
    task_id = str(uuid.uuid4())
    statuses = await _get_statuses(db, project["id"])
    status = data.get("status") or statuses[0]["key"]
    await _check_status(db, project["id"], status)
    discussion = data.get("discussion")
    discussion = (1 if discussion else 0) if discussion is not None else int(project.get("discussion_default", 1) or 0)

    # Get max position for the status column
    pos_cursor = await db.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM tasks WHERE project_id = ? AND status = ?",
        (project["id"], status),
    )
    position = (await pos_cursor.fetchone())[0]

    issue_type_id = data.get("issue_type_id") or None

    # Auto-number the task
    await db.execute(
        "UPDATE task_projects SET task_number_seq = COALESCE(task_number_seq, 0) + 1 WHERE id = ?",
        (project["id"],),
    )
    seq_cursor = await db.execute(
        "SELECT task_number_seq, prefix FROM task_projects WHERE id = ?",
        (project["id"],),
    )
    seq_row = await seq_cursor.fetchone()
    task_number = seq_row["task_number_seq"]
    prefix = seq_row["prefix"] or ""
    task_key = f"{prefix}-{task_number}" if prefix else ""

    await db.execute(
        """INSERT INTO tasks (id, project_id, title, description, status, priority, assignee, created_by, due_date, issue_type_id, position, task_number, task_key, discussion, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            task_id, project["id"], title,
            data.get("description", ""),
            status,
            data.get("priority", "medium"),
            data.get("assignee") or None,
            username, data.get("due_date") or None,
            issue_type_id, position, task_number, task_key, discussion, now, now,
        ),
    )

    # Labels
    label_ids = data.get("label_ids", [])
    for lid in label_ids:
        await db.execute(
            "INSERT OR IGNORE INTO task_label_assignments (task_id, label_id) VALUES (?,?)",
            (task_id, lid),
        )

    # Custom field values
    custom_values = data.get("custom_values", {})
    for field_id, value in custom_values.items():
        cv_id = str(uuid.uuid4())
        await db.execute(
            "INSERT OR REPLACE INTO task_custom_values (id, task_id, field_id, value) VALUES (?,?,?,?)",
            (cv_id, task_id, field_id, str(value)),
        )

    await db.execute(
        "UPDATE task_projects SET updated_at = ? WHERE id = ?", (now, project["id"])
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    task = await _build_task(db, await cursor.fetchone())

    members = await _get_project_members(db, project["id"])
    await manager.send_to_channel(members, {
        "event": "task_created", "project_id": project["id"], "task": task,
    })
    await _notify_task(db, project, task, "task_assigned", [task.get("assignee")], username)
    return task


async def _notify_task(db, project: dict, task: dict, kind: str, users, actor: str, body: str = ""):
    """Событие в «Важное» людям задачи. Себе не пишем — это делает notify()."""
    import notifications
    for u in {u for u in users if u}:
        await notifications.notify(db, u, kind, actor=actor, title=task.get("title") or "",
                                   body=body or project.get("name", ""), project_slug=project.get("slug", ""),
                                   task_id=task.get("id", ""), task_key=task.get("task_key") or "")


async def _is_done(db, project_id: str, status: str) -> bool:
    return status in await _done_keys(db, project_id)


async def _kind_of(db, project_id: str, status: str) -> str:
    for st in await _get_statuses(db, project_id):
        if st["key"] == status:
            return st["kind"]
    return "work"


async def _first_of_kind(db, project_id: str, kind: str) -> str | None:
    for st in await _get_statuses(db, project_id):
        if st["kind"] == kind:
            return st["key"]
    return None


async def _approvers(db, project_id: str, task: dict) -> list[str]:
    """Кто решает по задаче на согласовании: автор задачи, владелец и руководители проекта."""
    cur = await db.execute(
        "SELECT username FROM task_project_members WHERE project_id = ? AND role IN ('owner', 'lead')", (project_id,)
    )
    out = {r["username"] for r in await cur.fetchall()}
    if task.get("created_by"):
        out.add(task["created_by"])
    return sorted(out)


async def _on_status_change(db, project: dict, task: dict, old_status: str, new_status: str, actor: str):
    """События в «Важное» при смене статуса: сдали на согласование, закрыли."""
    if new_status == old_status:
        return
    new_kind = await _kind_of(db, project["id"], new_status)
    old_kind = await _kind_of(db, project["id"], old_status)
    if new_kind == "review" and old_kind != "review":
        await _notify_task(db, project, task, "task_review", await _approvers(db, project["id"], task), actor,
                           body=project.get("name", ""))
    elif new_kind == "done" and old_kind != "done":
        await _notify_task(db, project, task, "task_done", [task.get("created_by")], actor)


# ── Public Ticket Submission ────────────────────────────────────

@router.get("/projects/{slug}/submit-info")
async def get_submit_info(slug: str, username: str = Depends(get_current_user)):
    """Lightweight info for the public ticket submission page."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, name, slug, description, visibility FROM task_projects WHERE slug = ? AND visibility = 'public'",
        (slug,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Project not found or not public")
    project = dict(row)
    ch_cursor = await db.execute(
        """SELECT id, name, slug, description,
           (SELECT COUNT(*) FROM tasks WHERE project_id = p.id) as task_count
           FROM task_projects p WHERE p.parent_id = ? ORDER BY name""",
        (project["id"],),
    )
    project["children"] = [dict(r) for r in await ch_cursor.fetchall()]
    return project


@router.post("/projects/{slug}/submit-ticket")
async def submit_ticket(slug: str, data: dict, username: str = Depends(get_current_user)):
    """Create a ticket from any authenticated user (public projects only)."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM task_projects WHERE slug = ? AND visibility = 'public'",
        (slug,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Project not found or not public")
    project = dict(row)

    title = (data.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title required")

    priority = data.get("priority", "medium")
    if priority not in ("low", "medium", "high", "critical"):
        priority = "medium"

    now = now_iso()
    task_id = str(uuid.uuid4())

    pos_cursor = await db.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM tasks WHERE project_id = ? AND status = 'todo'",
        (project["id"],),
    )
    position = (await pos_cursor.fetchone())[0]

    default_assignee = project.get("default_assignee") or None

    # Auto-number the task
    await db.execute(
        "UPDATE task_projects SET task_number_seq = COALESCE(task_number_seq, 0) + 1 WHERE id = ?",
        (project["id"],),
    )
    seq_cursor2 = await db.execute(
        "SELECT task_number_seq, prefix FROM task_projects WHERE id = ?",
        (project["id"],),
    )
    seq_row2 = await seq_cursor2.fetchone()
    task_number = seq_row2["task_number_seq"]
    prefix = seq_row2["prefix"] or ""
    task_key = f"{prefix}-{task_number}" if prefix else ""

    await db.execute(
        """INSERT INTO tasks (id, project_id, title, description, status, priority,
           assignee, created_by, due_date, issue_type_id, position, task_number, task_key, discussion, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (task_id, project["id"], title, (data.get("description") or "").strip(),
         (await _get_statuses(db, project["id"]))[0]["key"], priority, default_assignee, username, None, None, position, task_number, task_key,
         int(project.get("discussion_default", 1) or 0), now, now),
    )
    await db.execute("UPDATE task_projects SET updated_at = ? WHERE id = ?", (now, project["id"]))
    await db.commit()

    cursor2 = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    task = await _build_task(db, await cursor2.fetchone())

    members = await _get_project_members(db, project["id"])
    await manager.send_to_channel(members, {
        "event": "task_created", "project_id": project["id"], "task": task,
    })
    await _notify_task(db, project, task, "task_assigned", [task.get("assignee")], username)
    return task


@router.get("/projects/{slug}/tasks/{task_id}")
async def get_task(slug: str, task_id: str, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    # «Вся компания»: открытый проект видят все сотрудники, менять — участники.
    # _get_project_by_slug уже отсеял закрытые проекты, где человек не участник.

    cursor = await db.execute(
        "SELECT * FROM tasks WHERE id = ? AND project_id = ?", (task_id, project["id"])
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")

    task = await _build_task(db, row)

    # Subtasks
    sub_cursor = await db.execute(
        "SELECT * FROM task_subtasks WHERE task_id = ? ORDER BY position ASC", (task_id,)
    )
    task["subtasks"] = [dict(r) for r in await sub_cursor.fetchall()]

    # Comments
    com_cursor = await db.execute(
        "SELECT * FROM task_comments WHERE task_id = ? ORDER BY created_at ASC", (task_id,)
    )
    task["comments"] = [dict(r) for r in await com_cursor.fetchall()]

    # Attachments
    att_cursor = await db.execute(
        "SELECT * FROM task_attachments WHERE task_id = ? ORDER BY created_at ASC", (task_id,)
    )
    task["attachments"] = [dict(r) for r in await att_cursor.fetchall()]

    # Что этот человек может сделать с задачей — кнопки на телефоне и в вебе
    # не гадают по ролям сами.
    st = {x["key"]: x for x in await _get_statuses(db, project["id"])}.get(task["status"]) or {}
    task["status_name"] = st.get("name", task["status"])
    task["status_color"] = st.get("color", "")
    task["status_kind"] = st.get("kind", "work")
    task["project_slug"] = project["slug"]
    task["project_name"] = project.get("name", "")
    task["project_color"] = project.get("color", "")
    role = await _get_project_role(db, project["id"], username)
    approvers = await _approvers(db, project["id"], task)
    task["can_decide"] = task["status_kind"] == "review" and username in approvers
    task["can_delegate"] = role in ("owner", "lead") or username in (task.get("created_by"), task.get("assignee"))
    task["can_submit"] = task.get("assignee") == username and task["status_kind"] in ("new", "work")
    task["has_review"] = bool(await _first_of_kind(db, project["id"], "review"))
    task["created_by_name"] = await get_display_name(task["created_by"]) if task.get("created_by") else ""
    return task


@router.put("/projects/{slug}/tasks/{task_id}")
async def update_task(slug: str, task_id: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)

    cursor = await db.execute(
        "SELECT * FROM tasks WHERE id = ? AND project_id = ?", (task_id, project["id"])
    )
    task_row = await cursor.fetchone()
    if not task_row:
        raise HTTPException(status_code=404, detail="Task not found")

    role = await _get_project_role(db, project["id"], username)
    # Свою задачу ведёт и исполнитель: двигает статус, ставит срок, правит описание.
    if role not in ("owner", "lead") and username not in (task_row["created_by"], task_row["assignee"]):
        raise HTTPException(status_code=403, detail="Not enough permissions")

    old_assignee = task_row["assignee"]
    old_status = task_row["status"]
    now = now_iso()
    fields = {}
    for f in ("title", "description", "status", "priority", "assignee", "due_date", "issue_type_id"):
        if f in data:
            fields[f] = data[f] if data[f] != "" else None
    if "status" in fields:
        if not fields["status"]:
            raise HTTPException(status_code=400, detail="Invalid status")
        await _check_status(db, project["id"], fields["status"])
    if "title" in fields and not (fields["title"] or "").strip():
        raise HTTPException(status_code=400, detail="Title required")
    if "discussion" in data:
        fields["discussion"] = 1 if data["discussion"] else 0

    if fields:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [now, task_id]
        await db.execute(
            f"UPDATE tasks SET {set_clause}, updated_at = ? WHERE id = ?", values
        )

    # Update labels if provided
    if "label_ids" in data:
        await db.execute("DELETE FROM task_label_assignments WHERE task_id = ?", (task_id,))
        for lid in data["label_ids"]:
            await db.execute(
                "INSERT OR IGNORE INTO task_label_assignments (task_id, label_id) VALUES (?,?)",
                (task_id, lid),
            )

    # Update custom field values if provided
    if "custom_values" in data:
        custom_values = data["custom_values"]
        for field_id, value in custom_values.items():
            cv_id = str(uuid.uuid4())
            await db.execute(
                "INSERT OR REPLACE INTO task_custom_values (id, task_id, field_id, value) VALUES (?,?,?,?)",
                (cv_id, task_id, field_id, str(value)),
            )

    await db.execute("UPDATE task_projects SET updated_at = ? WHERE id = ?", (now, project["id"]))
    await db.commit()

    cursor2 = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    task = await _build_task(db, await cursor2.fetchone())

    members = await _get_project_members(db, project["id"])
    await manager.send_to_channel(members, {
        "event": "task_updated", "project_id": project["id"], "task": task,
    })
    if "assignee" in fields and fields["assignee"] and fields["assignee"] != old_assignee:
        await _notify_task(db, project, task, "task_assigned", [fields["assignee"]], username)
    if "status" in fields:
        await _on_status_change(db, project, task, old_status, fields["status"], username)
    return task


@router.delete("/projects/{slug}/tasks/{task_id}")
async def delete_task(slug: str, task_id: str, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner", "lead"])

    await db.execute("DELETE FROM tasks WHERE id = ? AND project_id = ?", (task_id, project["id"]))
    await db.commit()

    members = await _get_project_members(db, project["id"])
    await manager.send_to_channel(members, {
        "event": "task_deleted", "project_id": project["id"], "task_id": task_id,
    })
    return {"ok": True}


@router.patch("/projects/{slug}/tasks/{task_id}/status")
async def change_task_status(slug: str, task_id: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner", "lead", "member"])

    new_status = data.get("status") or ""
    await _check_status(db, project["id"], new_status)

    cursor = await db.execute(
        "SELECT status FROM tasks WHERE id = ? AND project_id = ?", (task_id, project["id"])
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")

    old_status = row["status"]
    now = now_iso()

    # Get max position in target column
    pos_cursor = await db.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM tasks WHERE project_id = ? AND status = ?",
        (project["id"], new_status),
    )
    position = (await pos_cursor.fetchone())[0]

    await db.execute(
        "UPDATE tasks SET status = ?, position = ?, updated_at = ? WHERE id = ?",
        (new_status, position, now, task_id),
    )
    await db.execute("UPDATE task_projects SET updated_at = ? WHERE id = ?", (now, project["id"]))
    await db.commit()

    members = await _get_project_members(db, project["id"])
    await manager.send_to_channel(members, {
        "event": "task_status_changed", "project_id": project["id"],
        "task_id": task_id, "old_status": old_status, "new_status": new_status,
        "username": username,
    })
    c = await db.execute("SELECT id, title, task_key, created_by, assignee FROM tasks WHERE id = ?", (task_id,))
    t = await c.fetchone()
    if t:
        await _on_status_change(db, project, dict(t), old_status, new_status, username)
    return {"ok": True}


# ── Labels ───────────────────────────────────────────────────────

@router.get("/projects/{slug}/labels")
async def list_labels(slug: str, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    cursor = await db.execute(
        "SELECT * FROM task_labels WHERE project_id = ? ORDER BY name", (project["id"],)
    )
    return [dict(r) for r in await cursor.fetchall()]


@router.post("/projects/{slug}/labels")
async def create_label(slug: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner", "lead"])

    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Label name required")

    label_id = str(uuid.uuid4())
    color = data.get("color", "#4a90d9")
    await db.execute(
        "INSERT INTO task_labels (id, project_id, name, color) VALUES (?,?,?,?)",
        (label_id, project["id"], name, color),
    )
    await db.commit()
    return {"id": label_id, "name": name, "color": color}


@router.delete("/projects/{slug}/labels/{label_id}")
async def delete_label(slug: str, label_id: str, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner", "lead"])

    await db.execute("DELETE FROM task_labels WHERE id = ? AND project_id = ?", (label_id, project["id"]))
    await db.commit()
    return {"ok": True}


# ── Subtasks ─────────────────────────────────────────────────────

@router.post("/projects/{slug}/tasks/{task_id}/subtasks")
async def add_subtask(slug: str, task_id: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner", "lead", "member"])

    title = (data.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title required")

    pos_cursor = await db.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM task_subtasks WHERE task_id = ?", (task_id,)
    )
    position = (await pos_cursor.fetchone())[0]

    sub_id = str(uuid.uuid4())
    now = now_iso()
    await db.execute(
        "INSERT INTO task_subtasks (id, task_id, title, completed, position, created_at) VALUES (?,?,?,0,?,?)",
        (sub_id, task_id, title, position, now),
    )
    await db.commit()

    subtask = {"id": sub_id, "task_id": task_id, "title": title, "completed": 0, "position": position}
    members = await _get_project_members(db, project["id"])
    await manager.send_to_channel(members, {
        "event": "subtask_updated", "project_id": project["id"],
        "task_id": task_id, "subtask": subtask,
    })
    return subtask


@router.put("/projects/{slug}/tasks/{task_id}/subtasks/{sub_id}")
async def update_subtask(slug: str, task_id: str, sub_id: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner", "lead", "member"])

    if "completed" in data:
        await db.execute(
            "UPDATE task_subtasks SET completed = ? WHERE id = ? AND task_id = ?",
            (1 if data["completed"] else 0, sub_id, task_id),
        )
    if "title" in data:
        await db.execute(
            "UPDATE task_subtasks SET title = ? WHERE id = ? AND task_id = ?",
            (data["title"], sub_id, task_id),
        )
    await db.commit()

    cursor = await db.execute("SELECT * FROM task_subtasks WHERE id = ?", (sub_id,))
    subtask = dict(await cursor.fetchone())

    members = await _get_project_members(db, project["id"])
    await manager.send_to_channel(members, {
        "event": "subtask_updated", "project_id": project["id"],
        "task_id": task_id, "subtask": subtask,
    })
    return subtask


@router.delete("/projects/{slug}/tasks/{task_id}/subtasks/{sub_id}")
async def delete_subtask(slug: str, task_id: str, sub_id: str, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner", "lead", "member"])

    await db.execute("DELETE FROM task_subtasks WHERE id = ? AND task_id = ?", (sub_id, task_id))
    await db.commit()
    return {"ok": True}


# ── Comments ─────────────────────────────────────────────────────

@router.get("/projects/{slug}/tasks/{task_id}/comments")
async def list_comments(slug: str, task_id: str, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    cursor = await db.execute(
        "SELECT * FROM task_comments WHERE task_id = ? ORDER BY created_at ASC", (task_id,)
    )
    return [dict(r) for r in await cursor.fetchall()]


@router.post("/projects/{slug}/tasks/{task_id}/comments")
async def add_comment(slug: str, task_id: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner", "lead", "member"])

    text = (data.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Comment text required")
    c_t = await db.execute("SELECT discussion FROM tasks WHERE id = ? AND project_id = ?", (task_id, project["id"]))
    t_row = await c_t.fetchone()
    if not t_row:
        raise HTTPException(status_code=404, detail="Task not found")
    if not t_row["discussion"]:
        raise HTTPException(status_code=409, detail="Discussion is off for this task")

    display_name = await get_display_name(username)
    comment_id = str(uuid.uuid4())
    now = now_iso()

    await db.execute(
        "INSERT INTO task_comments (id, task_id, username, display_name, text, created_at) VALUES (?,?,?,?,?,?)",
        (comment_id, task_id, username, display_name, text, now),
    )
    await db.commit()

    comment = {
        "id": comment_id, "task_id": task_id, "username": username,
        "display_name": display_name, "text": text, "created_at": now,
    }
    members = await _get_project_members(db, project["id"])
    await manager.send_to_channel(members, {
        "event": "task_comment_added", "project_id": project["id"],
        "task_id": task_id, "comment": comment,
    })
    c = await db.execute("SELECT id, title, task_key, assignee, created_by FROM tasks WHERE id = ?", (task_id,))
    t = await c.fetchone()
    if t:
        await _notify_task(db, project, dict(t), "task_comment", [t["assignee"], t["created_by"]], username,
                           body=f"{display_name}: {text[:200]}")
    return comment


@router.delete("/projects/{slug}/tasks/{task_id}/comments/{comment_id}")
async def delete_comment(slug: str, task_id: str, comment_id: str, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)

    cursor = await db.execute("SELECT * FROM task_comments WHERE id = ?", (comment_id,))
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Comment not found")

    role = await _get_project_role(db, project["id"], username)
    if row["username"] != username and role not in ("owner", "lead"):
        raise HTTPException(status_code=403, detail="Not enough permissions")

    await db.execute("DELETE FROM task_comments WHERE id = ?", (comment_id,))
    await db.commit()
    return {"ok": True}


# ── Attachments ──────────────────────────────────────────────────

@router.post("/projects/{slug}/tasks/{task_id}/attachments")
async def upload_attachment(
    slug: str, task_id: str,
    file: UploadFile = File(...),
    username: str = Depends(get_current_user),
):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner", "lead", "member"])

    # Verify task exists
    c = await db.execute(
        "SELECT 1 FROM tasks WHERE id = ? AND project_id = ?", (task_id, project["id"])
    )
    if not await c.fetchone():
        raise HTTPException(status_code=404, detail="Task not found")

    # Save file
    task_dir = TASK_UPLOAD_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    filename = file.filename or "file"
    filepath = task_dir / filename
    # Avoid overwrite
    counter = 1
    stem = filepath.stem
    suffix = filepath.suffix
    while filepath.exists():
        filepath = task_dir / f"{stem}_{counter}{suffix}"
        counter += 1

    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    att_id = str(uuid.uuid4())
    now = now_iso()
    rel_path = f"/uploads/task_attachments/{task_id}/{filepath.name}"

    await db.execute(
        "INSERT INTO task_attachments (id, task_id, filename, filepath, filesize, uploaded_by, created_at) VALUES (?,?,?,?,?,?,?)",
        (att_id, task_id, filepath.name, rel_path, len(content), username, now),
    )
    await db.commit()

    return {"id": att_id, "filename": filepath.name, "filepath": rel_path, "filesize": len(content)}


@router.delete("/projects/{slug}/tasks/{task_id}/attachments/{att_id}")
async def delete_attachment(slug: str, task_id: str, att_id: str, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)

    cursor = await db.execute("SELECT * FROM task_attachments WHERE id = ?", (att_id,))
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Attachment not found")

    role = await _get_project_role(db, project["id"], username)
    if row["uploaded_by"] != username and role not in ("owner", "lead"):
        raise HTTPException(status_code=403, detail="Not enough permissions")

    # Delete file
    try:
        full_path = UPLOAD_DIR.parent / row["filepath"].lstrip("/")
        if full_path.exists():
            full_path.unlink()
    except Exception:
        pass

    await db.execute("DELETE FROM task_attachments WHERE id = ?", (att_id,))
    await db.commit()
    return {"ok": True}


# ── Issue Types ─────────────────────────────────────────────────

@router.get("/projects/{slug}/issue-types")
async def list_issue_types(slug: str, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    cursor = await db.execute(
        "SELECT * FROM task_issue_types WHERE project_id = ? ORDER BY position, name",
        (project["id"],),
    )
    return [dict(r) for r in await cursor.fetchall()]


@router.post("/projects/{slug}/issue-types")
async def create_issue_type(slug: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner", "lead"])

    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")

    it_id = str(uuid.uuid4())
    icon = data.get("icon", "📋")
    color = data.get("color", "#4a90d9")

    pos_cursor = await db.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM task_issue_types WHERE project_id = ?",
        (project["id"],),
    )
    position = (await pos_cursor.fetchone())[0]

    await db.execute(
        "INSERT INTO task_issue_types (id, project_id, name, icon, color, position) VALUES (?,?,?,?,?,?)",
        (it_id, project["id"], name, icon, color, position),
    )
    await db.commit()
    return {"id": it_id, "name": name, "icon": icon, "color": color, "position": position}


@router.put("/projects/{slug}/issue-types/{type_id}")
async def update_issue_type(slug: str, type_id: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner", "lead"])

    fields = {}
    for f in ("name", "icon", "color"):
        if f in data:
            fields[f] = data[f]
    if not fields:
        return {"ok": True}

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [type_id, project["id"]]
    await db.execute(
        f"UPDATE task_issue_types SET {set_clause} WHERE id = ? AND project_id = ?", values
    )
    await db.commit()
    return {"ok": True}


@router.delete("/projects/{slug}/issue-types/{type_id}")
async def delete_issue_type(slug: str, type_id: str, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner", "lead"])

    await db.execute(
        "DELETE FROM task_issue_types WHERE id = ? AND project_id = ?",
        (type_id, project["id"]),
    )
    await db.commit()
    return {"ok": True}


# ── Custom Fields ───────────────────────────────────────────────

@router.get("/projects/{slug}/fields")
async def list_custom_fields(slug: str, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    cursor = await db.execute(
        "SELECT * FROM task_custom_fields WHERE project_id = ? ORDER BY position, name",
        (project["id"],),
    )
    fields = [dict(r) for r in await cursor.fetchall()]
    # Parse options JSON
    for f in fields:
        try:
            f["options"] = json.loads(f["options"]) if f["options"] else []
        except Exception:
            f["options"] = []
    return fields


@router.post("/projects/{slug}/fields")
async def create_custom_field(slug: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner", "lead"])

    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Field name required")

    field_type = data.get("field_type", "text")
    if field_type not in ("text", "number", "select", "date", "checkbox", "url"):
        raise HTTPException(status_code=400, detail="Invalid field type")

    options = data.get("options", [])
    required = 1 if data.get("required") else 0

    pos_cursor = await db.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM task_custom_fields WHERE project_id = ?",
        (project["id"],),
    )
    position = (await pos_cursor.fetchone())[0]

    field_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO task_custom_fields (id, project_id, name, field_type, options, required, position) VALUES (?,?,?,?,?,?,?)",
        (field_id, project["id"], name, field_type, json.dumps(options), required, position),
    )
    await db.commit()
    return {"id": field_id, "name": name, "field_type": field_type, "options": options, "required": required, "position": position}


@router.put("/projects/{slug}/fields/{field_id}")
async def update_custom_field(slug: str, field_id: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner", "lead"])

    updates = []
    values = []
    for f in ("name", "field_type", "required"):
        if f in data:
            updates.append(f"{f} = ?")
            values.append(data[f] if f != "required" else (1 if data[f] else 0))
    if "options" in data:
        updates.append("options = ?")
        values.append(json.dumps(data["options"]))
    if not updates:
        return {"ok": True}

    values.extend([field_id, project["id"]])
    await db.execute(
        f"UPDATE task_custom_fields SET {', '.join(updates)} WHERE id = ? AND project_id = ?",
        values,
    )
    await db.commit()
    return {"ok": True}


@router.delete("/projects/{slug}/fields/{field_id}")
async def delete_custom_field(slug: str, field_id: str, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner", "lead"])

    await db.execute(
        "DELETE FROM task_custom_fields WHERE id = ? AND project_id = ?",
        (field_id, project["id"]),
    )
    await db.commit()
    return {"ok": True}


# ── Issue Type <-> Field bindings ───────────────────────────────

@router.get("/projects/{slug}/issue-types/{type_id}/fields")
async def list_issue_type_fields(slug: str, type_id: str, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    cursor = await db.execute(
        """SELECT cf.*, itf.required as type_required
           FROM task_custom_fields cf
           JOIN task_issue_type_fields itf ON itf.field_id = cf.id
           WHERE itf.issue_type_id = ? AND cf.project_id = ?
           ORDER BY cf.position""",
        (type_id, project["id"]),
    )
    fields = [dict(r) for r in await cursor.fetchall()]
    for f in fields:
        try:
            f["options"] = json.loads(f["options"]) if f["options"] else []
        except Exception:
            f["options"] = []
    return fields


@router.post("/projects/{slug}/issue-types/{type_id}/fields")
async def bind_field_to_issue_type(
    slug: str, type_id: str, data: dict, username: str = Depends(get_current_user)
):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner", "lead"])

    field_id = data.get("field_id")
    if not field_id:
        raise HTTPException(status_code=400, detail="field_id required")

    required = 1 if data.get("required") else 0
    await db.execute(
        "INSERT OR REPLACE INTO task_issue_type_fields (issue_type_id, field_id, required) VALUES (?,?,?)",
        (type_id, field_id, required),
    )
    await db.commit()
    return {"ok": True}


@router.delete("/projects/{slug}/issue-types/{type_id}/fields/{field_id}")
async def unbind_field_from_issue_type(
    slug: str, type_id: str, field_id: str, username: str = Depends(get_current_user)
):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner", "lead"])

    await db.execute(
        "DELETE FROM task_issue_type_fields WHERE issue_type_id = ? AND field_id = ?",
        (type_id, field_id),
    )
    await db.commit()
    return {"ok": True}


# ── Статусы проекта ───────────────────────────────────────────────

@router.get("/projects/{slug}/statuses")
async def list_statuses(slug: str, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    return await _get_statuses(db, project["id"])


@router.put("/projects/{slug}/statuses")
async def save_statuses(slug: str, data: dict, username: str = Depends(get_current_user)):
    """Сохранить весь список разом: порядок, названия, цвета, типы.
    Задачи из удалённых статусов переезжают в первый статус списка."""
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    await _require_role(db, project["id"], username, ["owner", "lead"])

    items = data.get("statuses") or []
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="At least one status required")
    if len(items) > 20:
        raise HTTPException(status_code=400, detail="Too many statuses")

    clean, seen = [], set()
    for it in items:
        name = (it.get("name") or "").strip()[:40]
        if not name:
            raise HTTPException(status_code=400, detail="Status name required")
        key = (it.get("key") or "").strip()[:40] or ("s_" + uuid.uuid4().hex[:8])
        if key in seen:
            raise HTTPException(status_code=400, detail="Duplicate status")
        seen.add(key)
        kind = it.get("kind") if it.get("kind") in STATUS_KINDS else "work"
        color = it.get("color") or "#8A958F"
        if not COLOR_RE.match(color):
            color = "#8A958F"
        clean.append((key, name, color, kind, 1 if it.get("on_board", True) else 0))
    if not any(c[4] for c in clean):
        raise HTTPException(status_code=400, detail="At least one status must be on the board")
    if not any(c[3] == "done" for c in clean):
        raise HTTPException(status_code=400, detail="At least one status must close the task")

    first = clean[0][0]
    keep = [c[0] for c in clean]
    marks = ",".join("?" * len(keep))
    await db.execute(
        f"UPDATE tasks SET status = ? WHERE project_id = ? AND status NOT IN ({marks})",
        (first, project["id"], *keep),
    )
    await db.execute("DELETE FROM task_statuses WHERE project_id = ?", (project["id"],))
    for pos, (key, name, color, kind, on_board) in enumerate(clean):
        await db.execute(
            "INSERT INTO task_statuses (id, project_id, key, name, color, kind, on_board, position) VALUES (?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), project["id"], key, name, color, kind, on_board, pos),
        )
    await db.execute("UPDATE task_projects SET updated_at = ? WHERE id = ?", (now_iso(), project["id"]))
    await db.commit()

    statuses = await _get_statuses(db, project["id"])
    members = await _get_project_members(db, project["id"])
    await manager.send_to_channel(members, {
        "event": "project_statuses_changed", "project_id": project["id"], "statuses": statuses,
    })
    return statuses


# ── Номера задач в чате ───────────────────────────────────────────

@router.get("/keys")
async def resolve_task_keys(keys: str = Query(""), username: str = Depends(get_current_user)):
    """ЛЕС-24 в сообщении → карточка задачи. Без доступа к проекту отдаём
    только сам номер: название чужой задачи не должно утекать в чат."""
    db = await get_db()
    wanted = [k.strip() for k in keys.split(",") if k.strip()][:50]
    out = []
    for key in wanted:
        c = await db.execute(
            """SELECT t.*, p.slug AS project_slug, p.name AS project_name, p.visibility,
                      pm.username AS me
               FROM tasks t JOIN task_projects p ON p.id = t.project_id
               LEFT JOIN task_project_members pm ON pm.project_id = p.id AND pm.username = ?
               WHERE UPPER(t.task_key) = UPPER(?) AND t.task_key != ''""",
            (username, key),
        )
        row = await c.fetchone()
        if not row:
            out.append({"key": key, "found": False})
            continue
        if row["me"] is None and row["visibility"] != "public":
            out.append({"key": row["task_key"], "found": True, "access": False})
            continue
        st = {s["key"]: s for s in await _get_statuses(db, row["project_id"])}.get(row["status"]) or {}
        task = await _build_task(db, row)
        out.append({
            "key": row["task_key"], "found": True, "access": True,
            "task_id": row["id"], "title": row["title"],
            "project_slug": row["project_slug"], "project_name": row["project_name"],
            "status": row["status"], "status_name": st.get("name", row["status"]),
            "status_color": st.get("color", ""), "status_kind": st.get("kind", "work"),
            "assignee_name": task.get("assignee_name", ""), "due_date": row["due_date"],
            "attachment_count": task.get("attachment_count", 0),
        })
    return out


# ── Решения по задаче: принять, вернуть, передать ──────────────────
# Отдельные ручки, а не общий PUT: у каждой своё правило «кто может» и своё
# событие получателю. Ими пользуются кнопки в «Важном», в карточке задачи и
# прямо в пуше на телефоне.

async def _load_task(db, project: dict, task_id: str) -> dict:
    cur = await db.execute("SELECT * FROM tasks WHERE id = ? AND project_id = ?", (task_id, project["id"]))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return dict(row)


async def _add_system_comment(db, task_id: str, username: str, text: str):
    display_name = await get_display_name(username)
    await db.execute(
        "INSERT INTO task_comments (id, task_id, username, display_name, text, created_at) VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), task_id, username, display_name, text, now_iso()),
    )


async def _broadcast_task(db, project: dict, task_id: str, extra_event: dict | None = None) -> dict:
    cur = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    task = await _build_task(db, await cur.fetchone())
    members = await _get_project_members(db, project["id"])
    await manager.send_to_channel(members, {"event": "task_updated", "project_id": project["id"], "task": task})
    if extra_event:
        await manager.send_to_channel(members, {**extra_event, "project_id": project["id"]})
    return task


@router.post("/projects/{slug}/tasks/{task_id}/approve")
async def approve_task(slug: str, task_id: str, data: dict | None = None, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    t = await _load_task(db, project, task_id)
    if username not in await _approvers(db, project["id"], t):
        raise HTTPException(status_code=403, detail="Принять задачу может автор или руководитель проекта")
    if await _kind_of(db, project["id"], t["status"]) == "done":
        raise HTTPException(status_code=409, detail="Задача уже закрыта")
    target = await _first_of_kind(db, project["id"], "done")
    if not target:
        raise HTTPException(status_code=409, detail="В проекте нет статуса «Завершена»")
    await db.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (target, now_iso(), task_id))
    note = ((data or {}).get("comment") or "").strip()
    if note:
        await _add_system_comment(db, task_id, username, note)
    await db.commit()
    task = await _broadcast_task(db, project, task_id)
    await _notify_task(db, project, task, "task_approved", [t.get("assignee")], username, body=note or project.get("name", ""))
    import notifications
    await notifications.mark_read(db, username, task_id=task_id)
    return task


@router.post("/projects/{slug}/tasks/{task_id}/submit")
async def submit_task(slug: str, task_id: str, data: dict | None = None, username: str = Depends(get_current_user)):
    """Исполнитель сдаёт работу: на согласование, а если такого статуса в проекте нет — сразу в «Завершена»."""
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    t = await _load_task(db, project, task_id)
    role = await _get_project_role(db, project["id"], username)
    if t.get("assignee") != username and role not in ("owner", "lead"):
        raise HTTPException(status_code=403, detail="Сдать задачу может её исполнитель")
    if await _kind_of(db, project["id"], t["status"]) in ("review", "done"):
        raise HTTPException(status_code=409, detail="Задача уже сдана")
    target = await _first_of_kind(db, project["id"], "review") or await _first_of_kind(db, project["id"], "done")
    if not target:
        raise HTTPException(status_code=409, detail="В проекте нет подходящего статуса")
    await db.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (target, now_iso(), task_id))
    note = ((data or {}).get("comment") or "").strip()
    if note:
        await _add_system_comment(db, task_id, username, note)
    await db.commit()
    task = await _broadcast_task(db, project, task_id)
    await _on_status_change(db, project, task, t["status"], target, username)
    return task


@router.post("/projects/{slug}/tasks/{task_id}/reject")
async def reject_task(slug: str, task_id: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    t = await _load_task(db, project, task_id)
    if username not in await _approvers(db, project["id"], t):
        raise HTTPException(status_code=403, detail="Вернуть задачу может автор или руководитель проекта")
    comment = (data.get("comment") or "").strip()
    if not comment:
        raise HTTPException(status_code=400, detail="Напишите, что поправить")
    target = await _first_of_kind(db, project["id"], "work") or await _first_of_kind(db, project["id"], "new")
    fields, values = ["status = ?", "updated_at = ?"], [target or t["status"], now_iso()]
    due = (data.get("due_date") or "").strip()
    if due:
        fields.append("due_date = ?"); values.append(due[:10])
    await db.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?", [*values, task_id])
    await _add_system_comment(db, task_id, username, f"Вернул(а) на доработку: {comment}")
    await db.commit()
    task = await _broadcast_task(db, project, task_id)
    await _notify_task(db, project, task, "task_returned", [t.get("assignee")], username, body=comment[:300])
    import notifications
    await notifications.mark_read(db, username, task_id=task_id)
    return task


@router.post("/projects/{slug}/tasks/{task_id}/delegate")
async def delegate_task(slug: str, task_id: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    project = await _get_project_by_slug(db, slug, username)
    t = await _load_task(db, project, task_id)
    role = await _get_project_role(db, project["id"], username)
    if role not in ("owner", "lead") and username not in (t["created_by"], t["assignee"]):
        raise HTTPException(status_code=403, detail="Передать задачу может автор, исполнитель или руководитель")
    to = (data.get("assignee") or "").strip()
    if not to or not await _get_project_role(db, project["id"], to):
        raise HTTPException(status_code=400, detail="Выберите участника проекта")
    fields, values = ["assignee = ?", "updated_at = ?"], [to, now_iso()]
    due = (data.get("due_date") or "").strip()
    if due:
        fields.append("due_date = ?"); values.append(due[:10])
    await db.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?", [*values, task_id])
    note = (data.get("comment") or "").strip()
    to_name = await get_display_name(to)
    await _add_system_comment(db, task_id, username, f"Передал(а) задачу: {to_name}" + (f". {note}" if note else ""))
    await db.commit()
    task = await _broadcast_task(db, project, task_id)
    await _notify_task(db, project, task, "task_assigned", [to], username, body=note or project.get("name", ""))
    import notifications
    await notifications.mark_read(db, username, task_id=task_id)
    return task


def _mine_sql(scope: str, username: str, select: str) -> tuple[str, list]:
    q = f"""SELECT {select}
              FROM tasks t
              JOIN task_projects p ON p.id = t.project_id
              JOIN task_project_members me ON me.project_id = t.project_id AND me.username = ?
              LEFT JOIN task_statuses s ON s.project_id = t.project_id AND s.key = t.status
              WHERE COALESCE(s.kind, 'work') != 'done'"""
    params: list = [username]
    if scope == "decide":
        q += " AND s.kind = 'review' AND (t.created_by = ? OR me.role IN ('owner', 'lead'))"
        params.append(username)
    elif scope == "created":
        q += " AND t.created_by = ? AND COALESCE(t.assignee, '') != ?"
        params += [username, username]
    else:
        q += " AND t.assignee = ?"
        params.append(username)
    return q, params


@router.get("/mine")
async def my_tasks(scope: str = Query("mine"), username: str = Depends(get_current_user)):
    """Задачи для телефона: decide — ждут моего решения, mine — на мне, created — поставил я.

    Открытые задачи из всех проектов, где я участник, сразу с проектом и
    статусом, плюс счётчики всех трёх вкладок — экран не ходит по проектам.
    """
    if scope not in ("decide", "mine", "created"):
        scope = "mine"
    db = await get_db()
    q, params = _mine_sql(scope, username, """t.*, p.slug AS project_slug, p.name AS project_name,
        p.color AS project_color, s.name AS status_name, s.color AS status_color,
        COALESCE(s.kind, 'work') AS status_kind, me.role AS my_role""")
    q += " ORDER BY CASE WHEN t.due_date IS NULL OR t.due_date = '' THEN 1 ELSE 0 END, t.due_date, t.updated_at DESC LIMIT 200"
    cur = await db.execute(q, params)
    items = []
    for r in await cur.fetchall():
        d = await _build_task(db, r)
        for k in ("project_slug", "project_name", "project_color", "status_name", "status_color", "status_kind"):
            d[k] = r[k]
        d["can_decide"] = r["status_kind"] == "review" and (r["created_by"] == username or r["my_role"] in ("owner", "lead"))
        items.append(d)
    counts = {}
    for sc in ("decide", "mine", "created"):
        cq, cp = _mine_sql(sc, username, "COUNT(*)")
        counts[sc] = (await (await db.execute(cq, cp)).fetchone())[0]
    return {"scope": scope, "items": items, "counts": counts}
