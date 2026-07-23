"""ChaosHelper Messenger — File manager (CRUD + upload-to-chat)."""

import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Depends, Form, Query
from fastapi.responses import FileResponse
from helpers import (
    get_current_user, user_root, safe_path,
    load_meta, save_meta, set_item_date, UPLOAD_DIR,
)

router = APIRouter(prefix="/api", tags=["files"])


# ── Folders ──────────────────────────────────────────────────────

@router.post("/folders")
async def create_folder(data: dict, username: str = Depends(get_current_user)):
    path = data.get("path", "").strip("/")
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Folder name required")
    folder = safe_path(username, f"{path}/{name}" if path else name)
    folder.mkdir(parents=True, exist_ok=True)
    rel = str(folder.relative_to(user_root(username))).replace("\\", "/")
    set_item_date(username, rel)
    return {"ok": True, "path": rel}


@router.post("/files/mkdir")
async def create_folder_alias(data: dict, username: str = Depends(get_current_user)):
    """Alias for POST /folders — Flutter mobile app uses this endpoint."""
    return await create_folder(data, username)


def _normalize_path(path: str) -> str:
    """Normalize path: treat '/' or leading '/' as empty (root)."""
    path = path.strip()
    if path == "/":
        return ""
    return path.lstrip("/")


# ── Upload ───────────────────────────────────────────────────────

@router.post("/upload")
async def upload_files(
    files: list[UploadFile] = File(...),
    path: str = Form(""),
    username: str = Depends(get_current_user),
):
    path = _normalize_path(path)
    base = safe_path(username, path) if path else user_root(username)
    base.mkdir(parents=True, exist_ok=True)
    uploaded = []
    for f in files:
        dest = base / f.filename
        if dest.exists():
            stem = dest.stem
            suffix = dest.suffix
            counter = 1
            while dest.exists():
                dest = base / f"{stem}_{counter}{suffix}"
                counter += 1
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        rel = str(dest.relative_to(user_root(username))).replace("\\", "/")
        set_item_date(username, rel)
        uploaded.append(rel)
    return {"ok": True, "files": uploaded}


@router.post("/files/upload-to-chat")
async def upload_to_chat(
    file: UploadFile = File(...),
    username: str = Depends(get_current_user),
):
    """Upload a file for sharing in chat. Returns file info for message attachment."""
    chat_dir = user_root(username) / "_chat"
    chat_dir.mkdir(parents=True, exist_ok=True)

    dest = chat_dir / file.filename
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        counter = 1
        while dest.exists():
            dest = chat_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    with open(dest, "wb") as out:
        shutil.copyfileobj(file.file, out)

    rel = str(dest.relative_to(user_root(username))).replace("\\", "/")
    set_item_date(username, rel)

    ext = dest.suffix.lower()
    file_type = _detect_type(ext)

    return {
        "ok": True,
        "file": {
            "name": dest.name,
            "path": rel,
            "url": f"/uploads/{username}/{rel}",
            "size": dest.stat().st_size,
            "type": file_type,
        },
    }


# ── List files ───────────────────────────────────────────────────

@router.get("/files")
async def list_files(path: str = Query(""), username: str = Depends(get_current_user)):
    path = _normalize_path(path)
    base = safe_path(username, path) if path else user_root(username)
    if not base.exists():
        return {"items": [], "path": path}
    meta = load_meta(username)
    items = []
    for entry in sorted(base.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
        if entry.name.startswith(".") or entry.name.startswith("_"):
            continue
        rel = str(entry.relative_to(user_root(username))).replace("\\", "/")
        is_dir = entry.is_dir()
        size = 0 if is_dir else entry.stat().st_size
        added = meta.get(rel, datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc).isoformat())
        ext = entry.suffix.lower() if not is_dir else ""
        file_type = "folder" if is_dir else _detect_type(ext)
        items.append({
            "name": entry.name,
            "path": rel,
            "is_dir": is_dir,
            "size": size,
            "type": file_type,
            "added": added,
        })
    return {"items": items, "path": path}


