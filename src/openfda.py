"""Thin openFDA client: count queries + totals, with retry/backoff."""
import os
import time
import requests

BASE = "https://api.fda.gov"
API_KEY = os.environ.get("OPENFDA_API_KEY", "")

_session = requests.Session()


_EMPTY = {"results": [], "meta": {"results": {"total": 0}}}


def _get(endpoint: str, params: dict, retries: int = 6) -> dict:
    """GET an openFDA endpoint with retry/backoff.

    Resilience policy: a single failing query must never abort an unattended
    weekly run. Transient errors (network, 429, 5xx) are retried with
    exponential backoff; if they persist past `retries`, we log loudly and
    return an empty result so the caller simply skips that product/pair rather
    than crashing the whole pipeline. openFDA transient 500/503 bursts are
    common, and every downstream screen already tolerates missing data."""
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
            return dict(_EMPTY)
        if r.status_code == 400:
            # malformed query — usually corrupted characters in product names
            # (e.g. mangled trademark symbols in MAUDE). Skip, don't crash.
            print(f"[openfda] 400 Bad Request, skipping: "
                  f"{str(params.get('search', ''))[:140]}", flush=True)
            return dict(_EMPTY)
        if r.status_code in (408, 429, 500, 502, 503, 504):
            time.sleep(2 ** attempt + 1)
            continue
        # any other unexpected status: log and skip rather than raise
        print(f"[openfda] HTTP {r.status_code}, skipping: "
              f"{str(params.get('search', ''))[:140]}", flush=True)
        return dict(_EMPTY)
    # exhausted retries on a transient error — degrade gracefully, don't abort
    print(f"[openfda] gave up after {retries} retries (transient), skipping: "
          f"{endpoint} {str(params.get('search', ''))[:140]}", flush=True)
    return dict(_EMPTY)


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
