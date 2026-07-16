"""Thin openFDA client: count queries + totals, with retry/backoff."""
import os
import time
import requests

BASE = "https://api.fda.gov"
API_KEY = os.environ.get("OPENFDA_API_KEY", "")

_session = requests.Session()


def _get(endpoint: str, params: dict, retries: int = 5) -> dict:
    if API_KEY:
        params = {**params, "api_key": API_KEY}
    url = f"{BASE}{endpoint}"
    for attempt in range(retries):
        try:
            r = _session.get(url, params=params, timeout=60)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            # openFDA returns 404 for "no results" on count queries
            return {"results": [], "meta": {"results": {"total": 0}}}
        if r.status_code in (429, 500, 502, 503):
            time.sleep(2 ** attempt + 1)
            continue
        r.raise_for_status()
    raise RuntimeError(f"openFDA gave up after {retries} retries: {endpoint} {params}")


def count(endpoint: str, search: str, field: str, limit: int = 100) -> list[dict]:
    """Return [{'term': ..., 'count': ...}] for a count aggregation.
    Without an API key openFDA rejects count limits above 500."""
    if not API_KEY:
        limit = min(limit, 500)
    data = _get(endpoint, {"search": search, "count": field, "limit": limit})
    return data.get("results", [])


def total(endpoint: str, search: str) -> int:
    """Total matching records for a search."""
    data = _get(endpoint, {"search": search, "limit": 1})
    return data.get("meta", {}).get("results", {}).get("total", 0)


def quote(term: str) -> str:
    """Quote a term for an .exact field query."""
    return '"' + term.replace('"', "") + '"'
