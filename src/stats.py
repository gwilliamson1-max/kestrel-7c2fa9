"""Disproportionality statistics: PRR, ROR, Yates chi-square, trajectory slope."""
from dataclasses import dataclass, asdict


@dataclass
class Signal:
    source: str
    product: str
    event: str
    a: int              # reports with product AND event
    product_total: int
    event_total: int
    n: int
    prr: float = 0.0
    ror: float = 0.0
    chi2: float = 0.0
    trajectory: list = None      # [{'quarter': 'YYYYQn', 'prr': x, 'a': n}, ...]
    prr_slope: float = 0.0       # avg quarter-over-quarter PRR change
    enrichment: dict = None
    score: dict = None

    def to_dict(self):
        return asdict(self)


def compute_stats(a: int, product_total: int, event_total: int, n: int):
    """Return (prr, ror, chi2) from the implied 2x2 table."""
    b = max(product_total - a, 0)
    c = max(event_total - a, 0)
    d = max(n - product_total - event_total + a, 0)
    prr = ror = chi2 = 0.0
    # PRR = [a/(a+b)] / [c/(c+d)]
    if (a + b) > 0 and (c + d) > 0 and c > 0:
        prr = (a / (a + b)) / (c / (c + d))
    if b > 0 and c > 0:
        ror = (a * d) / (b * c)
    row1, row2, col1, col2 = a + b, c + d, a + c, b + d
    if row1 and row2 and col1 and col2:
        num = (abs(a * d - b * c) - n / 2)
        if num > 0:
            chi2 = n * num * num / (row1 * row2 * col1 * col2)
    return round(prr, 2), round(ror, 2), round(chi2, 2)


def screen(data, cfg) -> list[Signal]:
    """Apply the classic screen (PRR >= 2, chi2 >= 4, >= 3 cases) to a SourceData."""
    th = cfg["thresholds"]
    out = []
    for (product, event), a in data.pair_counts.items():
        if a < th["case_min"]:
            continue
        pt = data.product_totals.get(product, 0)
        et = data.event_totals.get(event, 0)
        if not pt or not et:
            continue
        prr, ror, chi2 = compute_stats(a, pt, et, data.n_total)
        if prr >= th["prr_min"] and chi2 >= th["chi2_min"]:
            out.append(Signal(source=data.source, product=product, event=event,
                              a=a, product_total=pt, event_total=et,
                              n=data.n_total, prr=prr, ror=ror, chi2=chi2))
    out.sort(key=lambda s: s.chi2, reverse=True)
    return out


def quarters_back(k: int, today=None):
    """Last k complete quarters as (label, (YYYYMMDD, YYYYMMDD)), oldest first."""
    from datetime import date
    today = today or date.today()
    y, q = today.year, (today.month - 1) // 3 + 1
    out = []
    for _ in range(k):
        q -= 1
        if q == 0:
            q, y = 4, y - 1
        start_month = 3 * (q - 1) + 1
        end_month = start_month + 2
        last_day = {3: 31, 6: 30, 9: 30, 12: 31}[end_month]
        out.append((f"{y}Q{q}",
                    (f"{y}{start_month:02d}01", f"{y}{end_month:02d}{last_day}")))
    return list(reversed(out))


def add_trajectory(signal: Signal, cfg, cache: dict, log=print):
    """Per-quarter PRR for a flagged pair. `cache` shares per-quarter N /
    product / event totals across signals to minimize API calls."""
    from . import ingest, openfda
    qs = quarters_back(cfg["window"]["trajectory_quarters"])
    traj = []
    src = signal.source
    if src == "faers":
        endpoint, base_fn = ingest.FAERS, ingest._faers_base_search
        pf, ef = ingest.FAERS_PRODUCT_FIELD, ingest.FAERS_EVENT_FIELD
    else:
        endpoint, base_fn = ingest.MAUDE, ingest._maude_base_search
        pf, ef = ingest.MAUDE_PRODUCT_FIELD, ingest.MAUDE_EVENT_FIELD

    for label, w in qs:
        base = base_fn(w)
        nk = (src, label, "N")
        if nk not in cache:
            cache[nk] = openfda.total(endpoint, base)
        pk = (src, label, "P", signal.product)
        if pk not in cache:
            cache[pk] = openfda.total(
                endpoint, f"{base} AND {pf}:{openfda.quote(signal.product)}")
        ek = (src, label, "E", signal.event)
        if ek not in cache:
            cache[ek] = openfda.total(
                endpoint, f"{base} AND {ef}:{openfda.quote(signal.event)}")
        a = openfda.total(
            endpoint,
            f"{base} AND {pf}:{openfda.quote(signal.product)}"
            f" AND {ef}:{openfda.quote(signal.event)}")
        prr, _, _ = compute_stats(a, cache[pk], cache[ek], cache[nk]) if cache[nk] else (0, 0, 0)
        traj.append({"quarter": label, "a": a, "prr": prr})

    signal.trajectory = traj
    diffs = [traj[i]["prr"] - traj[i - 1]["prr"] for i in range(1, len(traj))]
    signal.prr_slope = round(sum(diffs) / len(diffs), 2) if diffs else 0.0
