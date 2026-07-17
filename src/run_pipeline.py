"""Orchestrator. Run:  python -m src.run_pipeline [--source faers|maude|both]
                        [--skip-llm] [--top N] [--max-flagged N]"""
import argparse
import sys
from pathlib import Path

import yaml

from . import ingest, stats, enrich, report, confounding

ROOT = Path(__file__).resolve().parent.parent


def log(msg):
    print(msg, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="both", choices=["faers", "maude", "both"])
    ap.add_argument("--skip-llm", action="store_true",
                    help="skip Claude scoring (testing)")
    ap.add_argument("--top", type=int, default=0,
                    help="override product universe size (testing)")
    ap.add_argument("--max-flagged", type=int, default=0,
                    help="override enrichment cap (testing)")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    if args.top:
        cfg["universe"]["faers_top_drugs"] = args.top
        cfg["universe"]["maude_top_devices"] = args.top
    if args.max_flagged:
        cfg["thresholds"]["max_flagged_for_enrichment"] = args.max_flagged

    sources = ["faers", "maude"] if args.source == "both" else [args.source]

    all_signals = []
    for src in sources:
        data = ingest.ingest(src, cfg, log=log)
        sigs = stats.screen(data, cfg)
        log(f"[{src}] {len(sigs)} pairs pass the statistical screen")
        all_signals.extend(sigs)

    # cap for trajectory + enrichment + LLM (ranked by chi2 within the screen)
    all_signals.sort(key=lambda s: s.chi2, reverse=True)
    flagged = all_signals[:cfg["thresholds"]["max_flagged_for_enrichment"]]

    # --- False-positive screen, pass 1 (pre-enrichment) --------------------- #
    # Drops indication-confounded pairs (the "event" is the disease the drug
    # treats, e.g. faricimab / retinal thickening) and pairs with implausibly
    # high PRR / chi-square, BEFORE we spend enrichment + LLM budget on signals
    # we're going to discard. Trajectory isn't computed yet, so the
    # spike/collapse quarterly check is deferred to pass 2.
    flagged, filtered = confounding.partition(flagged, cfg, log=log)

    cache = {}
    for i, sig in enumerate(flagged, 1):
        stats.add_trajectory(sig, cfg, cache, log=log)
        enrich.enrich_signal(sig, cfg, log=log)
        if i % 10 == 0:
            log(f"[enrich] {i}/{len(flagged)}")

    # --- False-positive screen, pass 2 (post-trajectory) -------------------- #
    # Now that per-quarter PRR exists, catch spike-then-collapse artifacts that
    # a single-period PRR under the ceiling would miss.
    flagged, filtered2 = confounding.partition(flagged, cfg, log=log)
    filtered += filtered2

    if filtered:
        fpath = confounding.dump_filtered(filtered, ROOT)
        log(f"[filter] removed {len(filtered)} false positives -> {fpath}")

    if not args.skip_llm and flagged:
        from . import score_llm
        score_llm.score_signals(flagged, cfg, log=log)

    # --- Generic-availability downweight (post-scoring) --------------------- #
    # Drugs with generics on the market carry heavy failure-to-warn preemption
    # risk (Mensing/Bartlett). Downweight viability and flag; do NOT drop, so
    # brand-window and innovator-liability signals survive for review.
    from . import generics
    n_generic = generics.apply_penalty(flagged, cfg, log=log)
    if n_generic:
        log(f"[generic] downweighted {n_generic} generic-available signals")

    # --- Device federal-preemption downweight (post-scoring, MAUDE) --------- #
    # PMA / Class III devices carry Riegel express preemption of design/warning
    # claims. Downweight viability and flag; do NOT drop (parallel claims survive).
    from . import preemption
    n_preempt = preemption.apply_penalty(flagged, cfg, log=log)
    if n_preempt:
        log(f"[preemption] downweighted {n_preempt} PMA/Class-III device signals")

    jpath = report.write_signals_json(flagged)
    mpath = report.write_memo_html(flagged, cfg)
    log(f"Wrote {jpath} and {mpath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
