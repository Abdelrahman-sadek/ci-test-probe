"""Reference export for the sources an answer cites: BibTeX, RIS, EndNote (.enw), APA 7, MLA 9, CSL-JSON.

Only fields the source provides are written; nothing is invented. Library pages are cited as web pages by the
AUC Libraries; theses and catalog records use the metadata harvested with them (Chunk.meta).
"""
import datetime
import json
import re
import unicodedata

PUBLISHER = "The American University in Cairo Libraries"
INSTITUTION = "The American University in Cairo"
FORMATS = {"bibtex": ("application/x-bibtex", "bib"), "ris": ("application/x-research-info-systems", "ris"),
           "enw": ("application/x-endnote-refer", "enw"), "csl": ("application/vnd.citationstyles.csl+json", "json"),
           "apa": ("text/plain", "txt"), "mla": ("text/plain", "txt")}
MONTHS = ["Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."]


def item(src: dict, accessed: str | None = None) -> dict:
    """Normalise an answer source ({title, section, url, page, updated, meta}) into one reference item."""
    meta = src.get("meta") or {}
    kind = meta.get("type") or "webpage"
    kind = {"masters thesis": "thesis", "doctoral dissertation": "thesis", "dissertation": "thesis"}.get(kind, kind)
    authors = meta.get("author") or []
    if isinstance(authors, str):
        authors = [a.strip() for a in authors.split(";") if a.strip()]
    url = meta.get("url") or src.get("url", "")
    return {"type": kind, "title": (meta.get("title") or src.get("title", "")).strip(), "section": src.get("section", ""),
            "authors": authors, "year": str(meta.get("year") or (src.get("updated") or "")[:4] or ""),
            "date": src.get("updated", "") if kind == "webpage" else "", "url": url if url.startswith("http") else "",
            "publisher": meta.get("publisher") or (PUBLISHER if kind == "webpage" else ""),
            "degree": meta.get("degree", ""), "advisor": meta.get("advisor", ""), "department": meta.get("department", ""),
            "institution": meta.get("institution") or (INSTITUTION if kind == "thesis" else ""),
            "repository": meta.get("repository", ""), "isbn": meta.get("isbn", ""), "doi": meta.get("doi", ""),
            "call_number": meta.get("call_number", ""), "accessed": accessed or datetime.date.today().isoformat()}


def _split(name: str) -> tuple[str, str]:
    """('Last', 'First Middle') from 'Last, First Middle' or 'First Middle Last'."""
    if "," in name:
        last, first = name.split(",", 1)
        return last.strip(), first.strip()
    parts = name.split()
    return (parts[-1], " ".join(parts[:-1])) if len(parts) > 1 else (name, "")


def _initials(first: str) -> str:
    return " ".join(p[0] + "." for p in re.split(r"[\s-]+", first) if p)


def _date_mla(iso: str) -> str:
    try:
        d = datetime.date.fromisoformat(iso[:10])
        return f"{d.day} {MONTHS[d.month - 1]} {d.year}"
    except ValueError:
        return iso


def _date_apa(iso: str) -> str:
    try:
        d = datetime.date.fromisoformat(iso[:10])
        return f"{d.year}, {d.strftime('%B')} {d.day}"
    except ValueError:
        return iso[:4] or "n.d."


def _degree(it: dict) -> str:
    deg = it["degree"].lower()
    return "Doctoral dissertation" if "doct" in deg or "phd" in deg else "Master's thesis" if it["type"] == "thesis" else ""


def apa(it: dict) -> str:
    names = [f"{last}, {_initials(first)}".rstrip(", ") for last, first in map(_split, it["authors"])]
    who = (", ".join(names[:-1]) + ", & " + names[-1]) if len(names) > 1 else (names[0] if names else "")
    title = it["title"] + (f": {it['section']}" if it["type"] == "webpage" and it["section"] else "")
    if it["type"] == "thesis":
        out = f"{who} ({it['year'] or 'n.d.'}). {title} [{_degree(it)}, {it['institution']}]."
        if it["repository"]:
            out += f" {it['repository']}."
    elif it["type"] == "book":
        out = f"{who} ({it['year'] or 'n.d.'}). {title}." + (f" {it['publisher']}." if it["publisher"] else "")
    else:  # web page: the organisation is the author, so the site name is not repeated
        out = f"{who or it['publisher']}. ({_date_apa(it['date']) if it['date'] else 'n.d.'}). {title}."
    if it["doi"]:
        out += f" https://doi.org/{it['doi']}"
    elif it["url"]:
        out += f" {it['url']}"
    return re.sub(r"\s+", " ", out).strip()


def mla(it: dict) -> str:
    if it["authors"]:
        last, first = _split(it["authors"][0])
        who = f"{last}, {first}".rstrip(", ") + (", et al" if len(it["authors"]) > 2 else
                                                 f", and {it['authors'][1]}" if len(it["authors"]) == 2 else "") + ". "
    else:
        who = ""
    if it["type"] == "thesis":
        out = f"{who}{it['title']}. {it['year']}. {it['institution']}, {_degree(it).lower().capitalize()}."
        if it["repository"]:
            out += f" {it['repository']}"
        out += f", {it['url']}." if it["url"] else ""
    elif it["type"] == "book":
        out = f"{who}{it['title']}. " + ", ".join(x for x in (it["publisher"], it["year"]) if x) + "."
    else:
        part = f"“{it['section']}.” " if it["section"] else ""
        out = (f"{who}{part}{it['title']}, {it['publisher']}" + (f", {_date_mla(it['date'])}" if it["date"] else "") +
               (f", {it['url'].removeprefix('https://').removeprefix('http://')}" if it["url"] else "") +
               f". Accessed {_date_mla(it['accessed'])}.")
    return re.sub(r"\s+", " ", out).strip()


