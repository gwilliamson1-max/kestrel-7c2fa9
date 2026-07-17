"""Indication-confounding + implausible-statistics screen.

Removes two classes of false-positive signal that survive the classic
disproportionality screen and the generic reaction stoplist:

  1. INDICATION CONFOUNDING / LACK OF EFFICACY
     The "adverse event" is really the disease the drug is prescribed to treat
     (or a manifestation of it). Reporting it is evidence the drug didn't work,
     not that it caused a new injury -- and there is no duty to warn that a drug
     may fail to cure its own indication, so these make hopeless failure-to-warn
     cases.

     The generic stoplist (DRUG INEFFECTIVE, CONDITION AGGRAVATED, ...) does not
     catch these, because the reported term is a *specific* clinical term that
     looks like an injury. Example: Faricimab (an anti-VEGF whose job is to
     REDUCE retinal/macular thickening in wet AMD and diabetic macular edema)
     paired with the event "Retinal thickening."

     Detection is concept-based and, importantly, partly self-updating: for each
     drug we assemble the set of "indication concepts" from (a) a small curated
     map and (b) the drug's own openFDA `indications_and_usage` label text, plus
     (c) a fully general direct match of the event term against that label text.
     So a brand-new anti-VEGF we've never hard-coded still gets caught, and
     rituximab / pemphigus is flagged straight from rituximab's own label.

  2. IMPLAUSIBLE STATISTICS
     A PRR in the thousands or a chi-square in the hundreds of thousands is not
     a strong signal -- it is the arithmetic signature of a near-zero background
     denominator (the event is reported almost only for this one drug, which is
     exactly what happens when the "event" is the indication). A quarterly PRR
     series that spikes and then collapses to zero is reporting noise on a tiny
     base, not an emerging trend.

Policy: indication-confounded signals are DROPPED (not injuries). Implausible-
statistics signals are dropped from the PURSUE memo and recorded to
docs/filtered.json for human review, so nothing is silently lost. Kept signals
whose event is a bidirectional off-label / paradoxical use of the drug (e.g.
adalimumab / pemphigus) are ANNOTATED with a review flag, not dropped.

Surgical by design: this must remove "event == the on-label indication" confounds
ONLY. It must NOT suppress genuine, mechanistically distinct injuries for the
same drug -- e.g. faricimab-associated retinal vasculitis or intraocular
inflammation, which are real, litigable signals.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import openfda


# --------------------------------------------------------------------------- #
# Concept model
# --------------------------------------------------------------------------- #
# A "concept" groups the many ways a single clinical idea gets coded. If the
# adverse-event term falls into a concept that is part of the drug's indication,
# the pair is confounded.
INDICATION_CONCEPTS: dict[str, set[str]] = {
    # Increased retinal/macular thickness & fluid -- what anti-VEGF drugs treat.
    "retinal_edema": {
        "retinal thickening", "macular thickening", "macular edema",
        "retinal edema", "diabetic macular edema", "cystoid macular edema",
        "central subfield thickness", "central subfield thickness increased",
        "retinal fluid", "subretinal fluid", "intraretinal fluid",
        "macular degeneration", "age related macular degeneration",
        "neovascular age related macular degeneration",
        "choroidal neovascularisation", "choroidal neovascularization",
        "retinal vein occlusion",  # anti-VEGF indication (macular edema 2/2 RVO)
    },
    "hyperglycemia": {
        "hyperglycemia", "blood glucose increased", "diabetes mellitus",
        "type 2 diabetes mellitus", "type 1 diabetes mellitus",
        "high blood sugar", "hyperglycaemic",
    },
    "hypertension_ind": {
        "hypertension", "high blood pressure", "blood pressure increased",
        "essential hypertension",
    },
    "hypothyroid_ind": {"hypothyroidism", "goitre", "myxoedema"},
    "depression_ind": {
        "depression", "major depressive disorder", "depressive disorder",
    },
    "seizure_ind": {"epilepsy", "seizure disorder", "partial seizures"},
    # Inflammatory arthritis & its manifestations -- what DMARDs / TNF & IL
    # blockers / JAK inhibitors treat. Reporting the disease (or its structural
    # progression) is lack of efficacy, not a drug injury.
    "inflammatory_arthritis": {
        "rheumatoid arthritis", "psoriatic arthritis",
        "juvenile idiopathic arthritis", "juvenile rheumatoid arthritis",
        "ankylosing spondylitis", "axial spondyloarthritis",
        "synovitis", "inflammatory arthritis", "joint swelling",
        "joint deformity", "hand deformity", "joint destruction",
    },
    # Autoimmune blistering disease treated by rituximab (and others).
    "pemphigus": {"pemphigus", "pemphigus vulgaris", "pemphigus foliaceus"},
}

# Curated drug/class -> indication concepts. Substring match on the (normalized)
# product name, so "FARICIMAB", "faricimab-svoa", etc. all hit "faricimab".
DRUG_INDICATION_CONCEPTS: dict[str, set[str]] = {
    # anti-VEGF ophthalmic biologics
    "faricimab": {"retinal_edema"},
    "aflibercept": {"retinal_edema"},
    "ranibizumab": {"retinal_edema"},
    "brolucizumab": {"retinal_edema"},
    "bevacizumab": {"retinal_edema"},   # off-label intravitreal
    "pegaptanib": {"retinal_edema"},
    # a few common classes, to show the mechanism generalizes
    "insulin": {"hyperglycemia"},
    "metformin": {"hyperglycemia"},
    "glargine": {"hyperglycemia"},
    "empagliflozin": {"hyperglycemia"},
    "levothyroxine": {"hypothyroid_ind"},
    "amlodipine": {"hypertension_ind"},
    "lisinopril": {"hypertension_ind"},
    "losartan": {"hypertension_ind"},
    "sertraline": {"depression_ind"},
    "escitalopram": {"depression_ind"},
    "levetiracetam": {"seizure_ind"},
    "lamotrigine": {"seizure_ind"},
    # DMARDs / biologics / JAK inhibitors for inflammatory arthritis
    "adalimumab": {"inflammatory_arthritis"},
    "etanercept": {"inflammatory_arthritis"},
    "infliximab": {"inflammatory_arthritis"},
    "golimumab": {"inflammatory_arthritis"},
    "certolizumab": {"inflammatory_arthritis"},
    "tocilizumab": {"inflammatory_arthritis"},
    "methotrexate": {"inflammatory_arthritis"},
    "leflunomide": {"inflammatory_arthritis"},
    "tofacitinib": {"inflammatory_arthritis"},
    "upadacitinib": {"inflammatory_arthritis"},
    "baricitinib": {"inflammatory_arthritis"},
    "rituximab": {"inflammatory_arthritis", "pemphigus"},  # RA + pemphigus vulgaris
}

# Explicit lack-of-efficacy terms (belt-and-suspenders; most are also stoplisted).
LACK_OF_EFFICACY = {
    "drug ineffective", "drug effect decreased", "therapeutic response decreased",
    "condition aggravated", "disease progression", "disease recurrence",
    "no therapeutic response", "therapeutic product ineffective",
}

# Bidirectional drug/event relationships: the event is a documented OFF-LABEL use
# of the drug AND a recognized paradoxical drug-induced reaction. These are NOT
# dropped (the event is not an on-label indication, and de novo onset is a
# genuine, litigable drug injury), but they are ANNOTATED so the reviewer knows
# the reported population is mixed -- some patients received the drug to TREAT the
# condition (off-label, treatment failure, no tort), others developed it de novo
# after exposure (paradoxical induction, the litigable subset).
OFF_LABEL_BIDIRECTIONAL: dict[str, set[str]] = {
    # TNF inhibitors: off-label steroid-sparing therapy for refractory pemphigus /
    # bullous pemphigoid, and also a recognized paradoxical trigger of the same.
    "adalimumab": {"pemphigus", "bullous pemphigoid", "pemphigoid"},
    "etanercept": {"pemphigus", "bullous pemphigoid", "pemphigoid"},
    "infliximab": {"pemphigus", "bullous pemphigoid", "pemphigoid"},
    "golimumab": {"pemphigus", "bullous pemphigoid", "pemphigoid"},
    "certolizumab": {"pemphigus", "bullous pemphigoid", "pemphigoid"},
}

_label_cache: dict[str, str] = {}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _norm(text: str) -> str:
    text = (text or "").lower().strip()
    text = text.replace("oedema", "edema")            # UK -> US
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _ccfg(cfg: dict) -> dict:
    return (cfg or {}).get("confounding", {}) or {}


def _label_indication_text(product: str) -> str:
    """openFDA `indications_and_usage` text for a product, cached. Never raises."""
    key = _norm(product)
    if key in _label_cache:
        return _label_cache[key]
    text = ""
    try:
        for field in ("generic_name", "brand_name"):
            data = openfda._get(
                "/drug/label.json",
                {"search": f"openfda.{field}:{openfda.quote(product)}", "limit": 1},
            )
            results = data.get("results", [])
            if results:
                blob = results[0].get("indications_and_usage", [])
                text = " ".join(blob) if isinstance(blob, list) else str(blob)
                if text:
                    break
    except Exception:
        text = ""
    _label_cache[key] = text
    return text


def active_concepts(product: str, cfg: dict, use_openfda: bool = True) -> set[str]:
    """Indication concepts for a drug, from the curated map + its openFDA label."""
    concepts: set[str] = set()
    pnorm = _norm(product)

    # 1. curated map (substring match)
    for drug_sub, cs in DRUG_INDICATION_CONCEPTS.items():
        if drug_sub in pnorm:
            concepts |= cs

    # 2. label-derived: activate any concept whose member phrase appears in the
    #    drug's own indications text (self-updating for unseen drugs).
    if use_openfda:
        ind_text = _norm(_label_indication_text(product))
        if ind_text:
            for concept, members in INDICATION_CONCEPTS.items():
                if any(m in ind_text for m in members):
                    concepts.add(concept)

    return concepts


def _member_phrases(concepts: set[str], cfg: dict) -> set[str]:
    phrases: set[str] = set()
    for c in concepts:
        phrases |= INDICATION_CONCEPTS.get(c, set())
    return {_norm(p) for p in phrases}


def _event_in_indication_text(event_norm: str, ind_text: str) -> bool:
    """True if the adverse-event term literally appears in the drug's own FDA
    indications_and_usage text. This is the general, hard-coding-free catch: if
    a drug is *indicated to treat* the reported event, reporting that event is
    lack of efficacy, not a new injury. Requires a reasonably specific term and
    that all of its significant tokens appear, to avoid spurious hits."""
    if not event_norm or not ind_text or len(event_norm) < 5:
        return False
    if event_norm in ind_text:
        return True
    toks = [t for t in event_norm.split() if len(t) > 3]
    return bool(toks) and all(t in ind_text for t in toks)


def _event_in_phrases(event_norm: str, phrases: set[str]) -> bool:
    if not event_norm or len(event_norm) < 4:
        return False
    ev_tokens = [t for t in event_norm.split() if len(t) > 3]
    for p in phrases:
        if not p:
            continue
        if event_norm == p or event_norm in p or p in event_norm:
            return True
        # all significant tokens of the event present in the phrase
        if ev_tokens and all(tok in p for tok in ev_tokens):
            return True
    return False


# --------------------------------------------------------------------------- #
# Screen 1: indication confounding
# --------------------------------------------------------------------------- #
def indication_confounded(product: str, event: str, cfg: dict,
                          use_openfda: bool = True) -> tuple[bool, str]:
    ev = _norm(event)

    if ev in {_norm(t) for t in LACK_OF_EFFICACY}:
        return True, (f'"{event}" is a lack-of-efficacy term, not a drug-caused '
                      f'injury.')

    # config override: indication_overrides: {drug_substring: [terms...]}
    overrides = _ccfg(cfg).get("indication_overrides", {}) or {}
    pnorm = _norm(product)
    for drug_sub, terms in overrides.items():
        if _norm(drug_sub) in pnorm:
            if _event_in_phrases(ev, {_norm(t) for t in terms}):
                return True, (f'"{event}" is a configured indication for '
                              f'{product}.')

    # (a) Direct match: the event term literally appears in the drug's own FDA
    #     indications text. General, needs no hard-coded drug knowledge.
    if use_openfda:
        ind_text = _norm(_label_indication_text(product))
        if _event_in_indication_text(ev, ind_text):
            return True, (f'"{event}" appears in {product}\'s FDA '
                          f'indications-and-usage (the drug is prescribed to '
                          f'treat it). Confounding by indication / lack of '
                          f'efficacy, not a signature injury.')

    # (b) Concept match: curated + label-derived indication concepts, which also
    #     bridge synonyms/manifestations (e.g. retinal thickening <-> macular
    #     edema; synovitis <-> rheumatoid arthritis).
    concepts = active_concepts(product, cfg, use_openfda=use_openfda)
    if not concepts:
        return False, ""
    phrases = _member_phrases(concepts, cfg)
    if _event_in_phrases(ev, phrases):
        return True, (f'"{event}" is the treated indication for {product} '
                      f'(concept: {", ".join(sorted(concepts))}). Confounding by '
                      f'indication / lack of efficacy, not a signature injury.')
    return False, ""


# --------------------------------------------------------------------------- #
# Review annotation (kept signals): bidirectional off-label / paradoxical events
# --------------------------------------------------------------------------- #
def review_flag(product: str, event: str) -> dict | None:
    """Annotation for a KEPT signal whose event is a documented off-label use of
    the drug that is ALSO a recognized paradoxical drug-induced reaction. Does
    not drop or rescore the signal -- it flags the mixed population so the
    reviewer separates treatment-failure reports from de novo drug-induced cases.
    Returns a flag dict or None."""
    ev = _norm(event)
    pnorm = _norm(product)
    for drug_sub, terms in OFF_LABEL_BIDIRECTIONAL.items():
        if drug_sub in pnorm and _event_in_phrases(ev, {_norm(t) for t in terms}):
            return {
                "flag": "indication_ambiguity",
                "note": (f'"{event}" is a documented off-label use of {product} '
                         f'(steroid-sparing anti-TNF for refractory disease) AND '
                         f'a recognized paradoxical drug-induced reaction. Reports '
                         f'are a mixed population: patients whose {event.lower()} '
                         f'predates exposure are treatment-failure (no tort); the '
                         f'litigable subset is de novo onset after starting the '
                         f'drug. Screen for prior diagnosis and temporal sequence.'),
            }
    return None


# --------------------------------------------------------------------------- #
# Screen 2: implausible statistics
# --------------------------------------------------------------------------- #
def _traj_prr_series(sig) -> list[float]:
    traj = getattr(sig, "trajectory", None) or []
    out = []
    for q in traj:
        try:
            out.append(float(q.get("prr", 0) or 0))
        except (TypeError, ValueError):
            out.append(0.0)
    return out


def stats_implausible(sig, cfg: dict, use_trajectory: bool = True) -> tuple[bool, str]:
    c = _ccfg(cfg)
    prr_ceiling = float(c.get("prr_ceiling", 1000))
    chi2_ceiling = float(c.get("chi2_ceiling", 100_000))
    instability = float(c.get("quarter_instability_ratio", 50))

    reasons: list[str] = []
    prr = getattr(sig, "prr", 0) or 0
    chi2 = getattr(sig, "chi2", 0) or 0

    if prr > prr_ceiling:
        reasons.append(
            f"PRR {prr:,.0f} exceeds ceiling {prr_ceiling:,.0f} (near-zero "
            f"background; event reported almost only for this product)")
    if chi2 > chi2_ceiling:
        reasons.append(
            f"chi-square {chi2:,.0f} exceeds ceiling {chi2_ceiling:,.0f} "
            f"(degenerate 2x2 table)")

    if use_trajectory:
        series = _traj_prr_series(sig)
        nz = [x for x in series if x > 0]
        unstable = False
        if len(series) >= 2 and series[-1] == 0 and max(series) > prr_ceiling:
            unstable = True
        for a, b in zip(nz, nz[1:]):
            lo, hi = sorted((a, b))
            if lo > 0 and hi / lo > instability:
                unstable = True
                break
        if unstable:
            reasons.append(
                f"quarterly PRR {[round(x, 1) for x in series]} is a "
                f"spike/collapse artifact, not a sustained trend")

    return (bool(reasons), "; ".join(reasons))


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def assess(sig, cfg: dict, use_openfda: bool = True) -> dict:
    """Return {'disposition': 'keep'|'drop_indication'|'drop_stats',
               'reasons': [...]}. Indication checked first (definitively not an
    injury); implausible stats second."""
    if not _ccfg(cfg).get("enabled", True):
        return {"disposition": "keep", "reasons": []}

    ind, ireason = indication_confounded(sig.product, sig.event, cfg,
                                         use_openfda=use_openfda)
    if ind:
        return {"disposition": "drop_indication", "reasons": [ireason]}

    imp, sreason = stats_implausible(sig, cfg)
    if imp:
        return {"disposition": "drop_stats", "reasons": [sreason]}

    # kept -- attach a review annotation if this is a bidirectional off-label /
    # paradoxical event (does not affect the disposition or score)
    return {"disposition": "keep", "reasons": [],
            "review": review_flag(sig.product, sig.event)}


def partition(signals: list, cfg: dict, log=print,
              use_openfda: bool = True) -> tuple[list, list]:
    """Split into (kept, filtered). Filtered signals get a `.filtered` dict.
    Kept signals get a `.review` attribute (a flag dict or None)."""
    kept, filtered = [], []
    for sig in signals:
        verdict = assess(sig, cfg, use_openfda=use_openfda)
        if verdict["disposition"] == "keep":
            sig.review = verdict.get("review")
            kept.append(sig)
        else:
            sig.filtered = verdict
            filtered.append(sig)
            reason = verdict["reasons"][0] if verdict["reasons"] else ""
            log(f"[filter] DROP {sig.product} / {sig.event} "
                f"[{verdict['disposition']}] — {reason}")
    return kept, filtered


def dump_filtered(filtered: list, root: Path) -> Path:
    """Write filtered signals + reasons to docs/filtered.json for audit."""
    out = []
    for sig in filtered:
        row = {
            "product": sig.product, "event": sig.event,
            "prr": getattr(sig, "prr", None), "chi2": getattr(sig, "chi2", None),
            "disposition": sig.filtered["disposition"],
            "reasons": sig.filtered["reasons"],
        }
        out.append(row)
    path = Path(root) / "docs" / "filtered.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2))
    return path
