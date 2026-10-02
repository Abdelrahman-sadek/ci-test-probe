"""Live connectors. Data that changes by the minute (availability, call numbers, hours) is fetched at question
time and never indexed. Each connector returns Chunks, so answers cite them like any other source."""
import hashlib
import json
import os
import urllib.parse
import urllib.request

from .arabic import detect_lang, tokenize
from .rag import Chunk


class PrimoCatalog:
    """Ex Libris Primo Search API: GET {api}/primo/v1/search?vid&tab&scope&q=any,contains,<query>&limit&apikey.

    Enabled only when AGENTKIT_PRIMO_URL, AGENTKIT_PRIMO_VID and AGENTKIT_PRIMO_KEY are set (AUC's discovery
    system is still [VERIFY]). PNX field names follow Primo's JSON; check them against a real response."""

    def __init__(self, api_url: str, vid: str, api_key: str, tab: str = "Everything",
                 scope: str = "MyInst_and_CI", discovery_url: str = "", fetch=None):
        self.api_url, self.vid, self.api_key = api_url.rstrip("/"), vid, api_key
        self.tab, self.scope = tab, scope
        self.discovery_url = (discovery_url or api_url).rstrip("/")
        self.fetch = fetch or self._get

    @classmethod
    def from_env(cls) -> "PrimoCatalog | None":
        url, vid, key = (os.getenv(f"AGENTKIT_PRIMO_{k}") for k in ("URL", "VID", "KEY"))
        if not (url and vid and key):
            return None
        return cls(url, vid, key, os.getenv("AGENTKIT_PRIMO_TAB", "Everything"),
                   os.getenv("AGENTKIT_PRIMO_SCOPE", "MyInst_and_CI"), os.getenv("AGENTKIT_PRIMO_DISCOVERY", ""))

    @staticmethod
    def _get(url: str) -> dict:
        if not url.startswith("https://"):
            raise ValueError("Primo API URL must use https")
        with urllib.request.urlopen(url, timeout=10) as r:  # nosec B310 — scheme checked above
            return json.load(r)

    def search(self, query: str, limit: int = 5) -> list[Chunk]:
        params = {"vid": self.vid, "tab": self.tab, "scope": self.scope, "q": f"any,contains,{query}",
                  "limit": limit, "apikey": self.api_key}
        data = self.fetch(f"{self.api_url}/primo/v1/search?{urllib.parse.urlencode(params)}")
        out = []
        for doc in data.get("docs", [])[:limit]:
            pnx = doc.get("pnx", {})
            disp = pnx.get("display", {})

            def first(key, d=disp):
                return (d.get(key) or [""])[0]

            best = doc.get("delivery", {}).get("bestlocation") or {}
            recid = first("recordid", pnx.get("control", {}))
            title = first("title") or "Untitled record"
            text = (f"{title} — {first('creator') or 'unknown author'} ({first('creationdate') or 'n.d.'}). "
                    f"Format: {first('type') or 'unknown'}. Availability: {best.get('availabilityStatus', 'unknown')}. "
                    f"Location: {best.get('mainLocation', '')} {best.get('subLocation', '')}".rstrip() +
                    f". Call number: {best.get('callNumber', 'n/a')}.")
            link = (f"{self.discovery_url}/discovery/fulldisplay?docid={urllib.parse.quote(recid)}&vid={self.vid}"
                    if recid else self.discovery_url)
            body = f"{title} › Catalog record\n{text}"
            out.append(Chunk(hashlib.sha1(body.encode(), usedforsecurity=False).hexdigest()[:12], body, title, "Catalog record", link, 1,
                             detect_lang(title), "live-catalog", tokens=tokenize(body)))
        return out


def _https_json(url: str, data: dict | None = None, headers: dict | None = None, method: str = "GET") -> dict:
    """Small JSON-over-HTTPS helper shared by the connectors (https only, 10 s timeout)."""
    if not url.startswith("https://"):
        raise ValueError("connector URLs must use https")
    body = None
    hdrs = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method if data is None else "POST")
    with urllib.request.urlopen(req, timeout=10) as r:  # nosec B310 — https enforced above
        return json.load(r)


