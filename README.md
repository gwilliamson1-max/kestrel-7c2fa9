# Mass Tort Signal Engine

Weekly pipeline that mines FDA adverse-event data (FAERS drugs + MAUDE devices) for emerging mass-tort candidates, scores them for litigation viability with the Claude API, and delivers a ranked memo by email plus a live dashboard.

## How it works

1. **Ingest** — openFDA count aggregations build 2×2 contingency tables per product-event pair over a trailing 365-day window of *serious* reports. (No multi-GB quarterly extracts needed; the count API gives the same tables with rolling freshness.)
2. **Screen** — classic disproportionality thresholds: PRR ≥ 2, χ² ≥ 4 (Yates), ≥ 3 cases. Nonspecific reactions (nausea, headache, "drug ineffective"…) are stoplisted in `config.yaml`.
3. **Trajectory** — for flagged pairs, PRR is recomputed per quarter over the last 6 quarters. Accelerating signals outrank flat-high ones.
4. **Enrich** — PubMed hit counts + recent titles, current FDA label check (is the risk in the warnings? failure-to-warn opening), CourtListener docket search (is someone already litigating it?).
5. **Score** — Claude scores each signal on injury severity/objectivity, mechanism plausibility, label gap, crowding, and trajectory → 0–100 viability + pursue/monitor/pass + rationale + likely defendants.
6. **Deliver** — `docs/signals.json` + `docs/index.html` (GitHub Pages dashboard) and an HTML memo emailed to your list.

## Setup (one time, ~15 minutes)

1. **Create a private GitHub repo** and push this folder:
   ```
   git init && git add -A && git commit -m "v1"
   git remote add origin git@github.com:YOU/mass-tort-signal-engine.git
   git push -u origin main
   ```
2. **Add repository secrets** (Settings → Secrets and variables → Actions):
   | Secret | Required | Where to get it |
   |---|---|---|
   | `ANTHROPIC_API_KEY` | yes | console.anthropic.com |
   | `SMTP_SERVER` / `SMTP_PORT` | yes | e.g. `smtp.gmail.com` / `465` |
   | `SMTP_USERNAME` / `SMTP_PASSWORD` | yes | Gmail: use an [app password](https://myaccount.google.com/apppasswords) |
   | `MEMO_RECIPIENTS` | yes | comma-separated emails |
   | `OPENFDA_API_KEY` | recommended | open.fda.gov/apis/authentication — lifts rate limit to 240 req/min |
   | `COURTLISTENER_TOKEN` | optional | courtlistener.com account → API token |
   | `NCBI_API_KEY` | optional | ncbi.nlm.nih.gov account settings |
3. **Enable GitHub Pages**: Settings → Pages → Deploy from branch → `main` / `/docs`. Your dashboard will be at `https://YOU.github.io/mass-tort-signal-engine/`. If the repo is private, Pages requires a paid plan — alternatively open `docs/index.html` locally after pulling.
4. **Test run**: Actions tab → "Weekly tort signal run" → Run workflow. It then runs every Monday automatically.

## Local testing

```
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
python -m src.run_pipeline --source faers --top 25 --max-flagged 10   # small run
python -m src.run_pipeline --source both --skip-llm                   # stats only
```

## Tuning

Everything lives in `config.yaml`: window length, universe sizes, thresholds, reaction stoplist, watchlist products you always want screened, LLM model, memo size.

## Cost & runtime notes

- A full weekly run makes roughly 2,000–4,000 openFDA calls (free; get the API key) and ~15 Claude API calls (a few dollars with Sonnet).
- Runtime is dominated by rate-limited API calls: expect 30–90 minutes in Actions. The workflow timeout is set to 3 hours.

## Roadmap (v2 candidates)

- EBGM (empirical Bayes) via full quarterly extracts for less noise on rare events
- RxNorm/GUDID normalization to merge name variants openFDA harmonization misses
- JPML pending-motion scraper for earlier crowding detection
- Orange Book / patent status and defendant solvency enrichment

**Disclaimer:** disproportionality statistics are hypothesis-generating screens, not evidence of causation. Attorney review required before acting on any signal.
