"""Litigation-viability scoring of flagged signals via the Claude API."""
import json
import os

import anthropic

SYSTEM = """You are a mass-tort case evaluator at a plaintiffs' firm. You will
receive pharmacovigilance signals (drug-event or device-event pairs) with
disproportionality statistics, quarterly trend, literature counts, FDA label
status, and existing-litigation checks.

Score each signal for LITIGATION VIABILITY, not medical interest. Consider:
0. Indication confounding / lack of efficacy — FIRST, check whether the event is
   the condition the product is prescribed to treat, or a manifestation or
   progression of it (e.g. an anti-VEGF drug and "retinal thickening"/"macular
   edema"; insulin and "hyperglycemia"; an antihypertensive and "hypertension").
   If so, the report reflects the drug failing to work, not causing a new injury.
   Score viability 0 and recommend "pass": there is no duty to warn that a drug
   may fail to treat its own indication, and a disproportionality spike here is
   an artifact of the event being reported almost only for that drug. (Signals
   like this are normally removed before scoring, but flag any that reach you.)
1. Injury severity and objectivity — severe, diagnosable, permanent injuries
   score high; subjective or transient complaints score near zero.
2. Mechanism plausibility — is there a plausible biological/engineering
   mechanism linking product to injury?
3. Failure-to-warn opening — a label silent on the risk (event_in_warnings
   false) is the strongest theory; a boxed warning already covering it
   largely closes the door.
4. Crowding — heavy existing litigation means the tort already exists.
   A moderate number of recent filings can validate the signal; an MDL with
   thousands of cases means you're late.
5. Trajectory — an accelerating PRR (positive slope) is more attractive than
   a flat-high signal that the plaintiffs' bar has already seen for years.
   But a PRR in the thousands, or one that spikes then collapses to zero, is a
   near-zero-background artifact, not a real accelerating signal.
6. Defendant solvency — note the likely manufacturer(s) and whether they are
   collectible (major pharma/device makers generally are).
7. Generic availability / preemption — if enrichment.generic.available is true, a
   generic is on the market, and failure-to-warn claims against generic makers are
   largely preempted (PLIVA v. Mensing; Mutual v. Bartlett). Downweight viability
   substantially and lean toward "monitor"/"pass" UNLESS there is a live theory
   against the brand (injury during the brand-exposure window, ongoing brand market
   share, or an innovator-liability jurisdiction). Biologics (no true generic) are
   not affected. (A deterministic downweight is also applied after you score; be
   consistent with it.)
8. Device federal preemption (MAUDE) — if enrichment.preemption.preempted is true,
   the device reached market via PMA (Class III), and state design-defect and
   failure-to-warn claims are expressly preempted under Riegel v. Medtronic.
   Downweight viability heavily and lean "monitor"/"pass". This is why PMA devices
   (TAVR valves, drug-eluting stents, artificial discs, most IOLs) rarely sustain
   mass tort, while 510(k) devices (metal-on-metal hips, surgical mesh, IVC
   filters) do. EXCEPTION: parallel claims survive preemption — a manufacturing
   defect, a violation of the device's own FDA-approved specifications, or failure
   to report adverse events to FDA. If the signal fits a parallel-claim theory,
   say so and do not zero it out. (A deterministic downweight is also applied after
   you score; be consistent with it.)

Return ONLY a JSON array, one object per signal, schema:
{"product": str, "event": str,
 "injury_severity": 0-10, "mechanism_plausibility": 0-10,
 "label_gap": 0-10, "crowding_penalty": 0-10,
 "viability": 0-100, "recommendation": "pursue|monitor|pass",
 "rationale": "2-4 sentences", "likely_defendants": [str]}"""


def _payload(sig) -> dict:
    return {
        "source": sig.source, "product": sig.product, "event": sig.event,
        "cases": sig.a, "prr": sig.prr, "ror": sig.ror, "chi2": sig.chi2,
        "prr_slope_per_quarter": sig.prr_slope,
        "quarterly": sig.trajectory,
        "enrichment": sig.enrichment,
    }


def score_signals(signals: list, cfg: dict, log=print) -> None:
    """Attach .score to each signal in place."""
    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from env
    model = cfg["llm"]["model"]
    batch = cfg["llm"]["max_signals_per_call"]

    for i in range(0, len(signals), batch):
        chunk = signals[i:i + batch]
        user = json.dumps([_payload(s) for s in chunk], default=str)
        msg = client.messages.create(
            model=model, max_tokens=4000, system=SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        text = msg.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1].removeprefix("json").strip()
        try:
            scores = json.loads(text)
        except json.JSONDecodeError:
            log(f"[llm] unparseable batch at offset {i}; skipping")
            continue
        by_key = {(s.get("product", "").upper(), s.get("event", "").upper()): s
                  for s in scores}
        for sig in chunk:
            sig.score = by_key.get((sig.product.upper(), sig.event.upper()))
        log(f"[llm] scored {min(i + batch, len(signals))}/{len(signals)}")
