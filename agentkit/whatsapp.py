"""WhatsApp channel (Meta WhatsApp Cloud API webhook).

  GET  /whatsapp  — verification handshake (hub.verify_token == AGENTKIT_WA_VERIFY_TOKEN)
  POST /whatsapp  — incoming messages; X-Hub-Signature-256 is verified with AGENTKIT_WA_APP_SECRET
Replies go out through the Graph API with AGENTKIT_WA_TOKEN and AGENTKIT_WA_PHONE_ID.
"""
import hashlib
import hmac
import json
import os
import urllib.request

MAX_TEXT = 4000  # WhatsApp text messages are limited to 4096 characters
VOICE_REPLY = ("I can't listen to voice notes yet. Please type your question in Arabic or English. "
               "لا أستطيع سماع الرسائل الصوتية بعد، من فضلك اكتب سؤالك.")


def verify_signature(body: bytes, header: str, secret: str) -> bool:
    if not secret or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[7:])


def parse_messages(payload: dict) -> list[dict]:
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for m in change.get("value", {}).get("messages", []):
                out.append({"from": m.get("from", ""), "type": m.get("type", ""),
                            "text": (m.get("text") or {}).get("body", "")})
    return out


def format_reply(answer) -> str:
    text = answer.text
    if answer.sources:
        text += "\n\n" + "\n".join(f"[{n}] {c.title}: {c.source}" for n, c in answer.sources)
    return text[:MAX_TEXT]


class WhatsAppSender:
    def __init__(self, token: str = "", phone_id: str = "", version: str = "", post=None):
        self.token = token or os.getenv("AGENTKIT_WA_TOKEN", "")
        self.phone_id = phone_id or os.getenv("AGENTKIT_WA_PHONE_ID", "")
        self.version = version or os.getenv("AGENTKIT_WA_API_VERSION", "v21.0")
        self.post = post or self._post

    def _post(self, url: str, body: dict, headers: dict):
        if not url.startswith("https://graph.facebook.com/"):
            raise ValueError("unexpected WhatsApp API URL")
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:  # nosec B310 — fixed https host checked above
            return r.status

    def send(self, to: str, text: str):
        url = f"https://graph.facebook.com/{self.version}/{self.phone_id}/messages"
        body = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
        return self.post(url, body, {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})


def handle(payload: dict, chat, sender: WhatsAppSender, limiter=None) -> int:
    """Answer every incoming message; returns how many replies were sent."""
    sent = 0
    for m in parse_messages(payload):
        if limiter and not limiter.allow(f"wa:{m['from']}")[0]:
            continue
        if m["type"] == "text" and m["text"].strip():
            reply = format_reply(chat.ask(m["text"], user=f"wa:{m['from']}"))
        elif m["type"] in ("audio", "voice"):
            reply = VOICE_REPLY
        else:
            continue
        sender.send(m["from"], reply)
        sent += 1
    return sent
