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
