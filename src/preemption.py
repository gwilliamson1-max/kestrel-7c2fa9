"""Device federal-preemption screen (downweight + flag, not drop) — MAUDE side.

Rationale (litigation, not engineering): a medical device that reached market
through FDA Premarket Approval (PMA, the Class III pathway) carries express
federal preemption of state-law tort claims under Riegel v. Medtronic (2008) —
because the PMA imposes federal "requirements," state design-defect and
failure-to-warn claims that would impose different requirements are preempted.
That is a near-dispositive barrier for the usual mass-tort theories.

By contrast, a device cleared through 510(k) substantial-equivalence is NOT
preempted (Medtronic v. Lohr, 1996) — 510(k) is not a finding of safety and
imposes no device-specific federal requirement. This is exactly why the big
device MDLs are 510(k) devices (metal-on-metal hips, surgical mesh, IVC filters)
and why PMA devices (TAVR valves, drug-eluting stents, artificial discs, most
IOLs) rarely sustain mass litigation.

So this screen DOWNWEIGHTS and FLAGS PMA/Class-III signals; it does not drop
them, because Riegel preserves "parallel claims" — state claims premised on a
violation of the device's own FDA requirements (manufacturing-defect / failure
to follow the approved specs, failure to report adverse events to FDA, off-label
promotion). Those survive preemption, so a PMA device is not categorically dead.

Detection (openFDA, MAUDE side):
  1. Read the device's FDA product code + device_class from its MAUDE event
     records (device_report_product_code, openfda.device_class).
  2. Class III AND at least one PMA on that product code -> PMA pathway ->
     preempted. Class III with only 510(k)s (transitional/pre-amendment, the
     Lohr situation) -> NOT preempted. Class I/II -> not preempted.
"""
from __future__ import annotations

import collections

from . import openfda

_cache: dict[str, dict] = {}


def _pcfg(cfg: dict) -> dict:
    return (cfg or {}).get("preemption_filter", {}) or {}


def device_pathway(generic: str, cfg: dict | None = None) -> dict:
    """Return {'product_code','device_class','pma_count','k510_count',
    'pathway','preempted','evidence'} for a MAUDE device generic name. Cached;
    never raises."""
    key = (generic or "").strip().upper()
    if key in _cache:
        return _cache[key]

    overrides = _pcfg(cfg or {}).get("overrides", {}) or {}
    for sub, val in overrides.items():
        if sub.upper() in key:
            res = {"product_code": None, "device_class": None, "pma_count": None,
                   "k510_count": None, "pathway": "override",
                   "preempted": bool(val), "evidence": "config override"}
            _cache[key] = res
            return res

    code = klass = None
    try:
        d = openfda._get("/device/event.json",
                         {"search": f'device.generic_name:"{generic}"', "limit": 50})
        codes: collections.Counter = collections.Counter()
        classes: collections.Counter = collections.Counter()
        for r in d.get("results", []):
            for dev in r.get("device", []):
                c = dev.get("device_report_product_code")
                of = dev.get("openfda", {}) or {}
                if c:
                    codes[c] += 1
                if of.get("device_class"):
                    classes[of["device_class"]] += 1
        code = codes.most_common(1)[0][0] if codes else None
        klass = classes.most_common(1)[0][0] if classes else None
    except Exception:
        pass

    pma_count = k510_count = None
    preempted = False
    if code:
        try:
            if klass == "3":
                pma_count = openfda.total("/device/pma.json", f"product_code:{code}")
                k510_count = openfda.total("/device/510k.json", f"product_code:{code}")
        except Exception:
            pass

    if klass == "3" and (pma_count or 0) > 0:
        pathway = "PMA (Class III)"
        preempted = True
        evidence = (f"Class III device (product code {code}) with {pma_count} PMA "
                    f"approval(s) — Riegel express preemption of design/warning claims")
    elif klass == "3":
        pathway = "Class III, no PMA found (transitional/510(k))"
        evidence = (f"Class III (code {code}) but no PMA located; likely 510(k)/"
                    f"transitional — Lohr, not preempted")
    elif klass in ("1", "2"):
        pathway = f"Class {klass} (510(k)/exempt)"
        evidence = (f"Class {klass} device (code {code}) — 510(k)/exempt pathway; "
                    f"Lohr, not preempted")
    else:
        pathway = "unknown"
        evidence = "could not determine FDA pathway from MAUDE data"

    res = {"product_code": code, "device_class": klass, "pma_count": pma_count,
           "k510_count": k510_count, "pathway": pathway, "preempted": preempted,
           "evidence": evidence}
    _cache[key] = res
    return res


def annotate_enrichment(sig, cfg: dict) -> None:
    """Attach device pathway to a MAUDE signal's enrichment packet."""
    if not _pcfg(cfg).get("enabled", True):
        return
    if getattr(sig, "source", None) != "maude":
        return
    if sig.enrichment is None:
        sig.enrichment = {}
    sig.enrichment["preemption"] = device_pathway(sig.product, cfg)


def apply_penalty(signals: list, cfg: dict, log=print) -> int:
    """Downweight scored MAUDE signals on PMA/Class-III devices (Riegel
    preemption). Mirrors generics.apply_penalty. Returns count adjusted."""
    pc = _pcfg(cfg)
    if not pc.get("enabled", True):
        return 0
    penalty = float(pc.get("penalty", 0.3))
    demote_below = float(pc.get("demote_to_monitor_below", 60))

    n = 0
    for sig in signals:
        if getattr(sig, "source", None) != "maude":
            continue
        pw = (sig.enrichment or {}).get("preemption") or device_pathway(sig.product, cfg)
        if not pw.get("preempted"):
            continue
        sig.preemption_flag = {
            "flag": "device_preemption",
            "note": (f"{sig.product} is a {pw.get('pathway')} device. Riegel v. "
                     f"Medtronic preempts state design-defect and failure-to-warn "
                     f"claims on PMA devices; viability is downweighted. Parallel "
                     f"claims (manufacturing defect / violation of the device's own "
                     f"FDA-approved requirements, failure to report to FDA) survive "
                     f"preemption — assess those before passing."),
        }
        sc = sig.score
        if isinstance(sc, dict) and isinstance(sc.get("viability"), (int, float)):
            raw = sc["viability"]
            sc.setdefault("viability_raw", raw)
            sc["viability"] = round(raw * penalty)
            sc["preemption_adjusted"] = True
            if sc["viability"] < demote_below and sc.get("recommendation") == "pursue":
                sc["recommendation"] = "monitor"
        n += 1
        log(f"[preemption] downweight {sig.product} / {sig.event} ({pw.get('pathway')})")
    return n
