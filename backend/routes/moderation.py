"""Zweig Messenger — user blocks and content reports.

App Store guideline 1.2 requires apps with user-generated content to let users
block abusive accounts and report offensive content, and requires the operator
to act on those reports. Google Play's UGC policy asks for the same.

This is separate from `users.blocked`, which is an admin switching an account
off for everyone. A block here is personal and one-directional: the blocker
stops seeing the blocked user's messages, and the blocked user is not told.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from helpers import get_current_user, get_display_name, require_admin, now_iso
from database import get_db

router = APIRouter(prefix="/api", tags=["moderation"])

REPORT_REASONS = (
    "spam",
    "harassment",
    "violence",
    "sexual",
    "other",
)


async def get_blocked_by(db, username: str) -> set[str]:
    """Usernames [username] has blocked."""
    cursor = await db.execute(
        "SELECT blocked FROM user_blocks WHERE blocker = ?", (username,)
    )
    return {r["blocked"] for r in await cursor.fetchall()}


async def get_blockers_of(db, username: str) -> set[str]:
    """Usernames that have blocked [username] — used to skip broadcasts."""
    cursor = await db.execute(
        "SELECT blocker FROM user_blocks WHERE blocked = ?", (username,)
    )
    return {r["blocker"] for r in await cursor.fetchall()}


# ---------------------------------------------------------------- blocks


@router.get("/blocks")
async def list_blocks(username: str = Depends(get_current_user)):
    db = await get_db()
    cursor = await db.execute(
        """SELECT b.blocked, b.created_at, u.display_name, u.avatar_path
           FROM user_blocks b
           LEFT JOIN users u ON u.username = b.blocked
           WHERE b.blocker = ?
           ORDER BY b.created_at DESC""",
        (username,),
    )
    return {
        "blocked": [
            {
                "username": r["blocked"],
                "display_name": r["display_name"] or r["blocked"],
                "avatar_path": r["avatar_path"] or "",
                "created_at": r["created_at"],
            }
            for r in await cursor.fetchall()
        ]
    }


@router.post("/blocks")
async def block_user(data: dict, username: str = Depends(get_current_user)):
    target = str(data.get("username") or "").strip().lower()
    if not target:
        raise HTTPException(status_code=400, detail="username is required")
    if target == username:
        raise HTTPException(status_code=400, detail="Cannot block yourself")

    db = await get_db()
    cursor = await db.execute("SELECT 1 FROM users WHERE username = ?", (target,))
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="User not found")

    # Blocking twice is not an error — the client may retry.
    await db.execute(
        "INSERT OR IGNORE INTO user_blocks (id, blocker, blocked, created_at)"
        " VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), username, target, now_iso()),
    )
    await db.commit()
    return {"ok": True, "username": target, "blocked": True}


@router.delete("/blocks/{target}")
async def unblock_user(target: str, username: str = Depends(get_current_user)):
    db = await get_db()
    await db.execute(
        "DELETE FROM user_blocks WHERE blocker = ? AND blocked = ?",
        (username, target.strip().lower()),
    )
    await db.commit()
    return {"ok": True, "username": target, "blocked": False}


# --------------------------------------------------------------- reports


@router.post("/reports")
async def create_report(data: dict, username: str = Depends(get_current_user)):
    reason = str(data.get("reason") or "other").strip().lower()
    if reason not in REPORT_REASONS:
        reason = "other"

    message_id = str(data.get("message_id") or "").strip()
    channel_slug = str(data.get("channel_slug") or "").strip()
    details = str(data.get("details") or "").strip()[:2000]
    reported_user = str(data.get("reported_user") or "").strip().lower()

    db = await get_db()

    # Snapshot the reported message server-side rather than trusting the client:
    # the report is evidence, and it must survive the author deleting the text.
    message_text = ""
    if message_id:
        cursor = await db.execute(
            "SELECT sender, text FROM messages WHERE id = ?", (message_id,)
        )
        row = await cursor.fetchone()
        if row:
            message_text = (row["text"] or "")[:4000]
            if not reported_user:
                reported_user = row["sender"]

    if not reported_user and not message_id:
        raise HTTPException(
            status_code=400, detail="reported_user or message_id is required"
        )

    report_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO content_reports
           (id, reporter, reported_user, message_id, channel_slug, reason,
            details, message_text, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
        (
            report_id,
            username,
            reported_user,
            message_id,
            channel_slug,
            reason,
            details,
            message_text,
            now_iso(),
        ),
    )
    await db.commit()
    return {"ok": True, "id": report_id}


@router.get("/admin/reports")
async def list_reports(
    status: str = "open", username: str = Depends(get_current_user)
):
    db = await get_db()
    await require_admin(username)

    if status == "all":
        cursor = await db.execute(
            "SELECT * FROM content_reports ORDER BY created_at DESC LIMIT 500"
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM content_reports WHERE status = ?"
            " ORDER BY created_at DESC LIMIT 500",
            (status,),
        )

    reports = []
    for r in await cursor.fetchall():
        reports.append(
            {
                "id": r["id"],
                "reporter": r["reporter"],
                "reporter_name": await get_display_name(r["reporter"]),
                "reported_user": r["reported_user"],
                "reported_name": await get_display_name(r["reported_user"])
                if r["reported_user"]
                else "",
                "message_id": r["message_id"],
                "channel_slug": r["channel_slug"],
                "reason": r["reason"],
                "details": r["details"],
                "message_text": r["message_text"],
                "status": r["status"],
                "created_at": r["created_at"],
                "resolved_at": r["resolved_at"],
                "resolved_by": r["resolved_by"],
            }
        )
    return {"reports": reports}


@router.put("/admin/reports/{report_id}")
async def resolve_report(
    report_id: str, data: dict, username: str = Depends(get_current_user)
):
    db = await get_db()
    await require_admin(username)

    new_status = str(data.get("status") or "").strip().lower()
    if new_status not in ("open", "reviewed", "dismissed"):
        raise HTTPException(status_code=400, detail="Invalid status")

    cursor = await db.execute(
        "SELECT 1 FROM content_reports WHERE id = ?", (report_id,)
    )
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Report not found")

    resolved_at = "" if new_status == "open" else now_iso()
    resolved_by = "" if new_status == "open" else username
    await db.execute(
        "UPDATE content_reports SET status = ?, resolved_at = ?, resolved_by = ?"
        " WHERE id = ?",
        (new_status, resolved_at, resolved_by, report_id),
    )
    await db.commit()
    return {"ok": True, "id": report_id, "status": new_status}


@router.delete("/admin/reports/{report_id}/message")
async def delete_reported_message(
    report_id: str, username: str = Depends(get_current_user)
):
    """Remove the message a report points at, and close the report.

    Guideline 1.2 requires the operator to actually be able to take content
    down, not just to collect complaints.
    """
    db = await get_db()
    await require_admin(username)

    cursor = await db.execute(
        "SELECT message_id FROM content_reports WHERE id = ?", (report_id,)
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Report not found")

    if row["message_id"]:
        await db.execute("DELETE FROM messages WHERE id = ?", (row["message_id"],))

    await db.execute(
        "UPDATE content_reports SET status = 'reviewed', resolved_at = ?,"
        " resolved_by = ? WHERE id = ?",
        (now_iso(), username, report_id),
    )
    await db.commit()
    return {"ok": True, "id": report_id, "status": "reviewed"}
