"""Harvest theses from an institutional repository over OAI-PMH (AUC Knowledge Fountain runs Digital Commons).

`agentkit harvest-theses <base-url> --out data/theses [--set publication:etds] [--fulltext]` writes, per record,
a Markdown page (title, abstract, metadata) plus a `.meta.json` sidecar carrying `biblio` (type, author, advisor,
degree, department, year, repository, url), and with --fulltext the open-access PDF. `agentkit ingest` then
indexes them; search filters by department, advisor and year (`/api/search?department=…`).

Rules: https only and the host must be on the ingestion allowlist; deleted records are removed; embargoed or
restricted records (rights text, or an availability date in the future) are skipped; resumable through
resumption tokens and an incremental `from` date kept in `<out>/state.json`.
Field mapping follows Dublin Core; Digital Commons puts the department in dc:publisher or dc:source [VERIFY]
— override with AGENTKIT_OAI_DEPT_FIELD.
"""
import datetime
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

from .security import source_allowed

NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "dc": "http://purl.org/dc/elements/1.1/",
      "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/"}
RESTRICTED = re.compile(r"embargo|restricted|not available|auc (community|users) only|campus only|"
                        r"\bclosed access\b|محظور|مقيد", re.I)
MAX_PDF_MB = int(os.getenv("AGENTKIT_MAX_FILE_MB", "30"))


class HarvestError(Exception):
    pass


