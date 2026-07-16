"""Litigation-viability scoring of flagged signals via the Claude API."""
import json
import os

import anthropic

SYSTEM = """You are a mass-tort case evaluator at a plaintiffs' firm. You will
receive pharmacovigilance signals (drug-event or device-event pairs) with
disproportionality statistics, quarterly trend, literature counts, FDA label
status, and existing-litigation checks.

Score each signal for LITIGATION VIABILITY, not medical interest. Consider:
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
6. Defendant solvency — note the likely manufacturer(s) and whether they are
   collectible (major pharma/device makers generally are).

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
