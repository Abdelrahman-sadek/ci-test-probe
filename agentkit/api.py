"""Production HTTP service (FastAPI). Run: `agentkit serve` (uvicorn, several workers optional).

Public:  GET /  /embed  /widget.js  /static/*  /healthz  /api/agents  /api/search   POST /api/ask  /api/ask/stream (SSE)
Admin:   GET /admin  /admin/api/jobs/{id}  /admin/api/review  /admin/api/stats  /metrics
         POST /admin/api/upload  /admin/api/approve
Channel: GET|POST /whatsapp

Security: auth (JWT / SSO proxy / admin key) → per-user access levels; token-bucket rate limits; body-size cap;
strict CSP and security headers; CORS and frame-ancestors only for configured embed origins.
"""
import base64
import json
import logging
import os
import re
import time
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from .agents import load_all
from .chat import LibraryChat
from .metrics import METRICS

log = logging.getLogger("agentkit.api")
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


class AskConvIn(AskIn):
    conversation_id: str | None = Field(default=None, max_length=40)


class FeedbackIn(BaseModel):
    answer_id: str = Field(max_length=40)
    rating: int = Field(ge=-1, le=1)
    reason: str = Field(default="", max_length=500)
    question: str = Field(default="", max_length=1000)
    mode: str = Field(default="", max_length=20)
    lang: str = Field(default="", max_length=10)


class SaveIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    answer: str = Field(default="", max_length=4000)


class HandoffIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    history: list[Turn] = Field(default_factory=list, max_length=12)
    email: str = Field(default="", max_length=200, pattern=r"^$|^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str = Field(default="", max_length=100)
    consent: bool = False


class RareRequestIn(BaseModel):
    request_type: str = Field(pattern=r"^(reading-room|reproduction)$")
    collection: str = Field(min_length=1, max_length=300)
    items: str = Field(default="", max_length=1000)
    visit_date: str = Field(default="", max_length=10, pattern=r"^$|^\d{4}-\d{2}-\d{2}$")
    purpose: str = Field(default="", max_length=1000)
    affiliation: str = Field(default="", pattern=r"^$|^(student|faculty|staff|alumni|external)$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    consent: bool


class CiteSource(BaseModel):
    title: str = Field(max_length=500)
    section: str = Field("", max_length=300)
    url: str = Field("", max_length=1000)
    updated: str = Field("", max_length=40)
    meta: dict = Field(default_factory=dict)


class CiteIn(BaseModel):
    sources: list[CiteSource] = Field(min_length=1, max_length=50)
    format: str = Field(pattern="^(bibtex|ris|enw|csl|apa|mla)$")


class MaintenanceIn(BaseModel):
    on: bool


class NoticeIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=2000)
    url: str = Field(default="", max_length=500)
    valid_from: str = Field(default="", pattern=r"^$|^\d{4}-\d{2}-\d{2}$")
    valid_to: str = Field(default="", pattern=r"^$|^\d{4}-\d{2}-\d{2}$")
    priority: str = Field(default="urgent", pattern=r"^(urgent|normal)$")
    lang: str = Field(default="en", pattern=r"^(en|ar)$")


class CorrectionIn(BaseModel):
    origin: str = Field(max_length=500)
    page: int = Field(ge=1, le=10000)
    text: str = Field(min_length=1, max_length=50000)


class StatusIn(BaseModel):
    status: str = Field(pattern=r"^(queued|sent|in-progress|answered|closed|escalated)$")


class RenewIn(BaseModel):
    loan_id: str = Field(max_length=60)


RIGHTS_NOTICE = ("Permission to reproduce or publish images from special collections is separate from access "
                 "and may require written approval and fees; staff will confirm rights for each item.")


def _origins() -> list[str]:
    return [o.strip() for o in os.getenv("AGENTKIT_EMBED_ORIGINS", "").split(",") if o.strip()]


def create_app(chat: LibraryChat, jobs=None, uploads_dir: str | Path = "data/uploads", handoff=None) -> FastAPI:
    app = FastAPI(title="AUC Library Assistant", docs_url=None, redoc_url=None, openapi_url=None)
    appdb = chat.appdb
    if handoff is None and appdb is not None:
        from .services import Handoff
        handoff = Handoff(appdb, chat.libcal)
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

    def staff(who: Principal = Depends(principal)) -> Principal:
        if not (who.admin or who.staff):
            raise HTTPException(403, "library staff only")
        return who

    def audit(who: Principal, action: str, detail: str = ""):
        if appdb is not None:
            appdb.log_action(who.user, action, detail)

    # Pages and static assets
    @app.get("/", response_class=HTMLResponse)
    @app.get("/embed", response_class=HTMLResponse)
    def page():
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page():  # the page is static; every admin API call is authorised separately
        return FileResponse(STATIC / "admin.html", media_type="text/html")

    @app.get("/request", response_class=HTMLResponse)
    def request_page():
        return FileResponse(STATIC / "request.html", media_type="text/html")

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
        llm_state = chat.llm.status() if hasattr(chat.llm, "status") else ("ok" if chat.llm.live else "offline")
        return {"status": "ok", "chunks": chat.index.size, "index_version": chat.index.version, "llm": llm_state}

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy():
        return FileResponse(STATIC / "privacy.html", media_type="text/html")

    # Chat API
    @app.get("/api/agents")
    def agents():
        return [{"name": a.name, "division": a.division, "description": a.description} for a in load_all().values()]

    def need(store):
        if store is None:
            raise HTTPException(503, "this feature is not enabled")
        return store

    def signed_in(who: Principal):
        if not who.user:
            raise HTTPException(401, "sign in to use this feature")
        return who.user

    @app.get("/api/me")
    def me(who: Principal = Depends(principal)):
        return {"signed_in": bool(who.user), "admin": who.admin, "access": list(who.access),
                "features": {"history": appdb is not None, "account": chat.account is not None,
                             "handoff": handoff is not None}}

    @app.post("/api/ask")
    def ask(body: AskConvIn, who: Principal = Depends(rate_limited)):
        hist = [t.model_dump() for t in body.history]
        ans = chat.ask(body.question, hist, access=who.access, user=who.user).to_dict()
        if who.user and appdb is not None and ans["mode"] != "account":  # history only for signed-in users
            try:
                cid = appdb.add_turn(who.user, body.conversation_id, "user", body.question)
                appdb.add_turn(who.user, cid, "assistant", ans["answer"])
                ans["conversation_id"] = cid
            except PermissionError as e:
                raise HTTPException(403, str(e)) from e
        return ans

    @app.get("/api/search")
    def search(q: str = Query(min_length=1, max_length=300), k: int = Query(5, ge=1, le=20),
               who: Principal = Depends(rate_limited)):
        """Hybrid search without the model: ranked passages the caller may see. Cheap, and still works when
        the model is down or over budget."""
        from .arabic import detect_lang
        hits = chat.index.search(q, k, chat._expand(q, detect_lang(q)), who.access)
        return {"query": q, "results": [{"score": round(sc, 4), "title": c.title, "section": c.section,
                                         "url": c.source, "page": c.page, "updated": c.updated,
                                         "snippet": c.text.partition("\n")[2][:300]} for sc, c in hits]}

    @app.post("/api/cite")
    def cite(body: CiteIn, _: Principal = Depends(rate_limited)):
        """Export the cited sources as BibTeX, RIS, EndNote, CSL-JSON (downloads) or APA/MLA text."""
        from .citations import FORMATS, export
        meta_ok = {"type", "title", "author", "year", "publisher", "degree", "advisor", "department", "institution",
                   "repository", "isbn", "doi", "call_number", "url"}
        sources = [{**s.model_dump(), "meta": {k: str(v)[:500] for k, v in s.meta.items() if k in meta_ok}}
                   for s in body.sources]
        text = export(sources, body.format)
        media, ext = FORMATS[body.format]
        return PlainTextResponse(text, media_type=media + "; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="references.{ext}"'})

    @app.get("/api/me/data")
    def export_my_data(who: Principal = Depends(principal)):
        """Data-subject access request: everything stored about the signed-in user, as JSON."""
        data = need(appdb).export_user(signed_in(who))
        audit(who, "data-export")
        return data

    @app.delete("/api/me/data")
    def delete_my_data(who: Principal = Depends(principal)):
        """Data-subject erasure request: saved chats, searches, feedback, tickets and log rows."""
        user = signed_in(who)
        removed = need(appdb).delete_user(user)
        if chat.log is not None:
            removed += chat.log.purge(user=user)
        appdb.log_action("", "data-erase", f"{removed} rows")  # no identity kept for an erasure
        return {"deleted_rows": removed}

    @app.post("/api/feedback")
    def feedback(body: FeedbackIn, who: Principal = Depends(rate_limited)):
        fid = need(appdb).add_feedback(body.answer_id, body.rating, body.reason, body.question, body.mode,
                                       body.lang, who.user)
        return {"ok": True, "id": fid}

    @app.get("/api/conversations")
    def conversations(who: Principal = Depends(principal)):
        return need(appdb).conversations(signed_in(who))

    @app.get("/api/conversations/{cid}")
    def conversation(cid: str, who: Principal = Depends(principal)):
        return need(appdb).conversation(signed_in(who), cid)

    @app.get("/api/saved")
    def saved(who: Principal = Depends(principal)):
        return need(appdb).saved(signed_in(who))

    @app.post("/api/saved")
    def save(body: SaveIn, who: Principal = Depends(rate_limited)):
        return {"id": need(appdb).save_search(signed_in(who), body.question, body.answer)}

    @app.delete("/api/saved/{sid}")
    def unsave(sid: str, who: Principal = Depends(principal)):
        return {"deleted": need(appdb).delete_saved(signed_in(who), sid)}

    @app.post("/api/handoff")
    def create_handoff(body: HandoffIn, who: Principal = Depends(rate_limited)):
        try:
            return need(handoff).create("question", body.question, [t.model_dump() for t in body.history],
                                        body.email, body.name, body.consent, user=who.user)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/api/requests/special-collections")
    def rare_request(body: RareRequestIn, who: Principal = Depends(rate_limited)):
        if not body.consent:
            raise HTTPException(422, "consent is required so staff can contact you")
        extra = body.model_dump(exclude={"name", "email", "consent"})
        res = need(handoff).create(f"rbscl-{body.request_type}", f"{body.request_type}: {body.collection}",
                                   [], body.email, body.name, True, extra={**extra, "rights_notice": RIGHTS_NOTICE},
                                   user=who.user)
        return {**res, "rights_notice": RIGHTS_NOTICE}

    @app.post("/api/account/renew")
    def renew(body: RenewIn, who: Principal = Depends(rate_limited)):
        if chat.account is None:
            raise HTTPException(503, "account connector not configured")
        try:
            return chat.account.renew(signed_in(who), body.loan_id)
        except PermissionError as e:
            raise HTTPException(403, str(e)) from e

    @app.get("/api/page-image")
    def page_image(origin: str, page: int, who: Principal = Depends(principal)):
        """PNG of a cited PDF page, so readers can check OCR-derived text against the scan. Only for indexed
        sources the caller may see — never an arbitrary path."""
        import pymupdf
        if origin not in chat.index.manifest() or not origin.lower().endswith(".pdf") or not Path(origin).exists():
            raise HTTPException(404)
        if not any(c.page == page and c.access in who.access and not c.blocked for c in _chunks_of(origin)):
            raise HTTPException(404)  # same visibility rules as search (OWASP LLM08)
        with pymupdf.open(origin) as doc:
            if not 1 <= page <= doc.page_count:
                raise HTTPException(404)
            png = doc[page - 1].get_pixmap(dpi=110).tobytes("png")
        from fastapi.responses import Response
        return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})

    def _chunks_of(origin: str):
        idx = chat.index
        if hasattr(idx, "chunks"):
            return [c for c in idx.chunks if c.origin == origin]
        rows = idx.db.execute("SELECT rowid FROM chunks WHERE origin=?", (origin,)).fetchall()
        return idx.get([r[0] for r in rows])

    @app.post("/api/ask/stream")
    def ask_stream(body: AskConvIn, who: Principal = Depends(rate_limited)):
        hist = [t.model_dump() for t in body.history]

        def events():
            for kind, data in chat.ask_stream(body.question, hist, access=who.access, user=who.user):
                if kind == "delta":
                    yield f"data: {json.dumps({'delta': data}, ensure_ascii=False)}\n\n"
                elif kind == "status":
                    yield f"event: status\ndata: {json.dumps({'status': data})}\n\n"
                else:
                    out = data.to_dict()
                    if who.user and appdb is not None and out["mode"] != "account":
                        try:
                            cid = appdb.add_turn(who.user, body.conversation_id, "user", body.question)
                            appdb.add_turn(who.user, cid, "assistant", out["answer"])
                            out["conversation_id"] = cid
                        except PermissionError:
                            pass
                    yield f"event: done\ndata: {json.dumps(out, ensure_ascii=False)}\n\n"
        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    # Admin API
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
                "pending_review": [o for o, e in chat.index.manifest().items() if e.get("status") == "pending"],
                "degraded_answers": METRICS.value("agentkit_degraded_total"),
                "overdue_tickets": len(appdb.overdue_tickets()) if appdb else None,
                "workload": appdb.workload() if appdb else None}

    @app.get("/admin/api/freshness")
    def freshness_report(_: Principal = Depends(staff)):
        from .rag import freshness
        return freshness(chat.index, appdb)

    @app.post("/admin/api/upload")
    def upload(body: UploadIn, who: Principal = Depends(staff)):
        audit(who, "upload", body.filename)
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
    def job(jid: str, _: Principal = Depends(staff)):
        if jobs is None or not (j := jobs.get(jid)):
            raise HTTPException(404)
        return j

    @app.get("/admin/api/feedback")
    def feedback_report(_: Principal = Depends(staff)):
        return need(appdb).feedback_report()

    @app.get("/admin/api/eval-candidates")
    def eval_candidates(_: Principal = Depends(staff)):
        return need(appdb).eval_candidates()

    @app.get("/admin/api/notices")
    def list_notices(_: Principal = Depends(staff)):
        return need(appdb).notices(include_expired=True)

    @app.get("/admin/api/role")
    def my_role(who: Principal = Depends(principal)):
        from .dashboard import TABS, allowed
        return {"role": who.role, "tabs": [t for t in TABS if allowed(who.role, t)]}

    @app.get("/admin/api/dashboard/{name}")
    def dashboard_tab(name: str, days: int = Query(30, ge=1, le=365), mode: str = "", agent: str = "",
                      lang: str = "", who: Principal = Depends(principal)):
        from .dashboard import TABS, allowed, tab
        if name not in TABS:
            raise HTTPException(404, "unknown tab")
        if not allowed(who.role, name):
            raise HTTPException(403, "your role cannot open this tab")
        need(appdb)
        if name == "conversations":
            audit(who, "view-conversations", f"{days}d {mode} {agent} {lang}".strip())
        now = time.time()
        return tab(chat, name, now - days * 86400, now, mode=mode, agent=agent, lang=lang,
                   index_path=getattr(jobs, "save_path", ""))

    @app.post("/admin/api/gaps/{cid}/ticket")
    def gap_ticket(cid: str, who: Principal = Depends(staff)):
        from .arabic import detect_lang
        from .gaps import detect
        c = next((c for c in detect(need(appdb), chat.index, expand=lambda q: chat._expand(q, detect_lang(q)))
                  ["clusters"] if c["id"] == cid), None)
        if not c:
            raise HTTPException(404, "unknown gap")
        audit(who, "gap-ticket", c["label"])
        return {"ticket": appdb.add_ticket("content-gap", {"question": c["label"], "examples": c["examples"],
                                                           "gap_kind": c["kind"], "nearest": c["nearest"]}, c["owner"])}

    @app.post("/admin/api/gaps/{cid}/candidate")
    def gap_candidate(cid: str, who: Principal = Depends(staff)):
        from .arabic import detect_lang
        from .gaps import detect
        c = next((c for c in detect(need(appdb), chat.index, expand=lambda q: chat._expand(q, detect_lang(q)))
                  ["clusters"] if c["id"] == cid), None)
        if not c:
            raise HTTPException(404, "unknown gap")
        path = Path(os.getenv("AGENTKIT_EVAL_CANDIDATES", "data/eval-candidates.md"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for q in c["examples"]:
                f.write(f"| gap-{cid} | {c['languages'] and max(c['languages'], key=c['languages'].get)} | "
                        f"{q.replace('|', '/')} | | answer | | |\n")
        audit(who, "gap-candidate", c["label"])
        return {"appended": len(c["examples"]), "file": str(path)}

    @app.get("/admin/api/maintenance")
    def get_maintenance(_: Principal = Depends(admin)):
        return {"maintenance": chat.maintenance}

    @app.post("/admin/api/maintenance")
    def set_maintenance(body: MaintenanceIn, who: Principal = Depends(admin)):
        audit(who, "maintenance", "on" if body.on else "off")
        """Kill switch: every question gets a librarian handoff instead of a generated answer."""
        chat.maintenance = body.on
        log.warning("maintenance mode %s by admin", "on" if chat.maintenance else "off")
        return {"maintenance": chat.maintenance}

    @app.post("/admin/api/notices")
    def add_notice(body: NoticeIn, who: Principal = Depends(staff)):
        audit(who, "notice-add", body.title)
        return {"id": need(appdb).add_notice(**body.model_dump())}

    @app.delete("/admin/api/notices/{nid}")
    def delete_notice(nid: str, who: Principal = Depends(staff)):
        audit(who, "notice-delete", nid)
        return {"deleted": need(appdb).delete_notice(nid)}

    @app.get("/admin/api/tickets")
    def tickets(kind: str | None = None, _: Principal = Depends(staff)):
        return need(appdb).tickets(kind)

    @app.post("/admin/api/tickets/{tid}")
    def ticket_status(tid: str, body: StatusIn, who: Principal = Depends(staff)):
        audit(who, "ticket-status", f"{tid} → {body.status}")
        return {"updated": need(appdb).set_ticket_status(tid, body.status)}

    @app.get("/admin/api/corrections")
    def corrections(_: Principal = Depends(staff)):
        out = []
        for origin, entry in chat.index.manifest().items():
            for low in entry.get("low_confidence_pages", []):
                text = "\n".join(c.text.partition("\n")[2] for c in _chunks_of(origin) if c.page == low["page"])
                out.append({"origin": origin, "page": low["page"], "confidence": low["confidence"], "text": text})
        return out

    @app.post("/admin/api/corrections")
    def correct(body: CorrectionIn, who: Principal = Depends(staff)):
        audit(who, "ocr-correction", f"{body.origin} p{body.page}")
        if body.origin not in chat.index.manifest() or jobs is None:
            raise HTTPException(404, "unknown source or jobs disabled")
        side = Path(body.origin + ".corrections.json")
        fixes = json.loads(side.read_text(encoding="utf-8")) if side.exists() else {}
        fixes[str(body.page)] = body.text
        side.write_text(json.dumps(fixes, ensure_ascii=False), encoding="utf-8")
        return {"job": jobs.submit([body.origin], review=False, force=True)}

    @app.get("/admin/api/review")
    def review(_: Principal = Depends(staff)):
        return {o: e for o, e in chat.index.manifest().items() if e.get("status") == "pending"}

    @app.post("/admin/api/approve")
    def approve_sources(body: ApproveIn, who: Principal = Depends(staff)):
        done = approve(chat.index, body.origin)
        audit(who, "approve", ", ".join(done))
        if jobs is not None:
            chat.index.save(jobs.save_path)
        return {"approved": done}

    # WhatsApp channel
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
