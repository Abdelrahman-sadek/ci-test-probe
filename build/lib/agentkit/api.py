"""Production HTTP service (FastAPI). Run: `agentkit serve` (uvicorn, several workers optional).

Public:  GET /  /embed  /widget.js  /static/*  /healthz  /api/agents   POST /api/ask  /api/ask/stream (SSE)
Admin:   GET /admin  /admin/api/jobs/{id}  /admin/api/review  /admin/api/stats  /metrics
         POST /admin/api/upload  /admin/api/approve
Channel: GET|POST /whatsapp

Security: auth (JWT / SSO proxy / admin key) → per-user access levels; token-bucket rate limits; body-size cap;
strict CSP and security headers; CORS and frame-ancestors only for configured embed origins.
"""
import base64
import json
import os
import re
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from .agents import load_all
from .chat import LibraryChat
from .metrics import METRICS
from .rag import approve
from .security import AuthError, Principal, RateLimiter, authenticate
from .whatsapp import WhatsAppSender, handle, verify_signature

STATIC = Path(__file__).parent / "static"
MAX_BODY = int(os.getenv("AGENTKIT_MAX_BODY", "16384"))
MAX_UPLOAD_MB = float(os.getenv("AGENTKIT_MAX_UPLOAD_MB", "20"))
UPLOAD_EXT = {".md", ".txt", ".html", ".htm", ".pdf", ".png", ".jpg", ".jpeg"}


class Turn(BaseModel):
    role: str = Field(max_length=20)
    content: str = Field(max_length=2000)


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[Turn] = Field(default_factory=list, max_length=12)


class UploadIn(BaseModel):
    filename: str = Field(max_length=200)
    content_b64: str
    title: str = Field(default="", max_length=200)
    url: str = Field(default="", max_length=500)
    access: str = Field(default="public", pattern=r"^[a-z-]{1,30}$")


class ApproveIn(BaseModel):
    origin: str | None = None


def _origins() -> list[str]:
    return [o.strip() for o in os.getenv("AGENTKIT_EMBED_ORIGINS", "").split(",") if o.strip()]


