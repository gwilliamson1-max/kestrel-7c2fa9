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

    by_source = {}
    for src in sources:
        data = ingest.ingest(src, cfg, log=log)
        sigs = stats.screen(data, cfg, log=log)
        by_source[src] = sigs

    # --- Per-source allocation --------------------------------------------- #
    # EB05/PRR are not comparable across FAERS and MAUDE (MAUDE expected counts
    # are structurally tiny, so device ratios dwarf drug ratios). Rank WITHIN
    # each source and take a fixed quota, so drug signals can never be crowded
    # out of the pipeline by devices.
    mix = cfg.get("source_mix", {}) or {}
    quota = {"faers": int(mix.get("enrich_faers", 70)),
             "maude": int(mix.get("enrich_maude", 50))}
    if args.max_flagged:            # testing override: cap each source
        quota = {k: max(1, args.max_flagged // max(len(sources), 1))
                 for k in quota}
    flagged = []
    for src, sigs in by_source.items():
        sigs.sort(key=lambda s: (s.eb05, s.chi2), reverse=True)
        take = sigs[:quota.get(src, 60)]
        log(f"[{src}] {len(sigs)} screened -> {len(take)} taken for enrichment")
        flagged.extend(take)

    # --- Device known-complication screen (MAUDE) --------------------------- #
    # Tier 1 (event IS the device's mechanism, e.g. atherectomy catheter /
    # embolism) is dropped here; tier 2 (disclosed procedural complication) is
    # flagged now and downweighted after scoring.
    from . import device_screen
    flagged, dev_dropped = device_screen.partition_and_flag(flagged, cfg, log=log)

    # --- False-positive screen, pass 1 (pre-enrichment) --------------------- #
    # Drops indication-confounded pairs (the "event" is the disease the drug
    # treats, e.g. faricimab / retinal thickening) and pairs with implausibly
    # high PRR / chi-square, BEFORE we spend enrichment + LLM budget on signals
    # we're going to discard. Trajectory isn't computed yet, so the
    # spike/collapse quarterly check is deferred to pass 2.
    flagged, filtered = confounding.partition(flagged, cfg, log=log)
    filtered += dev_dropped

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

    # --- Known-complication downweight (post-scoring, MAUDE tier 2) --------- #
    n_known = device_screen.apply_penalty(flagged, cfg, log=log)
    if n_known:
        log(f"[device] downweighted {n_known} known-complication device signals")

    jpath = report.write_signals_json(flagged)
    mpath = report.write_memo_html(flagged, cfg)
    log(f"Wrote {jpath} and {mpath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
