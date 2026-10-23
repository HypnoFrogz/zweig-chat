"""ChaosHelper Messenger — File manager (CRUD + upload-to-chat)."""

import asyncio
import errno
import hashlib
import re
import shutil
from urllib.parse import quote
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Depends, Form, Query
from fastapi.responses import FileResponse
from helpers import (
    get_current_user, user_root, safe_path,
    load_meta, save_meta, set_item_date, UPLOAD_DIR,
)

router = APIRouter(prefix="/api", tags=["files"])


# Запрещённое в имени файла: разделители пути, символы, которых не терпят
# Windows и Android, и управляющие.
_FORBIDDEN_IN_NAME = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]')
# Длина имени в байтах. Файловые системы терпят 255, запас оставлен под
# суффикс «_N», который добавляется при совпадении имён.
_MAX_NAME_BYTES = 150


def _safe_filename(raw: str | None, default: str = "file") -> str:
    """Имя, под которым файл можно положить на диск и отдать обратно.

    Имя присылает клиент, и верить ему нельзя. Файл, присланный в мобильное
    приложение через «Поделиться», приходит с тем, что придумал чужой
    провайдер: `document:1234`, путь с косыми чертами, пустая строка или имя на
    сотни символов. Раньше оно шло на диск как есть — загрузка падала или файл
    ложился не туда.
    """
    name = (raw or "").replace("\\", "/").split("/")[-1]
    name = _FORBIDDEN_IN_NAME.sub("_", name).strip().strip(".").strip()
    if not name:
        return default
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    ext = ext[:16]
    suffix = f".{ext}" if ext else ""
    while stem and len((stem + suffix).encode("utf-8")) > _MAX_NAME_BYTES:
        stem = stem[:-1]
    return (stem.strip() or default) + suffix


def upload_url(username: str, rel: str) -> str:
    """Ссылка на файл для вложения: каждый кусок пути закодирован.

    Раньше путь шёл в ссылку как есть. «фото #3.jpg» обрывалось на «#»
    (дальше браузер считал всё якорем) и отдавало 404, «отчёт 100%.xlsx»
    ломалось на «%». Пробелы и кириллица после кодирования тоже надёжнее.
    """
    return "/uploads/" + quote(f"{username}/{rel}", safe="/")


def _save_upload(src, dest: Path) -> None:
    """Записать загружаемый файл; при нехватке места — 507, а не 500.

    Недописанный файл удаляется, чтобы в папке не оставались обрывки.
    """
    try:
        with open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
    except OSError as e:
        try:
            dest.unlink()
        except OSError:
            pass
        if e.errno == errno.ENOSPC:
            raise HTTPException(status_code=507, detail="На сервере закончилось место")
        raise HTTPException(status_code=500, detail="Не удалось сохранить файл")


# ── Миниатюры картинок ───────────────────────────────────────────
# Сетка «Файлов» грузила каждую картинку целиком: папка с фотографиями с
# телефона — это десятки мегабайт ради значков 120×120. Теперь рядом лежат
# уменьшенные копии (до 480 px по ширине) в скрытой папке `_thumbs` того же
# пользователя. Делает их ffmpeg — он уже стоит в образе ради обложек видео.
_THUMB_MAX_W = 480
_THUMB_MIN_BYTES = 150 * 1024  # маленькую картинку проще отдать как есть
_THUMB_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".heic"}
_thumbs_in_progress: set[str] = set()
_thumb_tasks: set = set()


def _thumb_rel(rel: str, src: Path) -> str:
    st = src.stat()
    key = f"{rel}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8")
    return f"_thumbs/{hashlib.sha1(key).hexdigest()[:24]}.jpg"


def _make_image_thumb(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part.jpg")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-frames:v", "1",
             "-vf", f"scale='min({_THUMB_MAX_W},iw)':-2", "-q:v", "5", str(tmp)],
            check=True, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(dest)
    except (subprocess.SubprocessError, OSError):
        pass
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


async def _make_thumbs_later(jobs: list[tuple[Path, Path]]) -> None:
    for src, dest in jobs:
        try:
            await asyncio.to_thread(_make_image_thumb, src, dest)
        finally:
            _thumbs_in_progress.discard(str(dest))