def _get(url: str, fetch=None) -> bytes:
    if not url.startswith("https://") or not source_allowed(url):
        raise HarvestError(f"not an allowed https source: {url}")
    if fetch:
        return fetch(url)
    req = urllib.request.Request(url, headers={"User-Agent": "agentkit-harvester/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:  # nosec B310 — https + allowlist checked above
        data = r.read(MAX_PDF_MB * 1024 * 1024 + 1)
    if len(data) > MAX_PDF_MB * 1024 * 1024:
        raise HarvestError(f"response larger than {MAX_PDF_MB} MB: {url}")
    return data


def _parse(xml: bytes):
    import xml.etree.ElementTree as ET  # nosec B405 — DTD/entity declarations rejected before parsing
    text = xml.decode("utf-8", "replace")
    if "<!ENTITY" in text or "<!DOCTYPE" in text:
        raise HarvestError("OAI response contains a DTD/entities; refusing to parse")
    return ET.fromstring(text)  # nosec B314 — DTD/entities rejected above


def record_meta(rec) -> dict | None:
    """Dublin Core record → biblio dict, or None for deleted records."""
    header = rec.find("oai:header", NS)
    if header is not None and header.get("status") == "deleted":
        return {"deleted": True, "id": header.findtext("oai:identifier", "", NS)}
    dc = rec.find(".//oai_dc:dc", NS)
    if dc is None:
        return None

    def all_(tag):
        return [e.text.strip() for e in dc.findall(f"dc:{tag}", NS) if e.text and e.text.strip()]
    types = " ".join(all_("type")).lower()
    dates = all_("date")
    year = next((m.group(0) for d in dates for m in [re.search(r"\d{4}", d)] if m), "")
    urls = [u for u in all_("identifier") if u.startswith("http")]
    pdf = next((u for u in urls if u.lower().endswith(".pdf") or "viewcontent.cgi" in u), "")
    page = next((u for u in urls if u != pdf), pdf)
    dept_field = os.getenv("AGENTKIT_OAI_DEPT_FIELD", "publisher")
    dept = next((d for d in all_(dept_field) if "american university in cairo" not in d.lower()), "")
    degree = next((t for t in all_("type") if re.search(r"thesis|dissertation|master|doctor|phd", t, re.I)), "")
    return {
        "id": header.findtext("oai:identifier", "", NS) if header is not None else "",
        "type": "thesis" if re.search(r"thesis|dissertation", types) else (all_("type") or ["text"])[0].lower(),
        "title": (all_("title") or ["Untitled"])[0], "author": all_("creator"),
        "advisor": "; ".join(all_("contributor")), "degree": degree, "department": dept, "year": year,
        "abstract": "\n\n".join(all_("description")), "subjects": all_("subject"), "language": (all_("language") or [""])[0],
        "rights": " ".join(all_("rights")), "url": page, "pdf": pdf, "dates": dates,
        "repository": "AUC Knowledge Fountain",
    }


def restricted(meta: dict, today: str | None = None) -> bool:
    today = today or datetime.date.today().isoformat()
    future = any(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d[:10]) and d[:10] > today for d in meta.get("dates", []))
    return bool(RESTRICTED.search(meta.get("rights", ""))) or future


def _slug(oai_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", oai_id.split(":")[-1]).strip("-")[:80] or "record"


def write_record(meta: dict, out: Path, pdf_bytes: bytes | None = None) -> list[Path]:
    slug = _slug(meta["id"])
    biblio = {k: meta[k] for k in ("type", "title", "author", "advisor", "degree", "department", "year",
                                   "repository", "url", "language") if meta.get(k)}
    side = {"title": meta["title"], "url": meta["url"], "updated": meta["year"], "access": "public", "biblio": biblio}
    lines = [f"# {meta['title']}", "", "## Abstract", "", meta["abstract"] or "No abstract provided.", "",
             "## Record", "", f"Author: {'; '.join(meta['author']) or 'unknown'}."]
    lines += [f"{label}: {meta[k]}." for k, label in (("degree", "Degree"), ("department", "Department"),
                                                      ("advisor", "Advisor"), ("year", "Year")) if meta.get(k)]
    if meta.get("subjects"):
        lines.append("Subjects: " + "; ".join(meta["subjects"]) + ".")
    written = []
    md = out / f"{slug}.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / f"{slug}.md.meta.json").write_text(json.dumps(side, ensure_ascii=False), encoding="utf-8")
    written.append(md)
    if pdf_bytes:
        pdf = out / f"{slug}.pdf"
        pdf.write_bytes(pdf_bytes)
        (out / f"{slug}.pdf.meta.json").write_text(json.dumps(side, ensure_ascii=False), encoding="utf-8")
        written.append(pdf)
    return written


def harvest(base_url: str, out: str | Path, set_spec: str = "", fulltext: bool = False, prefix: str = "oai_dc",
            fetch=None, limit: int | None = None) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    state_file = out / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    params = {"verb": "ListRecords"}
    if state.get("token"):
        params["resumptionToken"] = state["token"]
    else:
        params["metadataPrefix"] = prefix
        if set_spec:
            params["set"] = set_spec
        if state.get("from"):
            params["from"] = state["from"]
    started = datetime.date.today().isoformat()
    report = {"written": 0, "skipped_restricted": 0, "deleted": 0, "not_thesis": 0, "pages": 0, "errors": []}
    while True:
        root = _parse(_get(f"{base_url}?{urllib.parse.urlencode(params)}", fetch))
        err = root.find("oai:error", NS)
        if err is not None:
            if err.get("code") == "noRecordsMatch":
                break
            raise HarvestError(f"OAI error {err.get('code')}: {err.text}")
        report["pages"] += 1
        for rec in root.iter(f"{{{NS['oai']}}}record"):
            meta = record_meta(rec)
            if not meta:
                continue
            if meta.get("deleted"):
                for f in out.glob(_slug(meta["id"]) + ".*"):
                    f.unlink()
                report["deleted"] += 1
                continue
            if meta["type"] != "thesis":
                report["not_thesis"] += 1
                continue
            if restricted(meta):
                report["skipped_restricted"] += 1
                continue
            pdf = None
            if fulltext and meta["pdf"]:
                try:
                    pdf = _get(meta["pdf"], fetch)
                    if not pdf.startswith(b"%PDF"):
                        pdf = None
                except HarvestError as e:
                    report["errors"].append(str(e))
            write_record(meta, out, pdf)
            report["written"] += 1
            if limit and report["written"] >= limit:
                break
        token = root.find(".//oai:resumptionToken", NS)
        token = token.text.strip() if token is not None and token.text else ""
        state = {"from": state.get("from", ""), "token": token}
        state_file.write_text(json.dumps(state), encoding="utf-8")  # resumable after an interruption
        if not token or (limit and report["written"] >= limit):
            break
        params = {"verb": "ListRecords", "resumptionToken": token}
    if not state.get("token"):
        state_file.write_text(json.dumps({"from": started, "token": ""}), encoding="utf-8")
    return report
