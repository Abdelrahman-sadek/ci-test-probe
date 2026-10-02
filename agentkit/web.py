"""Minimal web chat (stdlib only): GET / (page), GET /app.js, GET /api/agents, POST /api/ask {question, history}.

Safe defaults: binds to localhost, caps request bodies (OWASP LLM10), renders model output with textContent
and only http(s) links (LLM05 improper output handling), and sends a strict Content-Security-Policy.
"""
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .chat import LibraryChat

MAX_BODY = 16_384
CSP = "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'"

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>AUC Library Assistant</title>
<style>
:root{--bg:#f7f7f5;--fg:#1d1d1f;--muted:#6b6b70;--card:#fff;--line:#e3e3e0;--accent:#7a1f2b}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#ececf0;--muted:#a0a0a8;--card:#1d1d21;--line:#2e2e34;--accent:#e07a86}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,"Segoe UI",Tahoma,sans-serif}
main{max-width:760px;margin:0 auto;padding:16px}h1{font-size:1.25rem;margin:8px 0 2px}
.note{color:var(--muted);font-size:.85rem;margin:0 0 12px}#log{display:flex;flex-direction:column;gap:10px}
.msg{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 14px;white-space:pre-wrap}
.me{align-self:flex-end;max-width:85%}.chip{font-size:.75rem;color:var(--accent);margin-bottom:4px}
.src{font-size:.85rem;margin-top:8px;border-top:1px solid var(--line);padding-top:6px}.src a{color:var(--accent)}
details{font-size:.8rem;color:var(--muted);margin-top:6px}
form{display:flex;gap:8px;margin-top:14px;position:sticky;bottom:8px}
input{flex:1;padding:12px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--fg);font-size:16px}
button{padding:0 18px;border:0;border-radius:10px;background:var(--accent);color:#fff;font-size:16px}
</style></head><body><main>
<h1>AUC Library Assistant</h1>
<p class="note">Independent demo, not affiliated with AUC. Answers come only from cited sources. Ask in English,
العربية, or Franco-Arabic.</p>
<div id="log"></div>
<form id="f"><input id="q" dir="auto" autocomplete="off" maxlength="1000" placeholder="Ask about borrowing, rare books, theses…" required>
<button>Ask</button></form></main><script src="/app.js"></script></body></html>"""

APP_JS = """const log=document.getElementById('log'),f=document.getElementById('f'),q=document.getElementById('q');let history=[];
function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;e.dir='auto';return e}
function safeUrl(u){try{const x=new URL(u);return['http:','https:'].includes(x.protocol)?x.href:null}catch(e){return null}}
f.addEventListener('submit',async ev=>{ev.preventDefault();const question=q.value.trim();if(!question)return;q.value='';
log.append(el('div','msg me',question));const box=el('div','msg','…');log.append(box);box.scrollIntoView();
try{const r=await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question,history:history.slice(-6)})});
const d=await r.json();box.textContent='';if(!r.ok){box.textContent=d.error||'Error';return}
box.append(el('div','chip',d.agent+' · '+d.mode),el('div','',d.answer));
if(d.sources.length){const s=el('div','src');for(const x of d.sources){const row=el('div','');row.append('['+x.n+'] ');
const u=safeUrl(x.url);if(u){const a=el('a','',x.title+(x.section?' › '+x.section:''));a.href=u;a.target='_blank';a.rel='noopener noreferrer';row.append(a)}else row.append(x.title);s.append(row)}box.append(s)}
if(d.retrieved.length){const det=el('details');det.append(el('summary','','retrieval ('+d.retrieved.length+')'));
for(const h of d.retrieved)det.append(el('div','',h.score+'  '+h.title+(h.section?' › '+h.section:'')));box.append(det)}
history.push({role:'user',content:question},{role:'assistant',content:d.answer})}catch(e){box.textContent='Network error'}});"""


def make_handler(chat: LibraryChat):
    from .agents import load_all

    class Handler(BaseHTTPRequestHandler):
        server_version = "agentkit"

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self):  # noqa: N802
            if self.path == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif self.path == "/app.js":
                self._send(200, APP_JS.encode(), "text/javascript; charset=utf-8")
            elif self.path == "/api/agents":
                self._json(200, [{"name": a.name, "division": a.division, "description": a.description}
                                 for a in load_all().values()])
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path != "/api/ask":
                return self._json(404, {"error": "not found"})
            size = int(self.headers.get("Content-Length") or 0)
            if size > MAX_BODY:
                return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request too large"})
            try:
                data = json.loads(self.rfile.read(size) or b"{}")
                question = str(data.get("question", ""))[:2000]
                history = [{"role": str(h.get("role")), "content": str(h.get("content", ""))[:2000]}
                           for h in data.get("history", [])[-6:] if isinstance(h, dict)]
            except (ValueError, AttributeError):
                return self._json(400, {"error": "invalid JSON"})
            if not question.strip():
                return self._json(400, {"error": "question is required"})
            self._json(200, chat.ask(question, history).to_dict())

        def log_message(self, fmt, *args):  # keep questions (possible PII) out of stdout logs
            pass

    return Handler


def serve(chat: LibraryChat, host: str = "127.0.0.1", port: int = 8000):
    httpd = ThreadingHTTPServer((host, port), make_handler(chat))
    print(f"AUC Library Assistant on http://{host}:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