def _key(it: dict) -> str:
    base = _split(it["authors"][0])[0] if it["authors"] else "auclib"
    word = next((w for w in re.findall(r"\w+", it["title"]) if len(w) > 3), "item")
    ascii_ = unicodedata.normalize("NFKD", f"{base}{it['year']}{word}").encode("ascii", "ignore").decode()
    return re.sub(r"\W", "", ascii_).lower() or "item"


def _bib(v: str) -> str:
    return v.replace("\\", "\\textbackslash{}").replace("{", "\\{").replace("}", "\\}").replace("%", "\\%").replace("&", "\\&")


def bibtex(items: list[dict]) -> str:
    out, keys = [], set()
    for it in items:
        key = _key(it)
        while key in keys:
            key += "a"
        keys.add(key)
        kind = {"thesis": "phdthesis" if "Doctoral" in _degree(it) else "mastersthesis", "book": "book"}.get(it["type"], "online")
        fields = {"author": " and ".join(it["authors"]), "title": it["title"] + (f": {it['section']}" if kind == "online" and it["section"] else ""),
                  "year": it["year"], "school": it["institution"] if it["type"] == "thesis" else "",
                  "publisher": it["publisher"] if kind in ("book", "online") else "", "organization": "",
                  "url": it["url"], "urldate": it["accessed"] if kind == "online" else "", "isbn": it["isbn"],
                  "doi": it["doi"], "note": f"Advisor: {it['advisor']}" if it["advisor"] else ""}
        body = ",\n".join(f"  {k} = {{{_bib(v)}}}" for k, v in fields.items() if v)
        out.append(f"@{kind}{{{key},\n{body}\n}}")
    return "\n\n".join(out) + "\n"


def ris(items: list[dict]) -> str:
    out = []
    for it in items:
        ty = {"thesis": "THES", "book": "BOOK"}.get(it["type"], "ELEC")
        lines = [("TY", ty), *[("AU", a) for a in it["authors"]], ("TI", it["title"]),
                 ("T2", it["section"] if ty == "ELEC" else ""), ("PY", it["year"]),
                 ("PB", it["institution"] if ty == "THES" else it["publisher"]), ("M3", _degree(it)),
                 ("UR", it["url"]), ("Y2", it["accessed"] if ty == "ELEC" else ""), ("SN", it["isbn"]),
                 ("DO", it["doi"]), ("CN", it["call_number"]), ("N1", f"Advisor: {it['advisor']}" if it["advisor"] else ""),
                 ("ER", "")]
        out.append("\n".join(f"{k}  - {v}".rstrip() if k == "ER" else f"{k}  - {v}" for k, v in lines if v or k == "ER"))
    return "\n".join(out) + "\n"


def enw(items: list[dict]) -> str:
    out = []
    for it in items:
        ty = {"thesis": "Thesis", "book": "Book"}.get(it["type"], "Web Page")
        lines = [("%0", ty), *[("%A", a) for a in it["authors"]], ("%T", it["title"]), ("%D", it["year"]),
                 ("%I", it["institution"] if ty == "Thesis" else it["publisher"]), ("%9", _degree(it)),
                 ("%U", it["url"]), ("%@", it["isbn"]), ("%R", it["doi"]), ("%L", it["call_number"]),
                 ("%Z", f"Advisor: {it['advisor']}" if it["advisor"] else ""),
                 ("%8", it["accessed"] if ty == "Web Page" else "")]
        out.append("\n".join(f"{k} {v}" for k, v in lines if v))
    return "\n\n".join(out) + "\n"


def csl(items: list[dict]) -> str:
    def one(i, it):
        d = {"id": _key(it) + str(i), "type": {"thesis": "thesis", "book": "book"}.get(it["type"], "webpage"),
             "title": it["title"], "URL": it["url"] or None, "publisher": it["institution"] or it["publisher"] or None,
             "genre": _degree(it) or None, "ISBN": it["isbn"] or None, "DOI": it["doi"] or None,
             "accessed": {"date-parts": [[int(x) for x in it["accessed"].split("-")]]}}
        if it["year"].isdigit():
            d["issued"] = {"date-parts": [[int(it["year"])]]}
        if it["authors"]:
            d["author"] = [{"family": last, "given": first} for last, first in map(_split, it["authors"])]
        return {k: v for k, v in d.items() if v is not None}
    return json.dumps([one(i, it) for i, it in enumerate(items)], ensure_ascii=False, indent=1)


def export(sources: list[dict], fmt: str, accessed: str | None = None) -> str:
    items = [item(s, accessed) for s in sources]
    if fmt == "apa":
        return "\n\n".join(apa(i) for i in items) + "\n"
    if fmt == "mla":
        return "\n\n".join(mla(i) for i in items) + "\n"
    return {"bibtex": bibtex, "ris": ris, "enw": enw, "csl": csl}[fmt](items)
