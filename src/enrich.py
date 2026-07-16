"""Enrichment for flagged signals: PubMed literature, FDA label check,
CourtListener litigation cross-check."""
import os
import time
import requests

from . import openfda

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
COURTLISTENER = "https://www.courtlistener.com/api/rest/v4/search/"

_session = requests.Session()


def _get_json(url, params, headers=None, retries=3):
    for attempt in range(retries):
        try:
            r = _session.get(url, params=params, headers=headers or {}, timeout=45)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(2 ** attempt + 1)
                continue
            return None
        except requests.RequestException:
            time.sleep(2 ** attempt)
    return None


def pubmed(product: str, event: str, max_titles: int = 5) -> dict:
    """Hit count + recent titles for the product-injury pair."""
    term = f'("{product}") AND ("{event}")'
    params = {"db": "pubmed", "term": term, "retmode": "json",
              "retmax": max_titles, "sort": "date"}
    key = os.environ.get("NCBI_API_KEY")
    if key:
        params["api_key"] = key
    data = _get_json(f"{EUTILS}/esearch.fcgi", params)
    if not data:
        return {"count": None, "titles": []}
    res = data.get("esearchresult", {})
    count = int(res.get("count", 0))
    ids = res.get("idlist", [])
    titles = []
    if ids:
        time.sleep(0.4)  # NCBI rate courtesy
        p2 = {"db": "pubmed", "id": ",".join(ids), "retmode": "json"}
        if key:
            p2["api_key"] = key
        summ = _get_json(f"{EUTILS}/esummary.fcgi", p2)
        if summ:
            for uid in ids:
                doc = summ.get("result", {}).get(uid, {})
                if doc.get("title"):
                    titles.append({"pmid": uid, "title": doc["title"],
                                   "date": doc.get("pubdate", "")})
    return {"count": count, "titles": titles}


def label_check(product: str, event: str) -> dict:
    """Does the current FDA label's warnings text mention the event?
    Silence on the risk supports a failure-to-warn theory."""
    search = f"openfda.generic_name:{openfda.quote(product)}"
    data = openfda._get("/drug/label.json", {"search": search, "limit": 1})
    results = data.get("results", [])
    if not results:
        return {"label_found": False, "event_in_warnings": None}
    lbl = results[0]
    sections = []
    for f in ("boxed_warning", "warnings", "warnings_and_cautions",
              "adverse_reactions", "precautions"):
        v = lbl.get(f)
        if v:
            sections.append(" ".join(v) if isinstance(v, list) else str(v))
    text = " ".join(sections).upper()
    # crude containment check; the LLM sees the verdict plus a snippet
    hit = event.upper() in text
    snippet = ""
    if hit:
        i = text.find(event.upper())
        snippet = text[max(0, i - 150):i + 150]
    return {"label_found": True, "event_in_warnings": hit,
            "warning_snippet": snippet,
            "has_boxed_warning": bool(lbl.get("boxed_warning"))}


def courtlistener(product: str, event: str, max_hits: int = 10) -> dict:
    """Federal docket search for existing litigation on the pair."""
    headers = {}
    token = os.environ.get("COURTLISTENER_TOKEN")
    if token:
        headers["Authorization"] = f"Token {token}"
    q = f'"{product}" "{event}"'
    data = _get_json(COURTLISTENER, {"q": q, "type": "r", "order_by": "score desc"},
                     headers=headers)
    if data is None:
        # retry with product only — pair phrasing often too narrow
        data = _get_json(COURTLISTENER,
                         {"q": f'"{product}" product liability', "type": "r"},
                         headers=headers)
    if not data:
        return {"docket_hits": None, "sample_cases": []}
    cases = [{"caseName": r.get("caseName"), "court": r.get("court"),
              "dateFiled": r.get("dateFiled"),
              "docket_id": r.get("docket_id")}
             for r in data.get("results", [])[:max_hits]]
    return {"docket_hits": data.get("count"), "sample_cases": cases}


def enrich_signal(sig, cfg, log=print):
    e = {}
    e["pubmed"] = pubmed(sig.product, sig.event,
                         cfg["enrichment"]["pubmed_max_titles"])
    if sig.source == "faers":
        e["label"] = label_check(sig.product, sig.event)
    e["litigation"] = courtlistener(sig.product, sig.event,
                                    cfg["enrichment"]["courtlistener_max_hits"])
    sig.enrichment = e
