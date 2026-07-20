"""Disproportionality statistics: PRR, ROR, Yates chi-square, expected/RRR,
BCPNN IC/IC025, ROR 95% CI, empirical-Bayes EBGM/EB05, trajectory slope."""
import math
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
    expected: float = 0.0        # expected count E = product_total*event_total/N
    rrr: float = 0.0             # relative report ratio = a / E
    ror025: float = 0.0          # lower bound of ROR 95% CI
    ic: float = 0.0              # BCPNN information component
    ic025: float = 0.0           # lower 2.5% bound of IC (Noren approximation)
    ebgm: float = 0.0            # empirical-Bayes geometric mean (MGPS)
    eb05: float = 0.0            # 5th percentile of EB posterior — signal score
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


def expected_count(product_total: int, event_total: int, n: int) -> float:
    """E = (drug_total * event_total) / N — reports expected under independence."""
    return (product_total * event_total / n) if n else 0.0


def ror_ci_low(a, product_total, event_total, n, z=1.96):
    """Lower bound of the ROR 95% CI (0.5 continuity correction on empty cells)."""
    b = product_total - a
    c = event_total - a
    d = n - product_total - event_total + a
    if min(a, b, c, d) <= 0:
        a, b, c, d = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    if b <= 0 or c <= 0:
        return 0.0
    ln_ror = math.log((a * d) / (b * c))
    se = math.sqrt(1 / a + 1 / b + 1 / c + 1 / d)
    return round(math.exp(ln_ror - z * se), 2)


def ic_ic025(a: int, E: float):
    """BCPNN information component and its lower 2.5% bound (Noren 2006 approx).
    IC025 >= 0 is the WHO/Uppsala signal threshold."""
    if E <= 0:
        return 0.0, 0.0
    ic = math.log2((a + 0.5) / (E + 0.5))
    ic025 = ic - 3.3 * (a + 0.5) ** -0.5 - 2.0 * (a + 0.5) ** -1.5
    return round(ic, 2), round(ic025, 2)


def screen(data, cfg, log=print) -> list["Signal"]:
    """Classic screen (PRR >= 2, chi2 >= 4, >= 3 cases) PLUS the empirical-Bayes
    EB05 gate. Fits one MGPS prior on all pairs in this source, then keeps pairs
    that clear both the frequentist screen and EB05 >= threshold."""
    th = cfg["thresholds"]
    bayes = (cfg or {}).get("bayesian", {}) or {}
    eb05_gate = bayes.get("eb05_gate", True)
    eb05_min = float(bayes.get("eb05_min", 2.0))
    ic025_min = bayes.get("ic025_min", None)   # optional extra gate; None = off

    # 1) Fit the MGPS prior on ALL pairs in this source (needs the broad set).
    from . import ebgm
    fit_n, fit_E = [], []
    for (product, event), a in data.pair_counts.items():
        pt = data.product_totals.get(product, 0)
        et = data.event_totals.get(event, 0)
        E = expected_count(pt, et, data.n_total)
        if E > 0:
            fit_n.append(a)
            fit_E.append(E)
    model = ebgm.MGPS().fit(fit_n, fit_E, log=log)

    # 2) Screen + score each candidate pair.
    out = []
    for (product, event), a in data.pair_counts.items():
        if a < th["case_min"]:
            continue
        pt = data.product_totals.get(product, 0)
        et = data.event_totals.get(event, 0)
        if not pt or not et:
            continue
        prr, ror, chi2 = compute_stats(a, pt, et, data.n_total)
        if not (prr >= th["prr_min"] and chi2 >= th["chi2_min"]):
            continue
        E = expected_count(pt, et, data.n_total)
        ebgm_v, eb05_v = model.ebgm_eb05(a, E)
        ic, ic025 = ic_ic025(a, E)
        ror025 = ror_ci_low(a, pt, et, data.n_total)

        if eb05_gate and eb05_v is not None and eb05_v < eb05_min:
            continue
        if ic025_min is not None and ic025 < float(ic025_min):
            continue

        out.append(Signal(
            source=data.source, product=product, event=event,
            a=a, product_total=pt, event_total=et, n=data.n_total,
            prr=prr, ror=ror, chi2=chi2,
            expected=round(E, 2), rrr=round(a / E, 2) if E else 0.0,
            ror025=ror025, ic=ic, ic025=ic025,
            ebgm=ebgm_v or 0.0, eb05=eb05_v or 0.0))

    # Rank by EB05 (reliability-adjusted) when available, else chi2.
    out.sort(key=lambda s: (s.eb05, s.chi2), reverse=True)
    log(f"[{data.source}] EBGM prior: {model.mode}; "
        f"{len(out)} pairs pass PRR/chi2"
        f"{' + EB05>=%.1f' % eb05_min if eb05_gate else ''}")
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
