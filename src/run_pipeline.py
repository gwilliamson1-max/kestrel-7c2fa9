"""Orchestrator. Run:  python -m src.run_pipeline [--source faers|maude|both]
                        [--skip-llm] [--top N] [--max-flagged N]"""
import argparse
import sys
from pathlib import Path

import yaml

from . import ingest, stats, enrich, report

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

    cache = {}
    for i, sig in enumerate(flagged, 1):
        stats.add_trajectory(sig, cfg, cache, log=log)
        enrich.enrich_signal(sig, cfg, log=log)
        if i % 10 == 0:
            log(f"[enrich] {i}/{len(flagged)}")

    if not args.skip_llm and flagged:
        from . import score_llm
        score_llm.score_signals(flagged, cfg, log=log)

    jpath = report.write_signals_json(flagged)
    mpath = report.write_memo_html(flagged, cfg)
    log(f"Wrote {jpath} and {mpath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
