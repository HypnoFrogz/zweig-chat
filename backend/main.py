"""Zweig Messenger — FastAPI entry point."""

import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import init_db, close_db
from fcm_sender import init_firebase

from routes.auth import router as auth_router
from routes.channels import router as channels_router
from routes.messages import router as messages_router
from routes.admin import router as admin_router
from routes.files import router as files_router
from routes.videocall import router as videocall_router
from routes import videocall
from routes import federation
from routes.preferences import router as preferences_router
from routes.devices import router as devices_router
from routes.chat_folders import router as chat_folders_router
from routes.tasks import router as tasks_router
from routes.push import router as push_router
from routes.federation import router as federation_router
from routes.moderation import router as moderation_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    init_firebase()
    if videocall.CALL_CLEANUP_ON_STARTUP:
        await videocall.run_calls_cleanup_once()
    cleanup_task = asyncio.create_task(videocall.run_calls_cleanup_loop())
    # Retries queued server-to-server messages (no-op without SERVER_DOMAIN).
    outbox_task = asyncio.create_task(federation.run_outbox_loop())
    yield
    for task in (cleanup_task, outbox_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await close_db()


app = FastAPI(title="Zweig Messenger", lifespan=lifespan)

# CORS — allows the web/PWA frontend to talk to this backend from a different
# origin (Mattermost-style "enter server address" flow). Auth uses Bearer
# tokens (not cookies), so wildcard origins are safe with allow_credentials=False.
# Override in production by setting CORS_ORIGINS to a comma-separated allowlist.
_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health_check():
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(channels_router)
app.include_router(messages_router)
app.include_router(admin_router)
app.include_router(files_router)
app.include_router(videocall_router)
app.include_router(preferences_router)
app.include_router(devices_router)
app.include_router(chat_folders_router)
app.include_router(tasks_router)
app.include_router(push_router)
app.include_router(federation_router)
app.include_router(moderation_router)
