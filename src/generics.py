"""Generic-availability screen (downweight + flag, not drop).

Rationale (litigation, not pharmacology): for a drug with generic equivalents on
the market, a failure-to-warn claim against the generic manufacturer is largely
barred by federal preemption -- PLIVA v. Mensing (2011) and Mutual Pharmaceutical
v. Bartlett (2013) hold that generic makers cannot unilaterally change their
labeling, so state-law failure-to-warn and design-defect claims against them are
preempted. That guts the usual mass-tort theory once a drug has gone generic.

But generic availability is NOT a categorical bar, so this screen DOWNWEIGHTS and
FLAGS rather than dropping:
  - The BRANDED manufacturer remains liable for the exposure window before
    genericization, and for its own market share after (e.g. Zantac/NDMA ran for
    years as brand and OTC).
  - A minority of states recognize innovator/brand liability for injuries from a
    generic (e.g. Calif. T.H. v. Novartis; Mo.), where the brand wrote the label.
  - Design-defect and other theories can survive in specific postures.

Detection: openFDA NDC directory. If any marketed product under the drug's
generic name has an ANDA (Abbreviated New Drug Application) or "NDA authorized
generic" marketing category, a generic is on the market. Biologics (BLA) have
biosimilars, not generics, and a different preemption analysis -- they are NOT
treated as generic here.
"""
from __future__ import annotations

import collections

from . import openfda

_cache: dict[str, dict] = {}


def _gcfg(cfg: dict) -> dict:
    return (cfg or {}).get("generic_filter", {}) or {}


def generic_available(product: str, cfg: dict | None = None) -> dict:
    """Return {'available': bool, 'categories': {...}, 'evidence': str}.
    Cached per product. Never raises."""
    key = (product or "").strip().upper()
    if key in _cache:
        return _cache[key]

    # config override: force-classify specific drugs (substring, case-insensitive)
    overrides = _gcfg(cfg or {}).get("overrides", {}) or {}
    for sub, val in overrides.items():
        if sub.upper() in key:
            res = {"available": bool(val), "categories": {}, "evidence": "config override"}
            _cache[key] = res
            return res

    cats: collections.Counter = collections.Counter()
    try:
        data = openfda._get(
            "/drug/ndc.json",
            {"search": f'generic_name:"{product}"', "limit": 50},
        )
        for r in data.get("results", []):
            cats[(r.get("marketing_category") or "?").upper()] += 1
    except Exception:
        pass

    anda = sum(v for k, v in cats.items()
               if k.startswith("ANDA") or "AUTHORIZED GENERIC" in k)
    available = anda > 0
    if available:
        evidence = f"{anda} ANDA/authorized-generic NDC listings on the market"
    elif any(k.startswith("BLA") for k in cats):
        evidence = "biologic (BLA) — biosimilar pathway, not generic; brand theory intact"
    elif cats:
        evidence = "no ANDA listings found — appears brand-only"
    else:
        evidence = "no NDC data — treated as brand (not downweighted)"

    res = {"available": available, "categories": dict(cats), "evidence": evidence}
    _cache[key] = res
    return res


def annotate_enrichment(sig, cfg: dict) -> None:
    """Attach generic status to a signal's enrichment packet (so it flows to the
    LLM payload, the dashboard, and signals.json). FAERS drugs only; devices have
    no generic analogue."""
    if not _gcfg(cfg).get("enabled", True):
        return
    if getattr(sig, "source", None) != "faers":
        return
    if sig.enrichment is None:
        sig.enrichment = {}
    sig.enrichment["generic"] = generic_available(sig.product, cfg)


def apply_penalty(signals: list, cfg: dict, log=print) -> int:
    """Deterministic downweight for scored signals whose drug has a generic on the
    market. Records the raw score and attaches a `.generic_flag`. Returns the
    count adjusted. LLM viability stays the source of truth; this guarantees the
    downweight the user asked for and makes it transparent (raw + adjusted shown).
    """
    gc = _gcfg(cfg)
    if not gc.get("enabled", True):
        return 0
    penalty = float(gc.get("penalty", 0.5))
    demote_below = float(gc.get("demote_to_monitor_below", 60))

    n = 0
    for sig in signals:
        if getattr(sig, "source", None) != "faers":
            continue
        gen = (sig.enrichment or {}).get("generic") or generic_available(sig.product, cfg)
        if not gen.get("available"):
            continue
        sig.generic_flag = {
            "flag": "generic_available",
            "note": (f"Generic equivalents are on the market for {sig.product} "
                     f"({gen.get('evidence')}). Failure-to-warn against the generic "
                     f"maker is largely preempted (Mensing/Bartlett); viability is "
                     f"downweighted. Brand-exposure-window and innovator-liability "
                     f"theories may still apply — review before passing."),
        }
        sc = sig.score
        if isinstance(sc, dict) and isinstance(sc.get("viability"), (int, float)):
            raw = sc["viability"]
            sc["viability_raw"] = raw
            sc["viability"] = round(raw * penalty)
            sc["generic_adjusted"] = True
            if sc["viability"] < demote_below and sc.get("recommendation") == "pursue":
                sc["recommendation"] = "monitor"
        n += 1
        log(f"[generic] downweight {sig.product} / {sig.event} "
            f"({gen.get('evidence')})")
    return n
