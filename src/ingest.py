"""Pull drug-event (FAERS) and device-event (MAUDE) pair counts from openFDA.

Strategy: rather than downloading quarterly extracts, we use openFDA count
aggregations to build 2x2 contingency tables per product-event pair:
  a           = reports mentioning product AND event
  drug_total  = reports mentioning product
  event_total = reports mentioning event
  N           = all reports in window
All restricted to serious reports within the screening window.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import openfda

FAERS = "/drug/event.json"
MAUDE = "/device/event.json"


@dataclass
class SourceData:
    source: str                       # 'faers' | 'maude'
    window: tuple[str, str]           # (YYYYMMDD, YYYYMMDD)
    n_total: int = 0
    product_totals: dict = field(default_factory=dict)   # product -> count
    event_totals: dict = field(default_factory=dict)     # event -> count
    pair_counts: dict = field(default_factory=dict)      # (product, event) -> count


def window_str(days: int, end: date | None = None) -> tuple[str, str]:
    end = end or date.today()
    start = end - timedelta(days=days)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _faers_base_search(w: tuple[str, str]) -> str:
    return f"receivedate:[{w[0]} TO {w[1]}] AND serious:1"


def _maude_base_search(w: tuple[str, str]) -> str:
    # Death/injury only; malfunctions rarely support personal-injury torts alone
    return f"date_received:[{w[0]} TO {w[1]}] AND (event_type:death OR event_type:injury)"


FAERS_PRODUCT_FIELD = "patient.drug.openfda.generic_name.exact"
FAERS_EVENT_FIELD = "patient.reaction.reactionmeddrapt.exact"
MAUDE_PRODUCT_FIELD = "device.generic_name.exact"
MAUDE_EVENT_FIELD = "patient.patient_problems.exact"


def ingest(source: str, cfg: dict, log=print) -> SourceData:
    if source == "faers":
        endpoint, base_fn = FAERS, _faers_base_search
        product_field, event_field = FAERS_PRODUCT_FIELD, FAERS_EVENT_FIELD
        top_n = cfg["universe"]["faers_top_drugs"]
        watchlist = cfg["universe"].get("faers_watchlist") or []
    elif source == "maude":
        endpoint, base_fn = MAUDE, _maude_base_search
        product_field, event_field = MAUDE_PRODUCT_FIELD, MAUDE_EVENT_FIELD
        top_n = cfg["universe"]["maude_top_devices"]
        watchlist = cfg["universe"].get("maude_watchlist") or []
    else:
        raise ValueError(source)

    w = window_str(cfg["window"]["screen_days"])
    base = base_fn(w)
    data = SourceData(source=source, window=w)

    data.n_total = openfda.total(endpoint, base)
    log(f"[{source}] N={data.n_total:,} serious reports in window {w[0]}-{w[1]}")

    stoplist = {s.upper() for s in cfg.get("reaction_stoplist", [])}

    # NOTE: .exact queries are case-sensitive — keep terms in original casing.
    products = openfda.count(endpoint, base, product_field, limit=top_n)
    data.product_totals = {p["term"]: p["count"] for p in products}
    for extra in watchlist:
        if extra not in data.product_totals:
            t = openfda.total(endpoint, f"{base} AND {product_field}:{openfda.quote(extra)}")
            if t:
                data.product_totals[extra] = t

    events = openfda.count(endpoint, base, event_field, limit=1000)
    data.event_totals = {
        e["term"]: e["count"] for e in events
        if e["term"].upper() not in stoplist
    }
    log(f"[{source}] {len(data.product_totals)} products, {len(data.event_totals)} event terms")

    per_product = cfg["universe"]["events_per_product"]
    for i, prod in enumerate(data.product_totals, 1):
        search = f"{base} AND {product_field}:{openfda.quote(prod)}"
        rows = openfda.count(endpoint, search, event_field, limit=per_product)
        for r in rows:
            ev = r["term"]
            if ev.upper() in stoplist or ev not in data.event_totals:
                continue
            data.pair_counts[(prod, ev)] = r["count"]
        if i % 50 == 0:
            log(f"[{source}] pair counts: {i}/{len(data.product_totals)} products")
    log(f"[{source}] {len(data.pair_counts):,} candidate pairs")
    return data


def pair_count_in_window(source: str, product: str, event: str,
                         w: tuple[str, str]) -> tuple[int, int, int, int]:
    """(a, product_total, event_total, N) for one pair in an arbitrary window.
    Used for trajectory analysis on flagged pairs."""
    if source == "faers":
        endpoint, base = FAERS, _faers_base_search(w)
        pf, ef = FAERS_PRODUCT_FIELD, FAERS_EVENT_FIELD
    else:
        endpoint, base = MAUDE, _maude_base_search(w)
        pf, ef = MAUDE_PRODUCT_FIELD, MAUDE_EVENT_FIELD
    pq, eq = openfda.quote(product), openfda.quote(event)
    a = openfda.total(endpoint, f"{base} AND {pf}:{pq} AND {ef}:{eq}")
    pt = openfda.total(endpoint, f"{base} AND {pf}:{pq}")
    et = openfda.total(endpoint, f"{base} AND {ef}:{eq}")
    n = openfda.total(endpoint, base)
    return a, pt, et, n