def create_app(chat: LibraryChat, jobs=None, uploads_dir: str | Path = "data/uploads") -> FastAPI:
    app = FastAPI(title="AUC Library Assistant", docs_url=None, redoc_url=None, openapi_url=None)
    limiter = RateLimiter(os.getenv("AGENTKIT_RATE", "30/min"))
    wa_sender = WhatsAppSender()
    uploads = Path(uploads_dir)
    if _origins():
        app.add_middleware(CORSMiddleware, allow_origins=_origins(), allow_methods=["GET", "POST"],
                           allow_headers=["Content-Type", "Authorization"])

    @app.middleware("http")
    async def guard_and_headers(request: Request, call_next):
        size = int(request.headers.get("content-length") or 0)
        limit = MAX_UPLOAD_MB * 1.4 * 1024 * 1024 if request.url.path == "/admin/api/upload" else MAX_BODY
        if size > limit:  # OWASP LLM10: refuse oversized bodies before reading them
            return JSONResponse({"error": "request too large"}, status_code=413)
        response = await call_next(request)
        ancestors = " ".join(["'self'", *_origins()]) if request.url.path == "/embed" else "'none'"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
            f"base-uri 'none'; form-action 'self'; frame-ancestors {ancestors}")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=()"
        return response

    def principal(request: Request) -> Principal:
        try:
            return authenticate(dict(request.headers))
        except AuthError as e:
            raise HTTPException(401, str(e)) from e

    def rate_limited(request: Request, who: Principal = Depends(principal)) -> Principal:
        ok, retry = limiter.allow(who.user or (request.client.host if request.client else "unknown"))
        if not ok:
            METRICS.inc("agentkit_rate_limited_total")
            raise HTTPException(429, "too many requests", headers={"Retry-After": str(int(retry) + 1)})
        return who

    def admin(who: Principal = Depends(principal)) -> Principal:
        if not who.admin:
            raise HTTPException(403, "admin only")
        return who

    # ---------------------------------------------------------------- pages and static assets
    @app.get("/", response_class=HTMLResponse)
    @app.get("/embed", response_class=HTMLResponse)
    def page():
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page():  # the page is static; every admin API call is authorised separately
        return FileResponse(STATIC / "admin.html", media_type="text/html")

    @app.get("/widget.js")
    def widget():
        return FileResponse(STATIC / "widget.js", media_type="text/javascript")

    @app.get("/static/{name}")
    def static(name: str):
        if not re.fullmatch(r"[a-z0-9-]+\.(js|css|svg)", name) or not (STATIC / name).exists():
            raise HTTPException(404)
        kinds = {"js": "text/javascript", "css": "text/css", "svg": "image/svg+xml"}
        return FileResponse(STATIC / name, media_type=kinds[name.rsplit(".", 1)[1]])

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "chunks": chat.index.size, "index_version": chat.index.version}

    # ---------------------------------------------------------------- chat API
    @app.get("/api/agents")
    def agents():
        return [{"name": a.name, "division": a.division, "description": a.description} for a in load_all().values()]

    @app.post("/api/ask")
    def ask(body: AskIn, who: Principal = Depends(rate_limited)):
        hist = [t.model_dump() for t in body.history]
        return chat.ask(body.question, hist, access=who.access, user=who.user).to_dict()

    @app.post("/api/ask/stream")
    def ask_stream(body: AskIn, who: Principal = Depends(rate_limited)):
        hist = [t.model_dump() for t in body.history]

        def events():
            for kind, data in chat.ask_stream(body.question, hist, access=who.access, user=who.user):
                if kind == "delta":
                    yield f"data: {json.dumps({'delta': data}, ensure_ascii=False)}\n\n"
                else:
                    yield f"event: done\ndata: {json.dumps(data.to_dict(), ensure_ascii=False)}\n\n"
        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    # ---------------------------------------------------------------- admin API
    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics(request: Request):
        if os.getenv("AGENTKIT_METRICS_PUBLIC") != "1":
            admin(principal(request))
        return METRICS.render()

    @app.get("/admin/api/stats")
    def stats(_: Principal = Depends(admin)):
        cache = chat.cache
        return {"chunks": chat.index.size, "index_version": chat.index.version,
                "cache_hit_rate": round(cache.hit_rate, 3) if cache else None,
                "requests": METRICS.value("agentkit_requests_total"),
                "handoffs": METRICS.value("agentkit_requests_total", mode="handoff"),
                "llm_cost_usd": round(METRICS.value("agentkit_llm_cost_usd_total"), 4),
                "pending_review": [o for o, e in chat.index.manifest().items() if e.get("status") == "pending"]}

    @app.post("/admin/api/upload")
    def upload(body: UploadIn, _: Principal = Depends(admin)):
        if jobs is None:
            raise HTTPException(503, "ingestion jobs are not enabled")
        name = re.sub(r"[^A-Za-z0-9._-]", "-", Path(body.filename).name).strip(".-")[:120] or "upload"
        ext = Path(name).suffix.lower()
        if ext not in UPLOAD_EXT:
            raise HTTPException(415, f"unsupported file type {ext}")
        try:
            data = base64.b64decode(body.content_b64, validate=True)
        except ValueError as e:
            raise HTTPException(400, "content_b64 is not valid base64") from e
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, "file too large")
        uploads.mkdir(parents=True, exist_ok=True)
        dest = uploads / name
        dest.write_bytes(data)
        meta = {k: v for k, v in {"title": body.title, "url": body.url, "access": body.access}.items() if v}
        (uploads / (name + ".meta.json")).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return {"job": jobs.submit([str(dest)], review=False), "file": name}

    @app.get("/admin/api/jobs/{jid}")
    def job(jid: str, _: Principal = Depends(admin)):
        if jobs is None or not (j := jobs.get(jid)):
            raise HTTPException(404)
        return j

    @app.get("/admin/api/review")
    def review(_: Principal = Depends(admin)):
        return {o: e for o, e in chat.index.manifest().items() if e.get("status") == "pending"}

    @app.post("/admin/api/approve")
    def approve_sources(body: ApproveIn, _: Principal = Depends(admin)):
        done = approve(chat.index, body.origin)
        if jobs is not None:
            chat.index.save(jobs.save_path)
        return {"approved": done}

    # ---------------------------------------------------------------- WhatsApp channel
    @app.get("/whatsapp", response_class=PlainTextResponse)
    def wa_verify(request: Request):
        q = request.query_params
        token = os.getenv("AGENTKIT_WA_VERIFY_TOKEN", "")
        if token and q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == token:
            return q.get("hub.challenge", "")
        raise HTTPException(403)

    @app.post("/whatsapp")
    async def wa_incoming(request: Request, background: BackgroundTasks):
        raw = await request.body()
        if not verify_signature(raw, request.headers.get("x-hub-signature-256", ""),
                                os.getenv("AGENTKIT_WA_APP_SECRET", "")):
            raise HTTPException(401, "bad signature")
        background.add_task(handle, json.loads(raw or b"{}"), chat, wa_sender, limiter)
        return {"ok": True}  # acknowledge fast; reply asynchronously

    return app


def serve(chat: LibraryChat, host: str = "127.0.0.1", port: int = 8000, jobs=None):
    import uvicorn
    print(f"AUC Library Assistant on http://{host}:{port}  (admin: /admin)")
    uvicorn.run(create_app(chat, jobs), host=host, port=port, log_level="warning",
                proxy_headers=os.getenv("AGENTKIT_TRUST_PROXY") == "1")