class LibCal:
    """Springshare LibCal API 1.1 (live hours, study-room availability, consultation booking link).

    AGENTKIT_LIBCAL_URL=https://<site>.libcal.com, AGENTKIT_LIBCAL_CLIENT_ID/_SECRET, AGENTKIT_LIBCAL_LID
    (hours location id), AGENTKIT_LIBCAL_SPACE_LID (rooms), AGENTKIT_LIBCAL_BOOKING_URL (appointments page).
    Whether AUC uses LibCal is [VERIFY]; field names follow the public API docs — check a real response."""

    def __init__(self, base: str, client_id: str, secret: str, lid: str, space_lid: str = "",
                 booking_url: str = "", fetch=None):
        self.base, self.client_id, self.secret = base.rstrip("/"), client_id, secret
        self.lid, self.space_lid, self.booking_url = lid, space_lid, booking_url
        self.fetch = fetch or _https_json
        self._token = ("", 0.0)

    @classmethod
    def from_env(cls) -> "LibCal | None":
        e = {k: os.getenv(f"AGENTKIT_LIBCAL_{k}", "") for k in ("URL", "CLIENT_ID", "CLIENT_SECRET", "LID",
                                                                 "SPACE_LID", "BOOKING_URL")}
        if not (e["URL"] and e["CLIENT_ID"] and e["CLIENT_SECRET"] and e["LID"]):
            return None
        return cls(e["URL"], e["CLIENT_ID"], e["CLIENT_SECRET"], e["LID"], e["SPACE_LID"], e["BOOKING_URL"])

    def _auth(self) -> dict:
        import time
        token, exp = self._token
        if not token or time.time() > exp:
            r = self.fetch(f"{self.base}/1.1/oauth/token", {"client_id": self.client_id,
                                                            "client_secret": self.secret,
                                                            "grant_type": "client_credentials"})
            token, exp = r["access_token"], time.time() + int(r.get("expires_in", 3600)) - 60
            self._token = (token, exp)
        return {"Authorization": f"Bearer {token}"}

    def hours(self, day: str) -> dict:
        """{'name', 'status' ('open'|'closed'|…), 'hours': 'from–to, …'} for one ISO date."""
        data = self.fetch(f"{self.base}/1.1/hours/{self.lid}?from={day}&to={day}", None, self._auth())
        loc = (data or [{}])[0]
        d = loc.get("dates", {}).get(day, {})
        spans = ", ".join(f"{h.get('from', '')}–{h.get('to', '')}" for h in d.get("hours", []))
        return {"name": loc.get("name", "Library"), "status": d.get("status", "unknown"), "hours": spans}

    def is_open_now(self) -> bool | None:
        import datetime
        try:
            return self.hours(datetime.date.today().isoformat())["status"] == "open"
        except Exception:  # noqa: BLE001 — unknown, not closed
            return None

    def hours_chunk(self, day: str) -> Chunk:
        h = self.hours(day)
        if h["status"] == "open" and h["hours"]:
            closes = h["hours"].split("–")[-1]
            text = f"Today ({day}) the {h['name']} is open {h['hours']} and closes at {closes}."
        else:
            text = f"Today ({day}) the {h['name']} is {h['status']}."
        body = f"{h['name']} hours today › {day}\n{text} (Live from the library calendar.)"
        return Chunk(hashlib.sha1(body.encode(), usedforsecurity=False).hexdigest()[:12], body,
                     f"{h['name']} hours", day, self.base + "/hours", 1, "en", "live-hours", tokens=tokenize(body))

    def rooms_chunk(self, day: str) -> Chunk | None:
        if not self.space_lid:
            return None
        items = self.fetch(f"{self.base}/1.1/space/items/{self.space_lid}?availability={day}", None, self._auth())
        free = [f"{i.get('name', 'Room')} ({len(i.get('availability', []))} free slots)"
                for i in items or [] if i.get("availability")]
        text = (f"Study rooms available on {day}: {', '.join(free)}." if free
                else f"No study rooms are free on {day}.") + " Book online through the library's room booking page."
        body = f"Study room availability › {day}\n{text}"
        return Chunk(hashlib.sha1(body.encode(), usedforsecurity=False).hexdigest()[:12], body,
                     "Study room availability", day, self.base + "/reserve", 1, "en", "live-rooms",
                     tokens=tokenize(body))


class AlmaAccount:
    """Ex Libris Alma Users API — the signed-in user's own loans, requests (incl. resource sharing/ILL) and
    fees. Read-only unless AGENTKIT_ALMA_ALLOW_RENEW=1 (OWASP LLM06: no actions without explicit opt-in).
    AGENTKIT_ALMA_URL=https://api-eu.hosted.exlibrisgroup.com, AGENTKIT_ALMA_KEY (users read/write key).
    The user id is the SSO identity (email or primary id) — mapping is [VERIFY] with AUC."""

    def __init__(self, base: str, key: str, fetch=None):
        self.base, self.key = base.rstrip("/"), key
        self.fetch = fetch or _https_json

    @classmethod
    def from_env(cls) -> "AlmaAccount | None":
        url, key = os.getenv("AGENTKIT_ALMA_URL", ""), os.getenv("AGENTKIT_ALMA_KEY", "")
        return cls(url, key) if url and key else None

    def _get(self, user: str, what: str) -> dict:
        uid = urllib.parse.quote(user, safe="")
        return self.fetch(f"{self.base}/almaws/v1/users/{uid}/{what}?format=json&limit=100",
                          None, {"Authorization": f"apikey {self.key}"})

    def summary(self, user: str) -> dict:
        loans = self._get(user, "loans").get("item_loan", []) or []
        reqs = self._get(user, "requests").get("user_request", []) or []
        try:
            ill = self._get(user, "resource-sharing-requests").get("user_resource_sharing_request", []) or []
        except Exception:  # noqa: BLE001 — optional module
            ill = []
        fees = self._get(user, "fees")
        return {"loans": [{"id": x.get("loan_id"), "title": x.get("title", ""), "due": x.get("due_date", "")[:10],
                           "status": x.get("loan_status", "")} for x in loans],
                "requests": [{"title": x.get("title", ""), "status": x.get("request_status", ""),
                              "type": x.get("request_type", "")} for x in reqs],
                "ill": [{"title": x.get("title", ""), "status": (x.get("status") or {}).get("desc", "")} for x in ill],
                "fees_total": (fees.get("total_sum") or 0)}

    def renew(self, user: str, loan_id: str) -> dict:
        if os.getenv("AGENTKIT_ALMA_ALLOW_RENEW") != "1":
            raise PermissionError("renewals are disabled (read-only mode)")
        uid, lid = urllib.parse.quote(user, safe=""), urllib.parse.quote(loan_id, safe="")
        return self.fetch(f"{self.base}/almaws/v1/users/{uid}/loans/{lid}?op=renew&format=json", {},
                          {"Authorization": f"apikey {self.key}"})
