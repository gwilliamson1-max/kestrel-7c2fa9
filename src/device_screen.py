"""Device known-complication screen (MAUDE) — the device analogue of the drug
indication-confounding screen.

Why this exists: src/confounding.py resolves a drug's indications from
/drug/label.json and a drug concept map, so it does NOTHING for devices. That let
signals like "CATHETER, PERIPHERAL, ATHERECTOMY / Embolism" reach the memo — an
atherectomy catheter's whole job is to break up and remove obstructive plaque, so
embolic debris is the mechanism of the procedure, not a novel drug-style injury.
Same family: phacoemulsification handpieces and corneal edema, hip femoral stems
and periprosthetic fracture.

Two tiers, because these are not all equally dead:

  TIER 1 — MECHANISM / PURPOSE (dropped).
    The reported event IS what the device does or acts upon. An atherectomy or
    embolectomy catheter reporting "embolus"; a lithotripter reporting stone
    fragmentation; an insulin pump reporting hyper/hypoglycemia. There is no
    failure-to-warn theory in telling a surgeon that the plaque-removal device
    encountered plaque debris.

  TIER 2 — KNOWN DISCLOSED COMPLICATION (downweighted + flagged, NOT dropped).
    A recognized, consented-for complication of the procedure: corneal edema
    after phacoemulsification, periprosthetic fracture around a femoral stem.
    These are poor failure-to-warn cases because the risk is disclosed in the
    IFU and consent — but they remain live as DESIGN-DEFECT cases if a specific
    model shows an excess rate versus its peers. So they are kept, sunk in the
    ranking, and flagged with that instruction.

Deliberately conservative. Real device torts run on exactly this data — metal-on-
metal hip ion release, surgical mesh erosion, IVC filter fracture/migration — and
none of those appear below. When in doubt a pair is left alone. Both maps are
config-editable under `device_screen:` in config.yaml.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# TIER 1 — the event is the device's mechanism / what it acts upon -> DROP
# device-name substring -> event terms
# --------------------------------------------------------------------------- #
MECHANISM_EVENTS: dict[str, set[str]] = {
    "atherectomy": {"embolism", "embolus", "embolization", "distal embolization",
                    "plaque", "debris", "particulate"},
    "embolectomy": {"embolism", "embolus", "embolization", "thrombus", "thrombosis"},
    "thrombectomy": {"thrombus", "thrombosis", "embolism", "embolus",
                     "embolization", "clot"},
    "lithotripsy": {"calculus", "stone fragment", "fragmentation", "stone"},
    "lithotripter": {"calculus", "stone fragment", "fragmentation", "stone"},
    "defibrillator": {"shock delivered", "electrical shock delivered",
                      "inappropriate shock"},
    "insulin infusion pump": {"hyperglycemia", "hypoglycemia", "hyperglycaemia",
                              "hypoglycaemia", "high blood sugar", "low blood sugar"},
    "insulin pump": {"hyperglycemia", "hypoglycemia", "hyperglycaemia",
                     "hypoglycaemia"},
}

# --------------------------------------------------------------------------- #
# TIER 2 — recognized, disclosed procedural complication -> DOWNWEIGHT + FLAG
# --------------------------------------------------------------------------- #
KNOWN_COMPLICATIONS: dict[str, set[str]] = {
    # cataract surgery (phacoemulsification)
    "phacofragmentation": {"corneal edema", "corneal oedema", "capsular bag tear",
                           "capsule rupture", "posterior capsule rupture",
                           "hypopyon", "endophthalmitis", "iris trauma",
                           "corneal decompensation"},
    "phacoemulsification": {"corneal edema", "corneal oedema", "capsular bag tear",
                            "capsule rupture", "hypopyon", "endophthalmitis"},
    # hip / knee arthroplasty structural complications
    "femoral stem": {"limb fracture", "periprosthetic fracture", "bone fracture",
                     "femur fracture", "dislocation", "subsidence"},
    "hip femoral": {"limb fracture", "periprosthetic fracture", "bone fracture",
                    "dislocation", "subsidence"},
    "prosthesis, knee": {"loosening", "stiffness", "reduced range of motion"},
    # indwelling vascular access
    "catheter, intravascular": {"thrombosis", "thrombus", "occlusion", "phlebitis",
                                "catheter related infection"},
    "port & catheter": {"thrombosis", "thrombus", "occlusion",
                        "catheter related infection"},
    # coronary/peripheral stents
    "stent": {"restenosis", "in-stent restenosis", "vascular dissection",
              "dissection"},
    # transcatheter valves
    "aortic valve": {"paravalvular leak", "aortic valve insufficiency",
                     "aortic valve regurgitation", "conduction disorder",
                     "atrioventricular block"},
}


def _norm(t: str) -> str:
    t = (t or "").lower().strip()
    t = t.replace("oedema", "edema")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _cfg(cfg: dict) -> dict:
    return (cfg or {}).get("device_screen", {}) or {}


def _merged(base: dict, extra) -> dict:
    """Merge config-supplied additions into a built-in map."""
    out = {k: set(v) for k, v in base.items()}
    for k, v in (extra or {}).items():
        out.setdefault(_norm(k), set()).update(_norm(x) for x in (v or []))
    return out


def _event_matches(event_norm: str, terms: set[str]) -> bool:
    if not event_norm:
        return False
    for t in terms:
        t = _norm(t)
        if not t:
            continue
        if event_norm == t or t in event_norm or event_norm in t:
            return True
    return False


def assess_device(product: str, event: str, cfg: dict) -> dict | None:
    """Return None (nothing to do), or a verdict dict:
       {'tier': 1, 'action': 'drop',      'reason': ...}
       {'tier': 2, 'action': 'downweight','reason': ...}"""
    if not _cfg(cfg).get("enabled", True):
        return None
    p, e = _norm(product), _norm(event)

    mech = _merged(MECHANISM_EVENTS, _cfg(cfg).get("mechanism_events"))
    known = _merged(KNOWN_COMPLICATIONS, _cfg(cfg).get("known_complications"))

    for dev_sub, terms in mech.items():
        if _norm(dev_sub) in p and _event_matches(e, terms):
            return {
                "tier": 1, "action": "drop",
                "reason": (f'"{event}" is the operating mechanism of {product} — '
                           f'the device acts on/produces exactly this. Reporting it '
                           f'is not a signature injury and supports no duty-to-warn '
                           f'theory.'),
            }
    for dev_sub, terms in known.items():
        if _norm(dev_sub) in p and _event_matches(e, terms):
            return {
                "tier": 2, "action": "downweight",
                "reason": (f'"{event}" is a recognized, disclosed complication of '
                           f'{product} (covered by the IFU and surgical consent), '
                           f'so failure-to-warn is weak. Still viable as a DESIGN-'
                           f'DEFECT case only if a specific model shows an excess '
                           f'rate versus peer devices — check for model-level '
                           f'clustering before discarding.'),
            }
    return None


def partition_and_flag(signals: list, cfg: dict, log=print):
    """Split MAUDE signals: tier-1 mechanism confounds are dropped; tier-2 known
    complications are kept but marked with `.device_flag` for later downweight.
    Returns (kept, dropped)."""
    kept, dropped = [], []
    for sig in signals:
        if getattr(sig, "source", None) != "maude":
            kept.append(sig)
            continue
        v = assess_device(sig.product, sig.event, cfg)
        if v and v["action"] == "drop":
            sig.filtered = {"disposition": "drop_device_mechanism",
                            "reasons": [v["reason"]]}
            dropped.append(sig)
            log(f"[device] DROP {sig.product} / {sig.event} — mechanism of the device")
        else:
            if v:
                sig.device_flag = {"flag": "known_complication",
                                   "note": v["reason"]}
                log(f"[device] known-complication flag {sig.product} / {sig.event}")
            kept.append(sig)
    return kept, dropped


def apply_penalty(signals: list, cfg: dict, log=print) -> int:
    """Downweight scored signals carrying a tier-2 known-complication flag."""
    c = _cfg(cfg)
    if not c.get("enabled", True):
        return 0
    penalty = float(c.get("known_complication_penalty", 0.35))
    demote_below = float(c.get("demote_to_monitor_below", 60))
    n = 0
    for sig in signals:
        if not getattr(sig, "device_flag", None):
            continue
        sc = sig.score
        if isinstance(sc, dict) and isinstance(sc.get("viability"), (int, float)):
            raw = sc["viability"]
            sc.setdefault("viability_raw", raw)
            sc["viability"] = round(raw * penalty)
            sc["known_complication_adjusted"] = True
            if sc["viability"] < demote_below and sc.get("recommendation") == "pursue":
                sc["recommendation"] = "monitor"
        n += 1
        log(f"[device] downweight {sig.product} / {sig.event} (known complication)")
    return n