# ── Recent ───────────────────────────────────────────────────────

@router.get("/files/recent")
async def recent_files(limit: int = Query(5), username: str = Depends(get_current_user)):
    meta = load_meta(username)
    root = user_root(username)
    files = []
    for rel, date_str in meta.items():
        fp = root / rel
        if fp.is_file():
            ext = fp.suffix.lower()
            files.append({
                "name": fp.name,
                "path": rel,
                "size": fp.stat().st_size,
                "type": _detect_type(ext),
                "added": date_str,
            })
    files.sort(key=lambda x: x["added"], reverse=True)
    return files[:limit]


# ── Download ─────────────────────────────────────────────────────

@router.get("/files/download")
async def download_file(path: str = Query(...), username: str = Depends(get_current_user)):
    target = safe_path(username, _normalize_path(path))
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target, filename=target.name)


# ── Delete ───────────────────────────────────────────────────────

@router.delete("/files")
async def delete_item(data: dict, username: str = Depends(get_current_user)):
    path = _normalize_path(data.get("path", ""))
    target = safe_path(username, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    meta = load_meta(username)
    keys_to_remove = [k for k in meta if k == path or k.startswith(path + "/")]
    for k in keys_to_remove:
        del meta[k]
    save_meta(username, meta)
    return {"ok": True}


# ── Rename ───────────────────────────────────────────────────────

@router.put("/files/rename")
async def rename_item(data: dict, username: str = Depends(get_current_user)):
    old_path = _normalize_path(data.get("path", ""))
    new_name = data.get("new_name", "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="New name required")
    target = safe_path(username, old_path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    new_target = target.parent / new_name
    target.rename(new_target)
    new_rel = str(new_target.relative_to(user_root(username))).replace("\\", "/")
    meta = load_meta(username)
    if old_path in meta:
        meta[new_rel] = meta.pop(old_path)
    save_meta(username, meta)
    return {"ok": True, "new_path": new_rel}


# ── Copy ────────────────────────────────────────────────────────

@router.post("/files/copy")
async def copy_item(data: dict, username: str = Depends(get_current_user)):
    src = data.get("path", "")
    dest_dir = data.get("dest", "").strip("/")
    source = safe_path(username, src)
    if not source.exists():
        raise HTTPException(status_code=404, detail="Source not found")
    target_dir = safe_path(username, dest_dir) if dest_dir else user_root(username)
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / source.name
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        counter = 1
        while dest.exists():
            if source.is_dir():
                dest = target_dir / f"{stem}_{counter}"
            else:
                dest = target_dir / f"{stem}_{counter}{suffix}"
            counter += 1
    if source.is_dir():
        shutil.copytree(source, dest)
    else:
        shutil.copy2(source, dest)
    rel = str(dest.relative_to(user_root(username))).replace("\\", "/")
    set_item_date(username, rel)
    return {"ok": True, "new_path": rel}


# ── Share file to chat (existing file → message) ───────────────

@router.post("/files/share-to-chat")
async def share_to_chat(data: dict, username: str = Depends(get_current_user)):
    """Share an existing file from the file manager as a chat attachment."""
    path = data.get("path", "")
    source = safe_path(username, path)
    if not source.exists() or not source.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    ext = source.suffix.lower()
    file_type = _detect_type(ext)
    rel = str(source.relative_to(user_root(username))).replace("\\", "/")
    return {
        "ok": True,
        "file": {
            "name": source.name,
            "path": rel,
            "url": f"/uploads/{username}/{rel}",
            "size": source.stat().st_size,
            "type": file_type,
        },
    }


# ── Helpers ──────────────────────────────────────────────────────

def _detect_type(ext: str) -> str:
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"):
        return "image"
    if ext in (".mp4", ".webm", ".mov", ".avi", ".mkv"):
        return "video"
    if ext in (".mp3", ".wav", ".ogg", ".flac", ".aac"):
        return "audio"
    if ext in (".pdf",):
        return "pdf"
    if ext in (".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".csv", ".md"):
        return "document"
    return "file"