def _thumb_for(username: str, rel: str, entry: Path, jobs: list) -> str | None:
    """Ссылка на миниатюру, если она уже есть; иначе ставим её в очередь."""
    if entry.suffix.lower() not in _THUMB_EXT or entry.stat().st_size < _THUMB_MIN_BYTES:
        return None
    t_rel = _thumb_rel(rel, entry)
    dest = user_root(username) / t_rel
    if dest.exists():
        return upload_url(username, t_rel)
    if str(dest) not in _thumbs_in_progress and len(jobs) < 40:
        _thumbs_in_progress.add(str(dest))
        jobs.append((entry, dest))
    return None


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
        dest = base / _safe_filename(f.filename)
        if dest.exists():
            stem = dest.stem
            suffix = dest.suffix
            counter = 1
            while dest.exists():
                dest = base / f"{stem}_{counter}{suffix}"
                counter += 1
        _save_upload(f.file, dest)
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

    dest = chat_dir / _safe_filename(file.filename)
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        counter = 1
        while dest.exists():
            dest = chat_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    _save_upload(file.file, dest)

    rel = str(dest.relative_to(user_root(username))).replace("\\", "/")
    set_item_date(username, rel)

    ext = dest.suffix.lower()
    file_type = _detect_type(ext)

    info = {
        "name": dest.name,
        "path": rel,
        "url": upload_url(username, rel),
        "size": dest.stat().st_size,
        "type": file_type,
    }

    # Кадр-обложка к видео. Делается здесь, а не на телефоне: обложку должны
    # видеть все — второй мобильный клиент, веб и тот, кто откроет переписку
    # с чужого сервера. Клиент, снявший кадр у себя, поделиться им не может.
    if file_type == "video":
        thumb_rel = _make_video_thumbnail(dest, user_root(username))
        if thumb_rel:
            info["thumbnail"] = upload_url(username, thumb_rel)

    return {"ok": True, "file": info}


def _make_video_thumbnail(video: Path, root: Path) -> str | None:
    """Снимает первый кадр видео рядом с ним и отдаёт путь от корня.

    Кадр берётся с первой секунды, а не с нулевой: в самом начале ролика часто
    чёрный кадр, и обложка получалась бы пустым прямоугольником. Неудача здесь
    не ошибка загрузки: файл уже принят, просто останется без обложки.
    """
    thumb = video.with_suffix(".thumb.jpg")
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", "1", "-i", str(video),
                "-frames:v", "1",
                "-vf", "scale=640:-2",
                str(thumb),
            ],
            check=True,
            timeout=20,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        return None

    if not thumb.exists() or thumb.stat().st_size == 0:
        # Ролик короче секунды — берём самый первый кадр.
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", str(video), "-frames:v", "1",
                    "-vf", "scale=640:-2", str(thumb),
                ],
                check=True,
                timeout=20,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.SubprocessError, OSError, FileNotFoundError):
            return None

    if not thumb.exists() or thumb.stat().st_size == 0:
        return None
    return str(thumb.relative_to(root)).replace("\\", "/")


# ── List files ───────────────────────────────────────────────────

@router.get("/files")
async def list_files(path: str = Query(""), username: str = Depends(get_current_user)):
    path = _normalize_path(path)
    base = safe_path(username, path) if path else user_root(username)
    if not base.exists():
        return {"items": [], "path": path}
    meta = load_meta(username)
    items = []
    thumb_jobs: list[tuple[Path, Path]] = []
    for entry in sorted(base.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
        if entry.name.startswith(".") or entry.name.startswith("_"):
            continue
        rel = str(entry.relative_to(user_root(username))).replace("\\", "/")
        is_dir = entry.is_dir()
        size = 0 if is_dir else entry.stat().st_size
        added = meta.get(rel, datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc).isoformat())
        ext = entry.suffix.lower() if not is_dir else ""
        file_type = "folder" if is_dir else _detect_type(ext)
        item = {
            "name": entry.name,
            "path": rel,
            "is_dir": is_dir,
            "size": size,
            "type": file_type,
            "added": added,
            "url": upload_url(username, rel) if not is_dir else "",
        }
        if file_type == "image":
            thumb = _thumb_for(username, rel, entry, thumb_jobs)
            if thumb:
                item["thumb"] = thumb
        items.append(item)
    if thumb_jobs:
        # Миниатюры делаются после ответа: список не ждёт ffmpeg, а при
        # следующем открытии папки они уже будут.
        task = asyncio.create_task(_make_thumbs_later(thumb_jobs))
        _thumb_tasks.add(task)
        task.add_done_callback(_thumb_tasks.discard)
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
            "url": upload_url(username, rel),
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
    if ext in (".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".opus", ".oga", ".weba"):
        return "audio"
    if ext in (".pdf",):
        return "pdf"
    if ext in (".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".csv", ".md"):
        return "document"
    return "file"
